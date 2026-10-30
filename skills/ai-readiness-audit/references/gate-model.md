# The Four-Gate + Engagement Model

Everything in this marketplace hangs off one causal model, taken directly from the brief's
appendix rather than invented alongside it.

> "For a page to be visible at all, three things have to succeed *in order* — the crawler
> has to be let in, it has to be able to read what's on the page, and it has to be able to
> pick out the specific fact someone is looking for." — Appendix A

Appendix B adds the fourth requirement: what ends up in an answer is what a machine can
"easily quote a clear fact from", and Appendix D adds that it must be corroborated to be
believed. The engagement half runs in parallel, because it only matters once a human has
arrived.

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

| Gate | Question | Skill | Brief appendix |
| :--- | :--- | :--- | :--- |
| G0 Reach | Is the crawler let in, and allowed to quote? | `access-policy-audit` | A (let in) |
| G1 Read | Does the text exist for a machine? | `render-extraction-audit` | A (read), C |
| G2 Interpret | Does a machine know what the facts mean? | `structured-data-audit` | A (pick out the fact) |
| G2–G3 Identity | Can it tell who this is and trust when? | `entity-trust-audit` | D |
| G3 Cite | Can a fact be lifted and quoted? | `answerability-audit` | B |
| GE Engage | Does the arriving visitor stay? | `engagement-audit` | brief: orientation, context retention |

## Three consequences, all of them scored

**1. Root-cause attribution.** A finding is reported at the gate where its *cause* lives,
not where the symptom shows. A price missing from the markup of a page whose price only
exists after JavaScript runs is a G1 problem; reporting it at G2 would send the fix to the
wrong team.

**2. Dominance masking.** When an upstream failure fully explains a downstream one, the
downstream finding is moved out of `findings[]` into `masked_findings[]` with `masked_by`
and a one-line reason. It is retained, shown in the report under "will surface once the
root cause is fixed", and excluded from the summary counts. Emitting both as peers is the
single largest false-positive generator in this problem space.

**3. Severity derivation.** Severity is computed from which gate failed, how much of the
site, what fact class, and how confident the evidence is. See `severity-model.md`.

## Masking rules

Implemented in `scripts/merge_findings.py` (`MASKING_RULES`), mirrored here. A rule applies
when the dominating finding is active and the two findings overlap in scope: either is
site-wide, they share a template cluster, or they share an example URL.

| Dominating finding | Masks | Why |
| :--- | :--- | :--- |
| `G0.ROBOTS.AI_AGENT_BLOCKED` for **both** Googlebot and Bingbot, site-wide | all G2 and G3 findings | The site is absent from the core indexes most assistants retrieve from. A block on one assistant's crawler masks nothing, because G2/G3 findings are not agent-specific. |
| `G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT` | G1, G2, G3 findings | The URL serves a login form; content checks describe the form. |
| `G0.HTTP.SOFT_404` | G1, G2, G3 findings | The URLs are error pages with a 200 status. |
| `G0.DIRECTIVE.NOINDEX_HEADER`, `NOINDEX_META` | G2, G3 findings | Interpretation and citation cannot matter for a page excluded from indexes. |
| `G1.RENDER.CSR_CONTENT_GAP` | `G2.SD.ABSENT`, `G2.SD.TYPE_MISMATCH`, `G1.TEXT.BOILERPLATE_DOMINANT`, `G1.TEXT.MAIN_LANDMARK_MISSING`, `G1.NONTEXT.ALT_*`, `G1.NONTEXT.FACT_LOCKED_IN_MEDIA`, `G3.CORROB.FACTS_ONLY_IN_MARKETING_COPY`, all `G3.ANSW.*` | The findings describe the empty response. The text is not there to mark up, quote or describe. |
| `G2.SD.JSONLD_SYNTAX_ERROR` | `G2.SD.REQUIRED_PROP_MISSING`, `RECOMMENDED_PROP_MISSING`, `MISSING_CONTEXT`, `UNKNOWN_TYPE`, `PROP_TYPE_INVALID`, `ABSENT` | Property judgements about markup that never parses are incoherent. |

