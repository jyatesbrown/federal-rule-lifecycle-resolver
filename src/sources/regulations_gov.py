"""Regulations.gov API v4 adapter (https://open.gsa.gov/api/regulationsgov/). Requires an api.data.gov key."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from ..lifecycle.dates import eastern_date
from ..utils.errors import ErrorCode, SourceError
from ..utils.http import get_json

API = "https://api.regulations.gov/v4"
KEY_ENV = "REGULATIONS_GOV_API_KEY"
RULE_DOCUMENT_TYPES = "Proposed Rule,Rule"
PAGE_SIZE = 50


@dataclass(frozen=True)
class RegsDocket:
    docket_id: str
    title: str | None
    agency: str | None
    rin: str | None
    docket_type: str | None

    @property
    def url(self) -> str:
        return f"https://www.regulations.gov/docket/{self.docket_id}"


@dataclass(frozen=True)
class RegsDocument:
    document_id: str
    docket_id: str | None
    fr_document_number: str | None
    document_type: str | None
    subtype: str | None
    title: str | None
    posted_date: date | None
    comment_end_date: date | None
    withdrawn: bool


@dataclass(frozen=True)
class RegsDocketRecord:
    docket: RegsDocket
    documents: tuple[RegsDocument, ...]
    rule_document_count: int | None


def _attrs(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or not isinstance(item.get("attributes"), dict):
        raise SourceError(ErrorCode.PARSER_FAILED, "Regulations.gov: item without attributes")
    return item["attributes"]


def parse_docket(payload: Any) -> RegsDocket:
    data = payload.get("data") if isinstance(payload, dict) else None
    attrs = _attrs(data)
    rin = attrs.get("rin")
    return RegsDocket(
        docket_id=data.get("id"),
        title=(attrs.get("title") or "").strip() or None,
        agency=attrs.get("agencyId"),
        rin=rin if isinstance(rin, str) and rin and rin.lower() != "not assigned" else None,
        docket_type=attrs.get("docketType"),
    )


def parse_documents(payload: Any) -> tuple[list[RegsDocument], int | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SourceError(ErrorCode.PARSER_FAILED, "Regulations.gov documents: unexpected shape")
    docs = []
    for item in payload["data"]:
        a = _attrs(item)
        docs.append(
            RegsDocument(
                document_id=item.get("id"),
                docket_id=a.get("docketId"),
                fr_document_number=a.get("frDocNum") or None,
                document_type=a.get("documentType"),
                subtype=a.get("subtype"),
                title=a.get("title"),
                posted_date=eastern_date(a.get("postedDate")),
                comment_end_date=eastern_date(a.get("commentEndDate")),
                withdrawn=bool(a.get("withdrawn")),
            )
        )
    total = (payload.get("meta") or {}).get("totalElements")
    return docs, total if isinstance(total, int) else None


class RegulationsGovAdapter:
    name = "Regulations.gov"

    def __init__(self, client: httpx.AsyncClient, api_key: str | None = None) -> None:
        self.client = client
        self.api_key = api_key if api_key is not None else os.environ.get(KEY_ENV)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise SourceError(ErrorCode.AUTHENTICATION_FAILED, f"{KEY_ENV} is not configured")
        return {"X-Api-Key": self.api_key}

    async def docket(self, docket_id: str) -> RegsDocketRecord:
        headers = self._headers()
        docket = parse_docket(await get_json(self.client, f"{API}/dockets/{docket_id}", headers=headers))
        params = {
            "filter[docketId]": docket_id,
            "filter[documentType]": RULE_DOCUMENT_TYPES,
            "page[size]": str(PAGE_SIZE),
            "sort": "postedDate",
        }
        documents, total = parse_documents(
            await get_json(self.client, f"{API}/documents", params=params, headers=headers)
        )
        return RegsDocketRecord(docket=docket, documents=tuple(documents), rule_document_count=total)
