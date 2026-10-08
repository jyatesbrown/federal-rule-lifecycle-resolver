from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from ..lifecycle.doctypes import NormalizedType

SCHEMA_VERSION = "1.0"

ResolutionStatus = Literal[
    "resolved", "partial", "ambiguous", "not_found", "conflicting_identifiers", "insufficient_data"
]
Stage = Literal[
    "pre_rule",
    "proposed_comment_open",
    "proposed_comment_closed",
    "final_rule_not_yet_effective",
    "effective",
    "withdrawn",
    "unknown",
]
Confidence = Literal["high", "medium", "low"]
SourceState = Literal[
    "success", "not_queried", "unavailable", "timeout", "authentication_failed", "rate_limited", "parser_failed"
]
InputType = Literal["federal_register_document_number", "federal_register_url", "rin", "docket_id", "query"]

SCOPE_NOTE = (
    "Administrative rulemaking record only (Federal Register and Regulations.gov). Does not reflect court decisions, "
    "injunctions, stays, appropriations riders, enforcement policy or state law unless a Federal Register document "
    "itself announces the change. Not legal advice."
)


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    def to_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class QueryInfo(_Model):
    input_type: InputType
    input: str
    agency: str | None = None


class Agency(_Model):
    name: str
    slug: str | None = None


class Action(_Model):
    canonical_title: str = Field(description="Title of the anchor document (latest final rule, else latest proposal).")
    agencies: list[Agency]
    rins: list[str]
    docket_ids: list[str] = Field(description="Regulations.gov-style docket IDs from the official records.")
    cfr_parts: list[str] = Field(description="CFR title/part references, e.g. '40 CFR 60'.")


class CommentPeriod(_Model):
    original_deadline: str | None = Field(description="Comment deadline of the first proposal (YYYY-MM-DD).")
    current_deadline: str | None = Field(description="Latest comment deadline after extensions/reopenings/supplements.")
    extended: bool | None = Field(description="True when an extension, reopening or supplemental proposal moved it.")
    open: bool | None = Field(description="currentDeadline is today or later (as of checkedAt, US Eastern).")


class EffectiveDate(_Model):
    original: str | None = Field(description="Effective date stated by the final rule when published.")
    current: str | None = Field(description="Effective date after any delay documents.")
    delayed: bool | None


class Lifecycle(_Model):
    current_stage: Stage = Field(
        description=(
            "pre_rule: only an advance notice exists. proposed_comment_open/closed: a proposal exists, no final rule, "
            "latest comment deadline in the future/past. final_rule_not_yet_effective / effective: a final rule exists "
            "and its current effective date is in the future/has passed. withdrawn: an official document withdraws the "
            "action. unknown: official data is insufficient. Extensions, corrections and delays adjust dates; they are "
            "never a stage themselves."
        )
    )
    stage_basis: list[str] = Field(description="Deterministic reasons for currentStage, citing document numbers.")
    proposed_rule_published: str | None
    comment_period: CommentPeriod
    final_rule_published: str | None
    effective_date: EffectiveDate
    withdrawn_on: str | None = None
    latest_official_action_date: str | None
    latest_official_action: str | None = Field(default=None, description="Document number of the latest action.")


class RelatedDocument(_Model):
    source: Literal["Federal Register"] = "Federal Register"
    normalized_type: NormalizedType
    native_type: str | None
    native_action: str | None
    title: str
    document_number: str
    publication_date: str | None
    comments_close_on: str | None = None
    effective_on: str | None = None
    rins: list[str]
    docket_ids: list[str]
    source_url: str | None
    regulations_gov_document_id: str | None = None
    relation_basis: list[str]


class Docket(_Model):
    docket_id: str
    title: str | None
    agency: str | None
    rin: str | None
    docket_type: str | None
    source_url: str


class RegulationsGovInfo(_Model):
    dockets: list[Docket]
    document_count: int | None = Field(description="Proposed/final rule documents in the docket(s); null if unknown.")
    comment_count: int | None = Field(default=None, description="Not retrieved in v1; always null.")


class Ambiguity(_Model):
    kind: str
    detail: str


class Resolution(_Model):
    confidence: Confidence | None = Field(
        description=(
            "high: exact document number, exact RIN, exact docket ID or several agreeing identifiers. medium: a single "
            "dominant natural-language match consistent in agency, title and identifiers. low is never returned as "
            "resolved; such queries return status 'ambiguous'."
        )
    )
    matched_by: list[str]
    ambiguities: list[Ambiguity]


class Candidate(_Model):
    title: str
    agency: str | None
    rins: list[str]
    docket_ids: list[str]
    federal_register_document_number: str
    publication_date: str | None
    document_type: str | None
    source_url: str | None


class SourceStatus(_Model):
    status: SourceState
    detail: str | None = None


class Sources(_Model):
    federal_register: SourceStatus
    regulations_gov: SourceStatus


class Billing(_Model):
    billable: bool
    event_name: str | None
    reason: str


class ResolutionResult(_Model):
    schema_version: str = Field(default=SCHEMA_VERSION)
    status: ResolutionStatus
    query: QueryInfo
    checked_at: str
    scope_note: str = Field(default=SCOPE_NOTE)
    action: Action | None = None
    lifecycle: Lifecycle | None = None
    related_documents: list[RelatedDocument] = Field(default_factory=list)
    regulations_gov: RegulationsGovInfo | None = None
    resolution: Resolution
    candidates: list[Candidate] = Field(default_factory=list)
    sources: Sources
    billing: Billing
