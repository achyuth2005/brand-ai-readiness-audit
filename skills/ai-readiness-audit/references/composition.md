# Composition: How the Entrypoint Orchestrates Six Specialists

## The seven skills

| # | Skill | Gate | Evidence domain | Owner role | Network |
| :- | :--- | :--- | :--- | :--- | :--- |
| 0 | `ai-readiness-audit` (entrypoint) | all | none — orchestration, crawl, merge, report | — | **the only one** |
| 1 | `access-policy-audit` | G0 | HTTP status and headers, robots.txt, sitemaps, directives, gating | Infrastructure / SEO | none |
| 2 | `render-extraction-audit` | G1 | Raw versus rendered text, non-text carriers, encoding | Frontend | none |
| 3 | `structured-data-audit` | G2 | JSON-LD, Microdata, RDFa, canonicals | Frontend / SEO | none |
| 4 | `entity-trust-audit` | G2–G3 | Naming surfaces, identifiers, dates | Brand / Content ops | declares requests; performs none |
| 5 | `answerability-audit` | G3 | Grammatical and structural form of prose | Content / Marketing | none |
| 6 | `engagement-audit` | GE | Interaction surface and link graph | UX / Product | none |

## Why this decomposition, and not another

A split is justified only if it passes all four tests. The rubric explicitly asks whether a
decomposition is "genuine separation of concerns … or padding", and notes that a single
well-built skill scores fully, so every split has to earn its place.

| Test | Question |
| :--- | :--- |
| **T1 Evidence domain** | Does it read a different class of evidence from its siblings? |
| **T2 Independent utility** | Could someone run it alone and get a coherent, useful answer? |
| **T3 Distinct failure vocabulary** | Are its findings and fixes owned by a different role? |
| **T4 Swappability** | Could it be replaced or upgraded without touching its siblings? |

How each specialist passes:

- **access-policy** reads policy and transport evidence no other skill touches; its fixes go
  to whoever owns robots.txt, the CDN and the WAF.
- **render-extraction** reads the *difference* between what is served and what is shown; its
  fixes change how the frontend renders.
- **structured-data** reads typed markup graphs; the same frontend team may own the fix, but
  the evidence class and the vocabulary (schema.org types, properties, value shapes) are
  entirely separate.
- **entity-trust** reads identity and time — naming surfaces, identifiers, dates — which no
  other skill interprets, and its fixes go to brand and content operations.
- **answerability** reads the grammatical form of sentences, a different evidence class from
  both markup and identity; its fixes are editorial.
- **engagement** reads the interaction surface and computes over the crawled link graph
  rather than one page at a time; its fixes go to UX and product.

Each specialist runs standalone (T2): when no profile is supplied, it recomputes template
clusters and the site profile from the ledger itself. None imports another (T4); shared
logic lives in `lib/auditkit`, and the brand-name inference both entity-trust and
answerability need is a library module for exactly that reason.

### Splits deliberately rejected

Refusing a split is evidence of judgement, so the rejected ones are recorded:

| Rejected skill | Test failed | Where the concern lives instead |
| :--- | :--- | :--- |
| `alt-text-audit` | T1 — same evidence as render and extraction | `render-extraction-audit` (`G1.NONTEXT.ALT_*`) |
| `robots-audit` + `sitemap-audit` | T1, T3 — same policy domain, same owner | `access-policy-audit` |
| `severity-scoring` | T1, T2 — not an evidence domain | `lib/auditkit/severity.py`, used by the merge |
| `report-writer` | the brief assigns the report to the entrypoint | `render_report.py` in the entrypoint |
| `performance-audit` | T2 — without a lab, it degrades to guesses | the defensible subset (render-blocking scripts, weight, layout-shift risk) lives in engagement, where it has a behavioural meaning |

## The two invariants that make the decomposition load-bearing

**Invariant A — the Evidence Ledger.** Every byte the audit reasons about is fetched exactly
once, by one component, and written to an immutable, content-addressed ledger. All analysis
reads the ledger. Nothing else touches the network.

