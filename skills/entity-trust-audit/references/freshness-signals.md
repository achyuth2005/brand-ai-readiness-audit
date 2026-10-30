# Freshness Signals

## Mechanism

Consumers use dates to decide whether to prefer a source and whether a fact is still true.
A page with no date cannot win on recency. Contradictory dates leave a consumer unable to
tell which is true. A **falsely** fresh signal is worse than none: once detected, it teaches
consumers to discount the site's freshness claims everywhere — including the genuine ones.

## Reference time

Every staleness judgement uses the audit date recorded in the evidence ledger (`audited_at`,
overridable with `--now`), **never the wall clock**. Judging against the current time would
make two runs over the same frozen ledger disagree, and break the determinism guarantee.

## Date sources

Collected per page and separated into two kinds, because like must be compared with like.

| Source | Kind |
| :--- | :--- |
| `datePublished` in structured data | published |
| `dateModified` in structured data | modified |
| `<time datetime>` | modified if its `itemprop`, class or parent text says updated/modified/revised; otherwise published |
| visible "Published / Posted / Written on <date>" in the first 3,000 characters of the main region | published |
| visible "Updated / Last updated / Modified / Reviewed on <date>" | modified |
| `Last-Modified` response header | presence only (servers often send the request time) |
| sitemap `lastmod` | presence only (checked separately for honesty) |

Dates are parsed to UTC calendar days from ISO-8601, "15 March 2024", "March 15, 2024",
"March 2024", and numeric forms only where the day exceeds 12 and settles the order.

## `G3.FRESH.NO_DATE_SIGNAL`

**Applies to:** article-class pages only — an article path class or an Article-family type.
Pricing, about and product pages are evergreen and do not need a publication date (rule R4).
**Fires when:** at least half of 3+ article pages have **no** date of any kind: no markup date,
no `<time>`, no visible published or updated date, no `Last-Modified`, no sitemap `lastmod`.
**Fix:** show "Published" and, on substantive change, "Updated" near the headline in a
`<time datetime>` element, and emit `datePublished`/`dateModified` in ISO-8601 from the same
stored timestamps.

## `G3.FRESH.DATE_CONTRADICTION`

**Fires when, on one page:** two dates of the **same kind** differ by more than 90 days
(`entity_trust.date_skew_days`); or the earliest modified date precedes the earliest
published date by more than 90 days.
**Guard:** a published date and an updated date are never compared as competitors — they are
supposed to differ.
**Evidence:** both sources named, with values, and refs to each (`jsonpath` for markup,
`dom` for the `<time>` element or visible text).
**Fix:** render every visible date, `<time>` element and markup date from one stored
timestamp per kind, and assert agreement in the build.

## `G3.FRESH.SITEMAP_LASTMOD_UNIFORM` — the freshness-lie detector

### Mechanism
A common CMS and static-site behaviour stamps every URL's `lastmod` with the deploy time.
`lastmod` exists to tell crawlers what changed; stamped uniformly it tells them nothing, and
teaches them to ignore it.

### Algorithm
1. Collect `lastmod` values from every sitemap in the ledger.
2. Require at least **20** entries (`entity_trust.lastmod_uniform_count`).
3. Compute the largest share taken by one identical value, and by one calendar day.
4. Uniform when that share exceeds **90%** (`entity_trust.lastmod_uniform_share`).
5. **Contradicting evidence is required (rule R1):** the crawled pages' own published and
   updated dates must span at least a year.

| Uniform lastmod | Page dates span ≥ 365 days | Output |
| :--- | :--- | :--- |
| yes | yes | finding: content visibly years apart is all declared modified on one day |
| yes | no | observation: expected on a new site or a first static build |
| no | — | nothing |

**Fix:** emit `lastmod` from each document's content-change time, excluding template-only
changes — or omit `lastmod` entirely. An absent signal is better than a false one.

## `G3.FRESH.STALE_CONTENT_MARKERS`

Independent markers of an unmaintained site:

| Marker | Condition |
| :--- | :--- |
| copyright lag | the newest footer copyright year is 2+ years before the audit date |
| dormant publishing | on a publisher profile, the newest dated article is over 18 months old |
| "coming soon" | the phrase appears on a content page |
| past future tense | "will be held / launching in / coming in … <year>" where the year has passed |

**Fires at:** 2 or more independent markers (`false_positive_control.min_signals_for_inference`).
A stale copyright year alone is never reported; a single marker is recorded as posture.
Three or more markers raise confidence to high.
**Fix:** generate the copyright year; remove past-dated forward-looking copy; publish or review
on a stated cadence and show updated dates when you do.

## `G3.FRESH.UPDATED_WITHOUT_CHANGE`

**Requires** `--prior-ledger` pointing at a previous run's evidence ledger; otherwise skipped
with that remedy.
**Observation when:** a page's main-content text is identical between runs while its
modified date advanced. Refreshing dates without changing content is a trust-eroding pattern,
but a single re-save can produce it innocently, so it is never counted as a defect.
