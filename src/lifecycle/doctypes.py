"""Conservative normalization of Federal Register document type + action text."""

from __future__ import annotations

import re
from typing import Literal

NormalizedType = Literal[
    "advance_notice",
    "proposed_rule",
    "supplemental_proposed_rule",
    "notice",
    "comment_period_extension",
    "comment_period_reopening",
    "final_rule",
    "interim_final_rule",
    "direct_final_rule",
    "correction",
    "effective_date_delay",
    "withdrawal",
    "amendment",
    "other",
]


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None


def normalize_document_type(native_type: str | None, action: str | None) -> NormalizedType:
    """Classify from the official `type` and `action` fields only (never from the title)."""
    t = (native_type or "").strip().lower()
    a = re.sub(r"\s+", " ", (action or "").strip().lower())
    if _has(a, r"\bpartial withdrawal\b"):
        return "proposed_rule" if t == "proposed rule" else "other"
    if _has(a, r"^withdrawal\b|\bwithdrawal\b\.?$|;\s*withdrawal\b|\bwithdrawn\b"):
        return "withdrawal"
    if _has(a, r"\bcorrect(ion|ing)\b") and not _has(a, r"\binterim final rule\b"):
        return "correction"
    if _has(a, r"delay(ed)? of (the )?effective date|\bdelay of effectiveness\b"):
        return "effective_date_delay"
    if _has(a, r"\breopening of (the )?(public )?comment period\b|\breopen(ing)?\b.*\bcomment"):
        return "comment_period_reopening"
    if _has(a, r"\bextension of (the )?(public )?comment period\b|\bcomment period extension\b"):
        return "comment_period_extension"
    if _has(a, r"\badvance notice of proposed rulemaking\b|\banprm\b"):
        return "advance_notice"
    if _has(a, r"\bsupplemental notice of proposed rulemaking\b|\bsnprm\b|\bsupplemental proposed rule\b"):
        return "supplemental_proposed_rule"
    if _has(a, r"\binterim final rule\b"):
        return "interim_final_rule"
    if _has(a, r"\bdirect final rule\b"):
        return "direct_final_rule"
    if t == "proposed rule":
        return "proposed_rule"
    if t == "rule":
        if _has(a, r"\bfinal (rule|regulations?|action)\b|^final\b|^rule\b"):
            return "final_rule"
        if _has(a, r"\bamend(ment|ments|ing)?\b|\btechnical amendment"):
            return "amendment"
        return "final_rule"
    if t == "notice":
        return "notice"
    return "other"


PROPOSAL_TYPES = frozenset({"proposed_rule", "supplemental_proposed_rule"})
FINAL_TYPES = frozenset({"final_rule", "interim_final_rule", "direct_final_rule"})
