# Facts Locked in Non-Text Carriers

## Mechanism

> "The more explicitly and unambiguously a fact is stated in plain, readable text, the more
> likely a machine is to extract it correctly; the more it's implied, buried, or locked
> inside something non-textual, the more likely it's missed." — brief, Appendix C

A price inside a hero image, opening hours in a JPEG, a specification table exported as a
PNG, a spec sheet only available as a PDF: every one is invisible to text extraction,
however well the page is crawled.

## Why there is no OCR

Optical character recognition is deliberately not used. It would add model weights against
the 50 MB limit, cost seconds per image against the time budget, and produce
non-deterministic output. More importantly it would point at the wrong fix: the goal is not
for the auditor to read the image, it is for the *site* to publish the fact as text.

Instead the skill detects the **structural pattern** of a fact-bearing section that has no
text — which is cheap, deterministic, and exactly what the fix changes.

## Fact classes

Each class has label vocabulary (what a heading announcing it says) and value shapes (what
proves it is present as text). Both live in `lib/auditkit/lexicons.py`.

| Class | Example labels | Proven present by |
| :--- | :--- | :--- |
| price | Pricing, Plans, Rates, Fees | a currency symbol or code beside a number; "per month", "free" |
| availability | Availability, Stock, Delivery | "in stock", "sold out", "pre-order", "ships in N" |
| hours | Opening hours, Business hours | weekday names near digits, `9am`, `17:00`, "24/7", "closed" |
| address | Address, Find us, Directions | a street pattern, a UK postcode, a US ZIP |
| phone | Call us, Telephone | a phone-shaped digit sequence |
| spec | Specifications, Dimensions, Technical data | a number with a unit (mm, kg, GB, W …), `N x N` |
| comparison | Compare, Which plan | "included", "unlimited", yes/no cells |
| ingredient | Ingredients, Nutrition, Allergens | g/mg/kcal/% values, "contains", "free from" |
| date | Dates, Agenda, Key dates | a year, a day-month pattern |
| company_facts | Our story, Key facts, By the numbers | "founded in YYYY", "N employees" |

Carriers: `<img>`, inline `<svg>` drawn as paths, `<iframe>`, a linked PDF.

## `G1.NONTEXT.FACT_LOCKED_IN_MEDIA`

### Algorithm (per page)
1. For each h2/h3 section whose heading matches a fact-class label (longest label wins):
2. Measure the section: word count, content-sized images, iframes, PDF links, path-drawn
   SVGs, tables, definition lists and lists.
3. **Fire only if all hold:** fewer than 25 words (`non_text.fact_region_max_words`); at least
   one carrier; no table, definition list or list; and **no value of that fact class appears
   anywhere in the page's visible text or its structured data.**
4. Strengthen to high confidence when two of: an image whose filename or alt text names the
   fact class (`pricing-table.png`); a declared size of at least 200px on both sides; a PDF
   link in the section; an iframe in the section.

### False-positive guards
- Decorative images (`alt=""` or `role="presentation"`) and icon-sized or sprite images are
  excluded.
- If the fact class **is** present in the page text or markup, an image repeating it is good
  practice, not a defect.
- Logos, team photos and hero decoration carry no fact-class label and are out of scope by
  construction.
- **Evidence is phrased as absence.** The audit never claims an image *contains* a price,
  because it has not read the image:

> 1/1 sampled pages contain a section headed "Pricing" that carries 2 words of text
> (threshold 25), contains 1 content-sized image(s), and contains no table, definition list
> or list. No value matching the price fact class appears anywhere in the page's visible
> text or its structured data … This audit does not read images, so it makes no claim about
> what the image contains — only that the fact is not available as text anywhere on the page.

That is the anti-hallucination discipline of the whole package in miniature: describe the
observation, never the inference dressed as an observation.

### Fix
Publish the values as an HTML `<table>` or `<dl>` beside the image (keep the image if the
design needs it); give the image descriptive alt text; mirror the values into structured
data where a schema.org property exists. **Verify:** a known value appears in the raw HTML.

## Companion checks

| Check | Signal | Guard |
| :--- | :--- | :--- |
| `G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES` | more than 30% of main-region **informative** images have no `alt` attribute at all, on pages with at least 4 informative images | `alt=""` is the correct declaration of decoration and is never flagged; absent ≠ empty |
| `G1.NONTEXT.ALT_NON_DESCRIPTIVE` | alt text is a placeholder word ("image", "photo", "logo" …), a filename, a dimension string, or 1–2 characters | only among informative images |
| `G1.NONTEXT.SVG_TEXT_AS_PATHS` | an inline SVG in the main region with 8+ drawing paths and no `<title>` or `<text>`, under a fact-class heading, where that fact class is absent from the page text | icons have few paths and are excluded |
| `G1.NONTEXT.PDF_ONLY_FACT` | a main-region link to a PDF whose anchor text names a fact class that appears nowhere in the page text | a PDF offered as a supplement to on-page facts is correct |
| `G1.NONTEXT.IFRAME_LOCKED_CONTENT` | a cross-origin iframe in the main region of a page whose own main text is under 400 characters | maps and videos beside substantive text are not flagged |
| `G1.NONTEXT.VIDEO_WITHOUT_TRANSCRIPT` | `<video>`/`<audio>` with no `<track>`, no nearby transcript or captions link, and no `transcript` on a `VideoObject`/`AudioObject` | — |

A transcript is usually the single largest body of quotable text on a media-led page; a
captions track alone serves accessibility but a published transcript serves extraction too.
