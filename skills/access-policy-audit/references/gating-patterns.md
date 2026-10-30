# Gating: Served to Humans, Refused to Machines

## `G0.GATING.BOT_STATUS_DIVERGENCE`

### Mechanism
Bot-management rules at the CDN or WAF layer frequently match on user-agent substrings. A
rule written to stop scrapers commonly catches the retrieval crawlers assistants use to
fetch citable sources. The site then loads for browsers and for Googlebot while the agent
that would have quoted it receives a 403 or a JavaScript challenge. None of this appears
in the site's analytics, because the refused requests never reach the application.

### How it is measured
During the crawl, the entrypoint takes a small fixed probe set (default 3 successfully
fetched content URLs) and requests each a second time. The second request is identical —
same address, no cookies, same crawl — except that its user-agent additionally names one
retrieval crawler:

```
BrandAIReadinessAudit/1.0.0 (read-only site audit; +<contact>) probe-profile=OAI-SearchBot
```

The analyzer compares each pair: status code, body size (the probe's body less than half the
baseline's at the same status — a *larger* body is ordinary variance on a dynamic page, since
block and challenge pages are small), and challenge-page wording present in the probe
response but not the baseline ("verify you are human", "checking your browser",
challenge-platform markers and similar).

### False-positive guards
- **Rule R1:** at least two probed URLs must diverge for the same agent. A single diverging
  URL is an observation: CDN and geographic variance produce one-off differences routinely.
- The probe agent is chosen only from agents that robots.txt **permits** on every probed
  path. Because robots.txt groups match by substring, a user-agent containing a crawler's
  token must also satisfy that crawler's group — the gateway enforces this on every probe.
- Confidence is capped at medium and severity at medium. The probe names a crawler without
  coming from that crawler's published addresses. CDNs that verify crawlers (Cloudflare's
  verified-bot categories, for example) refuse exactly such unverified claims while
  admitting the real crawler, so a divergence proves a rule keyed on the user-agent — not
  that the real crawler is blocked. The suggested fix says to check whether the rule already
  verifies addresses before changing anything.

### The boundary: measurement, not evasion
This is diagnosis, and the line is drawn deliberately.

**This audit does:** send truthful, identifiable user-agent strings that begin with its own
product token; observe whether a site treats an honestly declared agent differently; report
differential treatment as a finding for the owner to fix.

**This audit never:** impersonates a browser or sends a bare third-party crawler token to
obtain access it was refused; rotates user-agents, addresses or proxies; solves, bypasses
or automates past a challenge; ignores robots.txt; retries into an open circuit breaker;
touches authenticated areas.

The consequence is a stated limitation: this detects differentiation implemented as a
**user-agent rule**, which is how most such rules are written. It cannot detect
differentiation based on IP allowlists or verified-crawler callbacks, so a clean result is
not proof that no gating exists. The finding's evidence says so.

### Fix
Allowlist the declared retrieval crawlers at the CDN or WAF, verified by the operators'
published IP ranges or reverse DNS rather than by user-agent alone. Serve non-JavaScript
agents a 200 with real content instead of an interstitial: a challenge page returns 200
with no content, so consumers store an empty page rather than retrying. Keep rate limiting;
the goal is correct identification, not unlimited access.
**Verify:** the same URL with and without the crawler token returns 200 with comparable
bodies and no challenge wording.

## `G0.GATING.NON_BROWSER_REFUSED`

### Mechanism
A bot-management layer at the CDN or WAF (managed challenges, "security checkpoint" pages,
JavaScript proof-of-work) refuses any client that cannot run a browser challenge. Retrieval
crawlers are such clients: they fetch raw HTML and do not execute challenge scripts. Unless
the provider recognises a crawler as verified and allowlists it, the crawler receives the
same 403 or 429 and stores nothing. The site loads normally in every browser, so the
refusal is invisible to the people who run it.

### How it is measured
The homepage and the other attempted content pages are requested once each, by this
audit's honest user-agent, with no cookies. Signals, of which at least two are required
(rule R1):

