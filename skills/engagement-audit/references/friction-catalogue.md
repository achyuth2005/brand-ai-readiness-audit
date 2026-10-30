# Friction and Navigation Catalogue

## What static CSS can and cannot tell us

Overlay, scroll-lock, sticky-chrome, font-display and contrast checks read **statically declared
CSS**: inline `style` attributes, `<style>` blocks, and up to 5 same-site stylesheets fetched
during the crawl. There is no cascade engine and no computed style. Where a linked stylesheet
could not be read, the check caps its confidence and says so; where a colour depends on the
cascade (`var()`, `currentColor`, gradients), contrast is skipped rather than guessed (rule R6).

## `GE.FRICTION.CONTENT_GATING_OVERLAY` — the most false-positive-prone check

Most modal markup on the web is correctly hidden until a script reveals it. Flagging it would
bury the report. So **all three** signals are required, and a fourth is needed for high
confidence:

| # | Signal |
| :-: | :--- |
| 1 | the element is named or roled as an overlay: `role="dialog"`/`alertdialog`, `aria-modal="true"`, an open `<dialog>`, or a class/id containing modal, overlay, popup, interstitial, lightbox, paywall, newsletter, subscribe, takeover … |
| 2 | it is **not** hidden: no `hidden` attribute, no `aria-hidden="true"`, no hidden-by-convention class (`hidden`, `is-hidden`, `d-none`, `sr-only`, `collapsed`, `fade` …), and no declared `display:none`, `visibility:hidden`, `opacity:0` or `scale(0)` |
| 3 | it declares **covering geometry**: `position` fixed, sticky or absolute; full inset (three zero edges or `inset: 0`) or 100% / 100vw / 100vh size; and a z-index of 100+ or fixed positioning |
| 4 | the page locks scrolling: `overflow: hidden` on `body` or `html` |

Consent containers are excluded here and handled below. Confidence is high only with the scroll
lock **and** complete stylesheets; otherwise medium.

**Fix:** never show a covering overlay on load. Trigger promotional modals on exit intent or after
engagement, keep them hidden by default in HTML and CSS, and never lock scrolling behind them.

## Other blocking friction

| Check | Fires when | Notes |
| :--- | :--- | :--- |
| `GE.FRICTION.COOKIE_WALL_BLOCKS_CONTENT` | a consent-named container (cookie, consent, gdpr, cmp, and common consent-platform names) is visible by default **and** scrolling is locked | high confidence when it also covers the viewport and CSS is complete. Also blocks non-interactive fetchers. |
| `GE.FRICTION.FORCED_REGISTRATION` | registration-wall wording in the main region, a main-region form with an email or password field, and main text under 60% of the template median | corroborated by `G0.GATING.*` when both fire |
| `GE.FRICTION.NO_VIEWPORT_META` | no `<meta name="viewport">` | read from the served head, so it runs on client-rendered shells too; takes primary weight — every mobile arrival gets a shrunken desktop layout |
| `GE.FRICTION.TAB_TRAP_CONTENT` | 2+ `role="tab"` and 2+ `role="tabpanel"`, with at least one panel empty (under 40 characters) in the raw document | medium confidence; corroborated by `G1.RENDER.BODY_REQUIRES_INTERACTION` |

These take primary fact weight in severity: a visitor who cannot reach the content is as lost as
one who never arrived.

## Performance and stability risks

Only the subset that is defensible without a lab, and each with a behavioural meaning.

| Check | Fires when | Notes |
| :--- | :--- | :--- |
| `GE.FRICTION.CLS_RISK` | more than 40% of 3+ main-region images declare neither `width`+`height` nor an inline `aspect-ratio` | capped at medium: dimensions set only in external CSS are invisible to this check |
| `GE.FRICTION.RENDER_BLOCKING_HEAD` | more than 3 external scripts in `<head>` without `defer`, `async` or `type="module"` | template-bound |
| `GE.FRICTION.HEAVY_PAGE` | the HTML document alone exceeds 2 MB (declared or stored length) | usually inlined data or markup bloat |
| `GE.FRICTION.AUTOPLAY_MEDIA` | `<video>` or `<audio>` with `autoplay` and without `muted` | — |
| `GE.FRICTION.LAYOUT_SHIFT_FONTS` | an `@font-face` rule without `font-display` | template-bound |
| `GE.FRICTION.INTRUSIVE_STICKY` | non-hidden fixed or sticky elements with **declared** heights stack above 200px | elements without a resolvable height are not guessed; medium confidence |

## Accessibility defects that are engagement defects

They measurably increase abandonment, including on the site's own conversion path.

| Check | Fires when | Guard |
| :--- | :--- | :--- |
| `GE.FRICTION.NO_SKIP_LINK` | navigation holds 15+ links and no in-page anchor whose text says "skip" or whose class matches a skip-link pattern appears among the first 30 body elements | small navigation does not need one |
| `GE.FRICTION.UNLABELLED_FORM_FIELDS` | a visible form field (hidden, submit and button inputs excluded) has no programmatic label | four mechanisms all count as labelled: a wrapping `<label>`, `<label for>`, `aria-label`, `aria-labelledby` (and `title`) |
| `GE.FRICTION.LOW_CONTRAST_DECLARED` | one inline style or one CSS rule declares **both** a literal text colour and a literal background colour whose WCAG ratio is under 4.5:1 | only literal hex, rgb(), hsl() and named colours; media-query rules skipped; template-bound |

## Navigation and context retention — computed over the link graph

| Check | Fires when | Guard |
| :--- | :--- | :--- |
| `GE.NAV.DEAD_END_PAGE` | a non-homepage content page of 120+ words has no same-site link in its main region | navigation links do not count — the question is whether the content offers a next step |
| `GE.NAV.ORPHAN_PAGES` | of 3+ fetched sitemap pages, some receive no internal link from any crawled page | measured over the crawled sample, and stated as such; **skipped** when any crawled page is a client-rendered shell, because shells carry no links and would inflate orphans |
| `GE.NAV.NO_RELATED_CONTENT` | an article or product page has no related, recommended, next or previous module (by class/id pattern) and no `rel="next"`/`"prev"` | template-bound only |
| `GE.NAV.NO_SITE_SEARCH` | the sitemap lists over 200 URLs, and the homepage has no search input and no `SearchAction` | not applied to brochure or ambiguous profiles |
| `GE.NAV.NAV_ONLY_IN_JS` | the homepage's raw HTML has fewer than 3 internal links, **and** either a framework mount node is empty or the rendered DOM holds 5+ more links | high confidence with a rendered DOM, medium without; corroborated by the render gap |
| `GE.NAV.EXCESSIVE_DEPTH` | at least half of sampled product and pricing pages were first reached at click depth 4+ | requires recorded click depth; skipped otherwise. Sitemap-only pages have no depth and are not counted |
| `GE.NAV.BROKEN_INTERNAL_LINKS` | of 5+ internal link targets requested (pages plus sampled HEADs), more than 10% returned 4xx/5xx | reported as k/n with the linking page for each example |

## Readability

| Check | Fires when |
| :--- | :--- |
| `GE.READ.NO_LISTS` | a page of 1,200+ words contains no list, table or definition list; template-bound |
| `GE.READ.LONG_SENTENCES` | mean sentence length over 34 words across 12+ sentences; template-bound, medium confidence |

A wall of text is owned by `G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE`, because its primary harm is to
retrieval; these readability findings corroborate it rather than duplicating it.
