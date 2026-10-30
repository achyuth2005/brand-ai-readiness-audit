# Quotability Rules

## Mechanism

> "The pages that get picked as sources tend to be the ones a machine could easily reach,
> easily read, and easily quote a clear fact from." — brief, Appendix B

Retrieval operates on **passages**. A page is split into chunks, one is selected, and it is
reproduced more or less standalone. Prose written to flow top-to-bottom for a human reader
can therefore be systematically unquotable, even on a page that passes every earlier gate.
Answerability is not named in the brief's example failure list, but its appendix argues
directly for it, and most audits stop at crawlability and markup.

## Calibrated honesty

Everything in this skill is a **content-form heuristic with published vocabulary and
thresholds**. None of it measures how any particular assistant behaves. The mechanisms are
sound properties of chunked retrieval; the thresholds are defaults with stated rationales in
`config/thresholds.json`, meant to be recalibrated against real sites, never tuned to one.

## Language scope

The lexicons are English. A page's language comes from `<html lang>`, then `og:locale`, then
a language meta tag. An undeclared page is treated as English only when at least 8% of its
word tokens are high-frequency English function words. Any other page is **skipped and
recorded** — judging French prose with an English pronoun list would produce nonsense
(rule R6).

## What counts as the page's prose

Main-region `p`, `li` and `dd` elements only. Excluded: navigation, footers, hidden elements,
anything inside `blockquote`, `figure`, `nav` or `footer`, and anything inside a container whose
class names a testimonial, quote, review or citation — a customer quote beginning "It changed
how we work" is not the brand's own writing. A list item containing a paragraph is scored once.

## `G3.ANSW.LOW_SELF_CONTAINMENT`

### Mechanism
"It also integrates with over 40 tools" is useless alone; its referent was in the previous
chunk. "Acme integrates with over 40 tools" is directly quotable. This is a mechanical property
of chunked retrieval, not a style preference, and no other check in the catalogue sees it.

### Algorithm
A sentence (4+ words, abbreviation-aware segmentation) is **dependent** when both hold:
1. It opens with a pronoun (`it`, `this`, `they`, `these`, `we`, `such` …), a bare connective
   (`also`, `however`, `moreover`, `therefore`, `plus`, `but` …), or a demonstrative phrase
   (`this means`, `these features`, `that approach` …).
2. It names nothing: no mention of the brand, and no proper noun later in the sentence.

`self_containment = 1 − dependent ÷ sentences` per page, over pages with at least 12 sentences.

### Fires when
At least 3 pages were measured, at least 2 fall below **0.60**, and failing pages are at least
half of those measured.

### Guards
- "We" is accepted under a section heading that names the brand.
- Short pages are exempt; ratios over few sentences are noise.
- Pages within 20% above the floor become near misses: not defects, but grounded advice — above
  roughly 0.75, passages survive retrieval materially better.

### Evidence and fix
Three verbatim dependent sentences with the heading they sit under, plus the ratio. Rewrite
section- and paragraph-opening sentences to name their subject; add "every paragraph must make
sense if it is the only paragraph someone reads" to the style guide.

## `G3.ANSW.NO_DEFINITIONAL_SENTENCE` — highest value in this skill

### Mechanism
"What is <brand>?" is the single most common query shape about a company, and it is answered by
quoting a definitional sentence. When none exists in plain text, the assistant synthesises one
from taglines and marketing copy — which is how brands get **misrepresented** rather than merely
missed. This check explains a large share of "the AI describes us wrong".

### Algorithm
Look in the first 3 paragraphs of the homepage, the first 3 of the about page when one exists,
and `Organization.description`. Accept a sentence only when the brand appears within its first
40 characters **as the subject**, followed by a defining predicate (`is`, `are`, `provides`,
`offers`, `builds`, `helps`, `enables`, `specialises in`, `was founded` …) or an appositive
(`<Brand>, the …`).

### Guards
- Skipped when the brand name cannot be inferred from any naming surface.
- A definition present only in the meta description is noted in the evidence and does not
  count: a meta description is not visible page text.

### Fix
One plain sentence — category, audience, differentiator — in the first visible paragraph of the
homepage, repeated as the first sentence of the about page, mirrored verbatim in
`Organization.description` and the press boilerplate. **Verify** with JavaScript disabled, then
by asking an assistant "What is <brand>?" after re-indexing.

## `G3.ANSW.FLUFF_DOMINANT`

A sentence is **fluff** when it contains an unquantified superlative (`world-class`,
`cutting-edge`, `seamless`, `industry-leading`, `game-changing`, `best-in-class`, `robust`,
`unlock`, `empower` …) **and** is not factual. A sentence is **factual** when it contains a
digit, a currency symbol, a proper noun after its first word, or defines the brand.

**Fires when both:** fluff share above 0.35 **and** factual share below 0.20, on pages with at
least 12 sentences. Requiring both means a page that is promotional *and* factual is never
flagged. Confidence is capped at medium: this is a content-strategy finding, not a technical
fault.
**Fix:** replace each superlative with the evidence behind it — "99.95% uptime over the last 12
months" instead of "industry-leading reliability" — or cut it.

## `G3.ANSW.NO_ANCHOR_ADDRESSABILITY`

**Fires on:** pages over 1,200 words with 3+ h2/h3 headings, **none** of which is addressable — no
`id` on the heading, on an enclosing `<section>`, or on an anchor inside it.
**Mechanism:** a heading id makes a fragment citable. Without one, the whole page must be cited or
nothing, and the relevant passage is diluted by everything around it.
**Fix:** stable slug ids generated from heading text, plus an in-page table of contents.

## `G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE`

**Fires on** pages of 120+ words when the median paragraph exceeds 90 words (over 3+ paragraphs),
or when the longest heading-free run exceeds 600 words.
**Mechanism:** chunkers split on structure; without boundaries, passages straddle topics. The
same structure is unscannable for a reader, which is why this is corroborated by the engagement
skill's readability checks when both fire.

## `G3.ANSW.KEY_FACTS_UNLISTED`

**Fires when** 3+ paragraph sentences state specifications (a number with a unit, `N x N`),
prices, or sequential steps ("Step 2", "first,", "then,") and the main region contains no table,
definition list, or list of 3+ items.
**Mechanism:** a fact inside a clause must be parsed out; a label and value in a table row survive
extraction and summarisation intact.

## `G3.ANSW.PRONOUN_ONLY_ENTITY_REFERENCE`

**Applies to** content pages of 120+ words, excluding the homepage and product pages (a product
page is anchored by its product name), and only when the brand name was inferred with at least
medium confidence.
**Fires when** at least half of 3+ such pages never name the brand in their main text or h1.
Evidence notes when the name appears only in the title tag, logo or navigation.
**Mechanism:** an extracted passage carries only its own words. With no entity anchor, even a
correctly quoted fact cannot be attributed to the brand.
**Fix:** name the brand in the first paragraph ("Acme's scheduler …" rather than "Our
scheduler …"), and again in sections likely to be quoted alone.
