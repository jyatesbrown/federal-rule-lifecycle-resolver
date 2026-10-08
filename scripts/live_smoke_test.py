"""Manual live check against the official APIs (never run in CI).

Usage: REGULATIONS_GOV_API_KEY=... python scripts/live_smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.input import ActorInput
from src.resolver import run_resolution

CASES = [
    ("exact FR document number", {"federalRegisterDocumentNumber": "2024-00366"}),
    ("RIN", {"rin": "2060-AV16"}),
    ("docket ID", {"docketId": "EPA-HQ-OAR-2021-0317"}),
    ("natural-language title", {"query": "Energy Conservation Program: Test Procedure for Fans and Blowers"}),
    ("broad natural language", {"query": "EPA methane rule"}),
    ("effective-date delay", {"rin": "1903-AA23"}),
    ("withdrawal", {"rin": "1235-AA52"}),
    ("conflicting identifiers", {"rin": "1235-AA52", "federalRegisterDocumentNumber": "2024-00366"}),
]


async def main() -> None:
    print(f"{'case':28} {'status':24} {'conf':6} {'stage':30} docs  FR       Regs                  ms")
    for label, payload in CASES:
        outcome = await run_resolution(ActorInput.model_validate(payload))
        r = outcome.result
        print(
            f"{label:28} {r.status:24} {r.resolution.confidence or '-':6} "
            f"{(r.lifecycle.current_stage if r.lifecycle else '-'):30} {len(r.related_documents):4}  "
            f"{r.sources.federal_register.status:8} {r.sources.regulations_gov.status:21} {outcome.elapsed_ms:6.0f}"
            f"  matched={r.resolution.matched_by} candidates={len(r.candidates)} billable={r.billing.billable}"
        )


if __name__ == "__main__":
    asyncio.run(main())
