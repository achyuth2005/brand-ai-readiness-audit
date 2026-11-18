# Phase 2 labels: critical and high findings, checked against the stored bytes

Method: for each critical or high finding, the stored response (`phase2/runs/<site>/ledger`) was
opened at the finding's example URLs and evidence refs (helper: `lk.py`). Labels:
**correct** · **overstated** (true, but the severity or wording claims more than the bytes show) ·
**FP** (the claim does not hold).

| Site | Finding | Sev | Label | What the bytes show |
| :-- | :-- | :-: | :-: | :-- |
| boat | G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES | H | correct | Homepage: 35 of 369 `<img>` tags have no alt attribute (lazy-loaded product thumbnails) |
| boat | G2.SD.REQUIRED_PROP_MISSING (FAQPage) | H | correct | Collection pages ship `{"@type":"FAQPage","mainEntity":[]}` |
| mamaearth | G0.SITEMAP.STALE_OR_TRUNCATED | H | overstated | `sitemap_pages_1.xml` really omits `/pages/contact`; 2 support pages don't justify "no longer describes the whole site" at high |
| mamaearth | G2.SD.REQUIRED_PROP_MISSING (Offer price) | H | **FP** | Price and currency are in `offers.priceSpecification[]` (UnitPriceSpecification 350.0 INR), which Google accepts |
| ikea_in | G0.DIRECTIVE.DATA_NOSNIPPET | H | **FP** | Wraps the header promo strip (`hnf-pageheader__messages`, "click & collect"), not primary content. The sample also drifted to /at/de/ and /cl/es/ from an /in/en/ input |
| chumbak | G2.SD.MARKUP_TEXT_CONTRADICTION | H | correct | Markup price 486.5 INR vs displayed "₹ 487" (a rounding display, but a real mismatch) |
| chumbak | G2.SD.REQUIRED_PROP_MISSING (Product offers) | H | **FP** | The Product node has aggregateRating; Google requires one of offers, review or aggregateRating. (The real problem, the brand marked up as a Product on every page, goes unreported) |
| figma | G0.ROBOTS.AI_AGENT_BLOCKED (OAI-SearchBot) | H | correct | Group at line 92 (OAI-SearchBot, PerplexityBot, Claude-SearchBot…) allowlists paths and ends in `Disallow: /` at line 9049 |
| figma | G0.ROBOTS.AI_AGENT_BLOCKED (Google-Extended) | H | correct | Line 43: `User-Agent: Google-Extended` / `Disallow: /` |
| calendly | G0.DIRECTIVE.NOINDEX_META | H | correct | `/ai-assistant` and `/contact` both carry `noindex, nofollow`. **A strong real finding** |
| razorpay | G0.SITEMAP.INVALID_XML | H | **FP** | `razorpay.txt` is a plain-text URL list, a valid sitemap format under sitemaps.org |
| thehindu | G0.ROBOTS.AI_AGENT_BLOCKED (OAI-SearchBot) | **C** | correct | robots.txt line 140 group (OAI-SearchBot, PerplexityBot, …) → `Disallow: /` |
| thehindu | G0.SITEMAP.INVALID_XML | H | **FP** | The "sitemaps" are RSS feeds declared in robots.txt; RSS 2.0 and Atom are accepted sitemap formats |
| thehindu | GE.FRICTION.CONTENT_GATING_OVERLAY | H | **FP** | `#user-session-popup` has `v-show="store.session_limit_exceeded === true"` and `v-cloak`, so it's hidden until the page's JavaScript reveals it |
| indianexpress | G0.ROBOTS.AI_AGENT_BLOCKED (PerplexityBot) | **C** | correct | Line 253: `User-agent: PerplexityBot` / `Disallow: /` |
| indianexpress | G0.DIRECTIVE.NOINDEX_META | H | correct | `/shorts/` and an e-paper page carry `noindex,nofollow` (possibly intentional, but true) |
| indianexpress | G2.SD.REQUIRED_PROP_MISSING (WebSite name) | H | correct | The WebSite node has url and SearchAction, but no name |
| wired | G0.DIRECTIVE.DATA_NOSNIPPET | **C** | **FP** | Wraps the collapsed comments panel and the footer legal text |
| wired | G0.DIRECTIVE.NOINDEX_META | **C** | overstated | Contributor/app pages with `noindex, follow`; 5/29 non-core pages don't make a critical |
| wired | G0.ROBOTS.AI_AGENT_BLOCKED (Claude-SearchBot) | **C** | correct | Line 61 group (Claude-SearchBot, Claude-User, ClaudeBot, …) → `Disallow: /` |
| wired | G2.SD.REQUIRED_PROP_MISSING (Organization name) | H | correct | `sourceOrganization` is an `{"@type":"Organization","@id":"https://www.wired.com"}` reference, and no stored page defines that `@id` with a name (confirmed by the evidence-check agent) |
| wired | GE.FRICTION.CONTENT_GATING_OVERLAY | H | **FP** | `aside.paywall-bar` inside `persistent-bottom`: a bottom strip, not a covering overlay |
| spiegel | G0.DIRECTIVE.NOARCHIVE_NOCACHE | H | overstated | noarchive is real and mechanism-sound (Copilot exclusion), but the evidence quotes `noindex, nofollow, noarchive` from a logout page as if it were the homepage's value |
| spiegel | G0.DIRECTIVE.NOINDEX_META | H | overstated | `/debatten/` is truly noindex, but the other example is a logout page (`gruppenkonto…/abmelden.html`) |
| nytimes | G0.GATING.NON_BROWSER_REFUSED | H | correct | 30/39 pages return 403 while robots.txt returns 200 |
| shakeshack | G0.GATING.NON_BROWSER_REFUSED | H | correct | 403 on content, robots.txt 200 |
| indiagov | G0.GATING.NON_BROWSER_REFUSED | H | correct | 403 "access denied" bot-management page |
| chaayos | G2.SD.ABSENT | H | correct | Homepage: 0 JSON-LD blocks, 0 Microdata |
| mit | G0.DIRECTIVE.DATA_NOSNIPPET | **C** | **FP** | Wraps the "press inquiries" box and the site footer on news articles |
| mit | G0.SITEMAP.MISSING | H | correct | No sitemap at conventional locations and no Sitemap directive |
| mit | G1.NONTEXT.VIDEO_WITHOUT_TRANSCRIPT | H | **FP** | `<video autoplay loop muted playsinline>` hero background with a pause control, so decorative |
| iitm | G0.SITEMAP.INVALID_XML | H | correct (mislabelled) | `/sitemap.xml` redirects to the homepage HTML, so there's no usable sitemap; the "invalid XML" wording is odd but the problem is real |

