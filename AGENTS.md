# AGENTS.md

Apify Actor (Python 3.12) that resolves one U.S. federal rulemaking across the Federal Register API and the
Regulations.gov API v4 and emits exactly one lifecycle record. Deterministic only: no LLMs, no browsers,
no third-party data sources.

## Layout
- `src/main.py` entry point (input, push one record, charge `rule-resolution` at most once).
- `src/resolver.py` orchestration: identifiers -> official records -> relationship graph -> lifecycle.
- `src/sources/` `federal_register.py` (no key), `regulations_gov.py` (`REGULATIONS_GOV_API_KEY`, X-Api-Key header).
- `src/lifecycle/` `doctypes.py` (type/action -> normalized type), `dates.py` (DATES text parsing),
  `relationships.py` (strong vs supporting evidence), `stage.py` (stage + date reconstruction).
- `src/billing.py` pure billing eligibility.

## Commands
```bash
source .venv/bin/activate
ruff check . && ruff format --check .
pytest                                   # fixtures + respx only, no network
python scripts/export_dataset_schema.py  # regenerate .actor/dataset_schema.json
apify validate-schema
python scripts/live_smoke_test.py        # live official APIs (manual only)
```

## Rules
- Tests never hit the network. Fixtures in `tests/fixtures/` are saved official API JSON (captured 2026-10-08);
  evaluate stages with an explicit `today`, never the real clock.
- Never classify withdrawal/delay/correction from titles; use official `type`/`action` text.
- Never link documents on title similarity alone; every related document needs `relationBasis`.
- Source failure must surface in `sources.*.status`, never as empty lifecycle fields.
- Never commit API keys.
