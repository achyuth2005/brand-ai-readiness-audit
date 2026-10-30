# Structured Data Validation Rules

## Mechanism

Appendix A's third step is picking out the specific fact. Typed markup states each fact once,
unambiguously, in a form that survives chunking, translation and summarisation. Its failures
are silent: consumers discard what they cannot use without telling anyone, so the author sees
markup in the source and believes it works.

## The validity ladder

Each rung is a separate check because each has a separate fix.

```
L0 present?      G2.SD.ABSENT                    (see schema-profiles.md)
L1 parses?       G2.SD.JSONLD_SYNTAX_ERROR
L2 typed?        G2.SD.MISSING_CONTEXT, G2.SD.UNKNOWN_TYPE
L3 right type?   G2.SD.TYPE_MISMATCH             (see schema-profiles.md)
L4 complete?     G2.SD.REQUIRED_PROP_MISSING, RECOMMENDED_PROP_MISSING
L5 well-formed?  G2.SD.PROP_TYPE_INVALID
L6 connected?    G2.SD.ENTITY_GRAPH_UNLINKED
L7 truthful?     G2.SD.MARKUP_TEXT_CONTRADICTION
```

## Extraction

- JSON-LD is always parsed **per block** with a JSON parser — never evaluated — so one broken
  block cannot hide the others, and a syntax error is a finding with a location.
- Every container shape is flattened to one node list before any property check: a single
  object, a root array, `{"@graph": [...]}`, and nodes nested inside properties
  (`offers`, `author`, `itemListElement`) at any depth. Skipping this makes coverage checks
  pass on markup they never inspected.
- `@type` is normalised from strings, lists and fully-qualified forms
  (`https://schema.org/Product`, `schema:Product`).
- Microdata (`itemscope`/`itemtype`/`itemprop`) and RDFa-lite (`typeof`/`property`) are
  extracted too. When `extruct` is installed, anything it finds that the built-in path missed
  is merged in; the built-in JSON-LD parse is kept because it carries line numbers.

## L1 — `G2.SD.JSONLD_SYNTAX_ERROR`

**Evidence:** the parser's own message, line and column, and a fragment around the failure.
Common real causes: a trailing comma, an unescaped quote inserted by CMS templating, a raw
newline inside a string.
**Fix:** generate JSON-LD with a serializer instead of string concatenation; escape `</` in
values; parse every emitted block in CI.
**Masks:** property-level G2 findings on the same pages.

## L2 — context and type

**`G2.SD.MISSING_CONTEXT`** — a parsed block with nodes but no `@context` containing
`schema.org`. Keys resolve through the context, so without it the block is valid JSON with no
schema.org meaning.

**`G2.SD.UNKNOWN_TYPE`** — reported **only** when the type is absent from the bundled
recognition list **and** is a near-miss of a known type: an entry in the correction table
(`Organisation` → `Organization`, `LocalBussiness` → `LocalBusiness`, `faq` → `FAQPage`), a
case-only difference, or within two edits. A type that is simply uncommon produces silence
(rule R6) — schema.org is large, and shipping a partial vocabulary while treating absences as
errors would flag valid markup. Consumers do not fuzzy-match type names, so a misspelled type
voids every property on its node.

## L5 — `G2.SD.PROP_TYPE_INVALID`

| Rule | Invalid example | Valid |
| :--- | :--- | :--- |
| `price`, `lowPrice`, `highPrice` are plain decimals | `"£49.00"`, `"1,299"` | `"49.00"`, `"1299.00"` |
| `priceCurrency` is ISO 4217 | `"$"`, `"pounds"` | `"USD"`, `"GBP"` |
| `availability` is an ItemAvailability member | `"Available"`, `"yes"` | `"https://schema.org/InStock"` |
| dates are ISO-8601 (`datePublished`, `dateModified`, `startDate`, `endDate`, `datePosted`, `validThrough`, `priceValidUntil`, `foundingDate`, `uploadDate`) | `"March 3, 2025"` | `"2025-03-03"`, `"2025-03-03T09:00:00Z"` |
| `ratingValue` lies within `0 … bestRating` (default 5) | `4.8` on a declared scale of 1 | `4.8` with `bestRating` 5 |

## L6 — `G2.SD.ENTITY_GRAPH_UNLINKED`

**Needs:** at least 3 pages carrying JSON-LD.
**Fires when:** no Organization-type node is published at all while content entities exist;
Organization nodes exist but none has an `@id`; or an `@id` exists but fewer than half of the
content entities (Product, Article, WebSite, WebPage, Service, Event …) reference it through
`publisher`, `brand`, `provider`, `author`, `manufacturer`, `seller`, `organizer` or `isPartOf`.
**Mechanism:** the `@id` is what tells a consumer that the publisher of an article, the brand
of a product and the organisation on the homepage are one entity. Without it every page
publishes a separate anonymous candidate.

