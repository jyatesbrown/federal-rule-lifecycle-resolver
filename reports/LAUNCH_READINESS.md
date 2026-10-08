# Federal Rule Lifecycle Resolver: launch-readiness report (build 1.0.2, private)

**Recommendation: READY FOR PRIVATE PLATFORM TEST**

## 1. Repository and deployment
- Repo: https://github.com/jyatesbrown/federal-rule-lifecycle-resolver (`main` at `67a1f4d` plus this branch).
- CI: GitHub Actions `test` job (ruff + pytest, offline) passed on PR #1.
- Apify Actor (private): https://console.apify.com/actors/k5WrcveaXwWLmTnl2, build 1.0.2 tagged `latest`.
- Run settings read back from the API: 256 MB, 120 s timeout, `LIMITED_PERMISSIONS`, Standby off, `isPublic: false`.
- `REGULATIONS_GOV_API_KEY` is a secret environment variable on version 1.0.

## 2. Architecture
- `src/identifiers.py` parses and normalizes the input. Precedence is FR document number > FR URL > RIN > docket ID > query.
- `src/sources/federal_register.py` covers exact document, RIN, docket and full-text search. Its failures are typed (`timeout`, `rate_limited`, `unavailable`, `parser_failed`).
- `src/sources/regulations_gov.py` covers docket metadata and the docket's Rule/Proposed Rule documents only. Comments are never downloaded. Failures map to `authentication_failed`, `rate_limited`, `timeout` and the other typed states.
- `src/relationships.py` links documents on strong evidence: same RIN, same docket, an explicit correction reference, or a Regulations.gov listing. Agency and CFR overlap only support a link. Title similarity alone never links documents. A document that shares a docket but has an incompatible RIN is excluded. Every related document carries `relationBasis`.
- `src/lifecycle/` normalizes document types and reconstructs dates. DATES text takes precedence over FR metadata, and earlier dates are kept as `original*` fields. The engine then picks the stage.
- `src/billing.py` is a pure billing decision. `src/main.py` pushes one record per run, charges at most once and guards against repeat charges on restart.

## 3. Official endpoints, authentication and rate limits
| Source | Endpoints | Auth | Rate limit observed |
|---|---|---|---|
| Federal Register | `GET /api/v1/documents/{number}.json`, `GET /api/v1/documents.json?conditions[...]` | none | no rate-limit headers returned |
| Regulations.gov v4 | `GET /v4/dockets/{id}`, `GET /v4/documents?filter[docketId]=...` | `X-Api-Key` (api.data.gov) | `x-ratelimit-limit: 1000` per hour for this key; `DEMO_KEY` is much lower |

A lookup by identifier makes 1–3 Regulations.gov calls, so the key supports several hundred lookups per hour.

## 4. Tests
- 82 offline tests (pytest + respx) use 11 saved official API fixtures (9 Federal Register, 2 Regulations.gov). CI never calls a live API.
- `scripts/live_smoke_test.py` was run locally against both live APIs with the real key. All 8 cases behaved as intended. Local latency was 0.45–0.94 s for identifier lookups, 0.70 s for a title and 0.47 s for a broad query.

## 5. Platform acceptance (build 1.0.2, 10 runs, one record each)
| Case | Input | Status | Stage | Related docs | Charged `rule-resolution` |
|---|---|---|---|---|---|
| A exact document | `2024-00366` | resolved/high | effective | 4 | 1 |
| B RIN | `2060-AV16` | resolved/high | effective | 4 | 1 |
| C docket | `EPA-HQ-OAR-2021-0317` | resolved/high | effective | 4 | 1 |
| D natural language | "Energy Conservation Program: Test Procedure for Fans and Blowers" | resolved/medium | effective | 3 | 1 |
| E ambiguous | "EPA methane rule" | ambiguous/low, 5 candidates | – | 0 | 0 |
| F comment extension | proposal URL `2021-24202` | resolved/high, `extended: true` | effective | 4 | 1 |
| G effective-date delay | `1903-AA23` | resolved/high, `delayed: true` | final_rule_not_yet_effective | 7 | 1 |
| H source outage | – | covered offline only (see below) | | | |
| I conflicting identifiers | `2060-AV16` + `2026-20523` | conflicting_identifiers | – | 0 | 0 |
| extra: withdrawal | `1235-AA52` | resolved/high | withdrawn | 2 | 1 |
| extra: comment open | `2060-AW66` | resolved/high, `open: true` until 2026-11-12 | proposed_comment_open | 1 | 1 |

