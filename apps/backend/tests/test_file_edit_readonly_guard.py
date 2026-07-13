"""Regression: read-only inspect prompts must not match file-edit planner intent."""

from __future__ import annotations

import re


def _would_enter_file_edit_planner(user_input: str) -> bool:
    """Mirror the file-edit gate in agent.core process_query preemption."""
    _fe_low = (user_input or "").lower()
    _fe_code_exts = r"\.(?:py|ts|tsx|js|jsx|json|md|css|html|yaml|yml|toml|cfg|ini|sh|bat|ps1|sql|go|rs|c|cpp|h|hpp|java|kt|swift|rb|php|lua|r|jl|txt|env|conf|xml|svg)"
    _fe_file_match = re.search(
        r"(?:^|\s)((?:[\w./\\-]+/)?\w[\w.-]*" + _fe_code_exts + r")\b",
        user_input,
        re.IGNORECASE,
    )
    if not _fe_file_match:
        _fe_file_match = re.search(r"\b(soul\.md|SOUL\.md|soul)\b", user_input)
    _fe_edit_match = re.search(
        r"\b(fix|edit|trim|shorten|update|change|modify|rewrite|cut|reduce|shrink|"
        r"add|insert|comment|annotate|patch|tweak|make .+ more)\b",
        _fe_low,
    )
    _inspect_only = bool(
        re.search(
            r"(?i)("
            r"\bdo\s+not\s+(change|edit|touch|modify|write|save)\s+anything\b|"
            r"\bdon'?t\s+(change|edit|touch|modify|write|save)\s+anything\b|"
            r"\bwithout\s+(changing|editing|modifying|updating)\b|"
            r"\bno\s+changes?\b|"
            r"\bread[\s-]?only\b|"
            r"\bjust\s+(check|look|read|inspect|peek|include|show|explain)\b|"
            r"\binspect\s+only\b|"
            r"\bcheck\s+\S+\s+but\s+do\s+not\b|"
            r"\binclude\s+the\s+corrected\s+code\b|"
            r"\b(check|inspect|look\s+at|read|peek\s+at|explain)\b[\s\S]{0,80}"
            r"\b(do\s+not|don'?t)\s+(change|edit|touch|modify)\s+(it|anything)\b"
            r")",
            user_input,
        )
    )
    _fe_positive_edit = bool(
        re.search(
            r"(?i)("
            r"\b(?<!do not )(?<!don't )(?<!dont )(change|edit|fix|update|modify|rewrite|patch|tweak)\b[\s\S]{0,60}"
            r"\.(?:html?|js|tsx?|py|css|json|md)\b|"
            r"\b(change|edit|fix|update|modify|rewrite|patch|tweak)\s+"
            r"(?:the\s+)?(?:title|content|text|code|file)\s+"
            r"(?:in\s+)?[\w./\\-]*\.(?:html?|js|tsx?|py|css|json|md)\b"
            r")",
            user_input,
        )
    )
    _fe_readonly = _inspect_only or (
        bool(_fe_edit_match)
        and not _fe_positive_edit
        and bool(re.search(r"(?i)\b(check|inspect|look\s+at|read|peek|include)\b", user_input))
    )
    if _fe_positive_edit and not _inspect_only:
        _fe_readonly = False
    return bool(_fe_file_match and _fe_edit_match and not _fe_readonly)


def test_readonly_inspect_does_not_enter_planner():
    assert not _would_enter_file_edit_planner(
        "Check game.js but do not change anything."
    )
    assert not _would_enter_file_edit_planner(
        "Inspect index.html only. Do not change anything."
    )
    assert not _would_enter_file_edit_planner(
        "Look at game.js without modifying it."
    )
    assert not _would_enter_file_edit_planner(
        "Read style.css — don't edit it."
    )


def test_real_edit_still_enters_planner():
    assert _would_enter_file_edit_planner(
        "Change the title in index.html to Game Application."
    )
    assert _would_enter_file_edit_planner(
        "Fix the bug in game.js"
    )
    assert _would_enter_file_edit_planner(
        "Update style.css with a darker background."
    )
    assert _would_enter_file_edit_planner(
        "Change the title in index.html only. Do not edit game.js."
    )
    assert _would_enter_file_edit_planner(
        "Change the title in index.html to Compat Gap Safe Title. Do not edit game.js."
    )


def test_do_not_edit_anything_stays_out_of_planner():
    assert not _would_enter_file_edit_planner(
        "Do not edit anything; just include the corrected code for index.html."
    )
