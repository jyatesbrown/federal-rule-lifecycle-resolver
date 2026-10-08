"""Deterministic extraction of dates from Federal Register `dates` free text."""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE = rf"(?:{_MONTHS})\s+\d{{1,2}},\s+\d{{4}}"
_EASTERN = ZoneInfo("America/New_York")

_COMMENT_EXTENDED = re.compile(rf"\bextended from\s+{_DATE}\s+to\s+({_DATE})", re.IGNORECASE)
_COMMENT_RECEIVED = re.compile(rf"\b(?:on or before|no later than|by)\s+({_DATE})", re.IGNORECASE)
_DELAYED_UNTIL = re.compile(rf"\bis\s+(?:further\s+)?(?:delayed|extended)\s+(?:until|to)\s+({_DATE})", re.IGNORECASE)


def parse_long_date(text: str) -> date | None:
    try:
        return datetime.strptime(re.sub(r"\s+", " ", text.strip()), "%B %d, %Y").date()
    except ValueError:
        return None


def comment_deadline_from_text(dates_text: str | None) -> date | None:
    if not dates_text:
        return None
    m = _COMMENT_EXTENDED.search(dates_text)
    if m:
        return parse_long_date(m.group(1))
    if not re.search(r"\bcomment", dates_text, re.IGNORECASE):
        return None
    m = _COMMENT_RECEIVED.search(dates_text)
    return parse_long_date(m.group(1)) if m else None


def delayed_effective_date_from_text(dates_text: str | None) -> date | None:
    matches = _DELAYED_UNTIL.findall(dates_text or "")
    return parse_long_date(matches[-1]) if matches else None


def eastern_date(timestamp: str | None) -> date | None:
    """Regulations.gov timestamps are UTC (e.g. 2023-02-14T04:59:59Z = Feb 13 11:59 pm Eastern)."""
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(_EASTERN).date()
    except ValueError:
        return None
