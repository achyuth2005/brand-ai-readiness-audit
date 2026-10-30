# robots.txt, Indexing Directives, HTTP and Sitemaps

## Two postures, never conflated

| Posture | Question | Governed by | Where |
| :--- | :--- | :--- | :--- |
| **Compliance** | May *this audit* fetch a path? | this audit's own user-agent | `netgate.py`, before every request |
| **Analysis** | What do the site's rules do to *other* agents? | each agent's effective group | `analyze_access.py` |

The audit never fetches a disallowed path "to check it anyway". Confusing the two postures
is how an auditor ends up violating the policy it is auditing.

## RFC 9309 behaviour implemented (`lib/auditkit/robots.py`)

| Rule | Behaviour |
| :--- | :--- |
| Group selection | Longest case-insensitive `User-agent` token match wins; fall back to `*`. Consecutive `User-agent` lines share one rule set. |
| Rule precedence | Longest matching pattern wins. `Allow` wins an exact-length tie. |
| Wildcards | `*` matches any sequence; a trailing `$` anchors to the end of the path. |
| Empty `Disallow:` | Grants access; it is not a rule. |
| `Crawl-delay` | Honoured as a floor on this audit's pace, never as licence to go faster. |
| `Sitemap:` | Global to the file, not scoped to a group. |
| 2xx | Parse and obey. A file with no recognised directive is treated as allow-all, with a warning. |
| 4xx | Allow all. |
| 5xx, timeout, or unreachable | Disallow all **for this audit's own fetching** — the conservative reading RFC 9309 requires when the file is undefined. |

When `protego` is installed it is used as a cross-check and any disagreement is recorded as
a parse warning. The built-in matcher stays authoritative, because a safety control cannot
depend on an optional package.

## `G0.ROBOTS.UNAVAILABLE`

**Signal:** the site's own robots.txt returned 5xx or no response.
**Mechanism:** an absent file grants access; an erroring one is commonly read as a refusal,
because a crawler cannot tell whether it is being denied. The whole site can become
uncrawlable while every page loads in a browser.
**Fix:** serve robots.txt statically or from the edge so it never depends on application
health; return 404 if no policy is intended; monitor it separately.
**Verify:** repeated requests return only 200 or 404.

## `G0.ROBOTS.AUDIT_PATH_DISALLOWED`

**Signal:** content URLs (never utility URLs) were not fetched because robots.txt *rules*
disallow this audit's generic, honestly identified user-agent.
**Mechanism:** agents without a named group fall through to `*`, so a restrictive catch-all
governs every assistant crawler the file does not name — including ones that appear next
year. The audit's own constraint is converted into evidence instead of a silent gap.
**False-positive guard:** refusals caused by an unavailable robots.txt are *not* counted here;
they belong to `G0.ROBOTS.UNAVAILABLE`, and a network failure on the auditing side is not a
defect of the site.
**Fix:** narrow `*` to explicit utility paths; name the crawlers that matter.

## Indexing and snippet directives — the silent citation killers

All of these are invisible in a browser. Directives are read from the `X-Robots-Tag`
response header and from robots meta tags (`name="robots"` and per-agent names such as
`googlebot`), parsed as comma-separated tokens.

| Check | Signal | Why it matters |
| :--- | :--- | :--- |
| `G0.DIRECTIVE.NOINDEX_HEADER` | `noindex` or `none` in `X-Robots-Tag` | Removes the page from indexes; never visible in page source. |
| `G0.DIRECTIVE.NOINDEX_META` | `noindex` or `none` in a robots meta tag | Same, and very often a staging or template default left in place. |
| `G0.DIRECTIVE.NOSNIPPET` | `nosnippet` in header or meta | **Forbids quoting.** The page can rank and still never be excerpted — a direct answer to "why are we never quoted", with a one-line fix. |
| `G0.DIRECTIVE.MAX_SNIPPET_ZERO` | `max-snippet:0` | Same effect, set numerically, often by a plugin default. |
| `G0.DIRECTIVE.DATA_NOSNIPPET` | `data-nosnippet` wrapping 10+ words | Suppresses exactly the sentences a consumer would lift. |
| `G0.DIRECTIVE.NOARCHIVE_NOCACHE` | `noarchive` or `nocache` | Microsoft documents that `noarchive` content is not included in or linked from Copilot answers, and that `nocache` limits Copilot to URL, title and snippet. The page stays indexed while being removed from, or thinned on, an assistant surface. |
| `G0.DIRECTIVE.CONFLICT` | header says `index` while meta says `noindex`, or the reverse | The most restrictive combination applies, and the author reading source cannot see why. |

**False-positive guards**

- **Path class (rule R4).** Utility surfaces — search results, faceted listings, cart,
  checkout, account, login, legal, taxonomy archives, print views, thank-you pages, error
  pages — are excluded before these checks run. `noindex` on them is correct.
- **Template collapse (rule R3).** One directive on 12 pages of one template is one finding
  with `12/12` evidence; a directive on one page of an otherwise indexable template stays
  page-scoped.
- **Paywalled publishers.** `nosnippet` on gated content may be a deliberate business
  choice; the better-supported alternative is to declare the gate (see
  `gating-patterns.md`) so the free portion remains quotable.

**Fix pattern:** find where the directive is set — template, CMS field, CDN rule or SEO plugin
default — and remove it from content page classes while keeping it on utility pages.
**Verify:** neither the response headers nor the page source of an affected URL carry it.

## HTTP hygiene

| Check | Signal | Guard |
| :--- | :--- | :--- |
| `G0.HTTP.REDIRECT_CHAIN` | a page or sitemap URL took more than 2 hops, or a chain ended in 4xx/5xx | every hop was individually re-validated during the crawl |
| `G0.HTTP.MIXED_CANONICAL_HOST` | two or more http/https, www/non-www variants of the root answer 200 **without** redirecting | a variant that redirects to the preferred host is correct and excluded |
| `G0.HTTP.SOFT_404` | see `gating-patterns.md` | two signals required |
| `G0.HTTP.SLOW_TTFB` | 75th-percentile time to first byte above 1500 ms over at least 3 pages | reported as an **observation**, because the measurement includes network distance between the auditor and the origin |

## Sitemaps and feeds

| Check | Signal |
| :--- | :--- |
| `G0.SITEMAP.MISSING` | no parseable urlset or sitemapindex at any `Sitemap:` directive, `/sitemap.xml` or `/sitemap_index.xml` |
| `G0.SITEMAP.NOT_IN_ROBOTS` | a usable sitemap exists but robots.txt declares no `Sitemap:` line |
| `G0.SITEMAP.INVALID_XML` | a retrieved sitemap does not parse, or its root is not urlset/sitemapindex; XML is parsed without resolving external entities |
| `G0.SITEMAP.URLS_BROKEN` | of at least 3 fetched sitemap URLs, some returned 4xx/5xx; reported as k/n |
| `G0.SITEMAP.STALE_OR_TRUNCATED` | the sitemap's URL count sits exactly on a round generator limit, or crawled, internally linked content sections are entirely absent from a sitemap of 20+ URLs |
| `G0.SITEMAP.NO_MACHINE_FEED` | a site profiled as a publisher offers no RSS/Atom/JSON feed at conventional paths and declares none with `rel="alternate"` |

Sitemap following is capped (10 child sitemaps, 5,000 URLs) so a very large index cannot
consume the time budget.
