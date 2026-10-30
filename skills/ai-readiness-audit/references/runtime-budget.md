# Runtime Budget: Under Five Minutes

The brief sets a hard constraint: an audit must finish in **under five minutes** on a
standard machine for a typical website. Audits usually miss that not because one step is
slow but because one step is *unbounded* — a 50,000-URL sitemap, a host taking 30 seconds
per page, a browser render that never settles. So every phase here is bounded, and the
whole run is governed by one budget object (`lib/auditkit/budget.py`) consulted at every
phase boundary.

## Defaults

All from `config/thresholds.json`, each with a rationale there.

| Budget | Default | Purpose |
| :--- | :-: | :--- |
| Wall clock | 240 s | leaves a full minute of headroom inside the five-minute limit |
| Analysis reserve | 60 s | held back from fetching for analysis, merge and report |
| Requests | 120 | robots, sitemaps, pages, probes and asset HEADs together |
| Content pages | 30 | template collapse makes more pages rarely change a conclusion |
| Browser renders | 5 | one per template cluster is what the render check needs |
| Per-host interval | 0.5 s | raised to `Crawl-delay` when robots.txt asks for longer |
| Per-host concurrency | 2 | hard cap 4 regardless of configuration |
| Timeouts | 5 s connect, 10 s read | clamped to the remaining fetch window |
| Response size | 5 MB | streamed and aborted beyond; decompression ratio capped at 50:1 |
| Specialist timeout | 90 s each | a slow analyzer is recorded as failed, not waited on |

## Expected time per phase

| Phase | Typical |
| :--- | :-: |
| 0 Preflight, robots.txt, sitemap index | ~5 s |
| 1 Discovery and stratified sampling | ~5–10 s |
| 2 Crawl: 30 pages at 2 concurrent, 0.5 s floor | ~60–90 s |
| 2 Probes, stylesheets, link sample | ~10–20 s |
| 2.5 Profiling | ~2–5 s |
| 2.5 Optional render tier, ≤5 pages | ~25–40 s (0 without Playwright) |
| 3 Six specialists in parallel processes | ~15–40 s |
| 3 Off-site identifiers (≤6 requests) | ~5 s |
| 4–6 Merge, recommendations, render, verify | ~5 s |
| **Total** | **~130–220 s** |

## The degradation ladder

When time runs short the budget does not truncate silently. It takes rungs of a documented
ladder, cheapest loss of confidence first, and records every rung in
`coverage.budget.degradations` so the report states exactly what was given up.

| Rung | Taken when the fetch window left falls below | What is lost | Why this order |
| :-: | :-: | :--- | :--- |
| 1 | 55% | the browser render tier | Tiers 1–2 still confirm client rendering from hydration payloads, so this costs the least confidence. |
| 2 | 40% | off-site `sameAs` resolution | On-site identity posture still stands; the sameAs checks are recorded as skipped. |
| 3 | 25% | page cap reduced (to at least the pages already fetched plus three, or half the cap) | Guaranteed pages and the per-section sample are already in hand from wave one. |
| 4 | 12% | asset HEAD checks (link sampling, stylesheets) | Broken-link and CSS-dependent checks are skipped or confidence-capped, and say so. |
| 5 | window closed | no further fetching | The audit proceeds to analysis with the evidence already collected. |

Thresholds are proportions of the fetch window, so the ladder behaves the same with
`--budget-seconds 60` as with 600.

## Honesty rules

- **Never** a silently shortened report. Every skipped path, check and rung is listed.
- **Never** a finding whose confidence ignores the degraded evidence behind it.
- One slow host cannot consume the budget: three consecutive 429 or 503 responses open a
  circuit breaker and the host is abandoned for the run, with partial coverage reported.
- If fewer than the minimum pages (default 3) were fetched when the budget closed, no report
  of single-page guesses is written: the orchestrator exits **5**.

## Tuning

- A fast, small site: `--budget-seconds 120 --max-pages 15`.
- A large site where depth matters more than time: raise `budget.max_pages` and
  `budget.max_requests` together, and keep `budget.wall_clock_seconds` under 280.
- A site that declares a long `Crawl-delay` will be crawled at that pace; the page cap is
  reached later or the ladder reduces it. This is the intended trade-off — the site's
  stated preference outranks coverage.
