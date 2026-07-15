"""LLM-assisted Memory Curator for EchoSpeak.

The model proposes MemoryCandidates. The deterministic runtime validates
provenance, ownership, safety, scope, duplication, and confidence — and is
the only authority that persists into AgentMemory.records.json.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from loguru import logger
from pydantic import BaseModel, Field

# ── Explicit memory signal phrases ──────────────────────────────────

_EXPLICIT_PREFIXES = (
    "remember that ",
    "remember this ",
    "remember ",
    "please remember that ",
    "please remember this ",
    "please remember ",
    "save this ",
    "save that ",
    "save to memory ",
    "keep this in mind ",
    "keep that in mind ",
    "keep in mind that ",
    "keep in mind ",
    "from now on ",
    "note that ",
    "note this ",
    "don't forget that ",
    "dont forget that ",
    "don't forget ",
    "dont forget ",
    "do not forget that ",
    "do not forget ",
)

_EXPLICIT_INLINE = re.compile(
    r"(?i)\b("
    r"remember\s+(?:this|that|i)|"
    r"save\s+this|"
    r"keep\s+(?:this|that)\s+in\s+mind|"
    r"from\s+now\s+on|"
    r"note\s+that|"
    r"don'?t\s+forget|"
    r"do\s+not\s+forget"
    r")\b"
)

_TEMPORARY_PATTERNS = re.compile(
    r"(?i)\b("
    r"playhead|selected\s+clip|current\s+selection|right\s+now|"
    r"for\s+this\s+one\s+turn|just\s+this\s+once|temporary|"
    r"one[- ]time|today\s+only|this\s+second|"
    r"selected\s+track|visible\s+range"
    r")\b"
)

_SENSITIVE_PATTERNS = re.compile(
    r"(?i)\b("
    r"password|passwd|api[_-]?key|secret|token|bearer|"
    r"ssn|social\s+security|credit\s+card|cvv|private\s+key"
    r")\b"
)

_ASSUMPTION_PATTERNS = re.compile(
    r"(?i)\b("
    r"i\s+think\s+you|you\s+probably|you\s+might\s+be|"
    r"seems\s+like\s+you|you\s+seem|maybe\s+you|"
    r"i\s+guess\s+you|inferred"
    r")\b"
)

_MEMORY_TYPES = frozenset({
    "identity", "preference", "project_convention", "workflow_preference",
    "relationship", "goal", "fact", "instruction", "profile", "note",
    "project", "contacts",
})

_CANONICAL_TYPE_MAP = {
    "identity": "profile",
    "relationship": "contacts",
    "project_convention": "project",
    "workflow_preference": "preference",
    "goal": "note",
    "fact": "note",
    "instruction": "note",
}


class MemoryCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner_id: str = ""
    type: str = "preference"
    scope: str = "account"  # account | project | session
    subject: str = ""
    text: str = ""
    structured_attributes: Dict[str, Any] = Field(default_factory=dict)
    source_session_id: str = ""
    source_execution_id: str = ""
    source_item_id: str = ""
    source_text: str = ""
    explicit: bool = False
    confidence: float = 0.0
    importance: float = 0.0
    expected_lifetime: str = "long_term"  # temporary | weeks | long_term
    sensitivity: str = "normal"  # normal | sensitive
    action: str = "create"  # create | update | supersede | ignore | ask_confirmation
    related_memory_ids: List[str] = Field(default_factory=list)
    reason: str = ""
    semantic_key: str = ""


class CuratorResult(BaseModel):
    """Outcome of a curation pass — never claim save without persisted_ids."""

    candidates_considered: int = 0
    accepted: List[MemoryCandidate] = Field(default_factory=list)
    rejected: List[MemoryCandidate] = Field(default_factory=list)
    needs_confirmation: List[MemoryCandidate] = Field(default_factory=list)
    session_only_ids: List[str] = Field(default_factory=list)
    persisted_ids: List[str] = Field(default_factory=list)
    acknowledgements: List[str] = Field(default_factory=list)
    confirmation_prompt: str = ""
    pending_confirmation_id: str = ""
    errors: List[str] = Field(default_factory=list)
    reflection_used: bool = False
    llm_invoked: bool = False
    llm_failed: bool = False
    used_deterministic_fallback: bool = False


class MemoryCurator:
    """Propose → validate → persist through AgentMemory."""

    _reflection_guard = threading.local()

    def __init__(
        self,
        memory: Any,
        *,
        llm_invoke: Optional[Callable[[str], str]] = None,
        session_store_root: Optional[Path] = None,
    ):
        self.memory = memory
        self.llm_invoke = llm_invoke
        try:
            from config import DATA_DIR
            default_root = Path(DATA_DIR)
        except Exception:
            default_root = Path("data")
        self._session_root = Path(session_store_root or default_root)

    # ── Signal detection ────────────────────────────────────────────

    @staticmethod
    def is_explicit_memory_request(text: str) -> bool:
        s = re.sub(r"(?i)^\s*please\s+", "", str(text or "")).strip()
        if not s:
            return False
        low = s.lower()
        for p in _EXPLICIT_PREFIXES:
            if low.startswith(p):
                return True
        return bool(_EXPLICIT_INLINE.search(s))

    @staticmethod
    def extract_explicit_payload(text: str) -> str:
        """Strip explicit-remember phrasing; return content to curate."""
        s = re.sub(r"(?i)^\s*please\s+", "", str(text or "")).strip()
        if not s:
            return ""
        low = s.lower()
        for p in _EXPLICIT_PREFIXES:
            if low.startswith(p):
                return s[len(p):].strip(" .!?\t")
        # Inline: "From now on, prefer X" → keep full useful sentence after marker
        m = re.search(
            r"(?i)\b(?:remember\s+(?:this|that)[,:]?\s*|keep\s+(?:this|that)\s+in\s+mind[,:]?\s*|"
            r"from\s+now\s+on[,:]?\s*|note\s+that[,:]?\s*|don'?t\s+forget\s+(?:that\s+)?)(.+)$",
            s,
        )
        if m:
            return m.group(1).strip(" .!?\t")
        # Fallback: use existing AgentMemory extractor if available
        try:
            payload = s
            # "Remember I like X" — keep full after remember
            m2 = re.match(r"(?i)^\s*remember\s+(.+)$", s)
            if m2:
                return m2.group(1).strip(" .!?\t")
            return payload
        except Exception:
            return s

    # ── Proposal (LLM + deterministic fallback) ─────────────────────

    def propose_candidates(
        self,
        *,
        user_text: str,
        response_text: str = "",
        explicit: bool = False,
        owner_id: str = "",
        session_id: str = "",
        execution_id: str = "",
        item_id: str = "",
        project_path: str = "",
        user_name: str = "",
        max_candidates: int = 3,
        meta_out: Optional[Dict[str, Any]] = None,
    ) -> List[MemoryCandidate]:
        """LLM is the primary semantic path when available; deterministic is fallback."""
        source = str(user_text or "").strip()
        if not source and not response_text:
            return []
        meta: Dict[str, Any] = meta_out if meta_out is not None else {}
        candidates: List[MemoryCandidate] = []
        llm_ok = False

        # 1) LLM primary for explicit always, and for nuanced implicit scans.
        want_llm = self.llm_invoke is not None and (
            explicit or self._worth_llm_scan(source, response_text)
        )
        if want_llm:
            meta["llm_invoked"] = True
            try:
                llm_cands, llm_errors = self._llm_propose_strict(
                    user_text=source if not explicit else (self.extract_explicit_payload(source) or source),
                    response_text=response_text,
                    explicit=explicit,
                    owner_id=owner_id,
                    session_id=session_id,
                    execution_id=execution_id,
                    item_id=item_id,
                    project_path=project_path,
                    user_name=user_name,
                    max_candidates=max_candidates,
                )
                if llm_errors:
                    meta["llm_failed"] = True
                    meta.setdefault("errors", []).extend(llm_errors)
                if llm_cands:
                    candidates.extend(llm_cands)
                    llm_ok = True
                else:
                    meta["llm_failed"] = True
            except Exception as exc:
                meta["llm_failed"] = True
                meta.setdefault("errors", []).append(f"llm_exception:{exc}")
                logger.debug("Memory curator LLM propose failed: {}", exc)

        # 2) Deterministic fallback when LLM unavailable, failed, or empty.
        if not candidates:
            meta["used_deterministic_fallback"] = True
            if explicit:
                payload = self.extract_explicit_payload(source) or source
                candidates.extend(
                    self._deterministic_rewrite(
                        payload,
                        source_text=source,
                        explicit=True,
                        owner_id=owner_id,
                        session_id=session_id,
                        execution_id=execution_id,
                        item_id=item_id,
                        project_path=project_path,
                        user_name=user_name,
                    )
                )
            else:
                candidates.extend(
                    self._implicit_heuristic(
                        source,
                        owner_id=owner_id,
                        session_id=session_id,
                        execution_id=execution_id,
                        item_id=item_id,
                        project_path=project_path,
                        user_name=user_name,
                    )
                )
        elif not llm_ok and self.llm_invoke is None:
            meta["used_deterministic_fallback"] = True

        # Deduplicate proposed text within this batch (prefer earlier = LLM-first when present)
        seen: set[str] = set()
        unique: List[MemoryCandidate] = []
        for c in candidates:
            key = re.sub(r"\s+", " ", c.text.strip()).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(c)
            if len(unique) >= max_candidates:
                break
        return unique

    def _worth_llm_scan(self, user_text: str, response_text: str) -> bool:
        low = str(user_text or "").lower()
        if len(low) < 12:
            return False
        if _TEMPORARY_PATTERNS.search(low):
            return False
        # Prefer LLM when preference/workflow/nuance language appears
        if re.search(
            r"\b(prefer|always|never|from now on|usually|normally|i like|i dislike|"
            r"don't|do not|convention|workflow|for this project|when coding|"
            r"manual testing|prompt|architecture|but |however|instead|"
            r"used to|now want|for coding agents|detailed|concise|vertical|"
            r"captions?|export)\b",
            low,
        ):
            return True
        return False

    def _user_label(self, user_name: str) -> str:
        name = str(user_name or "").strip()
        if name:
            return name
        # Fall back to profile
        try:
            profile = getattr(self.memory, "_profile", {}) or {}
            n = str(profile.get("user_name") or "").strip()
            if n:
                return n
        except Exception:
            pass
        return "The user"

    def _deterministic_rewrite(
        self,
        payload: str,
        *,
        source_text: str,
        explicit: bool,
        owner_id: str,
        session_id: str,
        execution_id: str,
        item_id: str,
        project_path: str,
        user_name: str,
    ) -> List[MemoryCandidate]:
        clean = re.sub(r"\s+", " ", str(payload or "")).strip(" .!?\t")
        if not clean:
            return []
        label = self._user_label(user_name)
        low = clean.casefold()
        candidates: List[MemoryCandidate] = []

        # Favorite team / hockey
        m = re.search(
            r"(?i)\b(?:i\s+(?:really\s+)?(?:like|love)|my\s+favou?rite\s+(?:hockey|nhl)\s+team\s+is|"
            r"(?:that'?s|thats)\s+my\s+favou?rite\s+(?:hockey|nhl)\s+team)\b",
            clean,
        )
        team = re.search(
            r"(?i)\b(?:edmonton\s+oilers|calgary\s+flames|toronto\s+maple\s+leafs|"
            r"vancouver\s+canucks|[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2})\b",
            clean,
        )
        if m or re.search(r"(?i)\bfavou?rite\s+(?:hockey|nhl)\s+team\b", clean):
            # Extract team name more carefully
            m2 = re.search(
                r"(?i)(?:like|love|is)\s+(?:the\s+)?([A-Za-z][A-Za-z\s.'-]{2,40}?)(?:\s+(?:and|,|that|they|is)|$)",
                clean,
            )
            m3 = re.search(r"(?i)favou?rite\s+(?:hockey|nhl)\s+team\s+is\s+(?:the\s+)?(.+)$", clean)
            value = ""
            if m3:
                value = m3.group(1).strip(" .,!?")
            elif m2:
                value = m2.group(1).strip(" .,!?")
            # Oilers-specific common phrasing
            m4 = re.search(r"(?i)\b(edmonton\s+oilers|calgary\s+flames)\b", clean)
            if m4:
                value = m4.group(1).title() if m4.group(1).islower() else m4.group(1)
                if "oilers" in value.lower():
                    value = "Edmonton Oilers"
                elif "flames" in value.lower():
                    value = "Calgary Flames"
            if value:
                text = f"{label}'s favorite hockey team is the {value}." if not value.lower().startswith("the ") else f"{label}'s favorite hockey team is {value}."
                if "favorite hockey team is the the" in text.lower():
                    text = text.replace("the the", "the")
                candidates.append(
                    MemoryCandidate(
                        owner_id=owner_id,
                        type="preference",
                        scope="account",
                        subject="sports",
                        text=text,
                        structured_attributes={"favorite_hockey_team": value},
                        source_session_id=session_id,
                        source_execution_id=execution_id,
                        source_item_id=item_id,
                        source_text=source_text[:500],
                        explicit=explicit,
                        confidence=1.0 if explicit else 0.85,
                        importance=0.9,
                        expected_lifetime="long_term",
                        action="create",
                        semantic_key="preference:favorite_hockey_team",
                        reason="Stable sports preference stated by the user",
                    )
                )

        # Generic favorite X is Y
        m = re.search(r"(?i)\bmy\s+favou?rite\s+(.+?)\s+is\s+(?:actually\s+)?(.+)$", clean)
        if m and not candidates:
            key = m.group(1).strip()
            value = m.group(2).strip(" .,!?")
            sk = "preference:favorite_" + re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
            candidates.append(
                MemoryCandidate(
                    owner_id=owner_id,
                    type="preference",
                    scope="account",
                    subject=key[:80],
                    text=f"{label}'s favorite {key} is {value}.",
                    structured_attributes={f"favorite_{sk.split('favorite_', 1)[-1]}": value},
                    source_session_id=session_id,
                    source_execution_id=execution_id,
                    source_item_id=item_id,
                    source_text=source_text[:500],
                    explicit=explicit,
                    confidence=1.0 if explicit else 0.8,
                    importance=0.85,
                    action="create",
                    semantic_key=sk,
                    reason="Favorite preference",
                )
            )

        # Name
        m = re.search(r"(?i)\bmy\s+name\s+is\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b", clean)
        if m:
            name = m.group(1)
            candidates.append(
                MemoryCandidate(
                    owner_id=owner_id,
                    type="identity",
                    scope="account",
                    subject="name",
                    text=f"The user's name is {name}.",
                    structured_attributes={"user_name": name},
                    source_session_id=session_id,
                    source_execution_id=execution_id,
                    source_item_id=item_id,
                    source_text=source_text[:500],
                    explicit=explicit,
                    confidence=1.0,
                    importance=1.0,
                    action="create",
                    semantic_key="profile:user_name",
                    reason="User stated their name",
                )
            )

        # Explicit identity correction: "I'm Memo, not Max".
        corrected_name = re.search(
            r"(?i)\b(?:i\s+am|i'm|im)\s+([A-Za-z][A-Za-z0-9_\-]{1,32})"
            r"\s*,?\s+not\s+[A-Za-z][A-Za-z0-9_\-]{1,32}\b",
            clean,
        )
        if corrected_name and not any(c.semantic_key == "profile:user_name" for c in candidates):
            name = corrected_name.group(1)
            candidates.append(
                MemoryCandidate(
                    owner_id=owner_id,
                    type="identity",
                    scope="account",
                    subject="name",
                    text=f"User name: {name}",
                    structured_attributes={"user_name": name},
                    source_session_id=session_id,
                    source_execution_id=execution_id,
                    source_item_id=item_id,
                    source_text=source_text[:500],
                    explicit=explicit,
                    confidence=1.0,
                    importance=1.0,
                    action="create",
                    semantic_key="profile:user_name",
                    reason="User corrected their identity",
                )
            )

        # Stable relationship fact: "my sister's name is Emily".
        relation = re.search(
            r"(?i)\bmy\s+([a-z][a-z']{1,32})\s*(?:'s)?\s+name\s+is\s+"
            r"([A-Za-z][A-Za-z\-']{1,64})\b",
            clean,
        )
        if not relation:
            relation = re.search(
                r"(?i)\bmy\s+([a-z][a-z']{1,32})\s+(?:is\s+named|named)\s+"
                r"([A-Za-z][A-Za-z\-']{1,64})\b",
                clean,
            )
        if relation:
            relation_name = relation.group(1).lower().rstrip("s").rstrip("'")
            value = relation.group(2)
            candidates.append(
                MemoryCandidate(
                    owner_id=owner_id,
                    type="relationship",
                    scope="account",
                    subject=relation_name,
                    text=f"Relation: {relation_name} name is {value}",
                    structured_attributes={f"relation_{relation_name}": value},
                    source_session_id=session_id,
                    source_execution_id=execution_id,
                    source_item_id=item_id,
                    source_text=source_text[:500],
                    explicit=explicit,
                    confidence=1.0 if explicit else 0.9,
                    importance=0.9,
                    action="create",
                    semantic_key=f"profile:relations:{relation_name}",
                    reason="Stable relationship fact stated by the user",
                )
            )

        # Prefer / dislike patterns → semantic preference
        if re.search(r"(?i)\b(prefer|dislike|don't like|do not like|hate huge|concise|short prompts|manual testing)\b", clean):
            # Rewrite to third-person durable form
            rewritten = clean
            rewritten = re.sub(r"(?i)^\s*i\s+", f"{label} ", rewritten)
            rewritten = re.sub(r"(?i)\bi\s+", f"{label} ", rewritten)
            rewritten = re.sub(r"(?i)\bmy\s+", f"{label}'s ", rewritten)
            if not rewritten.endswith("."):
                rewritten += "."
            # Capitalize
            rewritten = rewritten[0].upper() + rewritten[1:] if rewritten else rewritten
            scope = "project" if project_path and re.search(r"(?i)\b(this project|for echospeak|project)\b", clean) else "account"
            mtype = "workflow_preference" if re.search(r"(?i)\b(test|prompt|workflow|coding|runtime|ui)\b", clean) else "preference"
            if project_path and re.search(r"(?i)\b(this project|for echospeak|project convention|export|timeline)\b", clean):
                mtype = "project_convention"
                scope = "project"
            candidates.append(
                MemoryCandidate(
                    owner_id=owner_id,
                    type=mtype,
                    scope=scope if scope != "project" or project_path else "account",
                    subject=mtype.replace("_", " "),
                    text=rewritten[:480],
                    source_session_id=session_id,
                    source_execution_id=execution_id,
                    source_item_id=item_id,
                    source_text=source_text[:500],
                    explicit=explicit,
                    confidence=1.0 if explicit else 0.75,
                    importance=0.8 if explicit else 0.65,
                    expected_lifetime="long_term",
                    action="create",
                    reason="Stable preference or workflow statement",
                )
            )

        # If explicit but no pattern matched, store curated note
        if explicit and not candidates:
            rewritten = clean
            rewritten = re.sub(r"(?i)^\s*i\s+", f"{label} ", rewritten)
            rewritten = re.sub(r"(?i)\bi\s+(am|like|prefer|want|need|use)\b", rf"{label} \1", rewritten)
            if not rewritten.endswith("."):
                rewritten += "."
            rewritten = rewritten[0].upper() + rewritten[1:] if rewritten else rewritten
            candidates.append(
                MemoryCandidate(
                    owner_id=owner_id,
                    type="note",
                    scope="account",
                    subject="explicit",
                    text=rewritten[:480],
                    source_session_id=session_id,
                    source_execution_id=execution_id,
                    source_item_id=item_id,
                    source_text=source_text[:500],
                    explicit=True,
                    confidence=1.0,
                    importance=0.85,
                    action="create",
                    reason="Explicit remember request",
                )
            )
        return candidates

    def _implicit_heuristic(
        self,
        user_text: str,
        *,
        owner_id: str,
        session_id: str,
        execution_id: str,
        item_id: str,
        project_path: str,
        user_name: str,
    ) -> List[MemoryCandidate]:
        low = str(user_text or "").lower()
        if _TEMPORARY_PATTERNS.search(low) or _SENSITIVE_PATTERNS.search(low):
            return []
        if not re.search(
            r"\b(always|never|prefer|from now on|usually|whenever|"
            r"i like|i don't like|i dislike|my favorite|"
            r"my\s+[a-z][a-z']{1,32}\s+(?:name\s+is|is\s+named|named)|"
            r"(?:i\s+am|i'm|im)\s+[a-z][a-z0-9_-]{1,32}\s+not\s+[a-z][a-z0-9_-]{1,32}|"
            r"for this project|convention)\b",
            low,
        ):
            return []
        return self._deterministic_rewrite(
            user_text,
            source_text=user_text,
            explicit=False,
            owner_id=owner_id,
            session_id=session_id,
            execution_id=execution_id,
            item_id=item_id,
            project_path=project_path,
            user_name=user_name,
        )

    def _llm_propose_strict(
        self,
        *,
        user_text: str,
        response_text: str,
        explicit: bool,
        owner_id: str,
        session_id: str,
        execution_id: str,
        item_id: str,
        project_path: str,
        user_name: str,
        max_candidates: int,
    ) -> tuple[List[MemoryCandidate], List[str]]:
        """Call LLM and fail closed on malformed / unsupported candidates."""
        if self.llm_invoke is None:
            return [], ["llm_unavailable"]
        label = self._user_label(user_name)
        prompt = (
            "You are EchoSpeak's Memory Curator. Propose 0-"
            f"{max_candidates} durable MemoryCandidates as JSON only.\n"
            'Schema: {"candidates":[{"type":"identity|preference|project_convention|workflow_preference|'
            'relationship|goal|fact|instruction","scope":"account|project|session",'
            '"subject":"str","text":"concise natural-language memory","structured_attributes":{},'
            '"confidence":0.0,"importance":0.0,"expected_lifetime":"temporary|weeks|long_term",'
            '"sensitivity":"normal|sensitive","action":"create|update|supersede|ignore|ask_confirmation",'
            '"reason":"str","semantic_key":"optional str"}]}\n'
            "Rules:\n"
            f"- Preferred name label: {label}\n"
            "- Rewrite raw user text into a durable third-person semantic memory sentence.\n"
            "- Prefer rich meaning over key:value stubs. Examples of GOOD rewrites:\n"
            f'  * "My name is Ty" → "{label} is the account owner\'s name."\n'
            f'  * "I prefer short answers" → "{label} prefers concise technical explanations."\n'
            f'  * "Remember my favorite NHL team is the Edmonton Oilers" → '
            f'"{label}\'s favorite NHL team is the Edmonton Oilers."\n'
            f'  * "For coding I want short prompts but detailed architecture" → '
            f'"{label} prefers short coding prompts while wanting detailed architecture discussions."\n'
            "- BAD forms (do not emit): user_name=Ty, favorite_team: Oilers, raw first-person quotes alone.\n"
            "- Preserve meaning; do not invent details, personality traits, or assumptions.\n"
            "- Capture nuanced contrasts when stated (short vs detailed, always vs never).\n"
            "- Project-only conventions use scope=project when a project path is present.\n"
            "- Reject temporary editor state (playhead, selection, current clip), one-offs, secrets, guesses, chain-of-thought.\n"
            "- Implicit: only if stable, useful, clearly stated.\n"
            "- Sensitive content: action=ask_confirmation and sensitivity=sensitive.\n"
            f"- explicit_request={explicit}\n"
            f"- project_path={project_path or '(none)'}\n"
            f"User: {user_text[:1200]}\n"
            f"Assistant: {str(response_text or '')[:800]}\n"
        )
        try:
            raw = str(self.llm_invoke(prompt) or "").strip()
        except Exception as exc:
            return [], [f"llm_invoke_failed:{exc}"]
        if not raw:
            return [], ["llm_empty_response"]
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not m:
            return [], ["llm_malformed_json"]
        try:
            data = json.loads(m.group(0))
        except Exception as exc:
            return [], [f"llm_json_parse_error:{exc}"]
        if not isinstance(data, dict) or "candidates" not in data:
            return [], ["llm_missing_candidates_field"]
        items = data.get("candidates")
        if not isinstance(items, list):
            return [], ["llm_candidates_not_list"]
        out: List[MemoryCandidate] = []
        errors: List[str] = []
        for idx, it in enumerate(items[:max_candidates]):
            if not isinstance(it, dict):
                errors.append(f"candidate_{idx}_not_object")
                continue
            try:
                text = str(it.get("text") or "").strip()
                if not text:
                    errors.append(f"candidate_{idx}_empty_text")
                    continue
                mtype = str(it.get("type") or "note").strip()
                if mtype not in _MEMORY_TYPES:
                    errors.append(f"candidate_{idx}_invalid_type:{mtype}")
                    continue
                scope = str(it.get("scope") or "account").strip()
                if scope not in {"account", "project", "session"}:
                    errors.append(f"candidate_{idx}_invalid_scope:{scope}")
                    continue
                action = str(it.get("action") or "create").strip()
                if action not in {"create", "update", "supersede", "ignore", "ask_confirmation"}:
                    errors.append(f"candidate_{idx}_invalid_action:{action}")
                    continue
                conf = float(it.get("confidence") if it.get("confidence") is not None else (1.0 if explicit else 0.6))
                imp = float(it.get("importance") if it.get("importance") is not None else 0.5)
                if conf < 0 or conf > 1 or imp < 0 or imp > 1:
                    errors.append(f"candidate_{idx}_confidence_importance_out_of_range")
                    continue
                # Fail closed on assumption language in model output
                if _ASSUMPTION_PATTERNS.search(text) and not explicit:
                    errors.append(f"candidate_{idx}_unsupported_inference")
                    continue
                attrs = it.get("structured_attributes")
                if attrs is not None and not isinstance(attrs, dict):
                    errors.append(f"candidate_{idx}_structured_attributes_not_object")
                    continue
                out.append(
                    MemoryCandidate(
                        owner_id=owner_id,
                        type=mtype,
                        scope=scope,
                        subject=str(it.get("subject") or "")[:120],
                        text=text[:500],
                        structured_attributes=dict(attrs or {}),
                        source_session_id=session_id,
                        source_execution_id=execution_id,
                        source_item_id=item_id,
                        source_text=user_text[:500],
                        explicit=explicit,
                        confidence=conf,
                        importance=imp,
                        expected_lifetime=str(it.get("expected_lifetime") or "long_term"),
                        sensitivity=str(it.get("sensitivity") or "normal"),
                        action=action,
                        reason=str(it.get("reason") or "")[:240],
                        semantic_key=str(it.get("semantic_key") or "")[:120],
                    )
                )
            except Exception as exc:
                errors.append(f"candidate_{idx}_schema_error:{exc}")
                continue
        # Fail closed: if model returned only invalid items, treat as LLM failure
        if items and not out and errors:
            return [], errors
        return out, errors

    def _llm_propose(self, **kwargs: Any) -> List[MemoryCandidate]:
        """Backward-compatible wrapper."""
        cands, _errs = self._llm_propose_strict(**kwargs)
        return cands

    # ── Validation ──────────────────────────────────────────────────

    def validate_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        existing: Optional[Sequence[Dict[str, Any]]] = None,
        project_path: str = "",
        allow_implicit_auto: bool = True,
    ) -> MemoryCandidate:
        c = candidate.model_copy(deep=True)
        # Owner cannot be set by model alone — stamp runtime owner
        try:
            c.owner_id = self.memory._owner_id(c.owner_id or None)
        except Exception:
            c.owner_id = c.owner_id or "local-owner"

        if not str(c.text or "").strip():
            c.action = "ignore"
            c.reason = "empty text"
            return c

        if c.scope not in {"account", "project", "session"}:
            c.scope = "account"
        if c.scope == "project" and not str(project_path or "").strip():
            # Downgrade or ignore
            if c.explicit:
                c.scope = "account"
            else:
                c.action = "ignore"
                c.reason = "project scope without project_path"
                return c

        if c.type not in _MEMORY_TYPES:
            c.type = "note"

        if _SENSITIVE_PATTERNS.search(c.text) or c.sensitivity == "sensitive":
            if not c.explicit:
                c.action = "ignore"
                c.reason = "sensitive implicit memory blocked"
                c.sensitivity = "sensitive"
                return c
            c.action = "ask_confirmation"
            c.reason = "sensitive content requires confirmation"
            c.sensitivity = "sensitive"
            return c

        # Playhead/selection: never durable and not useful session continuity
        if re.search(r"(?i)\b(playhead|selected\s+clip|selected\s+track|visible\s+range)\b", c.text):
            c.action = "ignore"
            c.reason = "temporary editor state is not memory"
            return c
        if c.expected_lifetime == "temporary" and c.scope != "session":
            # Useful short-lived context can be Session-only; raw one-offs ignored
            if re.search(r"(?i)\b(right\s+now|just\s+this\s+once|one[- ]time|today\s+only)\b", c.text):
                c.action = "ignore"
                c.reason = "one-off temporary fact is not memory"
                return c
            c.scope = "session"
            c.action = "create"
            c.reason = (c.reason or "") + " → session-only temporary context"


        if _ASSUMPTION_PATTERNS.search(c.text) and not c.explicit:
            c.action = "ignore"
            c.reason = "unsupported inference about the user"
            return c

        # Secrets via memory module
        try:
            if self.memory._is_sensitive_text(c.text):
                c.action = "ignore"
                c.reason = "secret-like content blocked"
                return c
        except Exception:
            pass

        # Confidence gates for implicit
        if not c.explicit:
            if c.confidence < 0.7 or c.importance < 0.55:
                c.action = "ignore"
                c.reason = "implicit confidence/importance too low"
                return c
            if not allow_implicit_auto and c.confidence < 0.9:
                c.action = "ask_confirmation"
                c.reason = "implicit memory needs confirmation"
                return c

        # Dedup / supersede against existing
        existing = list(existing or [])
        if not existing:
            try:
                existing = self.memory.list_items(
                    offset=0,
                    limit=200,
                    thread_id=c.source_session_id or None,
                    project_path=project_path or None,
                )
            except Exception:
                existing = []

        for rec in existing:
            if str(rec.get("metadata", {}).get("owner_id") or rec.get("owner_id") or "") not in {"", c.owner_id}:
                # Cross-owner isolation: never match other owners
                if str((rec.get("metadata") or {}).get("owner_id") or "") != c.owner_id:
                    continue
            rid = str(rec.get("id") or "")
            rtext = str(rec.get("text") or "")
            rkey = str((rec.get("metadata") or {}).get("semantic_key") or "")
            if c.semantic_key and rkey and c.semantic_key == rkey:
                # Same key → update/supersede
                if self.memory._normalize_memory_content(rtext) == self.memory._normalize_memory_content(c.text):
                    c.action = "ignore"
                    c.related_memory_ids = [rid]
                    c.reason = "duplicate of existing memory"
                    return c
                c.action = "supersede"
                c.related_memory_ids = [rid]
                c.reason = "correction supersedes prior semantic key"
                return c
            # Fuzzy same preference topic
            if self._texts_conflict(rtext, c.text):
                c.action = "supersede"
                c.related_memory_ids = [rid]
                c.reason = "correction of related preference"
                return c
            if self._texts_reinforce(rtext, c.text):
                c.action = "ignore"
                c.related_memory_ids = [rid]
                c.reason = "reinforces existing memory"
                return c
            if self._texts_refine(rtext, c.text):
                c.action = "update"
                c.related_memory_ids = [rid]
                c.reason = "refine existing memory with more detail"
                return c

        if c.action not in {"create", "update", "supersede", "ignore", "ask_confirmation"}:
            c.action = "create"
        if c.explicit and c.action == "ignore" and "duplicate" not in c.reason and "reinforces" not in c.reason:
            c.action = "create"
        return c

    @staticmethod
    def _texts_reinforce(existing: str, new: str) -> bool:
        a = re.sub(r"\s+", " ", existing.casefold())
        b = re.sub(r"\s+", " ", new.casefold())
        if not a or not b:
            return False
        if a == b:
            return True
        # Oilers example
        if "oilers" in a and "oilers" in b and "favorite" in a and "favorite" in b:
            return True
        try:
            import difflib
            return difflib.SequenceMatcher(a=a, b=b).ratio() >= 0.9
        except Exception:
            return False

    @staticmethod
    def _texts_conflict(existing: str, new: str) -> bool:
        a = existing.casefold()
        b = new.casefold()
        # Same subject different value: favorite team Oilers vs Flames
        if "favorite" in a and "favorite" in b and "team" in a and "team" in b:
            teams_a = set(re.findall(r"\b(oilers|flames|maple leafs|canucks|canadiens)\b", a))
            teams_b = set(re.findall(r"\b(oilers|flames|maple leafs|canucks|canadiens)\b", b))
            if teams_a and teams_b and teams_a != teams_b:
                return True
        if re.search(r"\b(actually|instead|no longer|not anymore|changed)\b", b):
            # Topic overlap
            wa = set(re.findall(r"[a-z]{4,}", a))
            wb = set(re.findall(r"[a-z]{4,}", b))
            if len(wa & wb) >= 3:
                return True
        return False

    @staticmethod
    def _texts_refine(existing: str, new: str) -> bool:
        a = existing.casefold()
        b = new.casefold()
        if len(b) <= len(a) + 10:
            return False
        # New is more specific extension of existing
        tokens = [t for t in re.findall(r"[a-z]{4,}", a) if t not in {"that", "this", "with", "from", "prefer"}]
        if not tokens:
            return False
        hits = sum(1 for t in tokens if t in b)
        return hits >= max(2, len(tokens) // 2) and len(b) > len(a) * 1.15

    # ── Persist ─────────────────────────────────────────────────────

    # ── Session-only memory (never records.json / Studio durable) ───

    def _session_only_path(self, session_id: str) -> Path:
        sid = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(session_id or "default")).strip("._") or "default"
        return self._session_root / "session_only_memory" / f"{sid}.json"

    def list_session_only(self, session_id: str) -> List[Dict[str, Any]]:
        path = self._session_only_path(session_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return []
            # Drop expired
            now = time.time()
            live = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                exp = float(it.get("expires_at") or 0)
                if exp and exp < now:
                    continue
                live.append(it)
            return live
        except Exception:
            return []

    def add_session_only(self, session_id: str, candidate: MemoryCandidate) -> str:
        path = self._session_only_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        items = self.list_session_only(session_id)
        rid = str(uuid.uuid4())
        ttl = 6 * 3600 if candidate.expected_lifetime == "temporary" else 24 * 3600
        items.append({
            "id": rid,
            "text": candidate.text,
            "type": candidate.type,
            "scope": "session",
            "subject": candidate.subject,
            "source_text": candidate.source_text,
            "reason": candidate.reason,
            "created_at": time.time(),
            "expires_at": time.time() + ttl,
            "durable": False,
            "session_id": session_id,
        })
        # Cap session-only list
        items = items[-24:]
        path.write_text(json.dumps({"schema_version": 1, "items": items}, indent=2) + "\n", encoding="utf-8")
        return rid

    def clear_session_only(self, session_id: str) -> None:
        path = self._session_only_path(session_id)
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    # ── Pending confirmation ────────────────────────────────────────

    def _pending_path(self, session_id: str) -> Path:
        sid = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(session_id or "default")).strip("._") or "default"
        return self._session_root / "pending_memory_confirmations" / f"{sid}.json"

    def store_pending_confirmation(
        self,
        session_id: str,
        candidates: List[MemoryCandidate],
        *,
        project_path: str = "",
        execution_id: str = "",
    ) -> str:
        path = self._pending_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        pid = str(uuid.uuid4())
        payload = {
            "id": pid,
            "session_id": session_id,
            "project_path": project_path,
            "execution_id": execution_id,
            "created_at": time.time(),
            "status": "awaiting_user_confirmation",
            "candidates": [c.model_dump(mode="json") for c in candidates],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return pid

    def get_pending_confirmation(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = self._pending_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("status") or "") != "awaiting_user_confirmation":
                return None
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def clear_pending_confirmation(self, session_id: str) -> None:
        path = self._pending_path(session_id)
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def format_confirmation_prompt(self, candidates: List[MemoryCandidate], existing_related: Optional[List[Dict[str, Any]]] = None) -> str:
        lines = [
            "I can save the following as durable memory after you confirm:",
        ]
        for i, c in enumerate(candidates, 1):
            lines.append(f"\n{i}. **{c.text}**")
            lines.append(f"   - type: {c.type} · scope: {c.scope}")
            if c.reason:
                lines.append(f"   - reason: {c.reason}")
            if c.related_memory_ids:
                lines.append(f"   - related existing memory ids: {', '.join(c.related_memory_ids[:3])}")
            if c.sensitivity == "sensitive":
                lines.append("   - sensitivity: sensitive (explicit confirmation required)")
        if existing_related:
            lines.append("\nRelated existing memories:")
            for r in existing_related[:4]:
                lines.append(f"- {str(r.get('text') or '')[:200]}")
        lines.append("\nReply **yes** / **confirm** to save, or **no** / **cancel** to discard. Nothing is saved until you confirm.")
        return "\n".join(lines)

    @staticmethod
    def is_memory_confirm(text: str) -> bool:
        """Only pure confirmation phrases — not 'yes please rewrite the file'."""
        low = re.sub(r"\s+", " ", str(text or "").strip().lower())
        return bool(
            re.fullmatch(
                r"(yes|yep|yeah|y|confirm|save\s+it|save\s+that|please\s+save|ok\s+save|"
                r"yes\s+save|yes\s+please|go\s+ahead|ok|okay)[.!]?",
                low,
            )
        )

    @staticmethod
    def is_memory_reject(text: str) -> bool:
        low = re.sub(r"\s+", " ", str(text or "").strip().lower())
        return bool(
            re.fullmatch(
                r"(no|nope|n|cancel|don'?t\s+save|do\s+not\s+save|discard|never\s+mind|forget\s+it)[.!]?",
                low,
            )
        )

    def confirm_pending(
        self,
        session_id: str,
        *,
        mode: Optional[str] = None,
        current_project_path: str = "",
    ) -> CuratorResult:
        pending = self.get_pending_confirmation(session_id)
        result = CuratorResult()
        if not pending:
            result.errors.append("no_pending_memory_confirmation")
            return result
        project_path = str(pending.get("project_path") or "")
        current = str(current_project_path or "")
        if project_path and current != project_path:
            result.errors.append("stale_project_scope")
            return result
        for raw in pending.get("candidates") or []:
            if not isinstance(raw, dict):
                continue
            try:
                cand = MemoryCandidate.model_validate(raw)
            except Exception as exc:
                result.errors.append(str(exc))
                continue
            # Confirmed candidates convert ask_confirmation → create/supersede
            if cand.action == "ask_confirmation":
                cand.action = "supersede" if cand.related_memory_ids else "create"
            validated = self.validate_candidate(cand, project_path=project_path, allow_implicit_auto=True)
            # Force allow after explicit confirmation for sensitive
            if validated.action == "ask_confirmation":
                validated.action = "create"
            if validated.action == "ignore" and validated.sensitivity == "sensitive":
                validated.action = "create"
            if validated.scope == "session":
                sid = self.add_session_only(session_id, validated)
                result.session_only_ids.append(sid)
                result.accepted.append(validated)
                result.acknowledgements.append(validated.text + " (session-only, not durable)")
                continue
            mid = self.persist_candidate(validated, project_path=project_path, mode=mode, thread_id=session_id)
            if mid:
                result.persisted_ids.append(mid)
                result.accepted.append(validated)
                result.acknowledgements.append(validated.text)
            else:
                result.rejected.append(validated)
                result.errors.append("confirm_persist_failed")
        self.clear_pending_confirmation(session_id)
        return result

    def reject_pending(self, session_id: str) -> bool:
        pending = self.get_pending_confirmation(session_id)
        self.clear_pending_confirmation(session_id)
        return pending is not None

    def persist_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        project_path: str = "",
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Optional[str]:
        if candidate.action in {"ignore", "ask_confirmation"}:
            return None
        # Session-only never enters records.json
        if candidate.scope == "session":
            return None
        mt = _CANONICAL_TYPE_MAP.get(candidate.type, candidate.type)
        if mt not in getattr(self.memory, "MEMORY_TYPES", {"note"}):
            # Expand allowed types on the store for new curator types
            try:
                self.memory.MEMORY_TYPES = set(self.memory.MEMORY_TYPES) | {
                    "identity", "workflow_preference", "project_convention",
                    "relationship", "goal", "fact", "instruction",
                }
            except Exception:
                pass
            mt = _CANONICAL_TYPE_MAP.get(candidate.type, "note")

        semantic_key = candidate.semantic_key
        if candidate.action in {"supersede", "update"} and candidate.related_memory_ids:
            # Force supersession path via semantic key of related if needed
            if not semantic_key:
                try:
                    rec = self.memory._records.get(candidate.related_memory_ids[0])
                    if rec:
                        semantic_key = str(rec.get("semantic_key") or "")
                except Exception:
                    pass

        # When updating, replace text on related then return that id via supersede
        mid = self.memory.add_memory_item(
            candidate.text,
            memory_type=mt if mt in self.memory.MEMORY_TYPES else "note",
            pinned=bool(candidate.explicit or candidate.importance >= 0.85),
            mode=mode,
            thread_id=thread_id or candidate.source_session_id,
            source="explicit_user" if candidate.explicit else "curator",
            project_path=project_path if candidate.scope == "project" else "",
            owner_id=candidate.owner_id,
            scope=candidate.scope if candidate.scope in {"account", "session", "project"} else "account",
            source_execution_id=candidate.source_execution_id,
            source_item_id=candidate.source_item_id,
            semantic_key=semantic_key,
        )
        if mid:
            # Attach rich metadata for Studio projection
            try:
                with self.memory._records_lock:
                    rec = self.memory._records.get(mid)
                    if rec is not None:
                        meta = dict(rec.get("metadata") or {})
                        meta.update({
                            "structured_attributes": dict(candidate.structured_attributes or {}),
                            "confidence": candidate.confidence,
                            "importance": candidate.importance,
                            "explicit": candidate.explicit,
                            "expected_lifetime": candidate.expected_lifetime,
                            "sensitivity": candidate.sensitivity,
                            "source_text": candidate.source_text,
                            "subject": candidate.subject,
                            "curator_type": candidate.type,
                            "curator_reason": candidate.reason,
                            "semantic_text": candidate.text,
                        })
                        rec["metadata"] = meta
                        rec["subject"] = candidate.subject
                        self.memory._save_records()
            except Exception as exc:
                logger.debug("Curator metadata attach failed: {}", exc)
            # Profile projection for structured attrs
            try:
                attrs = candidate.structured_attributes or {}
                if "user_name" in attrs:
                    self.memory.update_profile_fact("user_name", str(attrs["user_name"]))
                for k, v in attrs.items():
                    if k.startswith("favorite_") or k == "favorite_hockey_team":
                        pref_key = k.replace("favorite_", "").replace("_", " ").strip()
                        if pref_key:
                            self.memory.update_preference(pref_key, str(v))
                    elif k.startswith("relation_"):
                        relation_name = k.removeprefix("relation_").replace("_", " ").strip()
                        if relation_name:
                            self.memory.update_relation(relation_name, str(v))
            except Exception:
                pass
        return mid

    def curate_and_persist(
        self,
        *,
        user_text: str,
        response_text: str = "",
        explicit: bool = False,
        owner_id: str = "",
        session_id: str = "",
        execution_id: str = "",
        item_id: str = "",
        project_path: str = "",
        user_name: str = "",
        mode: Optional[str] = None,
        allow_implicit_auto: bool = True,
        max_candidates: int = 3,
    ) -> CuratorResult:
        result = CuratorResult()
        meta: Dict[str, Any] = {}
        try:
            proposals = self.propose_candidates(
                user_text=user_text,
                response_text=response_text,
                explicit=explicit,
                owner_id=owner_id,
                session_id=session_id,
                execution_id=execution_id,
                item_id=item_id,
                project_path=project_path,
                user_name=user_name,
                max_candidates=max_candidates,
                meta_out=meta,
            )
        except Exception as exc:
            result.errors.append(str(exc))
            return result

        result.llm_invoked = bool(meta.get("llm_invoked"))
        result.llm_failed = bool(meta.get("llm_failed"))
        result.used_deterministic_fallback = bool(meta.get("used_deterministic_fallback"))
        if meta.get("errors"):
            result.errors.extend([str(e) for e in meta["errors"]])

        result.candidates_considered = len(proposals)
        for cand in proposals:
            validated = self.validate_candidate(
                cand,
                project_path=project_path,
                allow_implicit_auto=allow_implicit_auto,
            )
            if validated.action == "ignore":
                result.rejected.append(validated)
                continue
            if validated.action == "ask_confirmation":
                result.needs_confirmation.append(validated)
                continue
            # Session-only path
            if validated.scope == "session":
                try:
                    sid = self.add_session_only(session_id or "default", validated)
                    result.session_only_ids.append(sid)
                    result.accepted.append(validated)
                    result.acknowledgements.append(validated.text + " (session-only)")
                except Exception as exc:
                    result.errors.append(str(exc))
                    result.rejected.append(validated)
                continue
            try:
                mid = self.persist_candidate(
                    validated,
                    project_path=project_path,
                    mode=mode,
                    thread_id=session_id,
                )
            except Exception as exc:
                result.errors.append(str(exc))
                result.rejected.append(validated)
                continue
            if mid:
                result.accepted.append(validated)
                result.persisted_ids.append(mid)
                result.acknowledgements.append(validated.text)
            else:
                result.rejected.append(validated)
                result.errors.append("persistence returned no id")

        # Package confirmation for durable save
        if result.needs_confirmation:
            related: List[Dict[str, Any]] = []
            try:
                for c in result.needs_confirmation:
                    for rid in c.related_memory_ids:
                        rec = (self.memory._records or {}).get(rid)
                        if rec and rec.get("active"):
                            related.append({"id": rid, "text": rec.get("text")})
            except Exception:
                pass
            pid = self.store_pending_confirmation(
                session_id or "default",
                result.needs_confirmation,
                project_path=project_path,
                execution_id=execution_id,
            )
            result.pending_confirmation_id = pid
            result.confirmation_prompt = self.format_confirmation_prompt(
                result.needs_confirmation, existing_related=related
            )
        return result

    def reflect_after_turn(
        self,
        *,
        user_text: str,
        response_text: str = "",
        session_id: str = "",
        execution_id: str = "",
        project_path: str = "",
        user_name: str = "",
        mode: Optional[str] = None,
    ) -> CuratorResult:
        """Bounded reflection — never recursive; never re-reflects its own writes."""
        if getattr(self._reflection_guard, "active", False):
            return CuratorResult(errors=["reflection_reentry_blocked"])
        self._reflection_guard.active = True
        try:
            if self.is_explicit_memory_request(user_text):
                # Explicit path is handled separately; do not double-save.
                return CuratorResult()
            return self.curate_and_persist(
                user_text=user_text,
                response_text=response_text,
                explicit=False,
                session_id=session_id,
                execution_id=execution_id,
                project_path=project_path,
                user_name=user_name,
                mode=mode,
                allow_implicit_auto=True,
                max_candidates=2,
            )
        finally:
            self._reflection_guard.active = False


def build_memory_context_for_turn(
    memory: Any,
    *,
    objective: str = "",
    project_path: str = "",
    mode: str = "",
    user_intent: str = "",
    limit: int = 8,
    owner_id: Optional[str] = None,
    session_id: str = "",
    curator: Optional[MemoryCurator] = None,
) -> str:
    """Selective retrieval of durable memories + optional Session-only context."""
    try:
        items = memory.list_items(
            offset=0,
            limit=80,
            owner_id=owner_id,
            thread_id=session_id or None,
            project_path=project_path or None,
        )
    except Exception:
        return ""
    query = " ".join(
        x for x in [objective, user_intent, mode, project_path] if x
    ).casefold()
    tokens = set(re.findall(r"[a-z0-9]{3,}", query)) if query else set()

    scored: List[tuple[float, Dict[str, Any]]] = []
    for it in items:
        meta = it.get("metadata") or {}
        scope = str(meta.get("scope") or "account")
        if scope == "session":
            continue  # records.json session scope is not used; Session-only store is separate
        if scope == "project":
            pp = str(meta.get("project_path") or "")
            if project_path and pp and pp != project_path:
                continue
            if not project_path:
                continue  # don't inject other projects' conventions into non-project turns
        text = str(it.get("text") or "")
        if not text:
            continue
        score = 0.0
        if meta.get("pinned"):
            score += 2.0
        score += float(meta.get("importance") or 0) * 0.5
        score += float(meta.get("confidence") or 0) * 0.3
        if meta.get("explicit"):
            score += 0.5
        tset = set(re.findall(r"[a-z0-9]{3,}", text.casefold()))
        if tokens:
            score += min(3.0, len(tokens & tset) * 0.6)
        else:
            score += 0.2  # tiny baseline for pinned profile
        if str(meta.get("type") or "") in {"profile", "preference"} and not tokens:
            score += 0.8
        if score > 0.3:
            scored.append((score, it))
    scored.sort(key=lambda x: -x[0])
    lines: List[str] = []
    for score, it in scored[:limit]:
        meta = it.get("metadata") or {}
        mid = str(it.get("id") or "")[:12]
        mtype = str(meta.get("curator_type") or meta.get("type") or "note")
        scope = str(meta.get("scope") or "account")
        origin = "durable personal memory" if scope == "account" else f"durable {scope} memory"
        lines.append(
            f"- [{origin} id={mid} type={mtype}] {str(it.get('text') or '').strip()}"
        )
    # Session-only (never claim durable)
    session_lines: List[str] = []
    if session_id:
        try:
            cur = curator or MemoryCurator(memory)
            for it in cur.list_session_only(session_id)[:4]:
                session_lines.append(
                    f"- [session-only context id={str(it.get('id') or '')[:8]} — not durable memory] "
                    f"{str(it.get('text') or '').strip()}"
                )
        except Exception:
            pass
    if not lines and not session_lines:
        return ""
    parts: List[str] = []
    if lines:
        parts.append(
            "Durable memories (validated; not Session context or inferences):\n"
            + "\n".join(lines)
        )
    if session_lines:
        parts.append(
            "Session-only context (expires with Session; not Studio durable memory):\n"
            + "\n".join(session_lines)
        )
    return "\n\n".join(parts)


def skill_memory_context(
    memory: Any,
    *,
    subjects: Optional[List[str]] = None,
    project_path: str = "",
    limit: int = 6,
) -> List[Dict[str, Any]]:
    """Governed read interface for skills — never writes."""
    try:
        items = memory.list_items(
            offset=0,
            limit=100,
            project_path=project_path or None,
        )
    except Exception:
        return []
    subjects_l = [s.casefold() for s in (subjects or []) if s]
    out: List[Dict[str, Any]] = []
    for it in items:
        meta = it.get("metadata") or {}
        scope = str(meta.get("scope") or "account")
        if scope == "project":
            if not project_path or str(meta.get("project_path") or "") != project_path:
                continue
        text = str(it.get("text") or "")
        if subjects_l:
            low = text.casefold()
            if not any(s in low or s in str(meta.get("subject") or "").casefold() for s in subjects_l):
                continue
        out.append({
            "id": it.get("id"),
            "text": text,
            "type": meta.get("curator_type") or meta.get("type"),
            "scope": scope,
            "structured_attributes": meta.get("structured_attributes") or {},
        })
        if len(out) >= limit:
            break
    return out
