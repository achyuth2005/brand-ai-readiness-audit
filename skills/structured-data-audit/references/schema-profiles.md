# Expected Structured Data by Detected Site Profile

## The generalization mechanism

This skill never asks "does this page have `Product` markup?". It asks: **given the evidence
says this is a product page, does it have what a product page needs?** Expectations are
selected by the site profile, and the profile is classified from on-page evidence only —
structured-data types, path classes, page text and interaction controls. The domain name is
never an input. That is what lets the same checks run correctly on a site nobody has seen.

## Profiles and expectations

Source of truth: `PROFILE_EXPECTED_TYPES` in `lib/auditkit/lexicons.py` and
`REQUIRED_BY_TYPE` / `RECOMMENDED_BY_TYPE` in `scripts/analyze_structured_data.py`.

| Profile | Expected types | Required (missing → finding) | Recommended |
| :--- | :--- | :--- | :--- |
| any site | `Organization`, `WebSite` | `name`, `url` | `logo`, `description`, `sameAs`, `potentialAction` |
| ecommerce | `Product` + `Offer` | Product: `name`, `image`, `offers`; Offer: `price`, `priceCurrency`, `availability` | `brand`, `description`, `sku`, `aggregateRating`; Offer `url`, `priceValidUntil`, `shippingDetails`, `hasMerchantReturnPolicy` |
| publisher | `Article`, `BlogPosting`, `NewsArticle` | `headline`, `datePublished`, `author` | `dateModified`, `publisher`, `image` |
| local_business | `LocalBusiness` or a subtype | `name`, `address`, `telephone` | `openingHoursSpecification`, `geo`, `url`, `image` |
| saas_software | `SoftwareApplication`, `Service` | `name` | `offers`, `applicationCategory`, `operatingSystem` |
| docs_knowledge | `TechArticle`, `HowTo`, `FAQPage` | `headline` (TechArticle) | `dateModified`, `author` |
| events | `Event` | `name`, `startDate`, `location` | `endDate`, `offers`, `eventStatus`, `image` |
| any with Q&A | `FAQPage` | `mainEntity` | — |
| deep pages | `BreadcrumbList` | `itemListElement` | — |

Requirements apply to **every node of a type that is present**, whatever the profile: a
`BlogPosting` without `author` is incomplete on any site. The profile decides which types a
page class is *expected* to carry.

A `Product` without `offers` is not flagged on sites that are not profiled as e-commerce;
catalogues and manufacturers legitimately describe products they do not sell online.

## Ambiguous profiles

When the top two profile scores fall within 15% of each other
(`profile.ambiguity_margin`), the site is classified `ambiguous`, and these checks are
skipped with a recorded reason: `G2.SD.TYPE_MISMATCH`, `G2.SD.REQUIRED_PROP_MISSING` and
`G2.SD.RECOMMENDED_PROP_MISSING` are replaced by type-agnostic checks only. A confident wrong
profile would produce a page of confident wrong findings; declining to guess is a feature.

## L0 — `G2.SD.ABSENT`

**Signal:** a template cluster whose sampled pages carry no JSON-LD, Microdata or RDFa of any
kind — and no JSON-LD block that failed to parse (that is `JSONLD_SYNTAX_ERROR`, a different
problem with a different fix).
**Guard (rule R2):** needs at least 3 pages (`structured_data.min_pages_for_absent_check`).
**Masked by:** `G1.RENDER.CSR_CONTENT_GAP` on the same template (there is no text to mark up)
and by a JSON-LD syntax error on the same pages.

## L3 — `G2.SD.TYPE_MISMATCH`

**Signal:** a page evidenced as a given class carries structured data, but none of the types
that express that class.

The page class is established **without using the markup**, because asking "is the markup
the right type" with the markup as evidence would be circular:

| Evidenced class | Requires |
| :--- | :--- |
| product | product path class, an e-commerce profile, a price in the main text, **and** an add-to-cart control |
| article | article path class, a publisher profile, **and** a `<time datetime>`, `rel="author"` or byline |
| faq | FAQ path class **and** at least three question headings |

Expected types for each class: product → `Product`, `ProductGroup`, `IndividualProduct`,
`SoftwareApplication`, …; article → `Article`, `NewsArticle`, `BlogPosting`, `TechArticle`, …;
faq → `FAQPage`, `QAPage`.

**Fix:** add the subject's own node alongside any `WebPage` node and link them with
`mainEntity` / `mainEntityOfPage`.

## L4 — completeness

**`G2.SD.REQUIRED_PROP_MISSING`** — reported for any page and template. Evidence names the node's
JSON path and the missing properties.

**`G2.SD.RECOMMENDED_PROP_MISSING`** — reported **only at template scope**, and only when no
required property is missing on the same node. One page lacking a recommended property is
not worth a reader's attention; a template that never emits it is one edit.

Empty strings, empty lists and empty objects count as missing. Nested values are resolved
through dotted paths, so `offers.price` is found whether `offers` is an object, a list or a
nested node.

## Deep pages and site search

**`G2.SD.BREADCRUMB_ABSENT_ON_DEEP_PAGES`** — at least 3 sampled pages at path depth 2 or more,
and **none** carries `BreadcrumbList`. Evidence notes whether a visual breadcrumb already
exists, in which case only the markup is missing.

**`G2.SD.SEARCHACTION_MISSING`** — the homepage has a search input (`type=search`,
`role=search`, or a `q`/`s`/`query` field) but no `WebSite` node with `potentialAction`.

```json
{
  "@context": "https://schema.org",
  "@type": "WebSite",
  "@id": "https://example.com/#website",
  "url": "https://example.com/",
  "name": "Example",
  "publisher": { "@id": "https://example.com/#organization" },
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://example.com/search?q={search_term_string}",
    "query-input": "required name=search_term_string"
  }
}
```
