# Federal Rule Lifecycle Resolver

Resolve one U.S. federal rulemaking and get its **administrative lifecycle** as one structured record, built from the
official **Federal Register API** and the **Regulations.gov API**.

Give it a RIN, a Regulations.gov docket ID, a Federal Register document number or URL, or a rule name. It returns:

- the **current stage**: `pre_rule`, `proposed_comment_open`, `proposed_comment_closed`,
  `final_rule_not_yet_effective`, `effective`, `withdrawn` or `unknown`, with a plain-language `stageBasis`;
- **comment period**: original and current deadline, whether it was extended or reopened, and whether it is open today;
- **final rule** publication date, **original and current effective date**, and whether the effective date was delayed;
- every **related Federal Register document** (proposal, supplemental proposal, extensions, final rule, corrections,
  delays, withdrawals) with its official URL and a `relationBasis` saying *why* it belongs to this rulemaking;
- the RINs, docket IDs, agencies and CFR parts of the action, plus Regulations.gov docket metadata;
- per-source status, so an outage is never reported as "no such rule".

It is a deterministic data utility: no AI model, no browser, no third-party data.

## Scope: administrative record only

The record reflects what the Federal Register and Regulations.gov publish. It **does not** reflect court decisions,
injunctions, stays, Congressional Review Act disapprovals, appropriations riders, enforcement policy or state law,
unless a Federal Register document itself announces the change (for example a delay or withdrawal notice). It is not
legal advice. Every record carries this note in `scopeNote`.

## Input

Provide at least one field. Several identifiers are cross-checked against each other.

| Field | Example | Notes |
|---|---|---|
| `federalRegisterDocumentNumber` | `2024-00366` | Highest confidence. |
| `federalRegisterUrl` | `https://www.federalregister.gov/documents/2024/03/08/2024-00366/...` | Document number is read from the URL. |
| `rin` | `2060-AV16` | Regulation Identifier Number. |
| `docketId` | `EPA-HQ-OAR-2021-0317` | Regulations.gov docket. |
| `query` | `Oil and Natural Gas Sector Climate Review` | Rule name. Resolved only when one rulemaking clearly dominates. |
| `agency` | `Environmental Protection Agency` | Optional; narrows a `query`. |

## Output (abridged)

```json
{
  "schemaVersion": "1.0",
  "status": "resolved",
  "query": {"inputType": "rin", "input": "2060-AV16"},
  "action": {
    "canonicalTitle": "Standards of Performance for New, Reconstructed, and Modified Sources and Emissions Guidelines for Existing Sources: Oil and Natural Gas Sector Climate Review",
    "rins": ["2060-AV16"],
    "docketIds": ["EPA-HQ-OAR-2021-0317"]
  },
  "lifecycle": {
    "currentStage": "effective",
    "proposedRulePublished": "2021-11-15",
    "commentPeriod": {"originalDeadline": "2022-01-14", "currentDeadline": "2023-02-13", "extended": true, "open": false},
    "finalRulePublished": "2024-03-08",
    "effectiveDate": {"original": "2024-05-07", "current": "2024-05-07", "delayed": false}
  },
  "relatedDocuments": [
    {"documentNumber": "2024-00366", "normalizedType": "final_rule",
     "relationBasis": ["matching RIN 2060-AV16", "matching docket ID EPA-HQ-OAR-2021-0317"]}
  ],
  "resolution": {"confidence": "high", "matchedBy": ["RIN"]},
  "sources": {"federalRegister": {"status": "success"}, "regulationsGov": {"status": "success"}},
  "billing": {"billable": true, "eventName": "rule-resolution"}
}
```

### Status

| `status` | Meaning | Charged |
|---|---|---|
| `resolved` | One rulemaking identified, lifecycle reconstructed. | Yes |
| `partial` | Identified from an exact identifier; one official system was unavailable but the lifecycle is complete from the other. | Yes (high confidence only) |
| `ambiguous` | A rule name matched several rulemakings; `candidates` lists up to 5 with identifiers to retry. | No |
| `not_found` | No official record matches. | No |
| `conflicting_identifiers` | The supplied identifiers point to different rulemakings. | No |
| `insufficient_data` | Official sources could not support a lifecycle (including a source outage). | No |

### How dates are reconstructed

Comment deadlines and effective dates come from the official DATES text of each Federal Register document first,
then from structured Federal Register fields, then from Regulations.gov. Historical dates are kept: an extended
comment period reports both `originalDeadline` and `currentDeadline`, and a delayed rule reports both `original`
and `current` effective dates. Corrections, extensions and delays adjust dates; they never become the stage.

### How documents are linked

Documents are linked only on strong evidence: the same RIN, the same docket ID, an official correction link, or a
listing in the Regulations.gov docket. Same agency and overlapping CFR parts are recorded as supporting evidence only.
Similar titles never link documents. A document that shares a docket but carries a different RIN is excluded and
noted in `resolution.ambiguities`.

## Pricing

Pay per event: **$0.05 per `rule-resolution`**, charged only for `resolved` results and high-confidence `partial`
results, at most once per run. Ambiguous, not-found, conflicting and insufficient-data results are free apart from
the platform's tiny actor-start event.

## Development

See `AGENTS.md`. Tests run offline against saved official API responses.
