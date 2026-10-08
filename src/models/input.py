from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from ..identifiers import (
    document_number_from_fr_url,
    normalize_docket_id,
    normalize_fr_document_number,
    normalize_rin,
)


class ActorInput(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    query: str | None = Field(default=None, max_length=300)
    rin: str | None = None
    docket_id: str | None = None
    federal_register_document_number: str | None = None
    federal_register_url: str | None = None
    agency: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate(self) -> ActorInput:
        for name in ("query", "rin", "docket_id", "federal_register_document_number", "federal_register_url", "agency"):
            value = getattr(self, name)
            if isinstance(value, str) and not value.strip():
                setattr(self, name, None)
        if not any(
            (self.query, self.rin, self.docket_id, self.federal_register_document_number, self.federal_register_url)
        ):
            raise ValueError(
                "Provide at least one of query, rin, docketId, federalRegisterDocumentNumber, federalRegisterUrl"
            )
        if self.rin and not normalize_rin(self.rin):
            raise ValueError("rin must look like 2060-AV16 (4 digits, hyphen, 2 letters + 2 digits)")
        if self.docket_id and not normalize_docket_id(self.docket_id):
            raise ValueError("docketId must be a Regulations.gov docket ID such as EPA-HQ-OAR-2021-0317")
        if self.federal_register_document_number and not normalize_fr_document_number(
            self.federal_register_document_number
        ):
            raise ValueError("federalRegisterDocumentNumber must look like 2024-00366")
        if self.federal_register_url and not document_number_from_fr_url(self.federal_register_url):
            raise ValueError(
                "federalRegisterUrl must be a federalregister.gov/documents/... URL with a document number"
            )
        return self

    @property
    def document_numbers(self) -> list[str]:
        out: list[str] = []
        for candidate in (
            normalize_fr_document_number(self.federal_register_document_number or ""),
            document_number_from_fr_url(self.federal_register_url or ""),
        ):
            if candidate and candidate not in out:
                out.append(candidate)
        return out

    @property
    def normalized_rin(self) -> str | None:
        return normalize_rin(self.rin or "")

    @property
    def normalized_docket_id(self) -> str | None:
        return normalize_docket_id(self.docket_id or "")

    @property
    def input_type(self) -> str:
        if self.federal_register_document_number:
            return "federal_register_document_number"
        if self.federal_register_url:
            return "federal_register_url"
        if self.rin:
            return "rin"
        if self.docket_id:
            return "docket_id"
        return "query"

    @property
    def primary_input(self) -> str:
        return {
            "federal_register_document_number": self.federal_register_document_number,
            "federal_register_url": self.federal_register_url,
            "rin": self.rin,
            "docket_id": self.docket_id,
            "query": self.query,
        }[self.input_type] or ""
