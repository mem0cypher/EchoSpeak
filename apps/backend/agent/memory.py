"""
Memory module for Echo Speak.
Provides FAISS-based vector store for conversation memory persistence.
"""

import os
import re
import json
import uuid
import difflib
import hashlib
import time
import shutil
import threading
from functools import wraps
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterable, Tuple
from datetime import datetime
from loguru import logger

try:
    from langchain_core.documents import Document
except ImportError:
    try:
        from langchain.schema import Document
    except ImportError:
        from langchain.docstore.document import Document
try:
    from langchain_openai import OpenAIEmbeddings
except ImportError:
    OpenAIEmbeddings = None
from langchain_community.vectorstores import FAISS

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config, ModelProvider


_RECORD_LOCKS_GUARD = threading.Lock()
_RECORD_LOCKS: Dict[str, threading.RLock] = {}
_MEMORY_INSTANCES_GUARD = threading.Lock()
_MEMORY_INSTANCES: Dict[str, "AgentMemory"] = {}


def _record_lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _RECORD_LOCKS_GUARD:
        return _RECORD_LOCKS.setdefault(key, threading.RLock())


def _synchronized_records(*, refresh: bool = True):
    """Serialize pooled AgentMemory instances sharing one canonical store."""
    def decorate(func):
        @wraps(func)
        def wrapped(self, *args, **kwargs):
            with self._records_lock:
                if refresh:
                    self._load_records()
                return func(self, *args, **kwargs)
        return wrapped
    return decorate


