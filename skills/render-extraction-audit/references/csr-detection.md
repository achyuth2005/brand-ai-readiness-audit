# Client-Side Rendering Detection

## Mechanism

> "A page that looks complete to a person isn't always complete to a machine. Some content
> is only assembled after a page loads." — brief, Appendix C

Many retrieval fetchers take the raw HTML response and execute little or no JavaScript. A
fact that exists only after hydration does not exist for them. The page can be permitted,
crawlable and beautifully designed, and still contribute nothing to an answer — and it
survives review because everyone who checks it uses a browser.

## Definitions

- **static text** — visible text of the raw response, with `script`, `style`, `noscript`,
  `template`, `head`, `svg`, `iframe`, HTML comments, `hidden` and `aria-hidden` subtrees
  removed, NFKC-normalised, whitespace collapsed. Main-region text is separated from header,
  navigation, sidebar and footer.
- **mount node** — a framework root such as `#root`, `#app`, `#__next`, `#__nuxt`,
  `#___gatsby`, `[data-reactroot]`, `[ng-version]`, `[data-server-rendered]`.
- **hydration payload** — `__NEXT_DATA__`, `window.__NUXT__`, `__remixContext`,
  `__APOLLO_STATE__`, `__INITIAL_STATE__`, `__PRELOADED_STATE__`, SvelteKit data, streamed
  `self.__next_f` chunks, and non-JSON-LD `<script type="application/json">` blocks.
- **blob prose** — string leaves of a parsed payload of at least 40 characters and 6 words
  that are not URLs, identifiers, hashes, CSS or code, with a letter ratio of at least 55%
  and real word variety.

## Tier 1 — suspicion (structural, one fetch, never conclusive alone)

| Signal | Condition | Threshold key |
| :--- | :--- | :--- |
| S1 | main-region text under 600 characters | `render.thin_text_chars` |
| S2 | a mount node whose subtree holds under 200 characters | `render.mount_node_text_chars` |
| S3 | visible text under 1.5% of HTML bytes | `render.text_to_html_ratio` |
| S4 | 5 or more scripts, or over 200 KB of script | `render.script_count`, `render.script_bytes` |
| S5 | `<noscript>` asks the visitor to enable JavaScript | — |
| S6 | title or descriptions are substantive while the main region is thin | — |

Tier 1 fires only on the composite **(S1 or S3) and S2 and (S4 or S5) and S6**. Requiring
an *empty* mount node together with substantive metadata is what keeps correctly
server-rendered framework pages silent.

## Tier 2 — confirmation without a browser (the key idea)

The framework's own hydration payload is a ground-truth oracle for what the page **will**
say once JavaScript runs. For each blob prose span:

- present in the static text → proof of working server rendering → ignored;
- consent, chat-widget, advertising or loading text → ignored (suppression lexicon);
- otherwise → counted as missing.

When the missing spans exceed **400 characters** (`render.blob_delta_chars`), client-side
rendering is confirmed at **high confidence** from the single static response. The evidence
quotes a missing span and names its JSON path, and the finding carries a `jsonpath` evidence
ref that the verification gate re-resolves in the stored payload.

A payload whose missing prose falls within 20% below the floor is recorded as a near miss:
some content on that template is already client-rendered.

## Tier 3 — direct measurement (only when a rendered DOM exists)

When the entrypoint's optional browser tier captured a rendered DOM, static and rendered
text are both normalised and case-folded, then diffed token by token. Confirmation needs a
delta ratio above **0.30** *and* more than **500** added characters. A delta made mostly of
suppression-lexicon text is chrome, not content.

When no render exists, the skill records that tier 3 did not run. Tier 1 is never silently
promoted to fill the gap.

## Severity follows what is missing, not how much

The missing text is classified into fact classes (price, availability, hours, address,
phone, specifications, dates, …). If any class is primary — generally, or for the detected
site profile — the finding takes primary fact weight and a template-level gap is critical.
If only enrichment content is missing, severity stays low or the item becomes an
observation. A site whose prices only exist after hydration has a different problem from
one whose testimonial slider is client-rendered.

## False-positive guards

| Guard | Rule |
| :--- | :--- |
| G1 | Framework fingerprints alone never fire (rule R5). A server-rendered page ships `__NEXT_DATA__` *and* full DOM text, so S2 fails and tier 2 finds its prose present. |
| G2 | Blob prose present in the static text is proof of server rendering and silences tier 2. |
| G3 | Utility pages are excluded by path class; thinness is judged against content pages only. |
| G4 | Both sides are NFKC-normalised, soft hyphens and zero-width characters stripped, quotes and dashes unified, whitespace collapsed and case-folded before comparison, so encoding noise cannot fake a delta. |
| G5 | Consent banners, chat widgets, newsletter prompts and loading text are excluded from both tier 2 and tier 3. |
| G6 | When tier 3 is unavailable, coverage says so and tier 1 is not upgraded. |
| G7 | A response truncated by the size cap is not judged: streaming server-rendered HTML genuinely looks thin mid-response. |

## Evidence, as emitted

> 3/3 sampled pages in template cluster T2 return a raw HTML response whose main region
> contains 29 characters of visible text (threshold 600) … A framework mount node
> `div#__next` is present and its subtree holds under 200 characters of text. 4
> natural-language span(s) totalling 612 characters are present in the page's own hydration
> payload but absent from the static HTML. Example, at JSON path
> `$.props.pageProps.product.description` in `__NEXT_DATA__` … The text missing from the
> response carries these fact classes: availability, price.

## Fix and verification

1. Move the template's data fetching to the server so its primary facts render into the
   response (server-side rendering, static generation, or a framework loader).
2. As a stopgap, emit the same facts in server-rendered JSON-LD — machine-readable whatever
   the hydration path — while the render change is scheduled.
3. Add a build-time assertion that a known value appears in the server response.

**Verify:** `curl -s <url> | grep -F '<known value>'`, or load the page with JavaScript
disabled. The fix is confirmed only when the fact is in the raw response body.

## Related checks

| Check | Rule |
| :--- | :--- |
| `G1.RENDER.BODY_REQUIRES_INTERACTION` | collapsed panels (closed `<details>`, hidden tab panels, hidden-by-convention containers) that are empty in the response, on a page whose whole main region is under 400 characters. Text inside a closed `<details>` *is* in the response and is not counted. |
| `G1.ENCODING.UNDECLARED_OR_WRONG` | no charset declared anywhere with over 20 non-ASCII characters; a meta charset contradicting the encoding the bytes decode as; or more than 3 Unicode replacement characters after decoding |
| `G1.TEXT.BOILERPLATE_DOMINANT` | header/nav/sidebar/footer words at least equal to main-region words, and main-region words below both 120 and 60% of the template median |
| `G1.TEXT.MAIN_LANDMARK_MISSING` | no `<main>`, `<article>` or `role="main"` on a template of at least 3 sampled pages; reported once, as a template-level omission |

`BOILERPLATE_DOMINANT` and `MAIN_LANDMARK_MISSING` on a client-rendered template are masked
by the render gap, because they describe the empty response.
