"""Orchestration: identifiers -> official records -> relationship graph -> lifecycle -> one result."""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
from rapidfuzz import fuzz

from .billing import billing_decision
from .lifecycle.doctypes import FINAL_TYPES, PROPOSAL_TYPES, normalize_document_type
from .lifecycle.relationships import ActionGraph, RegsLink, build_action_graph
from .lifecycle.stage import reconstruct_lifecycle
from .models.input import ActorInput
from .models.output import (
    Action,
    Agency,
    Ambiguity,
    Candidate,
    Confidence,
    Docket,
    QueryInfo,
    RegulationsGovInfo,
    RelatedDocument,
    Resolution,
    ResolutionResult,
    ResolutionStatus,
    Sources,
    SourceStatus,
)
from .sources.federal_register import FederalRegisterAdapter, FRDocument
from .sources.regulations_gov import RegsDocketRecord, RegulationsGovAdapter
from .utils.errors import SOURCE_STATE, ErrorCode, SourceError
from .utils.http import create_client

EASTERN = ZoneInfo("America/New_York")
MAX_EXPANSION_QUERIES = 3
MAX_REGS_DOCKETS = 2
MAX_LINKED_FETCHES = 5
NL_RESOLVE_SCORE = 88.0
NL_RESOLVE_MARGIN = 12.0
MAX_CANDIDATES = 5


@dataclass
class Outcome:
    result: ResolutionResult
    elapsed_ms: float


class _SourceTracker:
    def __init__(self) -> None:
        self.state: dict[str, SourceStatus] = {}

    def fail(self, source: str, exc: SourceError) -> None:
        if source not in self.state or self.state[source].status == "success":
            self.state[source] = SourceStatus(status=SOURCE_STATE.get(exc.code, "unavailable"), detail=_public(exc))

    def ok(self, source: str) -> None:
        self.state.setdefault(source, SourceStatus(status="success"))

    def get(self, source: str) -> SourceStatus:
        return self.state.get(source, SourceStatus(status="not_queried"))

    def failed(self, source: str) -> bool:
        return self.get(source).status not in ("success", "not_queried")


def _public(exc: SourceError) -> str:
    return {
        ErrorCode.UNAVAILABLE: "Source could not be retrieved.",
        ErrorCode.TIMEOUT: "Source did not respond in time.",
        ErrorCode.AUTHENTICATION_FAILED: "Source rejected or lacks the API credentials configured for this Actor.",
        ErrorCode.RATE_LIMITED: "Source rate limit reached.",
        ErrorCode.PARSER_FAILED: "Source responded but its structure was not recognised.",
    }.get(exc.code, "Source error.")


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def _agency_matches(doc: FRDocument, agency: str | None) -> bool:
    if not agency:
        return True
    want = _normalize_text(agency)
    return any(want in _normalize_text(name) or want == _normalize_text(slug or "") for name, slug in doc.agencies)


def _title_score(query: str, title: str) -> float:
    """Share of query words found in the title, blended with fuzzy phrase similarity (0-100)."""
    words = [w for w in query.split() if w not in _STOPWORDS]
    title_norm = _normalize_text(title)
    coverage = 100.0 * sum(1 for w in words if w in title_norm.split()) / len(words) if words else 0.0
    return round(0.5 * coverage + 0.5 * fuzz.token_set_ratio(query, title_norm), 1)


_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "for",
        "and",
        "rule",
        "rules",
        "regulation",
        "regulations",
        "final",
        "proposed",
        "epa",
        "status",
        "on",
        "to",
        "in",
    }
)


def _group_key(doc: FRDocument) -> str:
    if doc.rins:
        return f"rin:{doc.rins[0]}"
    if doc.docket_ids:
        return f"docket:{doc.docket_ids[0]}"
    return f"doc:{doc.document_number}"


