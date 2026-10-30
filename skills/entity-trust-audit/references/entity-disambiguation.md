# Entity Disambiguation and the Corroboration Surface

## Mechanism

> "Machines tend to treat a fact as more trustworthy when many independent places say the same
> thing … A related problem is mistaken identity: when several different things share a name,
> a system can mix them up unless there's something that clearly distinguishes one from the
> others." — brief, Appendix D

## What is measured, and what is not

The world's namesakes cannot be enumerated honestly offline, and this skill does not pretend
to. It measures **the site's own disambiguation posture**: whether the site gives a machine
enough to tell this entity apart. That is observable on the site, and it is exactly what the
fix changes. Findings state that they make no claim about any specific namesake.

## Brand-name inference

Shared with `answerability-audit` through `lib/auditkit/entity.py`. The name is inferred from
the surfaces where a site names itself, weighted by how deliberate each surface is:

| Surface | Weight |
| :--- | :-: |
| `Organization.name` (and Organization subtypes) in structured data | 5 |
| `WebSite.name` | 3 |
| `og:site_name` | 3 |
| a title-tag segment repeated across 2+ pages | 2 |
| logo image `alt` text, in page chrome or a link | 2 |
| footer copyright holder | 2 |
| `application-name` meta | 1 |
| registrable domain label | 1, fallback only |

Surfaces are clustered so that values reducing to one another are one name: case,
punctuation, `&`/"and", and legal suffixes (Inc, Ltd, LLC, GmbH, plc …) are folded, and a
value whose words are a subset of another's ("Acme" / "Acme Tools Ltd") is the same name. The
heaviest cluster wins, and its shortest common form is displayed.

## The posture score (0–6)

| Point | Present when |
| :--- | :--- |
| Stable identifier | every Organization node carries an `@id`, and only one distinct `@id` value is used |
| Authoritative identifiers | at least 2 `sameAs` URLs on registries or identity hosts: Wikidata, Wikipedia, LinkedIn company pages, Crunchbase, OpenCorporates, company registries, GLEIF, ORCID, ROR, GitHub, and similar. Social profiles are counted separately and do not earn the point — they are easy to create and are not registries. |
| Entity-home page | an about or company page was fetched |
| Definitional sentence | a sentence whose subject is the brand and whose predicate defines it ("<Brand> is a …", "<Brand> provides …", "<Brand>, the … company") in the first 3 paragraphs of the homepage or about page, or in `Organization.description` |
| Disambiguating modifiers | at least 2 of: a category noun; geography (address, `areaServed`, location, a `PostalAddress`, or "headquartered/based in …"); a founding year; a parent or subsidiary relation |
| Consistent naming | fewer than 3 irreducible name variants |

**Amplifiers** lower the effective score by one, because such names collide more on the
open web: the name is a common English word (from a short, high-frequency list), or it is 4
characters or fewer.

| Effective score | Result |
| :-: | :--- |
| ≤ 2 | `G3.ENTITY.AMBIGUITY_RISK` (primary fact weight) |
| 3–4 | `G3.ENTITY.WEAK_DISAMBIGUATION` (secondary) |
| ≥ 5 | posture recorded; missing points may become a proactive recommendation |

Evidence lists all six points with what was observed for each, so a reader can see exactly
which anchors are missing.

**Fix:** one Organization node site-wide with a stable `@id`; `sameAs` to the identifiers that
already exist for the company; a plain definitional sentence at the top of the homepage and
about page, mirrored in `Organization.description`; category, headquarters and founding year
stated as text on the about page.

## `G3.ENTITY.NAME_INCONSISTENCY`

**Fires at:** 3 or more irreducible name clusters across the naming surfaces (the domain label
is excluded from the count).
**Guard:** long and short forms and legal-suffix variants are merged before counting, so every
company's ordinary "Acme" / "Acme Corporation" pair never fires.
**Mechanism:** each irreducible variant is a separate candidate entity string. A variant naming
something else — a former name, a sub-brand used site-wide, a developer's name in the footer
template — actively attaches the brand's content to a different entity.
**Evidence:** each variant with the surfaces it appears on, how often, and an example URL, with
`jsonpath` refs for markup surfaces and `dom` refs for page surfaces.
**Fix:** standardise every surface on one canonical name and one permitted short form; list
legitimate alternates in `Organization.alternateName` instead of using them interchangeably.

## `sameAs` resolution — the two-pass protocol

1. **Pass 1** (`--emit-requests`): the skill writes the declared `sameAs` URLs it wants
   resolved — authoritative hosts first, capped at 6 — and stops. It fetches nothing.
2. The entrypoint accepts only plain GET requests with purpose `offsite`, applies the cap and
   the time budget, and fetches each through the egress gateway with SSRF validation,
   robots.txt compliance and pacing.
3. **Pass 2:** pure analysis over the extended ledger.

| Response | Interpretation |
| :--- | :--- |
| 404, 410, or the host does not resolve | `G3.ENTITY.SAMEAS_BROKEN` — a trust signal pointing at nothing |
| 401, 403, 429, 451, 999, 5xx, timeout | **inconclusive** — identity sites routinely refuse automated requests; never reported as broken |
| 200 on an authoritative host, page never names the brand | `G3.ENTITY.SAMEAS_MISMATCH` at medium confidence — the brand's own markup merges it with another entity |

Without off-site results (disabled, or dropped by the budget), both checks are recorded as
skipped and the on-site posture stands alone.

## The corroboration surface

The whole web cannot be measured, so this skill measures **the surface the site offers for
corroboration**: whether third parties have something consistent to repeat.

| Check | Rule | Guards |
| :--- | :--- | :--- |
| `G3.CORROB.NO_CITABLE_PRIMARY_SOURCE` | the about page states fewer than 2 canonical fact types (founding date, location, leadership, headcount, category definition); or no about page exists and a requested about path returned no page | only reported when an about page exists or was actually requested |
| `G3.CORROB.NO_PRESS_KIT` | none of at least 3 fetched pages is, or links to, a press, media, newsroom or brand-assets page | not applied to local businesses or ambiguous profiles |
| `G3.CORROB.FACTS_ONLY_IN_MARKETING_COPY` | on the about page, canonical facts appear only in paragraphs that contain promotional superlatives, and no table or list states them | needs facts present at all |
| `G3.CORROB.NO_AUTHOR_IDENTITY` | at least half of 3+ article pages give `author` as a bare string or not at all — no `Person` with `url`, `@id` or `sameAs`, and no linked author profile | template-collapsed |
| `G3.CORROB.NO_EXTERNAL_REFERENCES` | at least half of 3+ article or documentation pages of 300+ words that make quantified claims (%, "x times", millions …) have no outbound link in the main region | — |

**The highest-leverage fix for most brands** is a canonical fact sheet on the about page:
legal and common names, a one-sentence definition, founding year, headquarters, leadership,
headcount band, category and markets — as a definition list *and* as `Organization` markup,
linked from every footer. It is what independent sources need in order to agree.
