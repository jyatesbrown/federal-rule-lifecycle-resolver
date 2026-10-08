from __future__ import annotations

import pytest

from src.billing import EVENT_NAME, billing_decision


@pytest.mark.parametrize(
    ("status", "confidence", "stage", "billable"),
    [
        ("resolved", "high", "effective", True),
        ("resolved", "medium", "proposed_comment_open", True),
        ("partial", "high", "effective", True),
        ("partial", "medium", "effective", False),
        ("resolved", "high", "unknown", False),
        ("ambiguous", "low", None, False),
        ("not_found", None, None, False),
        ("conflicting_identifiers", None, None, False),
        ("insufficient_data", None, None, False),
        ("insufficient_data", "high", "unknown", False),
    ],
)
def test_billing_eligibility(status, confidence, stage, billable) -> None:
    decision = billing_decision(status, confidence, stage)
    assert decision.billable is billable
    assert decision.event_name == (EVENT_NAME if billable else None)