1. **Refusal status** — the homepage and at least 60% of attempted content pages return
   401, 403, 405, 406, 429 or 503 (or were not sent because the host's circuit breaker
   opened after such refusals). 405 and 406 on a plain GET are how several WAFs refuse
   clients; 404 and 410 describe content, not the client, and never count.
2. **Policy served, content refused** — robots.txt on the same host returned 200, so the
   host answers machine clients and refuses them only for content.
3. **Bot-management fingerprint** — challenge wording in the refusal body ("verify you are
   human", "security checkpoint" …) or a header or cookie that such layers set
   (`cf-mitigated`, `__cf_bm`, `x-vercel-mitigated`, `x-datadome`, `_px…`, `incap_ses_…`,
   `ak_bmsc`, `x-amzn-waf-action` …).

### False-positive guards
- A refused page among pages that load is not this finding: the homepage must be refused
  and refusals must cover the site-wide share of attempted pages.
- 404 and 410 are answers about content, not refusals of the client, and never count.
- Confidence is fixed at **medium**. The refusal this audit received is observed; whether a
  specific crawler is on the provider's verified-bot allowlist is not observable without
  impersonation, which this audit never does. The evidence says so.
- When this finding fires, content findings that could only have come from the missing
  pages (no sitemap, no about page) are masked behind it: they are consequences of the
  refusal, not separate defects.

### Fix
In the bot-management settings, allow verified search and retrieval crawlers (most
providers publish a verified-bot category) and confirm the named crawlers you want to be
cited by are in it. Serve those agents a 200 with the real HTML rather than a challenge.
Keep challenges for unverified automation and keep rate limits.
**Verify:** from a server without a browser, request the homepage with a plain HTTP client
and confirm a 200 with content — or confirm in the provider's logs that requests from the
crawler's published IP ranges are allowed.

## `G0.GATING.RATE_LIMITED`

Recorded as an **observation** when any request received 429 or 503, or a host's circuit
breaker opened, during a crawl running at most 2 concurrent requests with a 0.5 s floor.
Aggressive limiting of well-behaved crawlers suppresses indexing without appearing in any
configuration as a block — but transient capacity problems produce the same signal, so it
is never counted as a defect.

## `G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT`

**Signals:** a password field inside a form in the **main content region**, or a redirect
through a login or sign-in URL; **and** fewer than 120 words of main-region text. Listing in
the public sitemap strengthens the evidence: the site asks for the URL to be crawled and
then serves a login form.
**Guards:** account, dashboard, login and similar paths are excluded by path class; a login
link in the header is not a wall — the gate must be in `<main>` and the content must be
missing.
**Fix:** decide per page class whether the content is public. If not, remove those URLs from
the sitemap and mark them `noindex`. If so, serve the content unauthenticated and gate only
personalised or interactive parts.

## `G0.GATING.PAYWALL_UNMARKED`

**Signals:** subscription wording in the main region ("subscribe to continue", "already a
subscriber", "free articles remaining" …) **and** either main text under half its template's
median or two distinct paywall phrases; **and** no `isAccessibleForFree` false value or
`hasPart` declaration anywhere in the page's structured data.
**Mechanism:** an undeclared gate makes a truncated page look thin — or like cloaking, when a
crawler receives less than a subscriber does.
**Fix — keep the paywall, declare it:**

```json
{
  "@context": "https://schema.org",
  "@type": "NewsArticle",
  "isAccessibleForFree": false,
  "hasPart": {
    "@type": "WebPageElement",
    "isAccessibleForFree": false,
    "cssSelector": ".paywalled-section"
  }
}
```

Serve the same free portion to every agent. Varying content by user-agent is cloaking.

## `G0.HTTP.SOFT_404`

**Signals — both required (rule R1):** not-found wording in the main region ("page not
found", "no longer available", "error 404" …) **and** main text under 40% of the template's
median word count, on a response that returned HTTP 200.
**Guard:** an article *about* error pages contains the wording without being thin, so it is
not flagged.
**Mechanism:** a 200 tells consumers the content exists, so error pages are indexed — and can
be retrieved and quoted — as real content, while monitoring that watches status codes sees
nothing wrong.
**Fix:** return 404 (or 410 for deliberately removed content), keep the helpful error page,
and 301-redirect content that moved.
