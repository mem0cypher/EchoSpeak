"""Deterministic boundaries between information requests and project creation.

Project allocation is a filesystem side effect, so it must be protected at the
sink as well as at the router. If an utterance could reasonably be an
information request, it is never allowed to create a project directory.
"""

from __future__ import annotations

import re


_INFO_OPENERS = re.compile(
    r"\b(?:what|when|where|who|why|how)\b|\?$|"
    r"\b(?:look at|read|inspect|describe|explain|tell me about|show me)\b",
    re.IGNORECASE,
)
_LIVE_INFO = re.compile(
    r"\b(?:weather|forecast|temperature|news|headline|score|scores|"
    r"schedule|fixture|match(?:es)?|standings|odds|price|stock|bitcoin|"
    r"fifa|nba|nfl|nhl|mlb|world cup)\b",
    re.IGNORECASE,
)
_FILE_INSPECTION = re.compile(
    r"\b(?:soul(?:\.md)?|personality|config(?:\.py)?|settings|"
    r"readme|guidelines|instructions|document|docs)\b",
    re.IGNORECASE,
)
_CREATE = re.compile(
    r"\b(?:build|create|scaffold|develop|code|make|write)\s+(?:me\s+|us\s+)?(?:a|an)\s+"
    r"([a-z0-9][\w\s-]{1,80})",
    re.IGNORECASE,
)
_NON_PROJECT_OBJECT = re.compile(
    r"\b(?:note|summary|plan|sandwich|coffee|meal|breakfast|lunch|dinner|"
    r"point|decision|sense|mistake)\b",
    re.IGNORECASE,
)


def normalize_request(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def is_information_request(text: str) -> bool:
    """Return true for a request to learn/search/read rather than create artifacts."""
    value = normalize_request(text)
    if not value:
        return False
    # Explicit artifact creation is build intent even when the product domain
    # contains live-info words, e.g. "build a weather dashboard".
    if _CREATE.search(value) and not _INFO_OPENERS.search(value):
        return False
    if _INFO_OPENERS.search(value) or _LIVE_INFO.search(value):
        return True
    return bool(
        _FILE_INSPECTION.search(value)
        and re.search(r"\b(?:look|read|inspect|scan|show|describe|explain|tell)\b", value, re.IGNORECASE)
    )


def is_explicit_new_project_request(text: str) -> bool:
    """Return true only for an unambiguous request to create a software artifact."""
    value = normalize_request(text)
    if not value or is_information_request(value):
        return False
    match = _CREATE.search(value)
    if not match:
        return False
    product = match.group(1)
    if _NON_PROJECT_OBJECT.search(product):
        return False
    # A single script/file is a file-write request, not permission to allocate
    # and scaffold a multi-file project directory.
    if re.search(r"\b(?:script|file)\b", product, re.IGNORECASE) and not re.search(
        r"\b(?:app|application|site|website|game|tool|dashboard|program|api|bot|"
        r"prototype|project|tracker|editor|manager|engine|simulator|simulation|adventure)\b",
        product,
        re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:app|application|site|website|game|tool|dashboard|program|"
            r"script|api|bot|prototype|project|tracker|editor|manager|engine|simulator|simulation|adventure)\b",
            product,
            re.IGNORECASE,
        )
    )


def may_materialize_project(text: str) -> bool:
    """Alias used at filesystem mutation points to make the invariant explicit."""
    return is_explicit_new_project_request(text)
