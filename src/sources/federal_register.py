"""Federal Register API adapter (https://www.federalregister.gov/developers/documentation/api/v1). No key required."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from ..identifiers import regulations_gov_docket_ids
from ..utils.errors import ErrorCode, SourceError
from ..utils.http import get_json

API = "https://www.federalregister.gov/api/v1"
FIELDS = (
    "document_number",
    "title",
    "type",
    "action",
    "publication_date",
    "comments_close_on",
    "effective_on",
    "regulation_id_numbers",
    "docket_ids",
    "correction_of",
    "corrections",
    "agencies",
    "cfr_references",
    "html_url",
    "dates",
)
MAX_PER_PAGE = 100
MAX_PAGES = 3
RULEMAKING_TYPES = ("RULE", "PRORULE")


@dataclass(frozen=True)
class FRDocument:
    document_number: str
    title: str
    native_type: str | None
    action: str | None
    publication_date: date | None
    comments_close_on: date | None
    effective_on: date | None
    rins: tuple[str, ...]
    docket_ids: tuple[str, ...]
    correction_of: str | None
    corrections: tuple[str, ...]
    agencies: tuple[tuple[str, str | None], ...]
    cfr_parts: tuple[str, ...]
    html_url: str | None
    dates_text: str | None
    raw_docket_ids: tuple[str, ...] = field(default=())


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _doc_number_from_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parts = [p for p in value.rstrip("/").split("/") if p]
    for part in reversed(parts):
        stem = part.split(".")[0].split("?")[0]
        if len(stem) >= 9 and stem[:4].isdigit() and stem[4] == "-":
            return stem
    return None


def parse_document(raw: dict[str, Any]) -> FRDocument:
    try:
        number = raw["document_number"]
        if not isinstance(number, str) or not number:
            raise KeyError("document_number")
        agencies = tuple(
            (a.get("name") or a.get("raw_name") or "", a.get("slug"))
            for a in raw.get("agencies") or []
            if isinstance(a, dict) and (a.get("name") or a.get("raw_name"))
        )
        cfr = tuple(
            dict.fromkeys(
                f"{c['title']} CFR {c['part']}"
                for c in raw.get("cfr_references") or []
                if c.get("title") and c.get("part")
            )
        )
        raw_dockets = tuple(raw.get("docket_ids") or [])
        return FRDocument(
            document_number=number,
            title=(raw.get("title") or "").strip(),
            native_type=raw.get("type"),
            action=raw.get("action"),
            publication_date=_date(raw.get("publication_date")),
            comments_close_on=_date(raw.get("comments_close_on")),
            effective_on=_date(raw.get("effective_on")),
            rins=tuple(dict.fromkeys(raw.get("regulation_id_numbers") or [])),
            docket_ids=tuple(regulations_gov_docket_ids(list(raw_dockets))),
            correction_of=_doc_number_from_url(raw.get("correction_of")),
            corrections=tuple(n for n in (_doc_number_from_url(c) for c in raw.get("corrections") or []) if n),
            agencies=agencies,
            cfr_parts=cfr,
            html_url=raw.get("html_url"),
            dates_text=raw.get("dates"),
            raw_docket_ids=raw_dockets,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise SourceError(ErrorCode.PARSER_FAILED, f"Federal Register document: {exc!r}") from exc


class FederalRegisterAdapter:
    name = "Federal Register"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    def _fields(self) -> list[tuple[str, str]]:
        return [("fields[]", f) for f in FIELDS]

    async def document(self, number: str) -> FRDocument:
        data = await get_json(self.client, f"{API}/documents/{number}.json", params=self._fields())
        if not isinstance(data, dict):
            raise SourceError(ErrorCode.PARSER_FAILED, "Federal Register document: not an object")
        return parse_document(data)

    async def _search(self, conditions: list[tuple[str, str]], *, order: str, pages: int) -> list[FRDocument]:
        docs: list[FRDocument] = []
        for page in range(1, pages + 1):
            params = (
                conditions
                + self._fields()
                + [
                    ("per_page", str(MAX_PER_PAGE)),
                    ("order", order),
                    ("page", str(page)),
                ]
            )
            data = await get_json(self.client, f"{API}/documents.json", params=params)
            if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
                raise SourceError(ErrorCode.PARSER_FAILED, "Federal Register search: unexpected shape")
            docs.extend(parse_document(r) for r in data.get("results") or [])
            if not data.get("next_page_url"):
                break
        return docs

    async def by_rin(self, rin: str) -> list[FRDocument]:
        return await self._search([("conditions[regulation_id_number]", rin)], order="oldest", pages=MAX_PAGES)

    async def by_docket(self, docket_id: str) -> list[FRDocument]:
        return await self._search([("conditions[docket_id]", docket_id)], order="oldest", pages=MAX_PAGES)

    async def search(self, text: str, agency_slug: str | None = None) -> list[FRDocument]:
        conditions = [("conditions[term]", text)] + [("conditions[type][]", t) for t in RULEMAKING_TYPES]
        if agency_slug:
            conditions.append(("conditions[agencies][]", agency_slug))
        return (await self._search(conditions, order="relevance", pages=1))[:25]