Invariants the merge enforces:

- A finding is never masked by one at a later gate.
- A masked finding never masks anything, so there are no cycles. Dominators are resolved
  in gate order.
- A masked finding's severity is capped one rank below its masker's, so a consequence can
  never outrank its cause.
- The action plan never contains a masked finding.

### Engagement is never masked — and never double-counts

`GE` findings are never masked by G0–G3: a visitor blocked by an overlay or lost on a deep
page is a separate harm that persists after an upstream fix unless it is fixed too.

The corollary is that engagement must not *re-report* an upstream cause. A page whose raw
response is an empty client-rendered shell shows a visitor with JavaScript an h1, calls to
action and links that the response lacks. `engagement-audit` therefore does not judge
human-visible orientation, conversion or readability on such shells, records those checks
as skipped with the reason, and leaves the render gap to G1. Checks that read what the
server sends regardless of rendering — viewport meta, render-blocking head scripts,
response weight, declared overlay CSS — still run.

## Cross-skill corroboration

When two skills independently observe one fault, the merge keeps one finding at the owning
gate, records `corroborated_by`, and raises confidence one rank. Overlap becomes a strength
signal instead of noise.

| Finding | Corroborated by |
| :--- | :--- |
| `GE.NAV.NAV_ONLY_IN_JS` | `G1.RENDER.CSR_CONTENT_GAP` |
| `GE.FRICTION.TAB_TRAP_CONTENT` | `G1.RENDER.BODY_REQUIRES_INTERACTION` |
| `G2.SD.SD_ONLY_IN_RENDERED_DOM` | `G1.RENDER.CSR_CONTENT_GAP` |
| `GE.FRICTION.FORCED_REGISTRATION` | `G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT`, `G0.GATING.PAYWALL_UNMARKED` |
| `G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE` | `GE.READ.NO_LISTS`, `GE.READ.LONG_SENTENCES` |
| `G3.CORROB.NO_CITABLE_PRIMARY_SOURCE` | `G3.ANSW.NO_DEFINITIONAL_SENTENCE` |
| `G2.SD.ENTITY_GRAPH_UNLINKED` | `G3.ENTITY.AMBIGUITY_RISK`, `G3.ENTITY.WEAK_DISAMBIGUATION` |

## Worked example

> A site is fully client-rendered on its product template. A naive auditor reports dozens
> of findings: missing JSON-LD on every product page, missing alt text, thin content,
> boilerplate-dominated pages, no definitional sentence. All of them are *true of the raw
> HTML*, and all of them have one cause. This marketplace reports **one critical finding**
> — "product content is assembled client-side and absent from the HTML response" — with
> evidence from the page's own hydration payload, and lists the rest as `masked_findings`
> that will resolve, or become independently actionable, once the render gap is fixed.
> The reader gets one thing to do, in the right order.

The offline integration fixture behind this document reproduces it: a client-rendered
product template produces `G1.RENDER.CSR_CONTENT_GAP` as a critical finding that masks
`G1.TEXT.BOILERPLATE_DOMINANT`, `G1.TEXT.MAIN_LANDMARK_MISSING` and `G2.SD.ABSENT` on the
same template, while a separate blog template's unparseable JSON-LD masks that template's
own `G2.SD.ABSENT`.

## Gate scorecard

`gate_scorecard` in the report gives each gate one status:

| Status | Meaning |
| :--- | :--- |
| `fail` | at least one critical or high finding at this gate |
| `partial` | only medium or low findings, or findings masked by an upstream gate |
| `pass` | no findings and none masked |
| `not_assessed` | the owning skill failed, so the gate was not examined |

The earliest failing gate is named in the executive summary, because problems there limit
every later stage.