Every run charged `apify-actor-start` exactly once. Right after each run finished, `chargedEventCounts` showed `rule-resolution: 0`; re-reading it later showed the values above. This is the same reporting delay seen with Actor #1.

**H (source outage)** isn't reproducible on the platform without breaking a live source. The offline acceptance tests cover it: when Regulations.gov fails with `authentication_failed`, `rate_limited` or `timeout`, the result is `partial`, and it is billed only at high confidence with a complete Federal Register lifecycle. A Federal Register outage gives `insufficient_data` and is not charged. An earlier local run without a key confirmed the `partial` + `authentication_failed` path against the live API.

### Abbreviated real lifecycle (case A, RIN 2060-AV16)
- Proposed 2021-11-15 (`2021-24202`). Comment deadline 2022-01-14, extended by `2021-27312`.
- Supplemental proposal `2022-24675`. The current comment deadline became 2023-02-13.
- Final rule `2024-00366`, published 2024-03-08, effective 2024-05-07, not delayed.
- Stage `effective`. The latest official action is `2024-00366`.
- Docket `EPA-HQ-OAR-2021-0317` lists 5 rule documents. Document `2024-13206`, in the same docket but under RIN 2060-AW18, is excluded.

## 6. Latency, cost and economics
- **Platform run time:** median 3.96 s, max 4.23 s, including container start. Resolution itself took about 0.9 s by the run log.
- **Platform usage:** about $0.00022 per run, for 10 runs totalling $0.0020.
- **Per paid resolution:** 0.8 × $0.05 − $0.00022 ≈ $0.0398 net, about 0.4% compute overhead.
- **Unpaid runs** (ambiguous, conflicting) cost about $0.00022 each, of which the start event recovers $0.00004.
- **Monthly:** about $398, $3,978 and $39,780 net at 10k, 100k and 1M paid resolutions, assuming unpaid runs are a small share.

## 7. Pricing configuration (read back from the platform)
- `pricingModel: PAY_PER_EVENT`.
- `apify-actor-start`: $0.00005, `isOneTimeEvent: true`. Apify replaced our event description with its default text.
- `rule-resolution`: $0.05, `isPrimaryEvent: true`.
- No `apify-default-dataset-item` event.
- The "Pay per event + usage" toggle is not visible through the API and was never set.

## 8. How ambiguous queries behave
A natural-language query resolves only when one candidate clearly dominates on title, agency, RIN/docket and document type. Otherwise the result is `ambiguous` with up to 5 Federal Register candidates, each with its document number, URL, RINs and dockets, and it is not charged. The candidates are Federal Register's own full-text matches. For "EPA methane rule" they include unrelated EPA rules (e.g. "Air Plan Approval; OR; Lane County"), which is acceptable because the record makes no claim about them.

## 9. Known limitations
- The Actor covers the administrative record only. It has no information on litigation, injunctions, stays, court vacatur, appropriations riders or enforcement policy.
- Docket relationships can be incomplete when a document carries neither the RIN nor the docket ID.
- Regulations.gov `commentCount` is `null` because no reliable direct count is retrieved, and the docket's own `rin` field is often empty.
- Natural-language search depends on Federal Register full-text ranking. It is conservative and returns `ambiguous` instead of guessing.
- Dates come from DATES text when it is present. Unusual wording falls back to FR metadata, which can be stale.

## 10. Competitive positioning
Store competitors are per-item search, scrape or monitor tools: `s-r/federalregister-scraper`, `challenge_logic/federal-register-deadline-monitor`, `automation-lab/regulations-gov-rulemaking-dockets-comments`, `skootle/federal-rulemaking-monitor` and Federal Register MCP servers. They charge $0.001–0.004 per item. `nexgenwatch/federal-rule-impact-mcp` charges $0.05 per call but searches by industry impact. None of them returns one reconciled lifecycle with relationship evidence, reconstructed extension and delay dates, and charging only on a useful result.

## 11. Benchmark status
`benchmarks/prompts.json` is fixed: 22 relevant prompts and 17 controls. It hasn't been run. Both arms (default MCP tools, and directed Store search) depend on Store discovery, which requires publication, and publication needs approval. The `BENCHMARK_LLM_API_KEY` used for Actor #1 is present in this session.

## 12. Decisions not covered by the specification
1. The Actor title was shortened to "Federal Rule Lifecycle Resolver" because `apify push` rejected the longer title.
2. Pricing was set through `PUT /v2/acts/{id}` before any public listing. Pricing took effect immediately for private runs.
3. The H case was validated offline only.