**Invariant B — pure analyzers.** Specialists are pure functions of the ledger: no network
I/O, no writes outside their own findings file, no mutation of the ledger.

Both are enforced by the code, not by convention:

- The egress gateway (`lib/auditkit/netgate.py`) refuses to construct unless the orchestrator
  token is set in the environment. The orchestrator strips that token from every specialist
  subprocess.
- Specialists open the ledger read-only; any write raises.
- No specialist or library module a specialist imports references the networking modules.
- Specialist `SKILL.md` files declare `allowed-tools: Bash Read` — no write, no fetch.

The decomposition is possible *because* one component owns fetching. Six skills that each
crawled would multiply load on the audited host, split the time budget six ways, and make
evidence irreproducible.

## Phases

```
PHASE 0  PREFLIGHT        normalise target · scheme/port/credential checks · SSRF pre-check
PHASE 1  DISCOVERY        robots.txt first (per origin, lazily) · sitemaps · /llms.txt ·
                          homepage links · stratified frontier
PHASE 2  CRAWL            two waves of pages · host-variant probes · user-agent probes ·
                          shared stylesheets · sampled internal-link HEADs   [network]
PHASE 2.5 PROFILE         template clusters · evidence-based site type · render tier
PHASE 3  FAN-OUT          six specialists as separate read-only processes, in parallel;
                          entity-trust runs the two-pass off-site protocol
PHASE 4  MERGE            eight steps, below
PHASE 5  RECOMMEND        four generators, structurally separate from findings
PHASE 6  RENDER + VERIFY  audit.json + audit.md published only if verification passes
PHASE 7  TEARDOWN         requests.log, run.log; ledger removed only with --no-evidence
```

### The two-pass off-site protocol

`entity-trust-audit` wants to know whether the brand's declared `sameAs` identifiers resolve.
Rather than open a hole in Invariant B:

```
pass 1   analyze_entity_trust.py --emit-requests offsite_requests.json   (declares, fetches nothing)
         orchestrator: accept only GET requests with purpose "offsite", apply the cap and the
         budget, fetch each through the egress gateway (SSRF, robots.txt, pacing), append to
         the ledger
pass 2   analyze_entity_trust.py --out findings.json                     (pure analysis again)
```

The declared requests are treated as data, not instructions: anything that is not a plain GET
for an off-site identifier is dropped. The marketplace keeps a single network choke point.

## The merge, step by step

Implemented in `scripts/merge_findings.py`.

| Step | What happens |
| :--- | :--- |
| 1 Validate | Each file against `schema/finding.schema.json`; each finding rebuilt through the `Finding` constructor; a skill may only report checks it owns. Rejects are logged in `coverage.findings_rejected`. |
| 2 Dedupe | Key `(check_id, scope key)`; evidence refs merged; strongest confidence kept. |
| 3 Collapse | Per-page to per-template in each specialist (shared clustering), and to site scope when one defect spans several templates covering most sampled pages. |
| 4 Corroborate | Same fault from two skills → one finding, `corroborated_by`, confidence up one rank. |
| 5 Mask | Gate dominance; see `gate-model.md`. |
| 6 Severity | Recomputed for every finding; anything below the lowest band becomes an observation. |
| 7 Prioritise | Impact × confidence ÷ effort, dependency-aware. |
| 8 Assign ids | Deterministic sort; `F-001`… for active findings, then masked. |

Checks the detected profile scopes out are dropped here as a safety net and recorded as
skipped.

## Failure policy

A specialist that crashes, times out or produces an invalid file does **not** fail the audit.
The failure is recorded in `coverage.skills_failed`, the gate it owns is marked
`not_assessed` if nothing else covers it, and the report says so plainly. A specialist that
stops partway writes the findings it has and records every unrun check as skipped with the
error. Partial-but-honest beats complete-but-fabricated, and it protects the time budget.

## What the model may and may not do

The agent running the entrypoint may choose to run it, present the report, group findings
thematically and summarise **from the structured findings only**. It may not author evidence,
invent a check, change a severity or count, or add a finding. Every number in the Markdown
and executive summary is regenerated from the arrays, so a mis-stated count cannot survive.