class Resolver:
    def __init__(self, fr: FederalRegisterAdapter, regs: RegulationsGovAdapter, today: date) -> None:
        self.fr = fr
        self.regs = regs
        self.today = today
        self.sources = _SourceTracker()
        self.ambiguities: list[Ambiguity] = []

    async def _fr(self, coro):  # type: ignore[no-untyped-def]
        try:
            value = await coro
        except SourceError as exc:
            if exc.code == ErrorCode.NOT_FOUND:
                self.sources.ok("fr")
                return None
            self.sources.fail("fr", exc)
            return None
        self.sources.ok("fr")
        return value

    async def resolve(self, inp: ActorInput, checked_at: str) -> ResolutionResult:
        query = QueryInfo(input_type=inp.input_type, input=inp.primary_input, agency=inp.agency)
        numbers, rin, docket = inp.document_numbers, inp.normalized_rin, inp.normalized_docket_id
        matched_by: list[str] = []
        confidence: Confidence | None = "high"

        tasks = [self._fr(self.fr.document(n)) for n in numbers]
        tasks.append(self._fr(self.fr.by_rin(rin)) if rin else asyncio.sleep(0, None))
        tasks.append(self._fr(self.fr.by_docket(docket)) if docket else asyncio.sleep(0, None))
        results = await asyncio.gather(*tasks)
        seeds: list[FRDocument] = [d for d in results[: len(numbers)] if d]
        rin_docs: list[FRDocument] = results[len(numbers)] or []
        docket_docs: list[FRDocument] = results[len(numbers) + 1] or []

        if self.sources.failed("fr"):
            return self._finish(query, checked_at, "insufficient_data", None, None, [], matched_by)

        explicit = bool(numbers or rin or docket)
        if explicit:
            missing = [n for n in numbers if n not in {d.document_number for d in seeds}]
            if missing or (rin and not rin_docs) or (docket and not docket_docs and not numbers and not rin):
                if not seeds and not rin_docs and not docket_docs:
                    detail = "No Federal Register record matches the supplied identifier(s)."
                    if docket and not rin and not numbers:
                        return await self._docket_only(query, checked_at, docket)
                    self.ambiguities.append(Ambiguity(kind="no_match", detail=detail))
                    return self._finish(query, checked_at, "not_found", None, None, [], matched_by)
                for n in missing:
                    self.ambiguities.append(Ambiguity(kind="identifier_not_found", detail=f"Document {n} not found."))
            conflict = self._conflicts(seeds, rin, rin_docs, docket, docket_docs)
            if conflict:
                self.ambiguities.append(Ambiguity(kind="conflicting_identifiers", detail=conflict))
                return self._finish(query, checked_at, "conflicting_identifiers", None, None, [], matched_by)
            matched_by = (
                (["federal_register_document_number"] if seeds else [])
                + (["RIN"] if rin_docs else [])
                + (["docket_id"] if docket_docs else [])
            )
            anchor_rins = {rin} if rin else set()
            for d in seeds:
                anchor_rins |= set(d.rins)
            if not anchor_rins and docket_docs:
                anchor_rins = self._dominant_rin(docket_docs)
            anchor_dockets = {docket} if docket else set()
            for d in seeds:
                anchor_dockets |= set(d.docket_ids)
            pool = seeds + rin_docs + docket_docs
        else:
            picked = await self._natural_language(inp)
            if isinstance(picked, ResolutionResult):
                return picked
            if picked is None:
                status: ResolutionStatus = "insufficient_data" if self.sources.failed("fr") else "not_found"
                return self._finish(query, checked_at, status, None, None, [], matched_by)
            confidence, matched_by = "medium", ["natural_language_title"]
            anchor_rins, anchor_dockets, pool = set(picked.rins), set(picked.docket_ids), [picked]
            numbers = [picked.document_number]

        pool = await self._expand(pool, anchor_rins, anchor_dockets, rin, docket)
        graph = build_action_graph(
            pool, anchor_rins=anchor_rins, anchor_dockets=anchor_dockets, requested_numbers=set(numbers)
        )
        regs_records = await self._regulations_gov(graph.docket_ids or sorted(anchor_dockets))
        links = {
            d.fr_document_number: RegsLink(d.document_id, rec.docket.docket_id)
            for rec in regs_records
            for d in rec.documents
            if d.fr_document_number
        }
        known = {d.document_number for d in pool}
        extra = [n for n in links if n not in known][:MAX_LINKED_FETCHES]
        if extra:
            pool += [d for d in await asyncio.gather(*(self._fr(self.fr.document(n)) for n in extra)) if d]
        graph = build_action_graph(
            pool,
            anchor_rins=anchor_rins,
            anchor_dockets=anchor_dockets,
            requested_numbers=set(numbers),
            regs_links=links,
        )
        for doc, reason in graph.excluded:
            self.ambiguities.append(Ambiguity(kind="excluded_document", detail=f"{doc.document_number}: {reason}."))
        if not graph.documents:
            return self._finish(query, checked_at, "not_found", None, None, [], matched_by)
        regs_end = {
            d.fr_document_number: d.comment_end_date
            for r in regs_records
            for d in r.documents
            if d.fr_document_number and d.comment_end_date
        }
        lifecycle = reconstruct_lifecycle(graph.documents, self.today, regs_end)
        if lifecycle.current_stage == "unknown":
            status = "insufficient_data"
        elif self.sources.failed("regs") or self.sources.failed("fr"):
            status = "partial"
        else:
            status = "resolved"
        return self._finish(query, checked_at, status, confidence, (graph, lifecycle, regs_records), [], matched_by)

    def _conflicts(self, seeds, rin, rin_docs, docket, docket_docs) -> str | None:  # type: ignore[no-untyped-def]
        groups: list[tuple[str, set[str], set[str]]] = [
            (f"document {d.document_number}", set(d.rins), set(d.docket_ids)) for d in seeds
        ]
        if rin and rin_docs:
            groups.append((f"RIN {rin}", {rin}, {k for d in rin_docs for k in d.docket_ids}))
        if docket and docket_docs:
            groups.append((f"docket {docket}", {r for d in docket_docs for r in d.rins}, {docket}))
        for i, (a_label, a_rins, a_dockets) in enumerate(groups):
            for b_label, b_rins, b_dockets in groups[i + 1 :]:
                if (a_rins & b_rins) or (a_dockets & b_dockets):
                    continue
                if (a_rins or a_dockets) and (b_rins or b_dockets):
                    return f"{a_label} and {b_label} share no RIN or docket ID."
        return None

    def _dominant_rin(self, docs: list[FRDocument]) -> set[str]:
        counts = Counter(r for d in docs for r in d.rins)
        if not counts:
            return set()
        main = min(
            counts,
            key=lambda r: (
                -sum(
                    1
                    for d in docs
                    if r in d.rins and normalize_document_type(d.native_type, d.action) in PROPOSAL_TYPES | FINAL_TYPES
                ),
                -counts[r],
                r,
            ),
        )
        others = sorted(set(counts) - {main})
        if others:
            self.ambiguities.append(
                Ambiguity(
                    kind="multiple_rins_in_docket",
                    detail=f"Docket contains documents under RIN(s) {', '.join(others)}; lifecycle follows RIN {main}.",
                )
            )
        return {main}

    async def _expand(self, pool, anchor_rins, anchor_dockets, rin, docket):  # type: ignore[no-untyped-def]
        queries = [self.fr.by_rin(r) for r in sorted(anchor_rins - ({rin} if rin else set()))[:MAX_EXPANSION_QUERIES]]
        queries += [
            self.fr.by_docket(k)
            for k in sorted(anchor_dockets - ({docket} if docket else set()))[:MAX_EXPANSION_QUERIES]
            if not anchor_rins
        ]
        for found in await asyncio.gather(*(self._fr(q) for q in queries)):
            pool = pool + (found or [])
        known = {d.document_number for d in pool}
        linked = [
            n
            for d in pool
            for n in ([d.correction_of] if d.correction_of else []) + list(d.corrections)
            if n not in known
        ]
        linked = list(dict.fromkeys(linked))[:MAX_LINKED_FETCHES]
        if linked:
            pool = pool + [d for d in await asyncio.gather(*(self._fr(self.fr.document(n)) for n in linked)) if d]
        return pool

    async def _regulations_gov(self, docket_ids: list[str]) -> list[RegsDocketRecord]:
        if not docket_ids:
            return []
        out: list[RegsDocketRecord] = []
        for result in await asyncio.gather(
            *(self.regs.docket(k) for k in docket_ids[:MAX_REGS_DOCKETS]), return_exceptions=True
        ):
            if isinstance(result, SourceError):
                if result.code == ErrorCode.NOT_FOUND:
                    self.sources.ok("regs")
                    continue
                self.sources.fail("regs", result)
            elif isinstance(result, BaseException):
                self.sources.fail("regs", SourceError(ErrorCode.PARSER_FAILED, repr(result)))
            else:
                self.sources.ok("regs")
                out.append(result)
        return out

    async def _docket_only(self, query: QueryInfo, checked_at: str, docket: str) -> ResolutionResult:
        records = await self._regulations_gov([docket])
        if records:
            self.ambiguities.append(
                Ambiguity(
                    kind="no_federal_register_documents",
                    detail="The docket exists on Regulations.gov but no Federal Register document cites it.",
                )
            )
            return self._finish(query, checked_at, "insufficient_data", None, None, records, [])
        status: ResolutionStatus = "insufficient_data" if self.sources.failed("regs") else "not_found"
        return self._finish(query, checked_at, status, None, None, [], [])

    async def _natural_language(self, inp: ActorInput) -> FRDocument | ResolutionResult | None:
        docs = await self._fr(self.fr.search(inp.query or ""))
        if not docs:
            return None
        docs = [d for d in docs if _agency_matches(d, inp.agency)]
        q = _normalize_text(inp.query or "")
        groups: dict[str, list[FRDocument]] = {}
        for d in docs:
            groups.setdefault(_group_key(d), []).append(d)
        scored = sorted(
            ((max(_title_score(q, d.title) for d in g), k, g) for k, g in groups.items()),
            key=lambda x: (-x[0], x[1]),
        )
        if not scored:
            return None
        top, second = scored[0][0], scored[1][0] if len(scored) > 1 else 0.0
        if top >= NL_RESOLVE_SCORE and top - second >= NL_RESOLVE_MARGIN:
            best = max(scored[0][2], key=lambda d: (d.publication_date or date.min, d.document_number))
            return best
        candidates = []
        for _score, _key, g in scored[:MAX_CANDIDATES]:
            d = max(g, key=lambda d: (d.publication_date or date.min, d.document_number))
            candidates.append(
                Candidate(
                    title=d.title,
                    agency=d.agencies[0][0] if d.agencies else None,
                    rins=list(d.rins),
                    docket_ids=list(d.docket_ids),
                    federal_register_document_number=d.document_number,
                    publication_date=d.publication_date.isoformat() if d.publication_date else None,
                    document_type=d.native_type,
                    source_url=d.html_url,
                )
            )
        self.ambiguities.append(
            Ambiguity(
                kind="multiple_candidate_actions",
                detail=f"{len(scored)} candidate rulemakings matched; retry with a RIN, docket ID or document number.",
            )
        )
        query = QueryInfo(input_type="query", input=inp.query or "", agency=inp.agency)
        result = self._finish(query, "", "ambiguous", "low", None, [], ["natural_language_title"])
        result.candidates = candidates
        return result

    def _finish(self, query, checked_at, status, confidence, built, records, matched_by) -> ResolutionResult:  # type: ignore[no-untyped-def]
        action = lifecycle = regs_info = None
        related: list[RelatedDocument] = []
        if built:
            graph, lifecycle, records = built
            action, related = _action(graph), _related(graph)
        if records:
            regs_info = RegulationsGovInfo(
                dockets=[
                    Docket(
                        docket_id=r.docket.docket_id,
                        title=r.docket.title and " ".join(r.docket.title.split()),
                        agency=r.docket.agency,
                        rin=r.docket.rin,
                        docket_type=r.docket.docket_type,
                        source_url=r.docket.url,
                    )
                    for r in records
                ],
                document_count=sum(r.rule_document_count or 0 for r in records)
                if all(r.rule_document_count is not None for r in records)
                else None,
            )
        if status not in ("resolved", "partial"):
            confidence = None if status != "ambiguous" else "low"
        stage = lifecycle.current_stage if lifecycle else None
        return ResolutionResult(
            status=status,
            query=query,
            checked_at=checked_at,
            action=action,
            lifecycle=lifecycle,
            related_documents=related,
            regulations_gov=regs_info,
            resolution=Resolution(confidence=confidence, matched_by=matched_by, ambiguities=self.ambiguities),
            sources=Sources(federal_register=self.sources.get("fr"), regulations_gov=self.sources.get("regs")),
            billing=billing_decision(status, confidence, stage),
        )


