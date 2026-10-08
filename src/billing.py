"""Pure billing eligibility for the `rule-resolution` pay-per-event charge."""

from __future__ import annotations

from .models.output import Billing, Confidence, ResolutionStatus, Stage

EVENT_NAME = "rule-resolution"


def billing_decision(status: ResolutionStatus, confidence: Confidence | None, stage: Stage | None) -> Billing:
    """Charge once only when a usable lifecycle was delivered.

    - resolved with high/medium confidence and a known stage -> charge
    - partial (one official system failed) -> charge only with high confidence and a known stage
    - ambiguous, not_found, conflicting_identifiers, insufficient_data -> never charge
    """
    if stage in (None, "unknown"):
        return Billing(billable=False, event_name=None, reason=f"No usable lifecycle delivered (status {status}).")
    if status == "resolved" and confidence in ("high", "medium"):
        return Billing(billable=True, event_name=EVENT_NAME, reason="Rulemaking resolved with a usable lifecycle.")
    if status == "partial" and confidence == "high":
        return Billing(
            billable=True,
            event_name=EVENT_NAME,
            reason="Resolved from authoritative identifiers; one official system was unavailable but the lifecycle "
            "is complete from the other.",
        )
    return Billing(billable=False, event_name=None, reason=f"Not charged: status {status}, confidence {confidence}.")