### Second batch (remaining sites and input variants)
| Site | Finding | Sev | Label | What the bytes show |
| :-- | :-- | :-: | :-: | :-- |
| elpais | G0.ROBOTS.AI_AGENT_BLOCKED (Claude-SearchBot) | **C** | correct | robots.txt lines 41–42: `User-agent: Claude-SearchBot` / `Disallow: /` |
| elpais | G0.DIRECTIVE.NOARCHIVE_NOCACHE | H | correct | `noarchive,index,follow` on 29/30 pages; Microsoft documents that noarchive keeps content out of Copilot answers (the engine's cited mechanism) |
| elpais | G0.SITEMAP.MISSING | H | correct | No Sitemap directive, and conventional locations have none |
| tldraw | G0.ROBOTS.AUDIT_PATH_DISALLOWED | H | correct | robots.txt line 27 `Disallow: /` for generic crawlers |
| tldraw | GE.NAV.NAV_ONLY_IN_JS | H | correct | Raw homepage: 0 internal links, empty mount node |
| squoosh | G0.SITEMAP.MISSING | H | overstated | True, but a one-page app gains little from a sitemap |
| swiggy | G0.SITEMAP.STALE_OR_TRUNCATED | H | **FP** | `sitemap-0.xml.gz` has exactly 50,000 URLs, which is the protocol's per-file maximum, not truncation |
| paulgraham | G0.SITEMAP.MISSING | H | unclear | Says a Sitemap directive "did not resolve", but requests.log shows no sitemap fetch |
| paulgraham | GE.FRICTION.NO_VIEWPORT_META | H | correct | No `<meta name="viewport">` on any page |
| reuters | G0.DIRECTIVE.NOINDEX_META | H | overstated | `noindex` on `/plus/` sponsored pages (probably intentional) |
| reuters | G0.ROBOTS.AI_AGENT_BLOCKED (Claude-SearchBot) | H | correct | `User-agent: *` → `Allow: /plus/` + `Disallow: /`; Claude-SearchBot has no own group, so it's blocked from all news. ("1/23" is an artifact: the audit could only crawl /plus/) |
| reuters | G0.SITEMAP.INVALID_XML | H | correct | `/plus/sitemap.xml?from=500` line 652 has a raw `&` |
| medium | G0.GATING.NON_BROWSER_REFUSED | H | correct | 30/30 pages return 403, robots.txt returns 200 |
| medium | G0.ROBOTS.AI_AGENT_BLOCKED (Amazonbot) | H | correct | robots.txt line 30 `Disallow: /` for Amazonbot |
| airbnb_in | G0.DIRECTIVE.DATA_NOSNIPPET | **C** | **FP** | Wraps the no-JavaScript alert and one small widget |
| airbnb_in | G0.DIRECTIVE.NOINDEX_META | **C** | unclear | Not inspected; `/airbnb-friendly/apartments/*` landing pages |
| airbnb_in | G0.ROBOTS.AI_AGENT_BLOCKED (PerplexityBot) | H | correct | robots.txt line 544 `Disallow: /rooms/` in the PerplexityBot group |
| airbnb_in | G2.SD.REQUIRED_PROP_MISSING (Product offers) | H | **FP** | The Product node has aggregateRating (one of offers/review/aggregateRating is enough) |
| v_http (wikipedia) | G0.DIRECTIVE.DATA_NOSNIPPET | **C** | **FP** | Wraps `div.printfooter` |
| v_http (wikipedia) | G0.SITEMAP.MISSING (sitemap 403) | H | correct | The REST sitemap returned 403 to a non-browser client |
| v_upper (apple) | G0.DIRECTIVE.DATA_NOSNIPPET | **C** | **FP** | Wraps `footer#ac-globalfooter` |
| v_upper (apple) | G0.DIRECTIVE.NOINDEX_META | H | **FP** | `noindex` on employee-purchase-program stores (`/us_epp_*/store`), which is correct engineering |
| v_upper (apple) | G2.SD.MARKUP_TEXT_CONTRADICTION (telephone) | H | **FP** | Two real Apple numbers for different purposes (support vs sales) |
| v_query (blog.hubspot) | G0.DIRECTIVE.DATA_NOSNIPPET | **C** | **FP** | Wraps `header.blog-header` |
| v_query (blog.hubspot) | G2.SD.MARKUP_TEXT_CONTRADICTION (datePublished) | H | **FP** | Compares the first ItemList entry's date with a different post's `<time>` |

## Tally (57 labelled, 36 published reports)
- **correct 30 · overstated 6 · FP 19 · unclear 2** (after re-labelling El País `noarchive` as correct: Microsoft documents the Copilot exclusion the engine cites).
- **True, including overstated: 36/55 = 65%** (excluding unclear). Strictly correct: 53%.
- **Criticals: 12 total → 4 correct (all AI-crawler robots.txt blocks), 1 overstated, 6 FP (all `data-nosnippet`), 1 unclear.**
- Excluding `G0.DIRECTIVE.DATA_NOSNIPPET` (7/7 FP): 36/48 = 75% true.
- Every `G0.ROBOTS.AI_AGENT_BLOCKED` finding checked (10) and every `G0.GATING.NON_BROWSER_REFUSED` (4) was correct. These are the engine's most reliable, highest-value checks.
- Input variants (bare domain, http://, uppercase, deep path, query string) all normalised and ran correctly.

## False positives cluster in five checks
| Check | Record | Pattern |
| :-- | :-- | :-- |
| `G0.DIRECTIVE.DATA_NOSNIPPET` | **7/7 FP (6 critical)** | Treats any `data-nosnippet` as wrapping primary content; it's on footers, headers, comments, promos, no-JS alerts |
| `G0.SITEMAP.INVALID_XML` | 2 FP / 3 correct | Treats RSS and plain-text sitemaps (both valid formats) as broken XML |
| `GE.FRICTION.CONTENT_GATING_OVERLAY` | 2/2 FP | Misses `v-show`/`v-cloak` state-hidden popups; treats bottom strips as covering |
| `G2.SD.REQUIRED_PROP_MISSING` | 3 FP / 4 correct | Ignores `priceSpecification` and the offers/review/aggregateRating alternatives |
| `G2.SD.MARKUP_TEXT_CONTRADICTION` | 2 FP / 1 correct | Compares values belonging to different entities (list item vs a different post; support vs sales phone) |
| `G1.NONTEXT.VIDEO_WITHOUT_TRANSCRIPT` | 1/1 FP | Autoplay-muted-loop background video is decorative |

## Engine bugs that withhold or block the report
| Bug | Sites | Effect |
| :-- | :-- | :-- |
| Empty-name evidence ref (`G3.ENTITY.WEAK_DISAMBIGUATION` / `AMBIGUITY_RISK` record `""` for a nameless or `@id`-only node; the verifier can't re-resolve it) | adobe, freshworks, githubdocs, k8sdocs, usagov, rakuten | exit 2, report withheld (an unverified copy is written) |
| Availability URL form (`https://schema.org/OutOfStock` vs recorded `OutOfStock`; the verifier's prefix match fails) | everlane | exit 2 |
| Page without a `<head>` element (`NO_VIEWPORT_META` evidence selector `head`) | cern | exit 2 |
| More than 100 response headers (a Python `http.client` limit) | nykaa | exit 4, no report |
| robots.txt read timeout | zomato, makemytrip | exit 4 (correct behaviour) |

## Safety note
The mutating-GET denylist is English-only: `gruppenkonto.spiegel.de/abmelden.html` ("abmelden" means log out) was fetched as a page and again as a probe. No cookies are sent, so it had no effect, but a reviewer reading `requests.log` could notice.

## Caveat added after Phase 3: the multi-host homepage bug
`analyzer.Context.homepage()` takes the **alphabetically first** sampled page whose path is `/`, and `site_url()`/`site_host()` derive from it. When the sample includes another subdomain's root that sorts earlier (`abo.`, `advertising.`, `catalyst.`, `forum.`, `ge.`, `web.` all sort before `www.`), the audit reads **that host's** robots.txt and homepage. Hijacked runs in this sweep: **iitm, mit, nytimes, spiegel, zoho, v_deep (zoho)**, 6/40 runs, plus 1 of 4 Figma runs (`forum.figma.com`). Labels above for those sites were made against the finding's own evidence, but homepage- and robots-level findings there may describe the wrong host (e.g. MIT's `SITEMAP.MISSING` was judged on `web.mit.edu`).
