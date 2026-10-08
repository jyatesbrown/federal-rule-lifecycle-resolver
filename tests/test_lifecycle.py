"""Fixture tests (saved official Federal Register API JSON, captured 2026-10-08). Stages are evaluated as of AS_OF."""

from __future__ import annotations

from datetime import date

import pytest

from src.lifecycle.dates import comment_deadline_from_text, delayed_effective_date_from_text, eastern_date
from src.lifecycle.doctypes import normalize_document_type
from src.lifecycle.relationships import RegsLink, build_action_graph
from src.lifecycle.stage import reconstruct_lifecycle
from tests.conftest import AS_OF, fr_docs


def lifecycle_for(name: str, rin: str, today: date = AS_OF):
    docs = fr_docs(name)
    graph = build_action_graph(docs, anchor_rins={rin}, anchor_dockets={k for d in docs for k in d.docket_ids})
    return graph, reconstruct_lifecycle(graph.documents, today)


@pytest.mark.parametrize(
    ("native_type", "action", "expected"),
    [
        ("Proposed Rule", "Proposed rule.", "proposed_rule"),
        ("Proposed Rule", "Proposed rulemaking; extension of public comment period", "comment_period_extension"),
        ("Proposed Rule", "Proposed rule; reopening of comment period.", "comment_period_reopening"),
        ("Proposed Rule", "Supplemental notice of proposed rulemaking.", "supplemental_proposed_rule"),
        ("Proposed Rule", "Advance notice of proposed rulemaking.", "advance_notice"),
        ("Proposed Rule", "Withdrawal of proposed rule.", "withdrawal"),
        ("Proposed Rule", "Proposed rule; withdrawal.", "withdrawal"),
        ("Proposed Rule", "Proposed rule and partial withdrawal of proposed rule.", "proposed_rule"),
        ("Rule", "Final rule.", "final_rule"),
        ("Rule", "Final regulations.", "final_rule"),
        ("Rule", "Final rule; correction.", "correction"),
        ("Rule", "Direct final rule (DFR); request for comments.", "direct_final_rule"),
        ("Rule", "Direct final rule; further delay of effective date.", "effective_date_delay"),
        ("Rule", "Final rule; delay of effective date.", "effective_date_delay"),
        ("Rule", "Interim final rule; correction; request for comments.", "interim_final_rule"),
        ("Rule", "Technical amendments.", "amendment"),
        ("Notice", "Notice of availability.", "notice"),
    ],
)
def test_document_type_normalization(native_type: str, action: str, expected: str) -> None:
    assert normalize_document_type(native_type, action) == expected


def test_title_words_never_drive_classification() -> None:
    assert normalize_document_type("Rule", "Final rule.") == "final_rule"
    assert normalize_document_type("Proposed Rule", None) == "proposed_rule"


def test_date_text_parsing() -> None:
    ext = "The public comment period ... (86 FR 63110) is extended from January 14, 2022 to January 31, 2022."
    assert comment_deadline_from_text(ext) == date(2022, 1, 31)
    assert comment_deadline_from_text("Comments must be received on or before February 13, 2023.") == date(2023, 2, 13)
    assert comment_deadline_from_text("This final rule is effective on May 7, 2024.") is None
    chain = (
        "... delayed until September 12, 2025 (90 FR 31137), further delayed until December 9, 2025 "
        "(90 FR 43539) is further delayed until December 24, 2026."
    )
    assert delayed_effective_date_from_text(chain) == date(2026, 12, 24)
    assert eastern_date("2023-02-14T04:59:59Z") == date(2023, 2, 13)


def test_effective_final_rule_with_extension_and_supplemental_proposal() -> None:
    graph, lc = lifecycle_for("rin-2060-AV16", "2060-AV16")
    assert [d.document_number for d in graph.documents] == ["2021-24202", "2021-27312", "2022-24675", "2024-00366"]
    assert lc.current_stage == "effective"
    assert lc.proposed_rule_published == "2021-11-15"
    assert lc.comment_period.original_deadline == "2022-01-14"
    assert lc.comment_period.current_deadline == "2023-02-13"  # SNPRM DATES text, not stale comments_close_on
    assert lc.comment_period.extended is True
    assert lc.comment_period.open is False
    assert lc.final_rule_published == "2024-03-08"
    assert lc.effective_date.model_dump() == {"original": "2024-05-07", "current": "2024-05-07", "delayed": False}
    assert any("2024-00366" in b for b in lc.stage_basis)


