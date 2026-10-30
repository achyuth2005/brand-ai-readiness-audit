#!/usr/bin/env python3
"""Phase 2 -- THE SINGLE POLITE CRAWL, into the evidence ledger.

The only network phase of the audit. Everything here goes through the egress gateway,
so every request inherits the method allowlist, the mutating-GET denylist, robots.txt
compliance, SSRF validation, per-host pacing and the time budget. This module decides
*what* to fetch; it has no way to decide *how*.

Fetch plan, in order of evidential value per request:

1. **Pages, in two waves.** The stratified frontier from discovery, then a second wave
   drawn from links on the pages just fetched, which adds templates the sitemap did not
   expose and records real click depth.
2. **Host-variant probes** (http/https, www/non-www) for canonical-host duplication.
3. **User-agent divergence probes** -- a small fixed set, truthful user-agents only.
4. **Shared stylesheets**, so overlay, scroll-lock and contrast checks read declared CSS.
5. **Internal link sample** by HEAD, reported as k/n rather than as an exhaustive check.
6. **Optional render tier** and the **off-site pass** for declared identifiers, both
   driven by the orchestrator after profiling.

The degradation ladder is consulted between steps, so a slow host costs coverage that is
recorded honestly, never a silently shortened report.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import bootstrap, budget as budget_mod, netgate, render, urlnorm  # noqa: E402
from auditkit.dom import Document  # noqa: E402

import discover  # noqa: E402  (same scripts directory)

PROBE_AGENTS = ("OAI-SearchBot", "PerplexityBot", "Claude-SearchBot")


def fetch_pages(gate: Any, items: Sequence[Tuple[str, Optional[int], str]],
                workers: int) -> List[Any]:
    """Fetch a wave of pages concurrently; results are returned in input order."""
    remaining = max(0, gate.budget.max_pages - gate.budget.pages_fetched)
    items = list(items)[:remaining]
    results: List[Any] = [None] * len(items)
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(gate.fetch, url, purpose="page", depth=depth, referrer=referrer): i
            for i, (url, depth, referrer) in enumerate(items)
        }
        for future in as_completed(futures):
            try:
                results[futures[future]] = future.result()
            except Exception:
                results[futures[future]] = None
    return results


def second_wave(gate: Any, wave: Sequence[Any], site_host: str,
                limit: int) -> List[Tuple[str, Optional[int], str]]:
    """Frontier drawn from links on pages already fetched, one new URL per section."""
    picked: List[Tuple[str, Optional[int], str]] = []
    sections: Set[str] = set()
    for entry in sorted([e for e in wave if e is not None and e.ok and e.is_html],
                        key=lambda e: e.url):
        document = Document(gate.ledger.text(entry), url=entry.url)
        for normal in discover.internal_links(document, entry.final_url or entry.url,
                                              site_host):
            key = urlnorm.dedupe_key(normal)
            if gate.ledger.has(key) or urlnorm.is_utility(normal):
                continue
            section = f"{discover._section(normal)}|{discover._primary_class(normal)}"
            if section in sections:
                continue
            sections.add(section)
            depth = entry.depth + 1 if entry.depth is not None else None
            picked.append((normal.url, depth, entry.url))
            if len(picked) >= limit:
                return picked
    return picked


def probe_host_variants(gate: Any, site_root: urlnorm.NormalUrl) -> None:
    for variant in urlnorm.host_variants(site_root):
        if urlnorm.normalise(variant).url == site_root.url or not gate.budget.may_request():
            continue
        gate.fetch(variant, purpose="probe", key_suffix="#hostprobe",
                   allow_non_document=True)


def probe_user_agents(gate: Any, rules: Any, pages: Sequence[Any],
                      config: Dict[str, Any]) -> str:
    """Issue the divergence probe with one retrieval agent the site permits. Returns it."""
    count = int(bootstrap.threshold(config, "crawl.ua_probe_urls", 3))
    candidates = sorted(e.url for e in pages
                        if e is not None and e.ok and e.is_html
                        and not urlnorm.is_utility(urlnorm.normalise(e.url)))[:count]
    if len(candidates) < 2:
        return ""
    paths = [urlnorm.normalise(u).path for u in candidates]
    for agent in PROBE_AGENTS:
        agent_ua = netgate.honest_user_agent(agent, gate.contact)
        if all(rules.verdict_for(agent_ua, p).allowed for p in paths):
            for url in candidates:
                if not gate.budget.may_request():
                    break
                gate.fetch(url, purpose="probe", probe_agent=agent)
            return agent
    return ""


def fetch_stylesheets(gate: Any, pages: Sequence[Any], site_host: str,
                      config: Dict[str, Any]) -> int:
    cap = int(bootstrap.threshold(config, "crawl.max_stylesheets", 5))
    hrefs: List[str] = []
    for entry in sorted([e for e in pages if e is not None and e.ok and e.is_html],
                        key=lambda e: e.url):
        document = Document(gate.ledger.text(entry), url=entry.url)
        for href in document.stylesheet_hrefs():
            normal = urlnorm.try_normalise(href, base=entry.final_url or entry.url)
            if normal and urlnorm.same_site(normal.host, site_host) \
                    and normal.url not in hrefs and not gate.ledger.has(normal.url):
                hrefs.append(normal.url)
    fetched = 0
    for url in hrefs[:cap]:
        if not gate.budget.may_check_assets():
            break
        gate.fetch(url, purpose="asset", allow_non_document=True)
        fetched += 1
    return fetched


def sample_internal_links(gate: Any, pages: Sequence[Any], site_host: str,
                          config: Dict[str, Any]) -> int:
    cap = int(bootstrap.threshold(config, "crawl.asset_head_sample", 10))
    targets: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for entry in sorted([e for e in pages if e is not None and e.ok and e.is_html],
                        key=lambda e: e.url):
        document = Document(gate.ledger.text(entry), url=entry.url)
        for link in document.links():
            if not link.in_main:
                continue
            normal = urlnorm.try_normalise(link.href, base=entry.final_url or entry.url)
            if normal is None or not urlnorm.same_site(normal.host, site_host):
                continue
            key = urlnorm.dedupe_key(normal)
            if key in seen or gate.ledger.has(key) or urlnorm.is_mutating_get(normal)[0]:
                continue
            seen.add(key)
            targets.append((normal.url, entry.url))
    done = 0
    for url, referrer in targets[:cap]:
        if not gate.budget.may_check_assets():
            break
        gate.head(url, referrer=referrer)
        done += 1
    return done


def probe_feeds(gate: Any, origin: str, lexicon_paths: Sequence[str]) -> None:
    for path in list(lexicon_paths)[:3]:
        if not gate.budget.may_request():
            return
        entry = gate.fetch(origin + path, purpose="feed", allow_non_document=True)
        if entry.ok:
            return


def render_tier(ledger: Any, urls: Sequence[str], budget: Any, config: Dict[str, Any],
                site_host: str, user_agent: str, mode: str) -> Dict[str, Any]:
    """Optional tier 3. The browser is the one component that makes requests outside the
    egress gateway, which is why it is constrained by request interception (GET only,
    same-site only, no interaction) and only renders pages the gateway already fetched
    successfully -- that is, pages robots.txt permits and SSRF validation passed."""
    if mode == "off":
        return {"mode": "static", "note": "the render tier was disabled with --render off",
                "rendered": 0}
    usable, reason = render.available()
    if not usable:
        return {"mode": "static", "note": reason, "rendered": 0}
    settle = int(bootstrap.threshold(config, "render.render_delta_chars", 500))  # noqa: F841
    rendered = 0
    bare = site_host[4:] if site_host.startswith("www.") else site_host
    with render.Renderer(allowed_hosts={bare, f"www.{bare}"}, user_agent=user_agent) as r:
        for url in urls:
            if not budget.may_render():
                budget.degrade(budget_mod.DEGRADE_RENDER,
                               "render cap or time budget reached during the render tier")
                break
            entry = ledger.get(url)
            if entry is None or not entry.ok:
                continue
            result = r.render(entry.final_url or entry.url)
            if result.ok:
                ledger.attach_rendered(entry, result.html)
                budget.note_render()
                rendered += 1
    return {"mode": "playwright" if rendered else "static",
            "note": "" if rendered else "the browser launched but no page rendered in time",
            "rendered": rendered}


def fetch_offsite(gate: Any, requests_path: Path, config: Dict[str, Any]) -> str:
    """Pass 1.5 of the two-pass protocol: validate and fetch declared off-site requests."""
    try:
        data = json.loads(Path(requests_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "disabled"
    requests = [r for r in data.get("requests", []) if isinstance(r, dict)]
    if not requests:
        return "no_identifiers_declared"
    cap = int(bootstrap.threshold(config, "entity_trust.max_sameas_to_check", 6))
    for request in requests[:cap]:
        # A declared request is data from an analyzer, not an instruction: anything other
        # than a plain GET for an off-site identifier is dropped.
        if request.get("method", "GET") != "GET" or request.get("purpose") != "offsite":
            continue
        if not gate.budget.may_go_offsite():
            gate.budget.degrade(budget_mod.DEGRADE_OFFSITE,
                                "the time budget closed during off-site corroboration")
            return "budget_exhausted"
        gate.fetch(str(request.get("url", "")), purpose="offsite", allow_non_document=True)
    return "enabled"
