"""Deterministic lifecycle reconstruction from the documents of one regulatory action."""

from __future__ import annotations

from datetime import date

from ..models.output import CommentPeriod, EffectiveDate, Lifecycle, Stage
from ..sources.federal_register import FRDocument
from .dates import comment_deadline_from_text, delayed_effective_date_from_text
from .doctypes import FINAL_TYPES, PROPOSAL_TYPES, normalize_document_type

COMMENT_TYPES = PROPOSAL_TYPES | {"comment_period_extension", "comment_period_reopening"}


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _key(doc: FRDocument) -> tuple[date, str]:
    return (doc.publication_date or date.min, doc.document_number)


def comment_deadline(doc: FRDocument, regs_comment_end: dict[str, date] | None = None) -> date | None:
    """Prefer the deadline stated in the document's DATES text; fall back to FR metadata, then Regulations.gov."""
    return (
        comment_deadline_from_text(doc.dates_text)
        or doc.comments_close_on
        or (regs_comment_end or {}).get(doc.document_number)
    )


def delayed_effective_date(doc: FRDocument) -> date | None:
    stated = delayed_effective_date_from_text(doc.dates_text)
    if stated:
        return stated
    if doc.effective_on and doc.publication_date and doc.effective_on > doc.publication_date:
        return doc.effective_on
    return None


def reconstruct_lifecycle(
    documents: list[FRDocument], today: date, regs_comment_end: dict[str, date] | None = None
) -> Lifecycle:
    docs = sorted(documents, key=_key)
    typed = [(d, normalize_document_type(d.native_type, d.action)) for d in docs]
    basis: list[str] = []

    proposals = [d for d, t in typed if t in PROPOSAL_TYPES]
    finals = [d for d, t in typed if t in FINAL_TYPES]
    final = finals[-1] if finals else None
    final_cutoff = final.publication_date if final and final.publication_date else date.max
    advance = [d for d, t in typed if t == "advance_notice"]

    comment_docs = [(d, t) for d, t in typed if t in COMMENT_TYPES and (d.publication_date or date.min) <= final_cutoff]
    deadlines = [(d, t, comment_deadline(d, regs_comment_end)) for d, t in comment_docs]
    deadlines = [(d, t, dl) for d, t, dl in deadlines if dl]
    original_deadline = deadlines[0][2] if deadlines else None
    current_doc, current_type, current_deadline = deadlines[-1] if deadlines else (None, None, None)
    extended = None
    if original_deadline and current_deadline:
        extended = current_deadline != original_deadline
        if extended:
            basis.append(
                f"Comment deadline moved from {_iso(original_deadline)} to {_iso(current_deadline)} by "
                f"{current_type.replace('_', ' ')} document {current_doc.document_number}."  # type: ignore[union-attr]
            )

    original_eff = final.effective_on if final else None
    current_eff = original_eff
    delay_doc = None
    if final:
        for d, t in typed:
            if t == "effective_date_delay" and _key(d) > _key(final):
                new = delayed_effective_date(d)
                if new:
                    current_eff, delay_doc = new, d
    delayed = None if original_eff is None else current_eff != original_eff
    if delayed and delay_doc:
        basis.append(
            f"Effective date delayed from {_iso(original_eff)} to {_iso(current_eff)} by document "
            f"{delay_doc.document_number}."
        )

    last_substantive = max([*proposals, *finals], key=_key, default=None)
    withdrawals = [
        d for d, t in typed if t == "withdrawal" and (last_substantive is None or _key(d) > _key(last_substantive))
    ]
    withdrawal = withdrawals[-1] if withdrawals else None

    stage: Stage
    if withdrawal:
        stage = "withdrawn"
        target = "final rule" if final else "proposal"
        basis.insert(
            0,
            f"Withdrawal document {withdrawal.document_number} published {_iso(withdrawal.publication_date)} "
            f"after the latest {target}.",
        )
    elif final:
        kind = normalize_document_type(final.native_type, final.action).replace("_", " ")
        basis.insert(
            0,
            f"{kind.capitalize()} published in Federal Register document {final.document_number} "
            f"on {_iso(final.publication_date)}.",
        )
        if current_eff is None:
            stage = "unknown"
            basis.append("The final rule has no effective date in the official metadata.")
        elif current_eff <= today:
            stage = "effective"
            basis.append(f"Current effective date {_iso(current_eff)} has passed.")
        else:
            stage = "final_rule_not_yet_effective"
            basis.append(f"Current effective date {_iso(current_eff)} is in the future.")
    elif proposals:
        latest = proposals[-1]
        basis.insert(
            0,
            f"Proposal published in Federal Register document {latest.document_number} on "
            f"{_iso(latest.publication_date)}; no final rule identified.",
        )
        if current_deadline is None:
            stage = "unknown"
            basis.append("No comment deadline is stated in the official records.")
        elif current_deadline >= today:
            stage = "proposed_comment_open"
            basis.append(f"Current comment deadline {_iso(current_deadline)} has not passed.")
        else:
            stage = "proposed_comment_closed"
            basis.append(f"Current comment deadline {_iso(current_deadline)} has passed.")
    elif advance:
        stage = "pre_rule"
        basis.insert(
            0, f"Only an advance notice of proposed rulemaking was identified ({advance[-1].document_number})."
        )
    else:
        stage = "unknown"
        basis.insert(0, "No proposed or final rule document was identified for this action.")

    comment_open = None
    if current_deadline is not None:
        comment_open = current_deadline >= today and final is None and withdrawal is None
    latest_doc = docs[-1] if docs else None
    return Lifecycle(
        current_stage=stage,
        stage_basis=basis,
        proposed_rule_published=_iso(proposals[0].publication_date) if proposals else None,
        comment_period=CommentPeriod(
            original_deadline=_iso(original_deadline),
            current_deadline=_iso(current_deadline),
            extended=extended,
            open=comment_open,
        ),
        final_rule_published=_iso(final.publication_date) if final else None,
        effective_date=EffectiveDate(original=_iso(original_eff), current=_iso(current_eff), delayed=delayed),
        withdrawn_on=_iso(withdrawal.publication_date) if withdrawal else None,
        latest_official_action_date=_iso(latest_doc.publication_date) if latest_doc else None,
        latest_official_action=latest_doc.document_number if latest_doc else None,
    )