def _action(graph: ActionGraph) -> Action:
    typed = [(d, normalize_document_type(d.native_type, d.action)) for d in graph.documents]
    anchor = next((d for d, t in reversed(typed) if t in FINAL_TYPES), None) or next(
        (d for d, t in reversed(typed) if t in PROPOSAL_TYPES), graph.documents[-1]
    )
    agencies = list(dict.fromkeys(a for d in graph.documents for a in d.agencies))
    return Action(
        canonical_title=anchor.title,
        agencies=[Agency(name=n, slug=s) for n, s in agencies],
        rins=graph.rins,
        docket_ids=graph.docket_ids,
        cfr_parts=list(dict.fromkeys(p for d in graph.documents for p in d.cfr_parts)),
    )


def _related(graph: ActionGraph) -> list[RelatedDocument]:
    return [
        RelatedDocument(
            normalized_type=normalize_document_type(d.native_type, d.action),
            native_type=d.native_type,
            native_action=d.action,
            title=d.title,
            document_number=d.document_number,
            publication_date=d.publication_date.isoformat() if d.publication_date else None,
            comments_close_on=d.comments_close_on.isoformat() if d.comments_close_on else None,
            effective_on=d.effective_on.isoformat() if d.effective_on else None,
            rins=list(d.rins),
            docket_ids=list(d.docket_ids),
            source_url=d.html_url,
            regulations_gov_document_id=graph.regs_ids.get(d.document_number),
            relation_basis=graph.basis[d.document_number],
        )
        for d in graph.documents
    ]


async def run_resolution(
    inp: ActorInput, *, client: httpx.AsyncClient | None = None, today: date | None = None
) -> Outcome:
    started = time.perf_counter()
    now = datetime.now(UTC)
    checked_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    own = client is None
    client = client or create_client()
    try:
        resolver = Resolver(
            FederalRegisterAdapter(client), RegulationsGovAdapter(client), today or now.astimezone(EASTERN).date()
        )
        result = await resolver.resolve(inp, checked_at)
        result.checked_at = checked_at
    finally:
        if own:
            await client.aclose()
    return Outcome(result=result, elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