class AgentMemory:
    """Manages conversation memory using FAISS vector store."""

    MEMORY_TYPES = {
        "conversation",
        "preference",
        "profile",
        "project",
        "contacts",
        "credentials_hint",
        "note",
        # Curator semantic types (also mapped on write when needed)
        "identity",
        "workflow_preference",
        "project_convention",
        "relationship",
        "goal",
        "fact",
        "instruction",
    }

    def _validate_embeddings(self) -> None:
        """Ensure embeddings are usable; if not, clear them so we can fall back safely."""
        if self.embeddings is None:
            return
        try:
            embed_query = getattr(self.embeddings, "embed_query", None)
            if callable(embed_query):
                embed_query("healthcheck")
        except Exception as e:
            logger.warning(f"Embeddings validation failed ({e}); disabling embeddings so we can fall back")
            self.embeddings = None

    def __init__(self, memory_path: Optional[str] = None):
        self.memory_root = Path(memory_path or str(config.memory_path))
        self.memory_path = str(self.memory_root)
        self._profile: Dict[str, Any] = {}
        self.use_faiss = True
        self.file_memory_enabled = bool(getattr(config, "file_memory_enabled", False))
        self.file_memory_dir = Path(str(getattr(config, "file_memory_dir", "")).strip() or str(Path(self.memory_path).parent))
        self.file_memory_log_conversations = bool(getattr(config, "file_memory_log_conversations", True))
        self.file_memory_max_chars = int(getattr(config, "file_memory_max_chars", 2000) or 2000)
        self.partition_enabled = bool(getattr(config, "memory_partition_enabled", False))
        self._vector_stores: Dict[str, FAISS] = {}

        api_key = config.openai.api_key if config.openai.api_key else os.getenv("OPENAI_API_KEY", "")
        embedding_provider = getattr(getattr(config, "embedding", None), "provider", None)
        embedding_model = getattr(getattr(config, "embedding", None), "model", None) or "text-embedding-3-small"

        self.embeddings = None
        if embedding_provider in {ModelProvider.OPENAI, ModelProvider.LM_STUDIO}:
            if OpenAIEmbeddings is None:
                logger.warning("langchain-openai not installed; falling back to local embeddings")
            else:
                try:
                    if embedding_provider == ModelProvider.OPENAI:
                        if not api_key:
                            raise RuntimeError("Missing OPENAI_API_KEY")
                        self.embeddings = OpenAIEmbeddings(model=embedding_model, api_key=api_key)
                    else:
                        base_url = getattr(getattr(config, "local", None), "base_url", "http://localhost:1234")
                        base_url = str(base_url or "").rstrip("/")
                        if not base_url.endswith("/v1"):
                            base_url = base_url + "/v1"
                        self.embeddings = OpenAIEmbeddings(
                            model=embedding_model,
                            api_key=api_key or "lm-studio",
                            base_url=base_url,
                            # Some OpenAI-compatible servers (LM Studio, llama.cpp server, etc.)
                            # reject token-id inputs and require strings. Disabling tiktoken
                            # ensures we send strings rather than token arrays.
                            tiktoken_enabled=False,
                        )
                        logger.info(f"Using LM Studio embeddings at {base_url}")
                except Exception as e:
                    logger.warning(
                        f"Embeddings init failed for provider={embedding_provider} ({e}); falling back to local embeddings"
                    )
                    self.embeddings = None

        # LM Studio/OpenAI embeddings can appear to initialize successfully but still fail
        # at first use (e.g., embeddings model not loaded). Validate once here so the
        # rest of the app can degrade gracefully.
        self._validate_embeddings()

        if self.embeddings is None:
            try:
                try:
                    from langchain_huggingface import HuggingFaceEmbeddings
                except ImportError:
                    from langchain_community.embeddings import HuggingFaceEmbeddings
                self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
                logger.info("Using local HuggingFace embeddings for memory (no OpenAI key)")
            except Exception as e:
                logger.warning(f"No OpenAI key and local embeddings unavailable ({e}). Using simple memory storage (FAISS disabled).")
                self.use_faiss = False
                self.simple_memory = []

        # Validate local embeddings as well; if they fail, disable FAISS to avoid crashing.
        self._validate_embeddings()
        if self.embeddings is None:
            self.use_faiss = False
            self.simple_memory = []

        if self.use_faiss and self.embeddings is not None:
            if self.partition_enabled:
                self.memory_root.mkdir(parents=True, exist_ok=True)
                self.vector_store = None
            else:
                self.vector_store = self._load_or_create_vectorstore(self.memory_root, create_if_missing=True)
        else:
            self.vector_store = None

        self._load_profile()
        # records.json is the durable source of truth. FAISS is a rebuildable
        # retrieval index; profile.json is a backward-compatible projection.
        self._records_path = self.memory_root / "records.json"
        self._records_lock = _record_lock_for(self._records_path)
        self._records: Dict[str, Dict[str, Any]] = {}
        with self._records_lock:
            self._load_records()
            self._migrate_legacy_memory_records()

    def _owner_id(self, owner_id: Optional[str] = None) -> str:
        return str(owner_id or getattr(config, "memory_owner_id", "local-owner") or "local-owner").strip()

    @staticmethod
    def _normalize_memory_content(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip()).casefold()

    def _load_records(self) -> None:
        if not self._records_path.exists():
            self._records = {}
            return
        try:
            payload = json.loads(self._records_path.read_text(encoding="utf-8"))
            records = payload.get("records") if isinstance(payload, dict) else None
            if not isinstance(records, dict):
                raise ValueError("records.json must contain an object-valued 'records' field")
            self._records = {
                str(key): dict(value)
                for key, value in records.items()
                if isinstance(value, dict)
            }
        except Exception as exc:
            quarantine = self.memory_root / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
            note = "quarantine unavailable"
            try:
                quarantine.mkdir(parents=True, exist_ok=False)
                copied = quarantine / self._records_path.name
                shutil.copy2(self._records_path, copied)
                recovery = quarantine / "RECOVERY.txt"
                recovery.write_text(
                    f"Authoritative memory file: {self._records_path}\n"
                    f"Quarantine copy: {copied}\nParse/read error: {exc}\n"
                    "Keep the backend stopped, repair or restore valid JSON, then restart one backend instance.\n",
                    encoding="utf-8",
                )
                note = f"quarantine copy: {copied}; guide: {recovery}"
            except Exception as quarantine_exc:
                note = f"quarantine failed: {quarantine_exc}"
            raise RuntimeError(
                f"Authoritative memory records are unreadable: {self._records_path}. "
                f"The file was not overwritten; {note}. Repair it before restarting. "
                f"({exc})"
            ) from exc

    def _save_records(self) -> None:
        with self._records_lock:
            self.memory_root.mkdir(parents=True, exist_ok=True)
            temp = self._records_path.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
            payload = {"schema_version": 1, "records": self._records}
            try:
                with temp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self._records_path)
            except Exception:
                # Restore in-memory truth from the untouched authoritative file so
                # a later operation cannot persist a partial failed mutation.
                if self._records_path.exists():
                    self._load_records()
                raise
            finally:
                if temp.exists():
                    try:
                        temp.unlink()
                    except OSError:
                        pass

    def _migrate_legacy_memory_records(self) -> None:
        """One-way projection of legacy FAISS/profile truth into records.json."""
        changed = False
        owner = self._owner_id()
        now = datetime.now().isoformat()
        try:
            legacy_items: List[Dict[str, Any]] = []
            if self.use_faiss:
                stores = list(self._iter_vector_stores(None, None))
                for _path, store in stores:
                    for doc_id, text, meta in self._iter_store_records(store):
                        legacy_items.append({"id": doc_id, "text": text, "metadata": meta})
            else:
                legacy_items = list(getattr(self, "simple_memory", []) or [])
            for item in legacy_items:
                iid = str(item.get("id") or uuid.uuid4())
                if iid in self._records:
                    continue
                meta = dict(item.get("metadata") or {})
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                created = str(meta.get("timestamp") or item.get("timestamp") or now)
                legacy_type = self._sanitize_memory_type(meta.get("type"))
                legacy_scope = (
                    "project" if str(meta.get("project_path") or "").strip()
                    else "session" if legacy_type == "conversation"
                    else "account"
                )
                self._records[iid] = {
                    "id": iid, "owner_id": owner,
                    "scope": legacy_scope,
                    "text": text, "normalized_content": self._normalize_memory_content(text),
                    "memory_type": legacy_type,
                    "source_session_id": str(meta.get("thread_id") or "legacy"),
                    "source_execution_id": "", "source_item_id": "",
                    "created_at": created, "updated_at": created,
                    "active": True, "deleted_at": None,
                    "index_state": "indexed", "supersedes": "", "semantic_key": "",
                    "metadata": {**meta, "legacy_import": True},
                }
                changed = True
        except Exception as exc:
            logger.warning("Legacy memory index migration skipped: {}", exc)
        # Make deterministic profile facts visible through the same owner/API.
        for key, value in (self._profile or {}).items():
            pairs = value.items() if isinstance(value, dict) else [(key, value)]
            for subkey, subvalue in pairs:
                text_value = str(subvalue or "").strip()
                if not text_value:
                    continue
                semantic = f"profile:{key}:{subkey}" if isinstance(value, dict) else f"profile:{key}"
                iid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"echospeak:{owner}:{semantic}"))
                if iid in self._records:
                    continue
                label = f"{subkey}: {text_value}" if isinstance(value, dict) else f"{key}: {text_value}"
                self._records[iid] = {
                    "id": iid, "owner_id": owner, "scope": "account", "text": label,
                    "normalized_content": self._normalize_memory_content(label),
                    "memory_type": "preference" if key == "preferences" else "profile",
                    "source_session_id": "legacy-profile", "source_execution_id": "", "source_item_id": "",
                    "created_at": now, "updated_at": now, "active": True, "deleted_at": None,
                    "index_state": "pending", "supersedes": "", "semantic_key": semantic,
                    "metadata": {"legacy_profile_projection": True, "pinned": True},
                }
                changed = True
        if changed:
            self._save_records()

    def _profile_path(self) -> Path:
        try:
            self.memory_root.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return self.memory_root / "profile.json"

    def _load_profile(self) -> None:
        path = self._profile_path()
        try:
            if not path.exists():
                self._profile = {}
                return
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            self._profile = data if isinstance(data, dict) else {}
            self._migrate_profile()
        except Exception as exc:
            logger.warning(f"Failed to load profile memory: {exc}")
            self._profile = {}

    def _migrate_profile(self) -> None:
        # Backward compatibility: older profiles stored sibling keys like sister_name/friend_name.
        rels = self._profile.get("relations")
        if not isinstance(rels, dict):
            rels = {}
            self._profile["relations"] = rels

        legacy_map = {
            "sister_name": "sister",
            "brother_name": "brother",
            "friend_name": "friend",
        }
        changed = False
        for legacy_key, relation in legacy_map.items():
            val = self._profile.get(legacy_key)
            if isinstance(val, str) and val.strip() and not isinstance(rels.get(relation), str):
                rels[relation] = val.strip()
                changed = True
        if changed:
            self._save_profile()

    def _save_profile(self) -> None:
        path = self._profile_path()
        try:
            path.write_text(json.dumps(self._profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to save profile memory: {exc}")

    def update_profile_fact(self, key: str, value: str) -> bool:
        k = str(key or "").strip()
        v = str(value or "").strip()
        if not k or not v:
            return False
        current = self._profile.get(k)
        if isinstance(current, str) and current.strip().lower() == v.lower():
            return False
        self._profile[k] = v
        self._save_profile()
        return True

    def update_relation(self, relation: str, value: str) -> bool:
        rel = str(relation or "").strip().lower()
        v = str(value or "").strip()
        if not rel or not v:
            return False
        rels = self._profile.get("relations")
        if not isinstance(rels, dict):
            rels = {}
            self._profile["relations"] = rels
        current = rels.get(rel)
        if isinstance(current, str) and current.strip().lower() == v.lower():
            return False
        rels[rel] = v
        self._save_profile()
        return True

    def update_preference(self, key: str, value: str) -> bool:
        pref_key = str(key or "").strip().lower()
        pref_val = str(value or "").strip()
        if not pref_key or not pref_val:
            return False
        prefs = self._profile.get("preferences")
        if not isinstance(prefs, dict):
            prefs = {}
            self._profile["preferences"] = prefs
        current = prefs.get(pref_key)
        if isinstance(current, str) and current.strip().lower() == pref_val.lower():
            return False
        prefs[pref_key] = pref_val
        self._save_profile()
        return True

    # Words that commonly follow "I'm" / "I am" but are NOT names.
    # Used to prevent "I'm going for a smoke" → user_name=going
    _NOT_A_NAME = frozenset({
        # States & feelings
        "good", "fine", "great", "okay", "ok", "alright", "well", "better",
        "tired", "sick", "bored", "happy", "sad", "mad", "angry", "hungry",
        "sorry", "sure", "ready", "done", "busy", "free", "excited", "confused",
        # Actions / verbs
        "going", "doing", "trying", "looking", "coming", "leaving", "working",
        "getting", "running", "playing", "eating", "sleeping", "thinking",
        "waiting", "talking", "saying", "asking", "telling", "making",
        "starting", "stopping", "wondering", "hoping", "planning",
        # Location / state
        "here", "there", "home", "back", "out", "in", "up", "down", "over",
        "around", "away", "outside", "inside",
        # Filler / misc
        "just", "not", "also", "still", "already", "about", "like", "really",
        "pretty", "so", "very", "too", "only", "gonna", "gotta", "gonna",
        "currently", "actually", "basically", "literally",
        # Professions (don't store as names)
        "developer", "student", "teacher", "engineer", "designer", "artist",
    })

    # Only these words are recognized as people-relations after "my".
    # Prevents "my discord contacts" → relation=discord, value=contacts
    _VALID_RELATIONS = frozenset({
        "sister", "sisters", "brother", "brothers", "friend", "friends",
        "mom", "mother", "dad", "father", "parent", "parents",
        "wife", "husband", "partner", "girlfriend", "boyfriend",
        "son", "daughter", "child", "children", "kid", "kids",
        "uncle", "aunt", "cousin", "cousins", "grandma", "grandpa",
        "grandmother", "grandfather", "nephew", "niece",
        "roommate", "neighbor", "boss", "coworker", "colleague",
        "pet", "dog", "cat",
    })

    def update_profile_from_text(self, text: str) -> bool:
        s = str(text or "").strip()
        if not s:
            return False
        changed = False

        # --- Name extraction: "my name is X" (explicit, highest priority) ---
        m = re.search(
            r"\bmy\s+name\s+is\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b",
            s, flags=re.IGNORECASE,
        )
        if m:
            changed = self.update_profile_fact("user_name", m.group(1)) or changed

        # --- Name correction: "I'm X not Y" → user is X (Y is who they are NOT) ---
        elif re.search(r"\b(?:i am|i'm|im)\s+[A-Za-z]+\s+not\s+[A-Za-z]+", s, flags=re.IGNORECASE):
            m = re.search(
                r"\b(?:i am|i'm|im)\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\s+not\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b",
                s, flags=re.IGNORECASE,
            )
            if m and m.group(1).lower() not in self._NOT_A_NAME:
                changed = self.update_profile_fact("user_name", m.group(1)) or changed
                # Note: do NOT store m.group(2) as a friend — "I'm Memo not Max"
                # means "my name is Memo, not Max". Max is NOT a friend.

        # --- Fallback name: "I'm X" / "I am X" (only if X looks like a proper name) ---
        else:
            m = re.match(
                r"^\s*(?:i am|i'm|im)\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b",
                s, flags=re.IGNORECASE,
            )
            if m:
                candidate = m.group(1)
                # Only accept if it's NOT a common verb/adjective/state
                if candidate.lower() not in self._NOT_A_NAME:
                    changed = self.update_profile_fact("user_name", candidate) or changed

        # --- Relation extraction: "my <relation> name is <Name>" (specific, high-priority) ---
        relation_matched = False
        m = re.search(
            r"\bmy\s+([a-zA-Z][a-zA-Z']{1,32})\s*(?:'s)?\s+name\s+is\s+([A-Za-z][A-Za-z\-']{1,64})\b",
            s, flags=re.IGNORECASE,
        )
        if not m:
            # Also try: "my <relation> is named <Name>"
            m = re.search(
                r"\bmy\s+([a-zA-Z][a-zA-Z']{1,32})\s+(?:is\s+named|named)\s+([A-Za-z][A-Za-z\-']{1,64})\b",
                s, flags=re.IGNORECASE,
            )
        if m:
            rel = m.group(1).lower().rstrip("s").rstrip("'")  # "sisters" → "sister"
            # Accept the relation even if not in whitelist when using explicit "name is" form
            val = m.group(2)
            changed = self.update_relation(m.group(1).lower(), val) or changed
            relation_matched = True

        # --- Fallback relation: "my friend Max" (only whitelisted relations) ---
        if not relation_matched:
            m = re.search(
                r"\bmy\s+([a-zA-Z][a-zA-Z']{1,32})\s+([A-Z][a-zA-Z\-']{1,64})\b",
                s,
            )
            if m:
                rel = m.group(1).lower()
                val = m.group(2)
                # Only accept whitelisted relations and require capitalized name
                if rel in self._VALID_RELATIONS and val.lower() not in self._NOT_A_NAME:
                    changed = self.update_relation(rel, val) or changed

        pref = re.search(
            r"\bmy\s+favou?rite\s+([a-zA-Z][a-zA-Z0-9_\- ]{1,32})\s+is\s+(.+?)\s*$",
            s,
            flags=re.IGNORECASE,
        )
        if pref:
            pref_key = re.sub(r"\s+", " ", pref.group(1)).strip().lower()
            pref_val = pref.group(2).strip(" .,!?")
            if pref_key and pref_val:
                changed = self.update_preference(pref_key, pref_val) or changed

        return changed

    def _canonical_profile_value(self, category: str, key: str) -> str:
        owner = self._owner_id()
        wanted_key = re.sub(r"\s+", " ", str(key or "").strip()).casefold()
        candidates = sorted(
            (
                record for record in self._records.values()
                if bool(record.get("active", True)) and str(record.get("owner_id") or "") == owner
            ),
            key=lambda record: str(record.get("updated_at") or record.get("created_at") or ""),
            reverse=True,
        )
        for record in candidates:
            semantic = str(record.get("semantic_key") or "").casefold()
            text = str(record.get("text") or "").strip()
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            attributes = metadata.get("structured_attributes") if isinstance(metadata.get("structured_attributes"), dict) else {}
            if category == "name" and (
                semantic in {"profile:user_name", "profile:user_name:user_name"}
                or re.match(r"(?i)^user(?:_name| name)\s*:", text)
            ):
                if str(attributes.get("user_name") or "").strip():
                    return str(attributes["user_name"]).strip()
                return text.split(":", 1)[-1].strip()
            if category == "relation" and (
                semantic == f"profile:relations:{wanted_key}"
                or re.match(rf"(?i)^{re.escape(wanted_key)}\s*:", text)
            ):
                relation_attr = f"relation_{wanted_key.replace(' ', '_')}"
                if str(attributes.get(relation_attr) or "").strip():
                    return str(attributes[relation_attr]).strip()
                return text.split(":", 1)[-1].strip()
            relation = re.match(r"(?i)^relation:\s*([^ ]+)\s+name\s+is\s+(.+)$", text)
            if category == "relation" and relation and relation.group(1).casefold() == wanted_key:
                return relation.group(2).strip()
            favorite_semantic = "preference:favorite_" + re.sub(r"[^a-z0-9]+", "_", wanted_key).strip("_")
            if category == "preference" and (
                semantic == f"profile:preferences:{wanted_key}"
                or semantic == favorite_semantic
                or re.match(rf"(?i)^(?:favorite\s+)?{re.escape(wanted_key)}\s*:", text)
            ):
                return text.split(":", 1)[-1].strip()
        return ""

    @_synchronized_records()
    def answer_profile_question(self, query: str) -> str:
        q = str(query or "").strip().lower()
        if not q:
            return ""

        # Generic relation question: "what is my <relation> name?".
        m = re.search(r"\bwhat\s+(?:is|\'s)\s+my\s+([a-zA-Z][a-zA-Z0-9_\-]{1,32})\s+name\b", q)
        if m:
            rel = m.group(1).strip().lower()
            val = self._canonical_profile_value("relation", rel)
            if val:
                return f"Your {rel}'s name is {val}."
            return ""

        if any(x in q for x in ["what is my name", "what's my name", "whats my name", "who am i"]):
            name = self._canonical_profile_value("name", "user_name")
            if name:
                return f"Your name is {name}."
            return ""

        if any(x in q for x in ["what my name", "my name?"]):
            name = self._canonical_profile_value("name", "user_name")
            if name:
                return f"Your name is {name}."
            return ""

        pref = re.search(r"\bwhat\s+(?:is|\'s)\s+my\s+favou?rite\s+([a-zA-Z][a-zA-Z0-9_\- ]{1,32})\b", q)
        if pref:
            pref_key = re.sub(r"\s+", " ", pref.group(1)).strip().lower()
            val = self._canonical_profile_value("preference", pref_key)
            if val:
                return f"Your favorite {pref_key} is {val}."
            return ""

        if any(x in q for x in ["what is my friend name", "what's my friend name", "whats my friend name", "my friend's name", "my friends name"]):
            name = self._canonical_profile_value("relation", "friend")
            if name:
                return f"Your friend's name is {name}."
            return ""

        return ""

    def extract_remember_payload(self, text: str) -> str:
        """Extract payload from explicit memory requests (expanded phrase set)."""
        try:
            from agent.memory_curator import MemoryCurator

            if not MemoryCurator.is_explicit_memory_request(text):
                # Legacy short prefixes only (including bare "save " for compatibility)
                s = str(text or "").strip()
                s = re.sub(r"(?i)^\s*please\s+", "", s).strip()
                low = s.lower().strip()
                for p in ("save ",):
                    if low.startswith(p) and not low.startswith("save file"):
                        return s[len(p):].strip()
                return ""
            return MemoryCurator.extract_explicit_payload(text)
        except Exception:
            s = str(text or "").strip()
            if not s:
                return ""
            s = re.sub(r"(?i)^\s*please\s+", "", s).strip()
            low = s.lower().strip()
            prefixes = [
                "remember that ", "remember this ", "remember ",
                "save to memory ", "save this ", "save ",
                "keep this in mind ", "from now on ", "note that ",
                "don't forget ", "dont forget ",
            ]
            for p in prefixes:
                if low.startswith(p):
                    return s[len(p):].strip()
            return ""

    def normalize_explicit_memory(self, payload: str) -> tuple[str, str, str]:
        """Return canonical text, type, and deterministic semantic key.

        Prefer curator rewrite for natural language; keep structured keys as indexes.
        """
        clean = re.sub(r"\s+", " ", str(payload or "")).strip(" .!?\t\r\n")
        low = clean.casefold()
        try:
            from agent.memory_curator import MemoryCurator

            curator = MemoryCurator(self)
            cands = curator._deterministic_rewrite(
                clean,
                source_text=clean,
                explicit=True,
                owner_id=self._owner_id(),
                session_id="",
                execution_id="",
                item_id="",
                project_path="",
                user_name=str((self._profile or {}).get("user_name") or ""),
            )
            if cands:
                best = cands[0]
                mt = best.type if best.type in self.MEMORY_TYPES else "preference"
                if best.type in {"identity"}:
                    mt = "profile"
                return best.text, mt, best.semantic_key or ""
        except Exception:
            pass
        # "I like Edmonton Oilers, that's my fav hockey team."
        m = re.search(
            r"(?i)\bi\s+(?:really\s+)?like\s+(.+?)\s+(?:that'?s|thats)\s+my\s+favou?rite\s+(?:hockey|nhl)\s+team\b",
            clean,
        )
        if m:
            value = m.group(1).strip(" .,!?")
            return f"Favorite hockey team: {value}", "preference", "preference:favorite_hockey_team"
        m = re.search(r"(?i)\bmy\s+favou?rite\s+(.+?)\s+is\s+(?:actually\s+)?(.+)$", clean)
        if m:
            key = re.sub(r"[^a-z0-9]+", "_", m.group(1).casefold()).strip("_")
            value = m.group(2).strip(" .,!?")
            return f"Favorite {m.group(1).strip()}: {value}", "preference", f"preference:favorite_{key}"
        if re.search(r"\b(?:prefer|like|love|enjoy)\b", low):
            return clean, "preference", ""
        if re.search(r"\bmy\s+name\s+is\b", low):
            return clean, "profile", "profile:user_name"
        return clean, "note", ""

    @_synchronized_records()
    def personal_memory_summary(self, owner_id: Optional[str] = None) -> List[Dict[str, str]]:
        owner = self._owner_id(owner_id)
        out: List[Dict[str, str]] = []
        for record in self._records.values():
            if not bool(record.get("active", True)) or str(record.get("owner_id") or "") != owner:
                continue
            if str(record.get("scope") or "account") != "account":
                continue
            if str(record.get("memory_type") or "") not in {"profile", "preference", "contacts", "note"}:
                continue
            out.append({
                "id": str(record.get("id") or ""), "text": str(record.get("text") or ""),
                "type": str(record.get("memory_type") or "note"),
                "source": str(record.get("source_execution_id") or record.get("source_session_id") or "legacy"),
                "index_state": str(record.get("index_state") or "pending"),
            })
        out.sort(key=lambda item: item["id"])
        return out

    @_synchronized_records()
    def forget_explicit_memory(self, payload: str, owner_id: Optional[str] = None) -> List[str]:
        """Resolve and tombstone exact owner memories; never delete by model prose."""
        clean = re.sub(r"(?i)^\s*(?:that\s+)?", "", str(payload or "")).strip(" .!?\t")
        normalized_text, _memory_type, semantic_key = self.normalize_explicit_memory(clean)
        wanted = set(re.findall(r"[a-z0-9]+", self._normalize_memory_content(normalized_text)))
        owner = self._owner_id(owner_id)
        matched: List[str] = []
        for record in self._records.values():
            if not bool(record.get("active", True)) or str(record.get("owner_id") or "") != owner:
                continue
            if semantic_key and str(record.get("semantic_key") or "") == semantic_key:
                matched.append(str(record.get("id") or ""))
                continue
            candidate = set(re.findall(r"[a-z0-9]+", str(record.get("normalized_content") or "")))
            if wanted and len(wanted & candidate) >= max(2, min(len(wanted), 4)):
                matched.append(str(record.get("id") or ""))
        matched = [item for item in dict.fromkeys(matched) if item]
        if not matched:
            return []
        self.delete_items(matched)
        if semantic_key.startswith("preference:favorite_"):
            prefs = self._profile.get("preferences")
            if isinstance(prefs, dict):
                label = normalized_text.split(":", 1)[0]
                pref_key = re.sub(r"(?i)^favorite\s+", "", label).strip().casefold()
                prefs.pop(pref_key, None)
                self._save_profile()
        return matched

    def importance_should_save(self, text: str) -> bool:
        s = str(text or "").strip()
        if not s:
            return False
        low = s.lower()
        # Explicit intent always wins.
        if self.extract_remember_payload(s):
            return True
        # Heuristic: relationship/name facts are usually durable.
        if re.search(r"\bmy\s+[a-zA-Z][a-zA-Z0-9_\-]{1,32}\s+(?:name\s+is|is\s+named|named)\s+[A-Za-z]", low):
            return True
        # Only trigger for "I'm X" if X doesn't look like a common word
        m = re.search(r"\b(?:i am|i'm|im)\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b", low)
        if m and m.group(1).lower() not in self._NOT_A_NAME:
            return True
        return False

    def curated_lines_from_text(self, text: str) -> list[str]:
        """Return curated-memory lines representing durable facts extracted from text."""
        s = str(text or "").strip()
        if not s:
            return []

        lines: list[str] = []

        # --- User name: "my name is X" (explicit) ---
        m = re.search(r"\bmy\s+name\s+is\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b", s, flags=re.IGNORECASE)
        if m:
            lines.append(f"User name: {m.group(1)}")

        # --- Name correction: "I'm Memo not Max" ---
        elif re.search(r"\b(?:i am|i'm|im)\s+[A-Za-z]+\s+not\s+[A-Za-z]+", s, flags=re.IGNORECASE):
            m = re.search(
                r"\b(?:i am|i'm|im)\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\s+not\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b",
                s, flags=re.IGNORECASE,
            )
            if m and m.group(1).lower() not in self._NOT_A_NAME:
                lines.append(f"User name: {m.group(1)}")
                # Do NOT store m.group(2) as friend — it's a correction, not a relationship

        # --- Fallback: "I'm X" (only if X is a proper name) ---
        else:
            m = re.match(r"^\s*(?:i am|i'm|im)\s+([A-Za-z][A-Za-z0-9_\-]{1,32})\b", s, flags=re.IGNORECASE)
            if m and m.group(1).lower() not in self._NOT_A_NAME:
                lines.append(f"User name: {m.group(1)}")

        # --- Relation: "my sister's name is Emily" ---
        m = re.search(
            r"\bmy\s+([a-zA-Z][a-zA-Z']{1,32})\s*(?:'s)?\s+name\s+is\s+([A-Za-z][A-Za-z\-']{1,64})\b",
            s, flags=re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r"\bmy\s+([a-zA-Z][a-zA-Z']{1,32})\s+(?:is\s+named|named)\s+([A-Za-z][A-Za-z\-']{1,64})\b",
                s, flags=re.IGNORECASE,
            )
        if m:
            rel = m.group(1).strip().lower()
            val = m.group(2).strip()
            lines.append(f"Relation: {rel} name is {val}")

        # If user used remember command with arbitrary payload, store payload verbatim as curated note.
        payload = self.extract_remember_payload(s)
        if payload:
            lines.append(payload)

        # Deduplicate while preserving order.
        out: list[str] = []
        seen: set[str] = set()
        for ln in lines:
            key = ln.strip()
            if not key:
                continue
            lowk = key.lower()
            if lowk in seen:
                continue
            seen.add(lowk)
            out.append(key)
        return out

    def _normalize_mode_value(self, mode: Optional[str]) -> str:
        default_mode = str(getattr(config, "memory_default_mode", "general") or "general").strip() or "general"
        if mode is None:
            return default_mode
        mode_val = str(mode).strip()
        return mode_val or default_mode

    def _normalize_thread_id(self, thread_id: Optional[str]) -> str:
        if thread_id is None:
            return "default"
        thread_val = str(thread_id).strip()
        return thread_val or "default"

    def _resolve_mode_filter(self, mode: Optional[str]) -> Optional[str]:
        if mode is None:
            return None
        mode_val = str(mode).strip()
        if not mode_val:
            return None
        if mode_val.lower() in {"all", "*"}:
            return None
        return mode_val

    def _mode_matches(self, candidate: Optional[str], mode: Optional[str]) -> bool:
        mode_filter = self._resolve_mode_filter(mode)
        if mode_filter is None:
            return True
        return self._normalize_mode_value(candidate) == mode_filter

    def _thread_matches(self, candidate: Optional[str], thread_id: Optional[str]) -> bool:
        if thread_id is None:
            return True
        thread_val = self._normalize_thread_id(thread_id)
        return self._normalize_thread_id(candidate) == thread_val

    def _build_namespace_key(self, mode: Optional[str], thread_id: Optional[str]) -> str:
        mode_val = self._normalize_mode_value(mode)
        thread_val = self._normalize_thread_id(thread_id)
        return f"{mode_val}:{thread_val}"

    def _build_namespaced_id(self, mode: Optional[str], thread_id: Optional[str]) -> str:
        namespace_key = self._build_namespace_key(mode, thread_id)
        return f"{namespace_key}:{uuid.uuid4()}"

    def _sanitize_component(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip())
        cleaned = cleaned.strip("_")
        return cleaned or "default"

    def _namespace_dir(self, mode: str, thread_id: str) -> Path:
        return self.memory_root / self._sanitize_component(mode) / self._sanitize_component(thread_id)

    def _has_index_files(self, path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return False
        return any((path / name).exists() for name in ["index.faiss", "index.pkl"])

    def _load_or_create_vectorstore(self, path: Path, create_if_missing: bool = True) -> Optional[FAISS]:
        if self.use_faiss and self._has_index_files(path):
            try:
                vector_store = FAISS.load_local(
                    str(path),
                    self.embeddings,
                    allow_dangerous_deserialization=True,
                )
                logger.info("Loaded existing memory from disk")
                return vector_store
            except Exception as e:
                logger.warning(f"Failed to load existing memory: {e}. Creating new memory.")
        if not create_if_missing:
            return None
        path.mkdir(parents=True, exist_ok=True)
        return FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])

    def _get_vector_store(self, mode: str, thread_id: str, create_if_missing: bool) -> Optional[FAISS]:
        if not self.use_faiss or self.embeddings is None:
            return None
        if not self.partition_enabled:
            return self.vector_store
        path = self._namespace_dir(mode, thread_id)
        cache_key = str(path)
        cached = self._vector_stores.get(cache_key)
        if cached is not None:
            return cached
        store = self._load_or_create_vectorstore(path, create_if_missing=create_if_missing)
        if store is not None:
            self._vector_stores[cache_key] = store
        return store

    def _iter_namespace_paths(self, mode: Optional[str], thread_id: Optional[str]) -> Iterable[Tuple[Path, Optional[str], Optional[str]]]:
        if not self.partition_enabled:
            yield self.memory_root, None, None
            return
        mode_filter = self._resolve_mode_filter(mode)
        thread_filter = self._normalize_thread_id(thread_id) if thread_id is not None else None
        if mode_filter is not None:
            mode_dir = self.memory_root / self._sanitize_component(mode_filter)
            if not mode_dir.exists() or not mode_dir.is_dir():
                return
            if thread_filter is not None:
                thread_dir = mode_dir / self._sanitize_component(thread_filter)
                if thread_dir.exists() and thread_dir.is_dir():
                    yield thread_dir, mode_filter, thread_filter
                return
            for thread_dir in mode_dir.iterdir():
                if thread_dir.is_dir():
                    yield thread_dir, mode_filter, thread_dir.name
            return
        for mode_dir in self.memory_root.iterdir() if self.memory_root.exists() else []:
            if not mode_dir.is_dir():
                continue
            if thread_filter is not None:
                thread_dir = mode_dir / self._sanitize_component(thread_filter)
                if thread_dir.exists() and thread_dir.is_dir():
                    yield thread_dir, mode_dir.name, thread_filter
                continue
            for thread_dir in mode_dir.iterdir():
                if thread_dir.is_dir():
                    yield thread_dir, mode_dir.name, thread_dir.name

    def _iter_vector_stores(self, mode: Optional[str], thread_id: Optional[str]) -> Iterable[Tuple[Path, FAISS]]:
        if not self.partition_enabled:
            if self.vector_store is not None:
                yield self.memory_root, self.vector_store
            return
        for path, mode_val, thread_val in self._iter_namespace_paths(mode, thread_id):
            cache_key = str(path)
            cached = self._vector_stores.get(cache_key)
            if cached is not None:
                yield path, cached
                continue
            store = self._load_or_create_vectorstore(path, create_if_missing=False)
            if store is None:
                continue
            self._vector_stores[cache_key] = store
            yield path, store

    def _iter_store_records(self, store: FAISS) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
        store_obj = getattr(store, "docstore", None)
        d = getattr(store_obj, "_dict", None) if store_obj is not None else None
        if isinstance(d, dict):
            for doc_id, doc in d.items():
                meta = getattr(doc, "metadata", {}) or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                yield str(doc_id), getattr(doc, "page_content", "") or "", meta if isinstance(meta, dict) else {}
            return
        payload = store.get() or {}
        ids = payload.get("ids") or []
        docs = payload.get("documents") or []
        metas = payload.get("metadatas") or []
        n = min(len(ids), len(docs), len(metas))
        for i in range(n):
            meta = metas[i] or {}
            if isinstance(meta, dict) and meta.get("bootstrap"):
                continue
            yield str(ids[i]), docs[i] or "", meta if isinstance(meta, dict) else {}

    def _save_vector_store(self, store: FAISS, path: Path) -> bool:
        if store is None:
            return False
        try:
            path.mkdir(parents=True, exist_ok=True)
            store.save_local(str(path))
            logger.debug("Memory saved to disk")
            return True
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            return False

    def _search_store(self, store: FAISS, query: str, k: int) -> List[Tuple[Document, Optional[float]]]:
        if store is None:
            return []
        if hasattr(store, "similarity_search_with_score"):
            try:
                scored = store.similarity_search_with_score(query, k=max(k, 4))
                return [
                    (doc, score)
                    for doc, score in scored
                    if not (getattr(doc, "metadata", {}) or {}).get("bootstrap")
                ]
            except Exception:
                pass
        docs = store.similarity_search(query, k=max(k, 4))
        return [
            (doc, None)
            for doc in docs
            if not (getattr(doc, "metadata", {}) or {}).get("bootstrap")
        ]

    def add_conversation(self, user_message: str, ai_response: str, mode: Optional[str] = None, thread_id: Optional[str] = None) -> None:
        timestamp = datetime.now().isoformat()
        combined_text = f"User: {user_message}\nAI: {ai_response}\nTimestamp: {timestamp}"
        mode_value = self._normalize_mode_value(mode)
        thread_value = self._normalize_thread_id(thread_id)
        # Canonical records own every durable memory, including the opt-in raw
        # conversation tier. FAISS/simple-memory are retrieval projections only.
        memory_id = self.add_memory_item(
            combined_text,
            memory_type="conversation",
            pinned=False,
            mode=mode_value,
            thread_id=thread_value,
            source="conversation_auto_store",
            scope="session",
        )
        if not memory_id:
            logger.warning("Conversation auto-store was enabled but canonical persistence failed.")
        if self.file_memory_enabled and self.file_memory_log_conversations:
            self.append_daily_memory(
                f"User: {user_message}\nAI: {ai_response}\nTimestamp: {timestamp}",
                mode=mode_value,
                thread_id=thread_value,
            )
        logger.debug(f"Added conversation to memory: {user_message[:50]}...")

    def _sanitize_memory_type(self, memory_type: Optional[str]) -> str:
        mt = str(memory_type or "note").strip().lower() or "note"
        return mt if mt in self.MEMORY_TYPES else "note"

    def _is_sensitive_text(self, text: str) -> bool:
        s = str(text or "")
        if not s.strip():
            return False
        low = s.lower()
        # Conservative denylist. We never want to store secrets.
        if any(k in low for k in ["api_key", "apikey", "password", "passwd", "secret", "token", "bearer ", "authorization:"]):
            return True
        if re.search(r"\bsk-[a-z0-9]{10,}\b", low):
            return True
        return False

    def _dedupe_should_skip(self, text: str, existing_text: str) -> bool:
        a = str(text or "").strip().lower()
        b = str(existing_text or "").strip().lower()
        if not a or not b:
            return False
        if a == b:
            return True
        try:
            ratio = difflib.SequenceMatcher(a=a, b=b).ratio()
            return ratio >= 0.94
        except Exception:
            return False

    @_synchronized_records()
    def add_memory_item(
        self,
        text: str,
        memory_type: str = "note",
        pinned: bool = False,
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
        source: str = "auto",
        project_path: Optional[str] = None,
        project_id: str = "",
        owner_id: Optional[str] = None,
        scope: str = "account",
        source_execution_id: str = "",
        source_item_id: str = "",
        semantic_key: str = "",
    ) -> Optional[str]:
        """Persist canonical memory first, then synchronize the retrieval index."""
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        if self._is_sensitive_text(cleaned):
            return None
        mt = self._sanitize_memory_type(memory_type)
        timestamp = datetime.now().isoformat()
        mode_value = self._normalize_mode_value(mode)
        thread_value = self._normalize_thread_id(thread_id)
        namespace_key = self._build_namespace_key(mode_value, thread_value)
        owner = self._owner_id(owner_id)
        normalized = self._normalize_memory_content(cleaned)
        scope_value = str(scope or "account").strip().lower()
        if scope_value not in {"account", "session", "project", "temporary"}:
            return None
        if scope_value == "project" and not str(project_path or "").strip():
            return None

        project_path_value = str(project_path or "").strip()
        project_id_value = str(project_id or "").strip()

        def same_partition(record: Dict[str, Any]) -> bool:
            """Scope identity is resolved before semantic matching."""
            if str(record.get("scope") or "account") != scope_value:
                return False
            meta = record.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            if scope_value == "project":
                record_project_id = str(record.get("project_id") or meta.get("project_id") or "").strip()
                record_project_path = str(meta.get("project_path") or "").strip()
                if project_id_value or record_project_id:
                    return bool(project_id_value and record_project_id == project_id_value)
                return bool(project_path_value and record_project_path == project_path_value)
            if scope_value == "session":
                return str(record.get("source_session_id") or "") == thread_value
            return True

        # Durable identity/deduplication is owner + exact scope identity +
        # semantic key/content. Semantic relevance never crosses that boundary.
        active_records = [
            record for record in self._records.values()
            if bool(record.get("active", True)) and str(record.get("owner_id") or "") == owner
            and same_partition(record)
        ]
        if semantic_key:
            for record in active_records:
                if str(record.get("semantic_key") or "") != semantic_key:
                    continue
                if str(record.get("normalized_content") or "") == normalized:
                    return str(record.get("id") or "") or None
                # Corrections preserve provenance by superseding the prior record.
                record["active"] = False
                record["status"] = "superseded"
                record["superseded_by"] = "pending"
                record["deleted_at"] = timestamp
                record["updated_at"] = timestamp
                supersedes = str(record.get("id") or "")
                break
            else:
                supersedes = ""
        else:
            supersedes = ""
            for record in active_records:
                if str(record.get("scope") or "") != scope_value or str(record.get("memory_type") or "") != mt:
                    continue
                if self._dedupe_should_skip(cleaned, str(record.get("text") or "")):
                    return str(record.get("id") or "") or None

        doc_id = self._build_namespaced_id(mode_value, thread_value)
        metadata: Dict[str, Any] = {
            "timestamp": timestamp,
            "type": mt,
            "pinned": bool(pinned),
            "mode": mode_value,
            "thread_id": thread_value,
            "namespace": namespace_key,
            "source": str(source or "auto"),
            "project_path": project_path_value,
            "project_id": project_id_value,
            "owner_id": owner,
            "scope": scope_value,
            "source_execution_id": str(source_execution_id or ""),
            "source_item_id": str(source_item_id or ""),
            "semantic_key": str(semantic_key or ""),
            "memory_id": doc_id,
        }
        self._records[doc_id] = {
            "id": doc_id, "owner_id": owner, "scope": scope_value,
            "project_id": project_id_value,
            "text": cleaned, "normalized_content": normalized, "memory_type": mt,
            "source_session_id": thread_value,
            "source_execution_id": str(source_execution_id or ""),
            "source_item_id": str(source_item_id or ""),
            "created_at": timestamp, "updated_at": timestamp,
            "active": True, "status": "active", "deleted_at": None, "index_state": "pending",
            "supersedes": supersedes, "superseded_by": "", "contradiction_ids": [],
            "semantic_key": str(semantic_key or ""), "version": 1,
            "checksum": hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
            "metadata": metadata,
        }
        if supersedes and supersedes in self._records:
            self._records[supersedes]["superseded_by"] = doc_id
        self._save_records()

        if not self.use_faiss:
            if not hasattr(self, "simple_memory"):
                self.simple_memory = []
            self.simple_memory.append(
                {
                    "id": doc_id,
                    "text": cleaned,
                    "timestamp": timestamp,
                    "mode": mode_value,
                    "thread_id": thread_value,
                    "namespace": namespace_key,
                    "metadata": metadata,
                }
            )
            if (
                self.file_memory_enabled
                and str(source or "auto") in {"curated", "curator", "explicit_user"}
                and not str(project_path or "").strip()
            ):
                self.append_curated_memory(cleaned)
            self._records[doc_id]["index_state"] = "unavailable"
            self._save_records()
            return doc_id

        store = self._get_vector_store(mode_value, thread_value, create_if_missing=True)
        if store is None:
            self._records[doc_id]["index_state"] = "failed"
            self._save_records()
            return doc_id
        try:
            store.add_texts([cleaned], metadatas=[metadata], ids=[doc_id])
            path = self._namespace_dir(mode_value, thread_value) if self.partition_enabled else self.memory_root
            saved = self._save_vector_store(store, path)
            self._records[doc_id]["index_state"] = "indexed" if saved else "failed"
        except Exception as exc:
            logger.warning("Memory {} persisted but index synchronization failed: {}", doc_id, exc)
            self._records[doc_id]["index_state"] = "failed"
        self._save_records()
        if (
            self.file_memory_enabled
            and str(source or "auto") in {"curated", "curator", "explicit_user"}
            and not str(project_path or "").strip()
        ):
            self.append_curated_memory(cleaned)
        return doc_id

    def list_pinned_items(
        self,
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
        limit: int = 50,
        project_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self.list_items(
            offset=0,
            limit=max(200, limit),
            mode=mode,
            thread_id=thread_id,
            project_path=project_path,
        )
        pinned: List[Dict[str, Any]] = []
        for it in items:
            meta = (it or {}).get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            if meta.get("pinned") is True:
                item_project = str(meta.get("project_path") or "").strip()
                if project_path and item_project and item_project != str(project_path).strip():
                    continue
                pinned.append(it)
        pinned.sort(key=lambda x: (x.get("timestamp") or ""), reverse=True)
        return pinned[:limit]

    def pinned_context(
        self,
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
        max_chars: int = 800,
        project_path: Optional[str] = None,
    ) -> str:
        items = self.list_pinned_items(
            mode=mode,
            thread_id=thread_id,
            limit=50,
            project_path=project_path,
        )
        if not items:
            return ""
        lines: List[str] = []
        used = 0
        for it in items:
            meta = (it or {}).get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            t = str((it or {}).get("text") or "").strip()
            if not t:
                continue
            mt = str(meta.get("type") or "note").strip().lower() or "note"
            prefix = f"[{mt}] " if mt and mt != "note" else ""
            candidate = prefix + t
            if used + len(candidate) + 2 > max_chars:
                break
            lines.append(candidate)
            used += len(candidate) + 2
        return "\n".join([f"- {ln}" for ln in lines if ln.strip()]).strip()

    def _rebuild_store_with_optional_upsert(
        self,
        store: FAISS,
        exclude_ids: set[str],
        upsert: Optional[Tuple[str, str, Dict[str, Any]]],
    ) -> FAISS:
        kept_ids: List[str] = []
        kept_texts: List[str] = []
        kept_metas: List[Dict[str, Any]] = []
        for doc_id, text, meta in self._iter_store_records(store):
            if str(doc_id) in exclude_ids:
                continue
            kept_ids.append(str(doc_id))
            kept_texts.append(text or "")
            kept_metas.append(meta if isinstance(meta, dict) else {})
        if upsert is not None:
            uid, utext, umeta = upsert
            kept_ids.append(str(uid))
            kept_texts.append(str(utext or ""))
            kept_metas.append(umeta if isinstance(umeta, dict) else {})
        new_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
        if kept_texts:
            try:
                new_store.add_texts(kept_texts, metadatas=kept_metas, ids=kept_ids)
            except Exception:
                new_store.add_texts(kept_texts, metadatas=kept_metas)
        return new_store

    def _record_matches_scope(
        self,
        record: Dict[str, Any],
        *,
        project_id: str = "",
        thread_id: str = "",
        include_global: bool = True,
    ) -> bool:
        """Check mutation/retrieval scope without granting new authority."""
        if not project_id and not thread_id:
            return True
        scope = str(record.get("scope") or "account")
        metadata = dict(record.get("metadata") or {})
        if scope == "account":
            return bool(include_global)
        if scope == "project":
            record_project_id = str(record.get("project_id") or metadata.get("project_id") or "")
            return bool(project_id) and record_project_id == str(project_id)
        if scope == "session":
            return bool(thread_id) and self._thread_matches(record.get("source_session_id"), thread_id)
        return False

    @_synchronized_records()
    def update_item(
        self,
        item_id: str,
        text: Optional[str] = None,
        memory_type: Optional[str] = None,
        pinned: Optional[bool] = None,
        owner_id: Optional[str] = None,
        project_id: str = "",
        thread_id: str = "",
        include_global: bool = True,
    ) -> bool:
        """Update a memory item's text/type/pinned.

        FAISS does not support in-place mutation cleanly; we rebuild the affected store.
        """
        iid = str(item_id or "").strip()
        if not iid:
            return False

        new_text = None if text is None else str(text or "").strip()
        if new_text is not None and not new_text:
            return False
        if new_text is not None and self._is_sensitive_text(new_text):
            return False
        record = self._records.get(iid)
        if (
            record is None
            or not bool(record.get("active", True))
            or str(record.get("owner_id") or "") != self._owner_id(owner_id)
            or not self._record_matches_scope(
                record,
                project_id=project_id,
                thread_id=thread_id,
                include_global=include_global,
            )
        ):
            return False
        canonical_changed = False
        if new_text is not None and str(record.get("text") or "") != new_text:
            record["text"] = new_text
            record["normalized_content"] = self._normalize_memory_content(new_text)
            canonical_changed = True
        if memory_type is not None:
            mt = self._sanitize_memory_type(memory_type)
            if str(record.get("memory_type") or "") != mt:
                record["memory_type"] = mt
                canonical_changed = True
        if pinned is not None:
            meta = dict(record.get("metadata") or {})
            if bool(meta.get("pinned")) != bool(pinned):
                meta["pinned"] = bool(pinned)
                record["metadata"] = meta
                canonical_changed = True
        if canonical_changed:
            record["updated_at"] = datetime.now().isoformat()
            record["index_state"] = "pending"
            record["version"] = int(record.get("version") or 1) + 1
            record["checksum"] = hashlib.sha256(
                str(record.get("text") or "").encode("utf-8")
            ).hexdigest()
            self._save_records()

        if not self.use_faiss:
            changed = False
            for m in getattr(self, "simple_memory", []) or []:
                mid = str(m.get("id") or "")
                if mid != iid:
                    continue
                meta = m.get("metadata")
                if not isinstance(meta, dict):
                    meta = {}
                    m["metadata"] = meta
                if new_text is not None and str(m.get("text") or "") != new_text:
                    m["text"] = new_text
                    changed = True
                if memory_type is not None:
                    mt = self._sanitize_memory_type(memory_type)
                    if str(meta.get("type") or "") != mt:
                        meta["type"] = mt
                        changed = True
                if pinned is not None:
                    if bool(meta.get("pinned")) != bool(pinned):
                        meta["pinned"] = bool(pinned)
                        changed = True
                return canonical_changed or changed
            return canonical_changed

        # Partitioned: we need to locate the store that contains iid.
        if self.partition_enabled:
            for path, store in self._iter_vector_stores(None, None):
                found_text = None
                found_meta: Optional[Dict[str, Any]] = None
                for doc_id, doc_text, meta in self._iter_store_records(store):
                    if str(doc_id) != iid:
                        continue
                    found_text = doc_text or ""
                    found_meta = meta if isinstance(meta, dict) else {}
                    break
                if found_text is None:
                    continue
                meta2 = dict(found_meta or {})
                if new_text is None:
                    new_text = found_text
                if memory_type is not None:
                    meta2["type"] = self._sanitize_memory_type(memory_type)
                if pinned is not None:
                    meta2["pinned"] = bool(pinned)
                meta2["timestamp"] = meta2.get("timestamp") or datetime.now().isoformat()
                new_store = self._rebuild_store_with_optional_upsert(store, exclude_ids={iid}, upsert=(iid, new_text, meta2))
                saved = self._save_vector_store(new_store, path)
                self._vector_stores[str(path)] = new_store
                record["index_state"] = "indexed" if saved else "failed"
                self._save_records()
                return True
            return canonical_changed

        # Non-partitioned single store.
        vs = self.vector_store
        if vs is None:
            return canonical_changed
        found_text = None
        found_meta: Optional[Dict[str, Any]] = None
        for doc_id, doc_text, meta in self._iter_store_records(vs):
            if str(doc_id) != iid:
                continue
            found_text = doc_text or ""
            found_meta = meta if isinstance(meta, dict) else {}
            break
        if found_text is None:
            return canonical_changed
        meta2 = dict(found_meta or {})
        if new_text is None:
            new_text = found_text
        if memory_type is not None:
            meta2["type"] = self._sanitize_memory_type(memory_type)
        if pinned is not None:
            meta2["pinned"] = bool(pinned)
        meta2["timestamp"] = meta2.get("timestamp") or datetime.now().isoformat()
        self.vector_store = self._rebuild_store_with_optional_upsert(vs, exclude_ids={iid}, upsert=(iid, new_text, meta2))
        saved = self._save_vector_store(self.vector_store, self.memory_root)
        record["index_state"] = "indexed" if saved else "failed"
        self._save_records()
        return True

    def _daily_memory_path(self, day: Optional[datetime] = None) -> Path:
        day = day or datetime.now()
        filename = f"{day.strftime('%Y-%m-%d')}.md"
        return self.file_memory_dir / "memory" / filename

    def _ensure_memory_dirs(self) -> None:
        try:
            (self.file_memory_dir / "memory").mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(f"Failed to ensure memory dirs: {exc}")

    def _sanitize_memory_text(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        max_chars = max(200, int(self.file_memory_max_chars or 0))
        if len(raw) > max_chars:
            raw = raw[:max_chars].rstrip() + "..."
        return raw

    def append_daily_memory(self, text: str, mode: Optional[str] = None, thread_id: Optional[str] = None) -> bool:
        if not self.file_memory_enabled:
            return False
        cleaned = self._sanitize_memory_text(text)
        if not cleaned:
            return False
        self._ensure_memory_dirs()
        path = self._daily_memory_path()
        stamp = datetime.now().strftime("%H:%M")
        mode_val = self._normalize_mode_value(mode)
        thread_val = self._normalize_thread_id(thread_id)
        header = f"- [{stamp}] ({mode_val}/{thread_val})"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{header} {cleaned}\n")
            return True
        except Exception as exc:
            logger.warning(f"Failed to write daily memory: {exc}")
            return False

    def append_curated_memory(self, text: str) -> bool:
        if not self.file_memory_enabled:
            return False
        cleaned = self._sanitize_memory_text(text)
        if not cleaned:
            return False
        self._ensure_memory_dirs()
        path = self.file_memory_dir / "MEMORY.md"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"- {cleaned}\n")
            return True
        except Exception as exc:
            logger.warning(f"Failed to write curated memory: {exc}")
            return False

    @_synchronized_records()
    def list_items(
        self,
        offset: int = 0,
        limit: int = 200,
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        project_id: str = "",
        project_path: Optional[str] = None,
        include_global: bool = True,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        owner = self._owner_id(owner_id)
        canonical: List[Dict[str, Any]] = []
        for record in self._records.values():
            if not bool(record.get("active", True)) or str(record.get("owner_id") or "") != owner:
                continue
            scope = str(record.get("scope") or "account")
            if scope == "temporary":
                continue
            if scope == "session" and thread_id is not None and not self._thread_matches(record.get("source_session_id"), thread_id):
                continue
            meta = dict(record.get("metadata") or {})
            record_project_id = str(record.get("project_id") or meta.get("project_id") or "").strip()
            record_project_path = str(meta.get("project_path") or "").strip()
            if scope == "project":
                requested_project_id = str(project_id or "").strip()
                requested_project_path = str(project_path or "").strip()
                if requested_project_id:
                    if record_project_id != requested_project_id:
                        continue
                elif requested_project_path:
                    if record_project_path != requested_project_path:
                        continue
                else:
                    # A scoped projection without an active Project cannot list
                    # arbitrary Project memories.
                    if thread_id is not None:
                        continue
            elif scope == "account" and not include_global:
                continue
            meta.update({
                "type": str(record.get("memory_type") or meta.get("type") or "note"),
                "owner_id": owner, "scope": scope,
                "source_session_id": str(record.get("source_session_id") or ""),
                "source_execution_id": str(record.get("source_execution_id") or ""),
                "source_item_id": str(record.get("source_item_id") or ""),
                "index_state": str(record.get("index_state") or "pending"),
                "semantic_key": str(record.get("semantic_key") or ""),
                "supersedes": str(record.get("supersedes") or ""),
                "superseded_by": str(record.get("superseded_by") or ""),
                "status": str(record.get("status") or ("active" if record.get("active", True) else "forgotten")),
                "project_id": record_project_id,
                "checksum": str(record.get("checksum") or ""),
                "version": int(record.get("version") or 1),
            })
            canonical.append({
                "id": str(record.get("id") or ""), "text": str(record.get("text") or ""),
                "timestamp": str(record.get("created_at") or ""),
                "updated_at": str(record.get("updated_at") or ""),
                "metadata": meta,
            })
        canonical.sort(key=lambda item: str(item.get("updated_at") or item.get("timestamp") or ""), reverse=True)
        return canonical[offset: offset + limit]

    @_synchronized_records()
    def delete_items(
        self,
        ids: List[str],
        owner_id: Optional[str] = None,
        *,
        project_id: str = "",
        thread_id: str = "",
        include_global: bool = True,
    ) -> int:
        if not ids:
            return 0
        id_set = {str(i) for i in ids if i is not None}
        if not id_set:
            return 0
        owner = self._owner_id(owner_id)
        # Unknown or foreign IDs never reach the retrieval-index deletion path.
        requested_ids = set(id_set)
        id_set = {
            iid for iid in id_set
            if iid in self._records
            and str((self._records.get(iid) or {}).get("owner_id") or "") == owner
            and self._record_matches_scope(
                self._records.get(iid) or {},
                project_id=project_id,
                thread_id=thread_id,
                include_global=include_global,
            )
        }
        if (project_id or thread_id) and id_set != requested_ids:
            raise PermissionError("One or more memory ids are outside the requested scope")
        if not id_set:
            return 0
        deleted_at = datetime.now().isoformat()
        canonical_deleted = 0
        for iid in id_set:
            record = self._records.get(iid)
            if record is None or not bool(record.get("active", True)):
                continue
            record["active"] = False
            record["status"] = "forgotten"
            record["deleted_at"] = deleted_at
            record["updated_at"] = deleted_at
            record["index_state"] = "deleted"
            semantic = str(record.get("semantic_key") or "")
            if semantic in {"profile:user_name", "profile:user_name:user_name"}:
                self._profile.pop("user_name", None)
            elif semantic.startswith("preference:favorite_"):
                prefs = self._profile.get("preferences")
                if isinstance(prefs, dict):
                    label = str(record.get("text") or "").split(":", 1)[0]
                    pref_key = re.sub(r"(?i)^favorite\s+", "", label).strip().casefold()
                    prefs.pop(pref_key, None)
            elif semantic.startswith("profile:preferences:"):
                prefs = self._profile.get("preferences")
                if isinstance(prefs, dict):
                    prefs.pop(semantic.split(":", 2)[-1], None)
            canonical_deleted += 1
        if canonical_deleted:
            self._save_records()
            self._save_profile()

        if not self.use_faiss:
            before = len(self.simple_memory)
            kept = []
            for i, m in enumerate(self.simple_memory):
                mid = m.get("id") or f"simple-{i}"
                if str(mid) in id_set:
                    continue
                kept.append(m)
            self.simple_memory = kept
            return canonical_deleted or (before - len(self.simple_memory))

        deleted_total = 0
        if self.partition_enabled:
            for path, store in self._iter_vector_stores(None, None):
                kept_ids: List[str] = []
                kept_texts: List[str] = []
                kept_metas: List[Dict[str, Any]] = []
                deleted = 0
                for doc_id, text, meta in self._iter_store_records(store):
                    if str(doc_id) in id_set:
                        deleted += 1
                        continue
                    kept_ids.append(str(doc_id))
                    kept_texts.append(text or "")
                    kept_metas.append(meta if isinstance(meta, dict) else {})
                if deleted <= 0:
                    continue
                deleted_total += deleted
                new_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
                if kept_texts:
                    try:
                        new_store.add_texts(kept_texts, metadatas=kept_metas, ids=kept_ids)
                    except Exception:
                        new_store.add_texts(kept_texts, metadatas=kept_metas)
                self._save_vector_store(new_store, path)
                self._vector_stores[str(path)] = new_store
            return canonical_deleted or deleted_total

        vs = self.vector_store
        if vs is None:
            return canonical_deleted
        store = getattr(vs, "docstore", None)
        d = getattr(store, "_dict", None) if store is not None else None

        kept_ids = []
        kept_texts = []
        kept_metas = []
        deleted = 0

        if isinstance(d, dict):
            for doc_id, doc in d.items():
                meta = getattr(doc, "metadata", {}) or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                if str(doc_id) in id_set:
                    deleted += 1
                    continue
                kept_ids.append(str(doc_id))
                kept_texts.append(getattr(doc, "page_content", "") or "")
                kept_metas.append(meta if isinstance(meta, dict) else {})
        else:
            payload = vs.get() or {}
            ids_all = payload.get("ids") or []
            docs_all = payload.get("documents") or []
            metas_all = payload.get("metadatas") or []
            n = min(len(ids_all), len(docs_all), len(metas_all))
            for i in range(n):
                meta = metas_all[i] or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                if str(ids_all[i]) in id_set:
                    deleted += 1
                    continue
                kept_ids.append(str(ids_all[i]))
                kept_texts.append(docs_all[i] or "")
                kept_metas.append(meta if isinstance(meta, dict) else {})

        if deleted <= 0:
            return 0

        self.vector_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
        if kept_texts:
            try:
                self.vector_store.add_texts(kept_texts, metadatas=kept_metas, ids=kept_ids)
            except Exception:
                self.vector_store.add_texts(kept_texts, metadatas=kept_metas)
        self._save_to_disk()
        return canonical_deleted or deleted

    def _lexical_memory_search(
        self,
        query: str,
        *,
        k: int,
        thread_id: Optional[str],
        project_path: Optional[str],
    ) -> List[Document]:
        query_tokens = set(re.findall(r"[a-z0-9]{2,}", str(query or "").casefold()))
        if not query_tokens:
            return []
        owner = self._owner_id()
        scored: list[tuple[float, Document]] = []
        for record in self._records.values():
            if not bool(record.get("active", True)) or str(record.get("owner_id") or "") != owner:
                continue
            scope = str(record.get("scope") or "account")
            metadata = dict(record.get("metadata") or {})
            if scope == "session" and not self._thread_matches(record.get("source_session_id"), thread_id):
                continue
            record_path = str(metadata.get("project_path") or "").strip()
            if scope == "project":
                if not project_path or record_path != str(project_path).strip():
                    continue
            text = str(record.get("text") or "").strip()
            tokens = set(re.findall(r"[a-z0-9]{2,}", text.casefold()))
            overlap = query_tokens & tokens
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_tokens))
            if query.casefold() in text.casefold():
                score += 1.0
            meta = {
                **metadata,
                "id": str(record.get("id") or ""),
                "memory_id": str(record.get("id") or ""),
                "scope": scope,
                "project_id": str(record.get("project_id") or metadata.get("project_id") or ""),
                "source_session_id": str(record.get("source_session_id") or ""),
                "memory_status": str(record.get("status") or "active"),
                "memory_version": int(record.get("version") or 1),
                "memory_checksum": str(record.get("checksum") or ""),
                "retrieval_lexical_score": score,
            }
            scored.append((score, Document(page_content=text, metadata=meta)))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for _score, doc in scored[: max(k * 4, 20)]]

    @staticmethod
    def _fuse_memory_results(vector_docs: List[Document], lexical_docs: List[Document], k: int) -> List[Document]:
        scores: Dict[str, float] = {}
        docs: Dict[str, Document] = {}
        sources: Dict[str, list[str]] = {}
        for source, rows in (("semantic", vector_docs), ("lexical", lexical_docs)):
            for rank, doc in enumerate(rows, start=1):
                metadata = dict(getattr(doc, "metadata", {}) or {})
                key = str(metadata.get("memory_id") or metadata.get("id") or "").strip()
                if not key:
                    key = hashlib.sha256(str(doc.page_content).encode("utf-8")).hexdigest()
                scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
                docs.setdefault(key, doc)
                sources.setdefault(key, []).append(source)
        ordered = sorted(scores, key=scores.get, reverse=True)
        out: List[Document] = []
        for key in ordered[:k]:
            doc = docs[key]
            doc.metadata = {
                **dict(getattr(doc, "metadata", {}) or {}),
                "retrieval_sources": list(dict.fromkeys(sources[key])),
                "retrieval_rrf_score": scores[key],
            }
            out.append(doc)
        return out

    def retrieve_relevant(
        self,
        query: str,
        k: int = 5,
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
        project_path: Optional[str] = None,
    ) -> List[Document]:
        lexical_docs = self._lexical_memory_search(
            query,
            k=k,
            thread_id=thread_id,
            project_path=project_path,
        )
        if not self.use_faiss:
            return lexical_docs[:k]
        if self.partition_enabled:
            scored: List[Tuple[Document, Optional[float]]] = []
            for _path, store in self._iter_vector_stores(mode, thread_id):
                scored.extend(self._search_store(store, query, k=max(k, 4)))
            if not scored:
                return lexical_docs[:k]
            if any(score is not None for _doc, score in scored):
                scored.sort(key=lambda item: float(item[1] if item[1] is not None else 0.0))
            docs = [doc for doc, _score in scored]
            docs = [
                doc for doc in docs
                if (
                    not str((getattr(doc, "metadata", {}) or {}).get("project_path") or "").strip()
                    if not project_path
                    else not str((getattr(doc, "metadata", {}) or {}).get("project_path") or "").strip()
                    or str((getattr(doc, "metadata", {}) or {}).get("project_path") or "").strip() == str(project_path).strip()
                )
            ]
            return self._fuse_memory_results(docs, lexical_docs, k)
        if self.vector_store is None:
            return lexical_docs[:k]
        results = self.vector_store.similarity_search(query, k=max(k * 6, 24))
        results = [d for d in results if not (getattr(d, "metadata", {}) or {}).get("bootstrap")]
        results = [
            doc for doc in results
            if (
                not str((getattr(doc, "metadata", {}) or {}).get("project_path") or "").strip()
                if not project_path
                else not str((getattr(doc, "metadata", {}) or {}).get("project_path") or "").strip()
                or str((getattr(doc, "metadata", {}) or {}).get("project_path") or "").strip() == str(project_path).strip()
            )
        ]
        # Drop vectors whose canonical record is inactive/deleted (stale FAISS).
        results = [d for d in results if self._vector_doc_is_active(d)]
        results = self._fuse_memory_results(results, lexical_docs, k)
        logger.debug(f"Retrieved {len(results)} relevant memories for query: {query[:50]}...")
        return results

    def _vector_doc_is_active(self, doc: Any) -> bool:
        """True when FAISS hit maps to an active owner record (or has no durable id)."""
        meta = getattr(doc, "metadata", {}) or {}
        if not isinstance(meta, dict):
            return True
        mid = str(meta.get("id") or meta.get("memory_id") or meta.get("doc_id") or "").strip()
        if not mid:
            # Match by normalized content against active records
            content = self._normalize_memory_content(getattr(doc, "page_content", "") or "")
            if not content:
                return False
            # Legacy/unmigrated vector stores may not have canonical records or
            # ids yet. Preserve those results until a canonical record set
            # exists; once it does, only an active exact owner record can pass.
            if not self._records:
                return True
            owner = self._owner_id()
            return any(
                bool(r.get("active", True))
                and str(r.get("owner_id") or "") == owner
                and self._normalize_memory_content(r.get("text") or r.get("normalized_content") or "") == content
                for r in self._records.values()
            )
        rec = self._records.get(mid)
        if rec is None:
            return False
        return bool(rec.get("active", True)) and str(rec.get("owner_id") or "") == self._owner_id()

    @_synchronized_records()
    def rebuild_faiss_from_canonical(self) -> Dict[str, Any]:
        """Rebuild FAISS from active records only — forgotten/superseded stay out."""
        owner = self._owner_id()
        active = [
            r for r in self._records.values()
            if bool(r.get("active", True))
            and str(r.get("owner_id") or "") == owner
            and str(r.get("scope") or "account") != "temporary"
        ]
        texts: List[str] = []
        metas: List[Dict[str, Any]] = []
        ids: List[str] = []
        for r in active:
            text = str(r.get("text") or "").strip()
            if not text:
                continue
            rid = str(r.get("id") or "").strip()
            if not rid:
                continue
            texts.append(text)
            ids.append(rid)
            metas.append({
                "id": rid,
                "memory_id": rid,
                "type": str(r.get("type") or r.get("memory_type") or "fact"),
                "owner_id": owner,
                "scope": str(r.get("scope") or "account"),
                "active": True,
            })
            r["index_state"] = "pending"
        if not self.use_faiss or self.embeddings is None:
            self._save_records()
            return {"ok": True, "indexed": 0, "active_records": len(active), "faiss": False}
        try:
            self.vector_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
            if texts:
                try:
                    self.vector_store.add_texts(texts, metadatas=metas, ids=ids)
                except Exception:
                    self.vector_store.add_texts(texts, metadatas=metas)
            self._save_to_disk()
            for rid in ids:
                if rid in self._records:
                    self._records[rid]["index_state"] = "indexed"
            self._save_records()
            return {"ok": True, "indexed": len(ids), "active_records": len(active), "faiss": True}
        except Exception as exc:
            logger.warning("FAISS rebuild failed: {}", exc)
            return {"ok": False, "error": str(exc), "indexed": 0, "active_records": len(active)}

    @_synchronized_records()
    def get_conversation_context(
        self,
        query: str,
        k: int = 5,
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
        project_path: Optional[str] = None,
    ) -> str:
        # When raw auto-store is off, do not inject type=conversation dumps into the
        # prompt (session + ephemeral chat history own multi-turn continuity). This
        # keeps session summary and FAISS from fighting each other with stale turns.
        inject_raw_conversations = bool(getattr(config, "memory_auto_store_conversations", False))

        if not self.use_faiss:
            if not self.simple_memory:
                return ""
            matched = [
                m
                for m in self.simple_memory
                if self._mode_matches(m.get("mode"), mode)
                and self._thread_matches(m.get("thread_id"), thread_id)
                and (
                    not str((m.get("metadata") or {}).get("project_path") or "").strip()
                    if not project_path
                    else not str((m.get("metadata") or {}).get("project_path") or "").strip()
                    or str((m.get("metadata") or {}).get("project_path") or "").strip() == str(project_path).strip()
                )
            ]
            if not inject_raw_conversations:
                filtered = []
                for m in matched:
                    mt = ""
                    if isinstance(m, dict):
                        meta = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
                        mt = str((meta or {}).get("type") or m.get("type") or "").strip().lower()
                    if mt == "conversation":
                        continue
                    filtered.append(m)
                matched = filtered
            if not matched:
                return ""
            return "\n\n".join([m["text"] for m in matched[-k:]])
        
        docs = self.retrieve_relevant(
            query,
            k=max(k * 3, 8),
            mode=mode,
            thread_id=thread_id,
            project_path=project_path,
        )
        if not docs:
            return ""

        typed_parts: List[str] = []
        conversation_parts: List[str] = []
        owner = self._owner_id()
        active_content = {
            str(record.get("normalized_content") or self._normalize_memory_content(record.get("text") or ""))
            for record in self._records.values()
            if bool(record.get("active", True)) and str(record.get("owner_id") or "") == owner
        }
        for doc in docs:
            if not doc.page_content.strip():
                continue
            meta = getattr(doc, "metadata", {}) or {}
            mode_val = meta.get("mode") if isinstance(meta, dict) else None
            if not self._mode_matches(mode_val, mode):
                continue
            if not self._thread_matches(meta.get("thread_id") if isinstance(meta, dict) else None, thread_id):
                continue
            mt = str(meta.get("type") or "").strip().lower() if isinstance(meta, dict) else ""
            if mt == "conversation":
                conversation_parts.append(doc.page_content)
            else:
                # The vector index is derived and may be stale after an index
                # write failure. Canonical tombstones/ownership always win.
                if self._normalize_memory_content(doc.page_content) in active_content:
                    typed_parts.append(doc.page_content)

        # Prefer durable typed memories; only fill with raw conversations when auto-store is on.
        context_parts = list(typed_parts)
        if inject_raw_conversations:
            for part in conversation_parts:
                if len(context_parts) >= k:
                    break
                context_parts.append(part)

        if not context_parts:
            return ""

        return "\n\n".join(context_parts[-k:])

    @_synchronized_records()
    def count_items(
        self,
        mode: Optional[str] = None,
        thread_id: Optional[str] = None,
        project_id: str = "",
        include_global: bool = True,
    ) -> int:
        owner = self._owner_id()
        return sum(
            1 for record in self._records.values()
            if bool(record.get("active", True))
            and str(record.get("owner_id") or "") == owner
            and str(record.get("scope") or "account") != "temporary"
            and self._record_matches_scope(
                record,
                project_id=project_id,
                thread_id=str(thread_id or ""),
                include_global=include_global,
            )
        )

    @_synchronized_records()
    def clear_scope(
        self,
        *,
        project_id: str,
        thread_id: str,
        include_global: bool = False,
    ) -> int:
        """Forget only records visible in one explicit Project/Session scope."""
        if not str(project_id or "").strip() or not str(thread_id or "").strip():
            raise ValueError("Project and Session scope are required")
        ids = [
            str(record.get("id") or "")
            for record in self._records.values()
            if bool(record.get("active", True))
            and self._record_matches_scope(
                record,
                project_id=project_id,
                thread_id=thread_id,
                include_global=include_global,
            )
        ]
        return self.delete_items(
            ids,
            project_id=project_id,
            thread_id=thread_id,
            include_global=include_global,
        )

    @_synchronized_records()
    def clear_memory(self) -> None:
        deleted_at = datetime.now().isoformat()
        for record in self._records.values():
            if bool(record.get("active", True)):
                record["active"] = False
                record["deleted_at"] = deleted_at
                record["updated_at"] = deleted_at
                record["index_state"] = "deleted"
        self._save_records()
        self._profile = {}
        self._save_profile()
        if self.use_faiss:
            if self.partition_enabled:
                for path, _store in list(self._iter_vector_stores(None, None)):
                    new_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
                    self._save_vector_store(new_store, path)
                    self._vector_stores[str(path)] = new_store
            else:
                self.vector_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
                self._save_to_disk()
        else:
            self.simple_memory = []
        logger.info("Memory cleared")

    def _save_to_disk(self) -> None:
        if self.use_faiss:
            try:
                if self.partition_enabled:
                    for path, store in self._iter_vector_stores(None, None):
                        self._save_vector_store(store, path)
                elif self.vector_store is not None:
                    self.vector_store.save_local(self.memory_path)
                    logger.debug("Memory saved to disk")
            except Exception as e:
                logger.error(f"Failed to save memory: {e}")

    @property
    @_synchronized_records()
    def memory_count(self) -> int:
        return sum(1 for record in self._records.values() if bool(record.get("active", True)))


def get_agent_memory(memory_path: Optional[str] = None) -> AgentMemory:
    """Return the one in-process owner for a configured durable memory path."""
    root = Path(memory_path or str(config.memory_path)).resolve(strict=False)
    key = os.path.normcase(str(root))
    with _MEMORY_INSTANCES_GUARD:
        existing = _MEMORY_INSTANCES.get(key)
        if existing is not None:
            return existing
        created = AgentMemory(str(root))
        _MEMORY_INSTANCES[key] = created
        return created
