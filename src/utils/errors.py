from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    PARSER_FAILED = "PARSER_FAILED"


SOURCE_STATE = {
    ErrorCode.UNAVAILABLE: "unavailable",
    ErrorCode.TIMEOUT: "timeout",
    ErrorCode.AUTHENTICATION_FAILED: "authentication_failed",
    ErrorCode.RATE_LIMITED: "rate_limited",
    ErrorCode.PARSER_FAILED: "parser_failed",
}


class SourceError(Exception):
    """Controlled failure of one official source. `detail` is developer-facing (logged, not published)."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