def test_comment_period_extension_alone() -> None:
    docs = [d for d in fr_docs("rin-2060-AV16") if d.document_number in ("2021-24202", "2021-27312")]
    lc = reconstruct_lifecycle(docs, date(2022, 1, 20))
    assert lc.current_stage == "proposed_comment_open"
    assert lc.comment_period.model_dump() == {
        "original_deadline": "2022-01-14",
        "current_deadline": "2022-01-31",
        "extended": True,
        "open": True,
    }
    assert reconstruct_lifecycle(docs, AS_OF).current_stage == "proposed_comment_closed"


def test_effective_date_delay_chain_ignores_bad_effective_on_metadata() -> None:
    _, lc = lifecycle_for("rin-1903-AA23", "1903-AA23")
    assert lc.effective_date.model_dump() == {"original": "2025-07-15", "current": "2026-12-24", "delayed": True}
    assert lc.current_stage == "final_rule_not_yet_effective"
    assert lc.latest_official_action == "2026-17381"
    assert lc.comment_period.current_deadline is None  # DFR adverse-comment window is not a proposal deadline


def test_withdrawn_proposal() -> None:
    _, lc = lifecycle_for("rin-1235-AA52", "1235-AA52")
    assert lc.current_stage == "withdrawn"
    assert lc.withdrawn_on == "2026-10-07"
    assert lc.comment_period.open is False


def test_proposal_with_open_comments() -> None:
    _, lc = lifecycle_for("rin-2060-AW66", "2060-AW66")
    assert lc.current_stage == "proposed_comment_open"
    assert lc.comment_period.open is True
    assert lc.comment_period.current_deadline == "2026-11-12"


def test_proposal_with_closed_comments() -> None:
    _, lc = lifecycle_for("rin-2060-AW66", "2060-AW66", today=date(2026, 11, 13))
    assert lc.current_stage == "proposed_comment_closed"


def test_final_rule_not_yet_effective() -> None:
    _, lc = lifecycle_for("rin-1904-AG06", "1904-AG06")
    assert lc.current_stage == "final_rule_not_yet_effective"
    assert lc.effective_date.current == "2026-12-07"
    assert (
        reconstruct_lifecycle(lifecycle_for("rin-1904-AG06", "1904-AG06")[0].documents, date(2026, 12, 7)).current_stage
        == "effective"
    )


def test_correction_does_not_become_the_stage() -> None:
    graph, lc = lifecycle_for("rin-0938-AV77", "0938-AV77")
    assert [d.document_number for d in graph.documents] == ["2026-06675", "2026-15588", "2026-20134"]
    assert lc.current_stage == "effective"
    assert lc.final_rule_published == "2026-07-31"
    assert lc.latest_official_action == "2026-20134"


def test_docket_with_other_rin_is_excluded_not_merged() -> None:
    docs = fr_docs("docket-EPA-HQ-OAR-2021-0317")
    graph = build_action_graph(docs, anchor_rins={"2060-AV16"}, anchor_dockets={"EPA-HQ-OAR-2021-0317"})
    assert "2024-13206" not in [d.document_number for d in graph.documents]
    assert [d.document_number for d, _ in graph.excluded] == ["2024-13206"]


def test_relation_basis_and_duplicate_removal() -> None:
    docs = fr_docs("rin-2060-AV16")
    links = {"2024-00366": RegsLink("EPA-HQ-OAR-2021-0317-3853", "EPA-HQ-OAR-2021-0317")}
    graph = build_action_graph(
        docs + docs, anchor_rins={"2060-AV16"}, anchor_dockets={"EPA-HQ-OAR-2021-0317"}, regs_links=links
    )
    assert len(graph.documents) == 4
    basis = graph.basis["2024-00366"]
    assert basis[:2] == ["matching RIN 2060-AV16", "matching docket ID EPA-HQ-OAR-2021-0317"]
    assert "listed in Regulations.gov docket EPA-HQ-OAR-2021-0317 as EPA-HQ-OAR-2021-0317-3853" in basis
    assert "same agency (supporting)" in basis
    assert graph.regs_ids == {"2024-00366": "EPA-HQ-OAR-2021-0317-3853"}


def test_similar_title_alone_never_links() -> None:
    docs = fr_docs("search-methane")
    graph = build_action_graph(docs, anchor_rins={"2060-AV16"}, anchor_dockets=set())
    assert all("2060-AV16" in d.rins for d in graph.documents)
