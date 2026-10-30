# Report Contract

The authoritative schema is `schema/audit-report.schema.json` (JSON Schema draft 2020-12).
This document explains each part and the invariants `verify_report.py` enforces beyond what a
schema can express.

## The brief's floor — always present

```json
{
  "site": "example.com",
  "audited_at": "2026-09-20T14:32:00Z",
  "summary": { "total_findings": 6, "critical": 1, "high": 2, "medium": 3 },
  "findings": [
    {
      "id": "F-001",
      "title": "…",
      "severity": "high",
      "evidence": "…",
      "suggested_action": { "summary": "…", "priority": "high" }
    }
  ]
}
```

- `critical`, `high` and `medium` are present **even at zero**. A missing key at zero fails
  the floor.
- `audited_at` is ISO-8601 UTC with a trailing `Z`, and is the only value that differs
  between two runs over the same evidence ledger. `--now` overrides it.
- `severity` and `suggested_action.priority` are one of `critical`, `high`, `medium`, `low`.

## A finding, in full

| Field | Meaning |
| :--- | :--- |
| `id` | `F-001`…, assigned after a deterministic sort (gate, severity, check id, scope). Presentation only. |
| `check_id` | Stable identity from the closed registry (`lib/auditkit/checks.py`), e.g. `G1.RENDER.CSR_CONTENT_GAP`. |
| `gate`, `category` | Where the cause lives, and its grouping. |
| `title` | The problem in one line. |
| `severity` | Computed; see `severity-model.md`. |
| `severity_derivation` | The computation, reproducible by hand. |
| `confidence` | `high` or `medium`. Low confidence never reaches `findings[]`. |
| `evidence` | A script-generated statement of what was observed, quantified as k/n where it can be. It describes observations, never inferences dressed as observations. |
| `evidence_refs` | Typed pointers into the evidence ledger; see below. At least one. |
| `mechanism` | Why this hurts discoverability or engagement, in terms of how crawlers and assistants work. |
| `scope` | `level` (site/template/section/page), `pages_affected`, `pages_sampled`, `template_id`, up to three `example_urls`. |
| `suggested_action` | The brief's `summary` and `priority`. |
| `suggested_action_detail` | Ordered `steps`, `effort`, `owner_role`, `verification` (how to confirm the fix worked — required), `expected_effect`. |
| `fact_class`, `signal_count` | Inputs to severity. |
| `detected_by`, `corroborated_by` | The owning skill, and any skill that independently observed the same fault. |
| `masks` / `masked_by`, `masked_reason` | Dependency links; see `gate-model.md`. |
| `references` | The reference file documenting the check's mechanism and false-positive guards. |

### Evidence reference kinds

| Kind | Carries | Re-resolved by checking that |
| :--- | :--- | :--- |
| `dom` | `selector`, `excerpt` | the selector still matches a node in the stored response |
| `header` | `name`, `value` | the header is present on the stored response, with that value |
| `file_line` | `path`, `line`, `text` | that line of the stored file contains that text |
| `jsonpath` | `path`, `value` | the path resolves in the page's JSON-LD or hydration payload to that value |
| `metric` | `name`, `value`, `threshold` | the ledger entry it names exists and the value is well-formed |

Every ref names a ledger entry by `url_hash` (the SHA-256 of the canonical URL). Metric
*values* are recomputed by re-running the analyzers over the frozen ledger — the determinism
test — rather than by re-implementing each detector inside the verifier.

## Additive top-level sections

| Section | Contents | Counted in `summary`? |
| :--- | :--- | :--- |
| `executive_summary` | Plain-language summary. Every number is regenerated from the arrays. | — |
| `action_plan` | `first_five` ids, `items` with action and owner, and the ordering `rationale`. | — |
| `gate_scorecard` | Status per gate: `pass`, `partial`, `fail`, `not_assessed`. | — |
| `masked_findings` | Findings fully explained by a root cause. | `summary.masked` only |
| `proactive_recommendations` | Advice where no defect was found; `is_defect: false`, `generator`, `rationale`, `mechanism`, `implementation`, `verification`, optional `measurement` and `calibration`. | `summary.proactive_recommendations` only |
| `observations` | Real but low-confidence or sub-threshold items. | `summary.observations` only |
| `site_profile` | Detected type, confidence, the signals that produced it, and checks scoped out. | — |
| `coverage` | Pages attempted, fetched and rendered; render mode; paths skipped with reasons; checks skipped with reasons and remedies; skills failed; findings rejected by the merge; budget; off-site state. | — |
| `methodology` | Version, gate model, severity model parameters, skills run, determinism statement, safety policy (method allowlist, mutating-GET denylist, SSRF policy, redaction, render tier, identity), limitations. | — |

## Verification invariants

`verify_report.py` refuses to let the report be published unless all of these hold:

1. The report validates against the schema.
2. `summary.total_findings` equals `len(findings)`, and each severity count matches exactly.
   `summary.masked` equals `len(masked_findings)`.
3. Every id is unique; every `check_id` is in the registry; every `gate` agrees with it.
4. `masks` and `masked_by` agree in both directions; no masked finding is itself a masker.
5. Every id in `action_plan.first_five` exists and none is masked.
6. Every recommendation carries `is_defect: false`.
7. Every stored body in the ledger re-hashes to its recorded hash.
8. Every evidence ref on every finding and masked finding re-resolves.
9. The report contains no credential-shaped strings.

On failure the orchestrator exits 2, writes the candidate as `audit.unverified.json` and
`audit.unverified.md`, and lists the problems in `work/verification.json`.

## Redaction

Evidence strings and ref excerpts pass through `lib/auditkit/redact.py`: email addresses,
phone numbers, Luhn-valid card numbers, IBANs, national identifiers and IP addresses are
masked, and excerpts are capped. Business contact details are preserved only in findings
whose subject *is* the contact detail (a marked-up telephone that contradicts the page).
The finished report is also scanned independently for secret patterns before it is written.
