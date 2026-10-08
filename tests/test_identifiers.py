from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.identifiers import (
    document_number_from_fr_url,
    normalize_docket_id,
    normalize_fr_document_number,
    normalize_rin,
    regulations_gov_docket_ids,
)
from src.models.input import ActorInput


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2060-AV16", "2060-AV16"),
        (" 2060-av16 ", "2060-AV16"),
        ("2060 – AV16", "2060-AV16"),
        ("2060AV16", None),
        ("2024-00366", None),
    ],
)
def test_rin(raw: str, expected: str | None) -> None:
    assert normalize_rin(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2024-00366", "2024-00366"), ("2024-0036", "2024-0036"), ("24-00366", None), ("2060-AV16", None)],
)
def test_fr_document_number(raw: str, expected: str | None) -> None:
    assert normalize_fr_document_number(raw) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.federalregister.gov/documents/2024/03/08/2024-00366/standards-of-performance", "2024-00366"),
        ("https://federalregister.gov/documents/2024/03/08/2024-00366", "2024-00366"),
        ("https://www.federalregister.gov/d/2024-00366", "2024-00366"),
        ("https://example.com/documents/2024/03/08/2024-00366/x", None),
        ("https://www.federalregister.gov/agencies/environmental-protection-agency", None),
        ("not a url", None),
    ],
)
def test_fr_url(url: str, expected: str | None) -> None:
    assert document_number_from_fr_url(url) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("epa-hq-oar-2021-0317", "EPA-HQ-OAR-2021-0317"),
        ("DOE-HQ-2025-0014", "DOE-HQ-2025-0014"),
        ("FRL-8510-01-OAR", None),
        ("CMS-1847-CN", None),
        ("2060-AV16", None),
    ],
)
def test_docket(raw: str, expected: str | None) -> None:
    assert normalize_docket_id(raw) == expected


def test_fr_docket_ids_filtered_to_regulations_gov_style() -> None:
    raw = [
        "EPA-HQ-OAR-2021-0317",
        "FRL-8510-01-OAR",
        "Docket No. WHD-2025-0002",
        "Docket ID: OPM-2025-0274",
        "TD 10058",
    ]
    assert regulations_gov_docket_ids(raw) == ["EPA-HQ-OAR-2021-0317", "WHD-2025-0002", "OPM-2025-0274"]


def test_input_requires_an_identifier() -> None:
    with pytest.raises(ValidationError):
        ActorInput.model_validate({"agency": "Environmental Protection Agency"})


@pytest.mark.parametrize(
    "bad",
    [
        {"rin": "nope"},
        {"docketId": "x"},
        {"federalRegisterDocumentNumber": "abc"},
        {"federalRegisterUrl": "https://example.com/2024-00366"},
        {"unknown": 1},
    ],
)
def test_input_rejects_malformed(bad: dict) -> None:
    with pytest.raises(ValidationError):
        ActorInput.model_validate(bad)


def test_input_priority_and_url_dedupe() -> None:
    inp = ActorInput.model_validate(
        {
            "rin": "2060-av16",
            "federalRegisterUrl": "https://www.federalregister.gov/documents/2024/03/08/2024-00366/x",
            "federalRegisterDocumentNumber": "2024-00366",
        }
    )
    assert inp.input_type == "federal_register_document_number"
    assert inp.document_numbers == ["2024-00366"]
    assert inp.normalized_rin == "2060-AV16"
