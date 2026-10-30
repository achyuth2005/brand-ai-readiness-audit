# Orientation and Context Retention

## The deep, context-free arrival

A visitor sent to a site by an AI assistant differs from one who browsed there:

- They land on a **deep page**, chosen by the assistant to satisfy one specific intent.
- They have **no journey behind them** — no homepage, no navigation path, no memory of the brand.
- They carry an expectation set by the assistant's answer and its link preview.
- They have **near-zero patience**, and most arrive on a phone.

The brief names the failures — "weak on-site orientation / no context retention". This skill
is organised around that arrival rather than around generic UX heuristics, which is what makes
it a coherent evidence domain rather than a grab bag.

## The three-question test

Every sampled page should answer, above the fold and without interaction:

1. **What is this?** — a real heading and a statement of the offer.
2. **Who is it for?** — a category and audience in plain words.
3. **What do I do next?** — one specific action.

## The above-the-fold proxy — stated, not implied

There is no layout engine in this package, so no real fold is measured. "Above the fold" means
the **first 120 elements of the main content region in document order**
(`engagement.above_fold_nodes`). The proxy is reasonable for server-rendered pages and is named
as a proxy in every finding it produces.

## Client-rendered shells are not judged

When a page's raw response is an empty client-rendered shell — main text under 600 characters
and an empty framework mount node — a visitor with JavaScript sees an h1, calls to action and
links that the response lacks. Judging orientation from the shell would report the render gap a
second time, disguised as an engagement problem. The orientation, conversion and readability
checks therefore skip such pages, record that they did, and leave the gap to
`G1.RENDER.CSR_CONTENT_GAP`. Checks that read what the server sends regardless of rendering —
viewport meta, head scripts, response weight, declared overlay CSS — still run.

## Orientation checks

| Check | Fires when | Scope rule |
| :--- | :--- | :--- |
| `GE.ORIENT.NO_H1` | no visible h1 | any template |
| `GE.ORIENT.MULTIPLE_H1` | 2+ visible h1 elements | template-bound only |
| `GE.ORIENT.HEADING_HIERARCHY_SKIP` | within the main region (2+ headings), a heading jumps more than one level below its predecessor, e.g. h2 → h4 | template-bound only |
| `GE.ORIENT.GENERIC_H1` | the first h1 is a placeholder ("Home", "Welcome", "Untitled", "Index" …) or exactly the brand name | any |
| `GE.ORIENT.NO_VALUE_PROP` | on the homepage, service or pricing pages: no sentence of 6+ words containing a category noun (platform, software, service, agency, clinic, restaurant, tool …) in the above-fold proxy or the first three headings | any |
| `GE.ORIENT.NO_PRIMARY_CTA` | on the homepage, service, pricing or product pages: no **specific** call to action in the above-fold proxy — a link or button whose label starts with an action verb and has an object ("Start a free trial", "Book a demo") | any |
| `GE.ORIENT.CTA_COMPETITION` | 5+ distinct targets of specific calls to action in the above-fold proxy | medium confidence |
| `GE.ORIENT.TITLE_MISMATCH` | the title tag (with brand words removed) and the h1 share under 20% of their content words, each having at least 2 | template-bound only; medium confidence |
| `GE.ORIENT.DEEP_PAGE_NO_CONTEXT` | at path depth 2+: no breadcrumb (visual pattern, `nav[aria-label*=readcrumb]`, or `BreadcrumbList`), no link to any ancestor path, and no section label (eyebrow, kicker, category, overline) above the fold | any |

### Why these guards
- Non-specific labels — "Learn more", "Read more", "Click here", "Submit", "Continue" — are what
  make `NO_PRIMARY_CTA` fire *despite* links being present. A link with no object is not a next step.
- `MULTIPLE_H1`, `HEADING_HIERARCHY_SKIP` and `TITLE_MISMATCH` are real but individually minor;
  requiring template scope keeps a single odd page out of the report.
- `DEEP_PAGE_NO_CONTEXT` accepts any one of three context signals. A page with a parent link but
  no breadcrumb has context.

`DEEP_PAGE_NO_CONTEXT` is the literal "no context retention" failure. It takes primary fact weight
because the visitor it affects is exactly the one an assistant sends.

## Conversion path

**`GE.CONV.PATH_UNCLEAR`** — on product and pricing pages: no purchase, cart, enquiry, quote,
contact, signup, trial or booking control anywhere on the page, and no form in the main region.
The page's own intent implies a next step, and the visitor's intent has nowhere to go.

**`GE.CONV.FORM_FRICTION`** — a main-region form with more than one field requires more than 6
fields (`engagement.max_form_required_fields`).

## Scope consolidation

Per-page results collapse to template scope (rule R3). When one defect appears across several
templates and together covers at least 60% of sampled pages, it is reported once, site-wide — a
missing element in a shared layout is one fix, not one finding per template.
