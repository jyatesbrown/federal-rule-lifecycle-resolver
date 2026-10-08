"""Deterministic relationship graph: which official documents belong to one regulatory action, and why."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..sources.federal_register import FRDocument


@dataclass(frozen=True)
class RegsLink:
    regs_document_id: str
    docket_id: str


@dataclass
class ActionGraph:
    documents: list[FRDocument] = field(default_factory=list)
    basis: dict[str, list[str]] = field(default_factory=dict)
    regs_ids: dict[str, str] = field(default_factory=dict)
    excluded: list[tuple[FRDocument, str]] = field(default_factory=list)

    @property
    def rins(self) -> list[str]:
        return list(dict.fromkeys(r for d in self.documents for r in d.rins))

    @property
    def docket_ids(self) -> list[str]:
        return list(dict.fromkeys(k for d in self.documents for k in d.docket_ids))


def _dedupe(docs: Iterable[FRDocument]) -> list[FRDocument]:
    seen: dict[str, FRDocument] = {}
    for doc in docs:
        seen.setdefault(doc.document_number, doc)
    return list(seen.values())


def build_action_graph(
    documents: Iterable[FRDocument],
    *,
    anchor_rins: set[str],
    anchor_dockets: set[str],
    requested_numbers: set[str] = frozenset(),  # type: ignore[assignment]
    regs_links: dict[str, RegsLink] | None = None,
) -> ActionGraph:
    """Include a document only on strong evidence; supporting evidence is recorded but never sufficient alone.

    Strong: requested document number, matching RIN, matching docket ID (unless the document carries a different RIN),
    an official correction link to an included document, or a Regulations.gov docket listing of its FR number.
    """
    regs_links = regs_links or {}
    graph = ActionGraph()
    pending = _dedupe(documents)
    included: dict[str, list[str]] = {}

    for doc in pending:
        basis: list[str] = []
        if doc.document_number in requested_numbers:
            basis.append("requested Federal Register document number")
        shared_rins = sorted(set(doc.rins) & anchor_rins)
        basis += [f"matching RIN {r}" for r in shared_rins]
        shared_dockets = sorted(set(doc.docket_ids) & anchor_dockets)
        conflicting_rin = bool(doc.rins) and bool(anchor_rins) and not shared_rins
        link = regs_links.get(doc.document_number)
        if conflicting_rin and doc.document_number not in requested_numbers:
            if shared_dockets or link:
                where = shared_dockets[0] if shared_dockets else link.docket_id  # type: ignore[union-attr]
                graph.excluded.append(
                    (doc, f"shares docket {where} but carries different RIN(s) {', '.join(doc.rins)}")
                )
            continue
        basis += [f"matching docket ID {k}" for k in shared_dockets]
        if link:
            basis.append(f"listed in Regulations.gov docket {link.docket_id} as {link.regs_document_id}")
        if basis:
            included[doc.document_number] = basis

    changed = True
    while changed:
        changed = False
        for doc in pending:
            if doc.document_number in included:
                continue
            targets = [n for n in ([doc.correction_of] if doc.correction_of else []) if n in included]
            targets += [
                d.document_number
                for d in pending
                if d.document_number in included
                and doc.document_number in d.corrections
                and d.document_number not in targets
            ]
            if targets:
                included[doc.document_number] = [f"official correction of document {n}" for n in targets]
                changed = True

    members = [d for d in pending if d.document_number in included]
    agencies = {slug or name for d in members for name, slug in d.agencies}
    cfr = {p for d in members for p in d.cfr_parts}
    for doc in members:
        basis = included[doc.document_number]
        others_agencies = {slug or name for d in members if d is not doc for name, slug in d.agencies}
        others_cfr = {p for d in members if d is not doc for p in d.cfr_parts}
        if len(members) > 1 and {slug or name for name, slug in doc.agencies} & (others_agencies or agencies):
            basis.append("same agency (supporting)")
        overlap = sorted(set(doc.cfr_parts) & (others_cfr if len(members) > 1 else cfr))
        if len(members) > 1 and overlap:
            basis.append(f"overlapping CFR part {', '.join(overlap)} (supporting)")
        graph.basis[doc.document_number] = basis
        if doc.document_number in regs_links:
            graph.regs_ids[doc.document_number] = regs_links[doc.document_number].regs_document_id

    graph.documents = sorted(members, key=lambda d: (d.publication_date or "", d.document_number))  # type: ignore[operator]
    return graph
