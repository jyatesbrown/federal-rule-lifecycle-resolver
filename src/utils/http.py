from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .errors import ErrorCode, SourceError

USER_AGENT = (
    "FederalRuleLifecycleResolver/1.0 (Apify Actor; +https://github.com/jyatesbrown/federal-rule-lifecycle-resolver)"
)
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 2.0
TRANSIENT_STATUSES = frozenset({408, 425, 500, 502, 503, 504})


def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)


def backoff_delay(attempt: int) -> float:
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: list[tuple[str, str]] | dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET JSON with bounded retries on transient failures; raise `SourceError` with a controlled code otherwise."""
    last: SourceError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url, params=params, headers={"Accept": "application/json", **(headers or {})})
        except httpx.TimeoutException as exc:
            last = SourceError(ErrorCode.TIMEOUT, f"{url}: {type(exc).__name__}")
        except httpx.HTTPError as exc:
            last = SourceError(ErrorCode.UNAVAILABLE, f"{url}: {type(exc).__name__}: {exc}")
        else:
            status = response.status_code
            if status == 404:
                raise SourceError(ErrorCode.NOT_FOUND, f"{url}: HTTP 404")
            if status in (401, 403):
                raise SourceError(ErrorCode.AUTHENTICATION_FAILED, f"{url}: HTTP {status}")
            if status == 429:
                raise SourceError(ErrorCode.RATE_LIMITED, f"{url}: HTTP 429")
            if status in TRANSIENT_STATUSES:
                last = SourceError(ErrorCode.UNAVAILABLE, f"{url}: HTTP {status}")
            elif status >= 400:
                raise SourceError(ErrorCode.UNAVAILABLE, f"{url}: HTTP {status}")
            else:
                try:
                    return json.loads(response.text)
                except json.JSONDecodeError as exc:
                    raise SourceError(ErrorCode.PARSER_FAILED, f"{url}: invalid JSON: {exc}") from exc
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(backoff_delay(attempt))
    assert last is not None
    raise last
