# Severity and Priority Model

Severity is **computed, never hand-assigned per check**. Hand-assigned severities drift —
whoever wrote check forty has forgotten the calibration of check three — and on an unseen
site the result is an ordering nobody can defend. Computing it from four observable factors
keeps every check consistent on every site, and lets any number in the report be
recomputed by hand. Implemented in `lib/auditkit/severity.py`.

## The formula

```
score = gate_weight × scope_weight × fact_weight × confidence_multiplier
```

| Factor | Values | Why |
| :--- | :--- | :--- |
| **gate_weight** | G0 1.00 · G1 0.95 · G2 0.70 · G3 0.55 · GE 0.65 | G0 and G1 are binary: content reaches the machine or it does not. G2 and G3 degrade the *quality* of what arrives. GE is binary per visitor, but only for visitors who arrive. |
| **scope_weight** | site 1.00 · template 0.80 · section 0.55 · page 0.30 | One line in one policy file affecting every URL outranks any single-page defect. |
| **fact_weight** | primary 1.00 · secondary 0.65 · enrichment 0.35 | What is missing matters more than how much. A client-rendered price is not a client-rendered testimonial carousel. |
| **confidence_multiplier** | high 1.00 · medium 0.75 · low 0.45 | Degraded evidence lowers severity. It is never rounded up. |

**Scope** is derived from counts (`scope_level_for`): a policy file is site-wide; a defect on
at least 60% of sampled pages (and at least three) is site-wide; a defect on the majority of
a template cluster with at least three pages is template scope; two or more pages is
section scope; one URL is page scope.

**Fact class** defaults per check in the registry and is raised by the detector when it knows
better — for example the render check escalates to `primary` only when the missing text
carries price, availability, hours, address, phone or specification facts. The detected
site profile decides which fact classes are primary: a missing price is primary on a shop
and not a finding at all on a documentation site.

**Confidence** follows rule R1: one independent signal supports only an observation, two
reach medium, three (or two plus cross-skill corroboration) reach high.

## Bands

| Score | Severity |
| :--- | :--- |
| ≥ 0.70 | critical |
| ≥ 0.46 | high |
| ≥ 0.25 | medium |
| ≥ 0.12 | low |
| below | not a finding — routed to `observations[]` |

## Floors and ceilings

A formula alone can still produce an indefensible claim, so six rules apply after it:

1. Low confidence can never produce `critical` (rule R7). Low-confidence items are routed to
   observations at emit time in any case.
2. Single-page scope can never produce `critical` (rule R2).
3. A masked finding is capped one rank below its masker, so a consequence never outranks its
   cause.
4. Engagement failures that *block access to content* — a covering overlay, a consent wall,
   forced registration, no viewport, tab-trapped content, navigation only in JavaScript —
   take primary fact weight. They stop every visitor, which is as binary as a G0 failure.
5. **G2 and G3 findings are capped at `high`.** They degrade content that does arrive; only a
   G0/G1 blocker — content never reached, or never read — is `critical`. Without this cap a
   site with no structured data at all scored as badly as a site that blocks the crawler,
   which reverses the order of work.
6. **Inferred consequences are capped.** Two checks observe how the site treats this audit's
   own client and infer what happens to crawlers the audit cannot observe without
   impersonating them:
   - `G0.GATING.NON_BROWSER_REFUSED` is capped at `high`: the refusal is certain, but
     whether the CDN's verified-bot allowlist admits a particular crawler is not observable.
   - `G0.GATING.BOT_STATUS_DIVERGENCE` is capped at `medium`: the probe names a crawler
     without coming from its published addresses, and a CDN that verifies crawlers by
     address correctly refuses exactly that request while admitting the real crawler.

The derivation string names any cap that changed the band, so the published formula and the
published severity never disagree.

## Priority

The action plan puts blockers first and then orders by what is worth doing first:

```
tier     = critical 0 · high 1 · everything else 2
priority = score × confidence_multiplier ÷ effort_weight      (within a tier)
effort_weight: trivial 0.5 · low 1 · medium 2 · high 4
```

Within a tier, a trivial one-line fix with moderate impact genuinely should precede a
high-impact re-architecture; a plan that ignores cost is a plan nobody starts. Across tiers,
cost never wins: without the tiers, calibration found trivial medium fixes pushing a site's
only high-severity problem out of the first five actions. `suggested_action.priority`
maps this value to the brief's labels (≥0.50 critical, ≥0.22 high, ≥0.08 medium, else low).
Dependencies override ordering: a masked finding never appears in the plan, so nothing is
scheduled before the finding that explains it. Proactive recommendations compete on a
lower scale than findings of equal priority, because they fix no observed defect.

## Worked examples

Each of these is a regression case; the numbers are exactly what the code produces.

| Case | Gate | Scope | Fact | Confidence | Score | Severity |
| :--- | :--- | :--- | :--- | :--- | :-: | :--- |
| Site-wide `nosnippet` | G0 1.00 | site 1.00 | primary 1.00 | high 1.00 | 1.000 | **critical** |
| Product template rendered client-side, price missing | G1 0.95 | template 0.80 | primary 1.00 | high 1.00 | 0.760 | **critical** |
| Required Product property missing on a template | G2 0.70 | template 0.80 | primary 1.00 | high 1.00 | 0.560 | **high** |
| No definitional sentence anywhere | G3 0.55 | site 1.00 | secondary 0.65 | high 1.00 | 0.358 | **medium** |
| Organization markup omits recommended properties, site-wide | G2 0.70 | site 1.00 | secondary 0.65 | high 1.00 | 0.455 | **medium** |
| No viewport meta, site-wide | GE 0.65 | site 1.00 | primary 1.00 | high 1.00 | 0.650 | **high** |
| No structured data on 18 of 30 pages, ecommerce | G2 0.70 | site 1.00 | primary 1.00 | high 1.00 | 0.700 | critical → **high** (rule 5) |
| Every content page refused by bot management | G0 1.00 | site 1.00 | primary 1.00 | medium 0.75 | 0.750 | critical → **high** (rule 6) |
| Crawler-named probe refused, others served | G0 1.00 | site 1.00 | primary 1.00 | medium 0.75 | 0.750 | critical → **medium** (rule 6) |
| Covering overlay, low confidence, one template | GE 0.65 | template 0.80 | primary 1.00 | low 0.45 | 0.234 | low → observation (R7) |
| Heading level skip on one page | GE 0.65 | page 0.30 | enrichment 0.35 | high 1.00 | 0.068 | not a finding |
| Client-rendered testimonial widget on one page | G1 0.95 | page 0.30 | enrichment 0.35 | medium 0.75 | 0.075 | not a finding |

The last two rows matter most. They are the cases that prove the model suppresses noise:
defects that are real, measurable, and not worth a reader's attention.

## Recomputing a severity from the report

Every finding carries `severity_derivation`, for example:

```
G0 gate 1.00 x site scope 1.00 x primary fact 1.00 x high confidence 1.00 = 1.000 -> critical
```

To challenge a severity, challenge one of the four named inputs — the scope count, the fact
class, or the confidence — rather than the verdict. The weights and bands are also published
in `methodology.severity_model` of every report.
