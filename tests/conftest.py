from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.sources.federal_register import FRDocument, parse_document

FIXTURES = Path(__file__).parent / "fixtures"
# Fixtures were captured on 2026-10-08; stage expectations are evaluated "as of" this date, not the real clock.
AS_OF = date(2026, 10, 8)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def fr_docs(name: str) -> list[FRDocument]:
    return [parse_document(r) for r in load(f"fr/{name}.json")["results"]]


@pytest.fixture
def as_of() -> date:
    return AS_OF
