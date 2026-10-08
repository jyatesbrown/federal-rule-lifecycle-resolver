"""Deterministic parsing and normalization of federal rulemaking identifiers."""

from __future__ import annotations

import re
from urllib.parse import urlparse

RIN_RE = re.compile(r"^\s*(\d{4})\s*[-–]\s*([A-Z]{2}\d{2})\s*$", re.IGNORECASE)
FR_DOC_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*[-–]\s*(\d{4,6})\s*$")
FR_DOC_IN_PATH_RE = re.compile(r"/((?:19|20)\d{2}-\d{4,6})(?:/|$)")
DOCKET_RE = re.compile(r"^\s*([A-Z][A-Z0-9]{1,15}(?:-[A-Z0-9]{1,12}){1,5})\s*$", re.IGNORECASE)
FR_HOSTS = frozenset({"www.federalregister.gov", "federalregister.gov"})


def normalize_rin(value: str) -> str | None:
    m = RIN_RE.match(value or "")
    return f"{m.group(1)}-{m.group(2).upper()}" if m else None


def normalize_fr_document_number(value: str) -> str | None:
    m = FR_DOC_RE.match(value or "")
    return f"{m.group(1)}-{m.group(2)}" if m else None


def document_number_from_fr_url(url: str) -> str | None:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in FR_HOSTS:
        return None
    m = FR_DOC_IN_PATH_RE.search(parsed.path)
    return m.group(1) if m else None


def normalize_docket_id(value: str) -> str | None:
    """Regulations.gov docket IDs look like AGENCY-YYYY-NNNN or AGENCY-PROGRAM-YYYY-NNNN (upper-cased)."""
    if normalize_fr_document_number(value) or normalize_rin(value):
        return None
    m = DOCKET_RE.match(value or "")
    if not m:
        return None
    candidate = m.group(1).upper()
    return candidate if re.search(r"-(?:19|20)\d{2}-\d{3,6}$", candidate) else None


def regulations_gov_docket_ids(values: list[str]) -> list[str]:
    """Keep only FR `docket_ids` entries that are Regulations.gov-style docket IDs (drops e.g. 'FRL-8510-01-OAR')."""
    out: list[str] = []
    for raw in values or []:
        cleaned = re.sub(r"^(?:docket\s+(?:id|no\.?|number)\s*:?\s*)", "", raw.strip(), flags=re.IGNORECASE)
        docket = normalize_docket_id(cleaned)
        if docket and docket not in out:
            out.append(docket)
    return out