```json
{ "@type": "Organization", "@id": "https://example.com/#organization", "name": "Example" }
{ "@type": "Product", "name": "Widget", "brand": { "@id": "https://example.com/#organization" } }
```

## L7 — `G2.SD.MARKUP_TEXT_CONTRADICTION`

### Mechanism
Markup that disagrees with the visible page is worse than no markup. A consumer trusting the
typed value quotes it — a stale price or a wrong stock status repeated to a customer — and
disagreement between markup and visible content is itself a spam signal that can cost
rich-result eligibility entirely.

### Fields and normalisation

| Field | Visible value read from | Normalisation |
| :--- | :--- | :--- |
| price | elements whose class, id or `itemprop` names price, amount, cost or sale | currency symbols and codes resolved; thousands separators and comma decimals handled; tolerance 0.005; `$`, `¥`, `kr` treated as ambiguous and compatible with any currency rather than guessed |
| availability | elements whose class or id names stock, availability or inventory | prose mapped to the enum; compatible pairs accepted (InStock/LimitedAvailability, OutOfStock/SoldOut, PreOrder/PreSale) |
| telephone | `tel:` links | digits only; the last 7 significant digits must agree, so `+44 20 …` and `020 …` match |
| datePublished | `<time>` elements marked as published, or inside date/byline containers | day granularity; a match to `dateModified` is accepted, since a visible "updated" date legitimately differs |
| ratingValue | elements whose class or id names rating, stars or score, in `N/M` or `N out of M` form | tolerance 0.1 |

### False-positive guards
- **Raw response only.** If the visible value exists only after rendering, that is a G1
  finding; it is never blamed on the markup.
- **A competing value under a labelled element is required.** No labelled visible value means
  uncorroborated — silence, not a contradiction.
- **Any offer matching corroborates.** Variants, ranges and configurators mark up several
  offers; a single mismatch among them is not a contradiction.
- **Personalisation caps confidence.** A currency or region switcher on the page, or several
  different visible phone numbers, caps confidence at medium and the evidence says why.
- **Unparseable is silent (rule R6).** If either side cannot be parsed confidently, nothing is
  emitted.

### Fix — a process fix, not an edit
Bind the markup and the visible value to the same data field in the template, so they cannot
diverge, and add a build-time assertion comparing them for a sample of items. A one-off
correction regresses the next time a price changes.

## Other checks

| Check | Rule |
| :--- | :--- |
| `G2.SD.DUPLICATE_CONFLICTING_ENTITIES` | one page carries Organization nodes whose names do not reduce to one another (after case, punctuation and legal-suffix folding), or whose `url` values differ — usually a theme or agency template emitting its own organisation |
| `G2.SD.FAQ_MARKUP_MISMATCH` | fewer than half of an `FAQPage`'s `acceptedAnswer` texts appear in the page's visible text |
| `G2.SD.SD_ONLY_IN_RENDERED_DOM` | entity types present in a rendered DOM and absent from the raw response — markup injected by a tag manager or client code; skipped and recorded when no render exists |
| `G2.SD.MICRODATA_ONLY_LEGACY` | observation only: 3+ pages carry Microdata or RDFa and no JSON-LD. Valid, but JSON-LD is independent of HTML structure, so a redesign cannot break it |

## Canonicalisation

Duplicate URLs split citation authority: links and references accumulate on several URLs
instead of one.

| Check | Rule | Guard |
| :--- | :--- | :--- |
| `G2.CANONICAL.MISSING` | no `rel="canonical"` on a template-bound group of 3+ content pages | — |
| `G2.CANONICAL.NON_SELF_REFERENTIAL` | a served page's canonical names a *different same-site* URL | cross-domain canonicals (syndication) and canonicals from a parameter variant to its clean path are correct and excluded; paginated pages are handled below |
| `G2.CANONICAL.CHAIN_OR_LOOP` | the canonical target is itself in the crawl and declares a different canonical | — |
| `G2.CANONICAL.TO_NON_200` | the canonical target was fetched and returned 3xx or an error without resolving | — |
| `G2.CANONICAL.VS_OGURL_CONFLICT` | `og:url` names a different path from the canonical | host and trailing-slash differences are ignored |
| `G2.CANONICAL.PARAM_DUPLICATES` | URLs differing only by query parameters returned **byte-identical** responses and none declares a canonical | identity is exact: the ledger is content-addressed, so identical bodies share one hash |
| `G2.CANONICAL.HREFLANG_ASYMMETRIC` | page A lists B as an hreflang alternate, B was crawled, and B does not list A | self-references are ignored |
| `G2.CANONICAL.PAGINATION_UNMARKED` | a paginated page canonicalises to another page (typically page 1) instead of itself | — |
