# Brand AI-Readiness Audit: Design Document

**An Agent Skill Marketplace that audits any website for why it is missing from, or
misrepresented in, AI assistants — and why visitors who arrive from them leave.** It
produces an evidence-backed report of problems with severity, and prioritized fixes that
say how to verify they worked. It is read-only and recommend-only: it never changes the
site it audits.

```bash
pip install -r requirements.txt
python skills/ai-readiness-audit/scripts/run_audit.py --url example.com \
    --out ./audit-output --contact achyuthanoop@gmail.com
```

The contact goes into the audit's user-agent so a site operator can reach whoever runs it.
It defaults to `achyuthanoop@gmail.com`; override it with `--contact` or the `AUDIT_CONTACT`
environment variable.

---

## Contents

1. [The gate model](#1-the-gate-model)
2. [The seven skills](#2-the-seven-skills)
3. [How the entrypoint composes them](#3-how-the-entrypoint-composes-them)
4. [Why this decomposition](#4-why-this-decomposition)
5. [Coverage of the brief's failure modes](#5-coverage-of-the-briefs-failure-modes)
6. [Evidence and anti-hallucination](#6-evidence-and-anti-hallucination)
7. [Safety posture](#7-safety-posture)
8. [Proactive recommendations](#8-proactive-recommendations)
9. [How it generalizes to unseen sites](#9-how-it-generalizes-to-unseen-sites)
10. [Report shape](#10-report-shape)
11. [Running it](#11-running-it)
12. [Validation](#12-validation)
13. [Limitations](#13-limitations)
14. [Rubric mapping](#14-rubric-mapping)
15. [Implementation notes](#15-implementation-notes)
16. [Layout](#16-layout)

---

## 1. The gate model

The architecture follows the brief's own appendix: to be cited, a page must be **let in**,
then **read**, then **interpreted**, then **quoted and trusted**, in that order. Engagement
runs in parallel, because it only matters once a human has arrived.

```
        OFF-SITE DISCOVERABILITY  (ordered: an earlier failure masks later ones)
   ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌──────────┐
   │ G0 REACH │ ▶ │ G1 READ  │ ▶ │ G2 INTERPRET │ ▶ │ G3 CITE  │
   └──────────┘   └──────────┘   └──────────────┘   └──────────┘
   let in?        text exists     machine knows      quotable &
   permitted?     for a machine?  what it means?     trusted?

        ON-SITE ENGAGEMENT  (independent -- applies once a human lands)
   ┌───────────┐
   │ GE ENGAGE │  oriented? unblocked? able to go somewhere next?
   └───────────┘
```

Three things follow, and all three are visible in every report:

- **Root-cause attribution.** A finding is reported at the gate where its cause lives.
- **Dominance masking.** A downstream finding fully explained by an upstream one is moved to
  `masked_findings` with `masked_by` — kept, shown, and never counted twice.
- **Computed severity.** `gate × scope × fact class × confidence`, never hand-assigned, so
  every severity is consistent across sites and recomputable from the report. Published caps
  keep it defensible: only a G0/G1 blocker is critical, and a consequence the audit infers
  rather than observes is capped. The derivation string names any cap it applied.

Detail: [`references/gate-model.md`](skills/ai-readiness-audit/references/gate-model.md),
[`references/severity-model.md`](skills/ai-readiness-audit/references/severity-model.md).

## 2. The seven skills

One entrypoint and six specialists. Each specialist owns one evidence domain.

| Skill | Gate | Evidence it reads | What it detects | Fixes go to |
| :--- | :--- | :--- | :--- | :--- |
| **`ai-readiness-audit`** (entrypoint) | all | — | orchestration, the single crawl, merge, recommendations, report | — |
| `access-policy-audit` | G0 | HTTP, robots.txt, sitemaps, directives | per-assistant crawler blocks, `noindex`/`nosnippet`, bot gating and bot-management refusals, auth walls, soft 404s, duplicate hosts, sitemap health — 26 checks | Infrastructure / SEO |
| `render-extraction-audit` | G1 | raw vs. rendered text, media | client-side render gaps (three tiers, no browser required), facts in images, SVG, PDFs, iframes, video — 12 checks | Frontend |
| `structured-data-audit` | G2 | JSON-LD, Microdata, RDFa | an eight-rung validity ladder, profile-conditional coverage, entity-graph linkage, markup contradicting the page, canonicals — 24 checks | Frontend / SEO |
| `entity-trust-audit` | G2–G3 | naming surfaces, identifiers, dates | entity disambiguation posture, name consistency, `sameAs` resolution, contradictory and deploy-stamped dates, corroboration surface — 15 checks | Brand / Content ops |
| `answerability-audit` | G3 | the form of the prose | passages that lose their subject when retrieved alone, no definitional sentence, fluff, buried answers, unaddressable sections — 9 checks | Content / Marketing |
| `engagement-audit` | GE | interaction surface, link graph | orientation for a deep arrival, blocking overlays and walls, dead ends and orphans, conversion path, readability — 34 checks | UX / Product |

120 checks in a closed registry ([`lib/auditkit/checks.py`](lib/auditkit/checks.py)). Each
skill's `SKILL.md` table is generated from that registry, so documentation cannot drift from
code.

## 3. How the entrypoint composes them

```
0  PREFLIGHT        normalise target · refuse unsafe input · SSRF pre-check
1  DISCOVERY        robots.txt first, per origin · sitemaps · homepage links · stratified sample
2  CRAWL            the single polite crawl into the evidence ledger    ← only network phase
2.5 PROFILE         template clusters · evidence-based site type · optional render tier
3  FAN-OUT          six specialists as separate read-only processes; entity-trust runs a
                    two-pass protocol to have off-site identifiers fetched for it
4  MERGE            validate · dedupe · collapse · corroborate · mask · score · prioritise · ids
5  RECOMMEND        four generators, structurally separate from findings
6  RENDER + VERIFY  audit.json and audit.md published only if every evidence ref re-resolves
7  TEARDOWN         requests.log, run.log; ledger kept unless --no-evidence
```

**The merge is where decomposition becomes composition.** A worked example, reproduced by
the test fixture: a product template renders its content client-side. A naive auditor
reports missing structured data, boilerplate-dominated pages, a missing main landmark and
more — all true of the raw HTML, all one cause. This marketplace reports **one critical
finding**, "product content is assembled client-side and absent from the HTML response",
with the missing prose quoted from the page's own hydration payload, and lists the rest as
masked findings that will resolve or surface once it is fixed. When two skills see one
fault — navigation built only in JavaScript, say — it is one finding with
`corroborated_by` and raised confidence.

A specialist that crashes or times out does not fail the audit: it is recorded in
`coverage.skills_failed`, its gate becomes `not_assessed`, and the report says so.

Detail: [`references/composition.md`](skills/ai-readiness-audit/references/composition.md).

## 4. Why this decomposition

A split is kept only if it passes four tests: a **different evidence domain**, **independent
utility** (every specialist runs standalone on a ledger, recomputing clusters and profile
when no profile is given), a **different owner** for its fixes, and **swappability** (no
specialist imports another; shared code lives in `lib/auditkit`).

Splits deliberately **rejected** as padding:

| Rejected | Why | Lives instead in |
| :--- | :--- | :--- |
| alt-text skill | same evidence as render and extraction | `render-extraction-audit` |
| separate robots and sitemap skills | same policy domain, same owner | `access-policy-audit` |
| severity-scoring skill | not an evidence domain | `lib/auditkit/severity.py` |
| report-writer skill | the brief assigns the report to the entrypoint | `render_report.py` |
| performance skill | without a lab it degrades to guesses | the defensible subset, in engagement |

## 5. Coverage of the brief's failure modes

| Failure mode named in the brief | Gate | Primary checks |
| :--- | :--- | :--- |
| crawlability | G0 | `G0.ROBOTS.*` (per assistant crawler, retrieval versus training), `G0.DIRECTIVE.*`, `G0.GATING.*` including bot-management layers that refuse every non-browser client (`NON_BROWSER_REFUSED`), `G0.HTTP.*`, `G0.SITEMAP.*` |
| JS-render gaps | G1 | `G1.RENDER.CSR_CONTENT_GAP` (three tiers), `BODY_REQUIRES_INTERACTION`; corroborated by `GE.NAV.NAV_ONLY_IN_JS`, `G2.SD.SD_ONLY_IN_RENDERED_DOM` |
| missing / invalid structured data | G2 | `G2.SD.*` validity ladder, profile-conditional required properties, `MARKUP_TEXT_CONTRADICTION` |
| facts locked in non-text | G1 | `G1.NONTEXT.*` — structural detection, no OCR |
| stale or uncorroborated facts | G3 | `G3.FRESH.*` including the deploy-stamped lastmod detector; `G3.CORROB.*` |
| entity ambiguity | G3 | `G3.ENTITY.AMBIGUITY_RISK` posture score, `NAME_INCONSISTENCY`, `SAMEAS_*` |
| weak on-site orientation | GE | `GE.ORIENT.*` — the three-question test for a deep arrival |
| no context retention | GE | `GE.ORIENT.DEEP_PAGE_NO_CONTEXT`, `GE.NAV.*` graph checks |
| *(added)* unquotable content | G3 | `G3.ANSW.*` — implied by Appendix B's "easily quote a clear fact", and missed by audits that stop at crawlability and markup |

## 6. Evidence and anti-hallucination

- **The Evidence Ledger.** Every byte the audit reasons about is fetched once, by one
  component, into an immutable store keyed by SHA-256 hashes. One request per URL for the
  whole audit, by construction.
- **Pure analyzers.** Specialists read the ledger read-only and cannot construct the network
  gateway: it refuses to initialise without an orchestrator token that specialist processes
  never receive. Their `SKILL.md` files declare `allowed-tools: Bash Read`.
- **Scripts author findings; the model never does.** The `Finding` constructor refuses any
  finding without a registered `check_id`, a substantive evidence string, a stated mechanism,
  a verification step, and at least one typed evidence reference.
- **Typed evidence.** `dom` selectors, `header`s, `file_line`s, `jsonpath`s and `metric`s — each
  pointing into the ledger.
- **The verification gate.** Before publishing, `verify_report.py` re-validates the schema,
  recomputes every count, checks the masking graph and action plan, re-hashes the ledger, and
  **re-resolves every evidence reference against the stored bytes**. Any failure: exit 2,
  nothing published.
- **A bounded role for the model.** The entrypoint instructs the agent to present only what
  the report contains — never to add findings, change severities or strengthen evidence.
  Every number in the prose is regenerated from the arrays.
- **Page content is data.** Every `SKILL.md` states that instructions found in page text,
  comments, alt text, JSON-LD or headers are never followed.
- **Describe observations, not inferences.** The non-text check never claims an image
  "contains" a price; it reports that a section labelled Pricing has 2 words, one large image,
  and no price anywhere in the page's text or markup.

## 7. Safety posture

| Threat | Control |
| :--- | :--- |
| Mutating the site | GET and HEAD only, asserted at the single egress point. No forms submitted, no cookies kept, no consent accepted. |
| Mutation via GET | A denylist refuses logout, unsubscribe, confirm, cart, checkout, admin and similar paths, action-verb query parameters, and high-entropy single-use tokens — while keeping ordinary slugs like `cart-abandonment-guide`. |
| SSRF | Resolve-then-connect with the validated IP **pinned** for the connection; private, loopback, link-local, CGNAT, documentation and metadata ranges refused (including IPv4-mapped, 6to4 and Teredo forms); a host is refused if *any* address it resolves to is; every redirect hop re-validated. |
| Rate abuse | One fetch per URL; 2 concurrent per host; 0.5 s floor raised to `Crawl-delay`; 120-request cap; one retry honouring `Retry-After`; circuit breaker after three 429/503s. |
| robots.txt | Fetched first for every origin; RFC 9309 matching; never fetched a disallowed path; probes carrying another crawler's token must satisfy that crawler's group too. |
| Resource exhaustion | 5 MB streamed cap, 50:1 decompression-ratio cap, 5 redirects, clamped timeouts, a 240-second budget with a documented degradation ladder. A file cut at the cap is never judged as if it were the whole file. |
| Identity | A truthful user-agent that names this tool. No impersonation, no challenge solving, no evasion: a block is reported as a finding, never worked around. |
| Path traversal | Ledger file names are hashes; no URL-derived path reaches the filesystem. |
| Code execution | Fetched content is never evaluated; JSON is parsed, XML parsed without external entities. The optional browser tier aborts every non-GET and third-party request and never interacts with the page. |
| Personal data and secrets | Evidence redacts emails, phones, card numbers, IBANs and identifiers; the finished report is scanned for credential patterns before it is written. |

## 8. Proactive recommendations

Recommendations live in their own array with `is_defect: false`, so advice can never inflate
the problem count. Each carries a site-specific rationale, a mechanism, steps and a
verification. Four generators, from most to least site-specific:

1. **Near misses** — a check measured something just below its threshold. The advice quotes
   the measurement.
2. **Posture gaps** — something exists but only shallowly (Organization markup with only
   social `sameAs` links).
3. **Profile playbooks** — type-conditional improvements, each gated on the site not already
   having the thing (a comparison page; pricing stated as text; hours on every page).
4. **Leverage synthesis** — one template causing most findings becomes one engineering task;
   drift-prone defects get process fixes; a scheduled re-audit keeps it monitored. `llms.txt`
   is suggested only with an explicit calibration note that it is an emerging convention and
   not a substitute for any finding.

## 9. How it generalizes to unseen sites

- **No site-specific logic.** No domains, brands or vendor selectors in detection code; a test
  enforces it.
- **Evidence-based profiling.** Site type comes from markup, path classes, page text and
  controls — never the domain name. Too-close scores yield `ambiguous`, and only type-agnostic
  checks run.
- **Template clustering.** Pages are clustered by DOM skeleton, so a defect on 12 pages of one
  template is one finding with `12/12` evidence, and "thin" means thin *for its template*.
- **A false-positive doctrine.** Two independent signals for any inference; no generalising
  from one page; template collapse; utility pages exempt; framework presence never a defect;
  a check that cannot run is skipped, not guessed; low confidence is an observation, not a
  finding.
- **Externalised thresholds.** Every tunable lives in
  [`config/thresholds.json`](config/thresholds.json) with a stated rationale.

## 10. Report shape

```json
{
  "site": "shop.test",
  "audited_at": "2026-09-13T10:00:00Z",
  "summary": { "total_findings": 8, "critical": 2, "high": 2, "medium": 1, "low": 3,
               "info": 0, "masked": 4, "observations": 0, "proactive_recommendations": 3 },
  "findings": [{
    "id": "F-002",
    "title": "Product content including availability is assembled client-side and absent from the HTML response",
    "severity": "critical",
    "evidence": "3/3 sampled pages in template cluster T2 return a raw HTML response whose main region contains 0 characters of visible text …",
    "suggested_action": { "summary": "Server-render or pre-render the primary content of this page class …", "priority": "high" },
    "check_id": "G1.RENDER.CSR_CONTENT_GAP", "gate": "G1", "confidence": "high",
    "mechanism": "Many assistant retrieval fetchers take the raw HTML response …",
    "scope": { "level": "template", "template_id": "T2", "pages_affected": 3, "pages_sampled": 3 },
    "evidence_refs": [{ "kind": "jsonpath", "url_hash": "…", "path": "$.props.pageProps.product.description", "value": "…" }],
    "suggested_action_detail": { "steps": ["…"], "effort": "medium", "owner_role": "Frontend engineering",
                                 "verification": "curl -s <url> | grep -F '<a known value>' …" },
    "severity_derivation": "G1 gate 0.95 x template scope 0.80 x primary fact 1.00 x high confidence 1.00 = 0.760 -> critical",
    "masks": ["F-009", "F-010", "F-011"]
  }],
  "executive_summary": "…", "action_plan": { "first_five": ["F-001", "…"] },
  "gate_scorecard": {}, "masked_findings": [], "proactive_recommendations": [],
  "observations": [], "site_profile": {}, "coverage": {}, "methodology": {}
}
```

The brief's required fields are always present, including zero counts. Schema:
[`schema/audit-report.schema.json`](schema/audit-report.schema.json). Contract:
[`references/report-schema.md`](skills/ai-readiness-audit/references/report-schema.md).

## 11. Running it

```bash
# A live audit
python skills/ai-readiness-audit/scripts/run_audit.py --url https://example.com --out ./out \
    [--contact <url-or-email>] [--max-pages 30] [--budget-seconds 240] \
    [--render auto|on|off] [--offsite on|off] [--now 2026-09-20T14:32:00Z] \
    [--prior-ledger ./previous/ledger] [--no-evidence] [--json-only]

# Offline, over an existing evidence ledger (no network)
python skills/ai-readiness-audit/scripts/run_audit.py --ledger ./out/ledger --out ./reanalysis

# One specialist, standalone
python skills/structured-data-audit/scripts/analyze_structured_data.py \
    --ledger ./out/ledger --out ./sd-findings.json
```

| Output | Contents |
| :--- | :--- |
| `audit.json` | the verified report |
| `audit.md` | the same report for a non-expert: summary, scorecard, first five actions, each problem with evidence, why it matters, steps and how to confirm the fix |
| `ledger/` | the evidence every finding cites |
| `requests.log` | every request made, with method, status and purpose |
| `run.log` | phase timings and decisions |
| `work/` | profile, per-skill findings, merge, recommendations, verification result |

**Exit codes:** `0` report written (findings are not failures) · `2` verification failed, nothing
published · `3` unsafe target refused · `4` target unreachable · `5` time budget closed before
minimum coverage · `6` a required package is missing (`pip install -r requirements.txt`). A site that refuses every page, by robots.txt or by a bot-management layer,
still gets a report (exit `0`): the refusal is the finding.

**Optional render tier:** `pip install playwright && playwright install chromium`. Without it,
client-side rendering is still detected from hydration payloads.

## 12. Validation

```bash
tests/run_tests.sh
```

The suite uses only the standard library's `unittest` and needs no network:

| Test module | What it proves |
| :--- | :--- |
| `test_safety.py` | method allowlist; mutating-GET denylist with slug controls; SSRF ranges and redirect re-validation; the gateway refuses to exist outside the orchestrator; robots precedence and probe compliance; size and compression-bomb caps; pacing and circuit breaker; read-only and immutable ledger; hash-named files; no `eval`; only the gateway modules import networking; every analyzer completes with sockets blocked; honest user-agent; redaction |
| `test_detectors.py` | positive fixtures fire the checks they target in every skill |
| `test_negative_controls.py` | false-positive guards hold: server-rendered framework pages (including payloads carrying text the page never shows), decorative images, `noindex` on search and quick-view pages, price variants, comma-decimal currencies, hidden modals and state-revealed backdrops, conditional scroll locks, cart-only robots rules, training-only and Common Crawl blocks, off-site profiles refused by their own robots.txt, one refused page among served ones, truncated and partly read sitemaps, refused sitemap URLs, index and version canonical consolidation, retailers named as sellers, nested and `isPartOf` references, RDFa breadcrumbs with or without extruct, software offers without stock, routing prefixes without parent pages, about pages on sister hosts, in other languages or told as a story, taglines in site names, link-index pages, documentation paths ending in `item` — and a well-built site produces **zero findings**. Each guard was added for a false positive found on a real site during calibration |
| `test_dominance.py` | masking suppresses consequences, retains them, never masks engagement, never lets a masked finding mask, and keeps masked findings out of the plan |
| `test_schema.py` | manifest has exactly one entrypoint; every skill folder is spec-compliant; the report validates; tampered counts and unresolvable evidence fail verification |
| `test_determinism.py` | two runs over one frozen ledger produce byte-identical reports |
| `test_config.py` | every threshold the code reads is declared with a rationale |
| `test_generalization.py` | no site-specific hosts in detection code; balanced evidence yields an `ambiguous` profile; skill tables match the registry |

Every `SKILL.md` also passes the official `skills-ref validate`.

**Field calibration.** Beyond the fixtures, the audit was run live against 29 public sites:
e-commerce, publishers (one non-English), SaaS, documentation, local businesses,
government, a marketplace and client-rendered web apps, with the render tier installed. Every
critical and high finding was checked against the stored response bodies, headers and
robots.txt, and against public reporting where it exists. The results:

- **70 labelled findings:** 39 false positives and 12 overstated severities, each fixed by a
  generic guard, scope or severity rule with a negative-control test; 15 confirmed correct;
  3 left unclear. No fix names a site; `test_generalization.py` enforces it.
- **Reports that were withheld now publish.** 11 of 29 first live runs produced no report:
  - verification-gate bugs on nested, truncated and mis-paired evidence (7 sites)
  - bot-management refusals with no finding to express them (3, one also honouring a
    30-second `Crawl-delay`)
  - a transient timeout that a later retry did not repeat (1)

  After calibration every ledger produces a verified report, and a site that refuses the
  audit gets `G0.GATING.NON_BROWSER_REFUSED`.
- **Severity now means something.** Across the 29 re-analysed reports there are 2 critical
  findings, both OpenAI retrieval crawlers disallowed in robots.txt and confirmed by public
  reporting, and 22 high. Before calibration, structured-data and entity checks reached
  critical on well-known brands.
- **Runtime.** Every live run finished within 182 seconds. The slowest was a site whose
  robots.txt asks for a 30-second crawl delay, which is honoured.

The site list, per-finding labels, per-check fire rates and the clustering evaluation are
kept with the calibration scripts, outside this package.

## 13. Limitations

Stated plainly, because a report whose limits are precise is one whose claims can be trusted:

- **Sampled, not exhaustive.** Up to 30 pages by default. Template clustering attributes a
  sampled defect to its template, and every finding states its k/n.
- **Static-first.** Browser measurement runs only with Playwright installed; the report states
  the render mode used.
- **Corroboration is measured as the site's corroboration surface**, plus resolution of declared
  identifiers — not as a web-wide survey of what others say.
- **Content-form checks are heuristics** with published vocabulary and thresholds, English-only;
  other languages are skipped and reported.
- **No layout engine.** "Above the fold" is a document-order proxy; overlay and contrast checks
  read declared CSS only.
- **Honest probing has limits.** User-agent gating is detected only where implemented as a
  user-agent rule; IP-based gating cannot be seen without impersonation, which is never done.
- **Nothing here measures an assistant's answers.** The checks encode the mechanisms by which
  content is found, read, interpreted and quoted.
- **Calibrated on 29 sites, not on assistant answers.** Thresholds and severities were tuned
  against verified labels on real sites (section 12). They were not tuned against what
  ChatGPT, Perplexity or Gemini actually answer, so no check claims to predict citation, and
  the labels were made by the audit's authors rather than an independent panel.
- **Some checks describe common gaps.** Partial identity posture, missing definitional
  sentences and absent structured data are common even on well-known brands. They are
  reported because they are true and fixable, at severities that keep them below real
  blockers — not because their presence alone predicts being ignored.
- **Crawler verification is invisible from outside.** Whether a CDN admits a verified
  retrieval crawler it refuses to this audit cannot be observed without impersonation; the
  affected checks cap their severity and state the limitation.

## 14. Rubric mapping

| Criterion | Where to look |
| :--- | :--- |
| **Detection accuracy** | 120 checks, each documented with mechanism, signals, algorithm and false-positive guards in its skill's `references/`; the FP doctrine; template collapse; dominance masking; `test_negative_controls.py`; field calibration on 29 live sites with 70 labelled findings (section 12) |
| **Suggested-action quality** | every finding's ordered `steps`, `owner_role`, `effort`, `verification` and `expected_effect`; computed, dependency-aware priority; four recommendation generators |
| **Output design** | `audit.md`; `action_plan`; `gate_scorecard`; `severity_derivation`; `coverage`; the schema |
| **Skill format & engineering hygiene** | seven `skills-ref`-valid skills; `marketplace.json` with exactly one entrypoint; least-privilege `allowed-tools`; single egress point; the verification gate; determinism; `tests/` |
| **Marketplace composition** | evidence-domain decomposition with rejected splits; the Evidence Ledger and pure analyzers; the eight-step merge; the two-pass off-site protocol |
| **Generalization** | evidence-based profiling with an `ambiguous` fallback; skeleton clustering; externalised thresholds; no site-specific logic; every calibration fix is a generic rule with a regression test, checked across e-commerce, publisher, SaaS, documentation, government, local-business and web-app sites |

## 15. Implementation notes

Deliberate choices, with reasons:

- **The network gateway uses `http.client`, not an HTTP client package.** Resolve-then-connect
  needs the connection pinned to the exact address that passed SSRF validation, with the
  hostname still used for TLS verification. `http.client` gives that directly and removes a
  dependency from the safety core. TLS verification uses `certifi`'s CA bundle when available,
  so it works on Python builds that ship without system certificates — verification is never
  turned off.
- **robots.txt matching is implemented, not imported.** `urllib.robotparser` mishandles
  wildcards and precedence. `protego` is correct but optional, and a safety control cannot
  depend on an optional package, so the built-in RFC 9309 matcher is authoritative and
  `protego` cross-checks it when installed.
- **JSON-LD extraction is built in; `extruct` augments it.** Each block is parsed individually
  to report syntax errors with line and column.
- **The entrypoint skill is `ai-readiness-audit`,** matching its folder as the spec requires;
  `brand-ai-readiness-audit` is the marketplace name.
- **`allowed-tools` is a space-delimited string**, per the Agent Skills specification.
- **Engagement checks skip client-rendered shells.** A visitor with JavaScript sees content the
  raw response lacks; judging orientation from the shell would re-report the render gap as a
  false engagement finding.
- **The user-agent probe never impersonates.** It appends a `probe-profile` token to this tool's
  own user-agent, obeys the robots.txt group that token matches, and states what it cannot see.

## 16. Layout

```
brand-ai-readiness-audit/
├── marketplace.json            manifest: seven skills, exactly one entrypoint
├── README.md  LICENSE  requirements.txt
├── config/thresholds.json      every tunable, each with a rationale
├── schema/                     report, findings-file, ledger-entry and site-profile schemas
├── lib/auditkit/               shared library
│   ├── netgate.py ssrf.py robots.py ratelimit.py urlnorm.py budget.py ledger.py   safety core
│   ├── dom.py textnorm.py sdextract.py cssutil.py template.py profile.py         analysis core
│   ├── entity.py sitemap.py lexicons.py render.py analyzer.py                     shared analysis
│   └── checks.py finding.py severity.py redact.py bootstrap.py                   contracts
├── skills/
│   ├── ai-readiness-audit/     SKILL.md · scripts/ (run_audit, discover, crawl, merge_findings,
│   │                           recommend, render_report, verify_report) · references/ (5)
│   ├── access-policy-audit/    SKILL.md · scripts/analyze_access.py · references/ (3)
│   ├── render-extraction-audit/  SKILL.md · scripts/analyze_render.py · references/ (2)
│   ├── structured-data-audit/  SKILL.md · scripts/analyze_structured_data.py · references/ (2)
│   ├── entity-trust-audit/     SKILL.md · scripts/analyze_entity_trust.py · references/ (2)
│   ├── answerability-audit/    SKILL.md · scripts/analyze_answerability.py · references/ (2)
│   └── engagement-audit/       SKILL.md · scripts/analyze_engagement.py · references/ (2)
└── tests/                      unittest suite, fixture sites, table sync tool
```

MIT licensed.
