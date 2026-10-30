#!/usr/bin/env python3
"""Phases 0-1 -- PREFLIGHT, DISCOVERY and STRATIFIED SAMPLING.

Preflight refuses unsafe input before a single request is made: an unsupported scheme or
port, embedded credentials, or a host that resolves to a private, loopback, link-local
or metadata address. The audit is an SSRF primitive unless this runs first.

Discovery then builds the URL frontier the single polite crawl will fetch. Its job is
not to find *many* pages but to find the *right* ones, because a sampled audit is only
as good as its sample:

* the homepage, and the entity-home page (about/company) whenever one exists
* at least one page from every site section, so a defect confined to one page class
  is not missed because the crawl happened to sample elsewhere
* at least one deep leaf, since context-retention failures only show on deep pages
* the site's own declared inventory (sitemaps), balanced against its link graph

robots.txt is fetched **first** for every origin, by a lazy resolver the egress gateway
consults before each request -- so the audit never fetches a disallowed path, including
on hosts it only reaches through a redirect or an off-site identifier.
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import bootstrap, ledger as ledger_mod, robots as robots_mod  # noqa: E402
from auditkit import sitemap, ssrf, urlnorm  # noqa: E402
from auditkit.dom import Document  # noqa: E402

# Content classes the sample tries to include first, in order.
PRIORITY_CLASSES = ("about", "pricing", "product", "article", "docs", "service", "contact",
                    "faq", "event", "careers")


class UnsafeTarget(ValueError):
    """Exit code 3: the requested target must not be audited."""


class UnreachableTarget(RuntimeError):
    """Exit code 4: the target could not be reached at all."""


def preflight(raw: str) -> urlnorm.NormalUrl:
    """Validate the audit target and return its canonical root URL."""
    text = (raw or "").strip()
    if not text:
        raise UnsafeTarget("no URL or domain was given")
    if "://" not in text:
        text = "https://" + text
    try:
        target = urlnorm.normalise(text)
    except urlnorm.UnsafeUrl as exc:
        raise UnsafeTarget(str(exc)) from None
    port = target.port or (443 if target.scheme == "https" else 80)
    try:
        ssrf.resolve_and_validate(target.host, port)
    except ssrf.SSRFDenied as exc:
        message = str(exc)
        if "does not resolve" in message or "no usable" in message:
            raise UnreachableTarget(message) from None
        raise UnsafeTarget(message) from None
    return urlnorm.normalise(target.origin + "/")


class RobotsResolver:
    """Fetch and cache robots.txt per origin, on first contact with that origin.

    Passed to the egress gateway as its ``robots_for`` callback. The robots.txt request
    itself is always permitted (RFC 9309), which is the one case where the resolver
    answers allow-all -- and only for the origin whose file is being fetched.
    """

    def __init__(self, site_host: str, limiter: Any, user_agent: str):
        self.site_host = site_host
        self.limiter = limiter
        self.user_agent = user_agent
        self.cache: Dict[str, robots_mod.RobotsFile] = {}
        self._in_progress: Set[str] = set()
        self.gate: Any = None

    def bind(self, gate: Any) -> None:
        self.gate = gate

    def __call__(self, origin: str) -> robots_mod.RobotsFile:
        if origin in self.cache:
            return self.cache[origin]
        if origin in self._in_progress or self.gate is None:
            return robots_mod.allow_all("the robots.txt request itself is always permitted")
        self._in_progress.add(origin)
        try:
            normal = urlnorm.normalise(origin + "/robots.txt")
            purpose = "robots" if urlnorm.same_site(normal.host, self.site_host) \
                else "offsite"
            entry = self.gate.fetch(normal.url, purpose=purpose, allow_non_document=True)
            body = self.gate.ledger.body(entry) if entry.content_hash else None
            if entry.outcome == ledger_mod.OUTCOME_BLOCKED_POLICY:
                # Could not be fetched safely: treat as unavailable, i.e. disallow-all for
                # our own fetching. The conservative reading.
                rules = robots_mod.RobotsFile(robots_mod.STATE_UNAVAILABLE,
                                              source_url=normal.url)
            else:
                rules = robots_mod.RobotsFile.from_response(
                    entry.status, body, source_url=normal.url,
                    timed_out=entry.outcome in (ledger_mod.OUTCOME_TIMEOUT,
                                                ledger_mod.OUTCOME_NETWORK_ERROR,
                                                ledger_mod.OUTCOME_CIRCUIT_OPEN,
                                                ledger_mod.OUTCOME_BUDGET))
            if rules.text:
                rules.warnings.extend(robots_mod.cross_check_with_protego(
                    rules.text, self.user_agent, ["/"]))
            self.limiter.configure_host(normal.host, rules.crawl_delay(self.user_agent))
        finally:
            self._in_progress.discard(origin)
        self.cache[origin] = rules
        return rules


def discover_sitemaps(gate: Any, rules: robots_mod.RobotsFile, origin: str,
                      config: Dict[str, Any], site_host: str) -> List[str]:
    """Fetch declared and conventional sitemaps, following index children within caps."""
    max_children = int(bootstrap.threshold(config, "crawl.max_sitemap_children", 10))
    max_urls = int(bootstrap.threshold(config, "crawl.max_sitemap_urls", 5000))
    declared = rules.sitemap_urls()
    queue: List[str] = list(declared) or [origin + "/sitemap.xml"]
    tried_index = bool(declared)
    seen: Set[str] = set()
    urls: List[str] = []
    children = 0

    while queue and gate.budget.may_request():
        normal = urlnorm.try_normalise(queue.pop(0))
        if normal is None or normal.url in seen or not urlnorm.same_site(normal.host,
                                                                        site_host):
            continue
        seen.add(normal.url)
        entry = gate.fetch(normal.url, purpose="sitemap", allow_non_document=True)
        if not entry.ok:
            if not tried_index:
                tried_index = True
                queue.append(origin + "/sitemap_index.xml")
            continue
        parsed = sitemap.parse(gate.ledger.body(entry))
        urls.extend(parsed["urls"][: max(0, max_urls - len(urls))])
        for child in parsed["children"]:
            if children >= max_children:
                break
            queue.append(child)
            children += 1
        if len(urls) >= max_urls:
            break
    return sorted(set(urls))


def probe_well_known(gate: Any, origin: str) -> None:
    """Request /llms.txt once, so its presence or absence is evidence, not a guess."""
    if gate.budget.may_request():
        gate.fetch(origin + "/llms.txt", purpose="asset", allow_non_document=True)


def _section(normal: urlnorm.NormalUrl) -> str:
    parts = [p for p in normal.path.split("/") if p]
    return parts[0].lower() if parts else ""


def _primary_class(normal: urlnorm.NormalUrl) -> str:
    classes = urlnorm.path_classes(normal)
    for name in PRIORITY_CLASSES:
        if name in classes:
            return name
    return classes[0] if classes else "generic"


def internal_links(document: Document, base: str, site_host: str) -> List[urlnorm.NormalUrl]:
    """Crawlable same-site document links, in document order, de-duplicated."""
    out: "OrderedDict[str, urlnorm.NormalUrl]" = OrderedDict()
    for link in document.links():
        href = link.href
        if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        if "nofollow" in link.rel:
            continue  # owner intent is respected for frontier expansion
        normal = urlnorm.try_normalise(href, base=base)
        if normal is None or not urlnorm.same_site(normal.host, site_host):
            continue
        if not urlnorm.is_document(normal) or urlnorm.is_mutating_get(normal)[0]:
            continue
        out.setdefault(urlnorm.dedupe_key(normal), normal)
    return list(out.values())


def build_frontier(home_url: str, home_doc: Optional[Document], sitemap_urls: Sequence[str],
                   site_host: str, origin: str, max_pages: int,
                   already: Set[str]) -> List[Tuple[str, Optional[int], str]]:
    """Stratified sample: ``(url, click_depth, referrer)`` in fetch order.

    Round-robin across (section, page class) groups so every part of the site is
    represented before any part is sampled twice; shallower pages first within a group.
    Click depth is known for links found on the homepage and unknown (``None``) for
    sitemap-only URLs, and the report never pretends otherwise.
    """
    candidates: "OrderedDict[str, Tuple[urlnorm.NormalUrl, Optional[int], str]]" = OrderedDict()
    if home_doc is not None:
        for normal in internal_links(home_doc, home_url, site_host):
            candidates.setdefault(urlnorm.dedupe_key(normal), (normal, 1, home_url))
    for raw in sitemap_urls:
        normal = urlnorm.try_normalise(raw)
        if normal is None or not urlnorm.same_site(normal.host, site_host):
            continue
        if not urlnorm.is_document(normal) or urlnorm.is_mutating_get(normal)[0]:
            continue
        candidates.setdefault(urlnorm.dedupe_key(normal), (normal, None, "sitemap"))

    for key in list(candidates):
        normal = candidates[key][0]
        if key in already or urlnorm.is_utility(normal) or normal.path in ("", "/"):
            del candidates[key]

    groups: Dict[Tuple[str, str], List[Tuple[urlnorm.NormalUrl, Optional[int], str]]] = {}
    for normal, depth, referrer in candidates.values():
        groups.setdefault((_section(normal), _primary_class(normal)), []).append(
            (normal, depth, referrer))
    for members in groups.values():
        members.sort(key=lambda m: (m[1] is None, m[1] or 0, urlnorm.depth(m[0]),
                                    m[0].url))

    def group_rank(key: Tuple[str, str]) -> Tuple[int, str, str]:
        cls = key[1]
        return (PRIORITY_CLASSES.index(cls) if cls in PRIORITY_CLASSES
                else len(PRIORITY_CLASSES), key[0], cls)

    ordered_keys = sorted(groups, key=group_rank)
    selected: List[Tuple[str, Optional[int], str]] = []
    budget = max(0, max_pages - 1)
    round_index = 0
    while len(selected) < budget and any(len(groups[k]) > round_index for k in ordered_keys):
        for key in ordered_keys:
            if len(groups[key]) > round_index and len(selected) < budget:
                normal, depth, referrer = groups[key][round_index]
                selected.append((normal.url, depth, referrer))
        round_index += 1

    if selected and not any(urlnorm.depth(urlnorm.normalise(u)) >= 2 for u, _d, _r in selected):
        deep = [(n.url, d, r) for n, d, r in candidates.values() if urlnorm.depth(n) >= 2]
        if deep:
            selected[-1 if len(selected) >= budget else len(selected):] = [deep[0]]

    if not any("about" in urlnorm.path_classes(urlnorm.normalise(u)) for u, _d, _r in selected):
        guess = urlnorm.normalise(origin + "/about").url
        if guess not in already:
            # One guessed entity-home request. Its 404, if any, is evidence for the
            # corroboration checks rather than wasted budget.
            selected.insert(0, (guess, None, "entity-home guess"))
    return selected[:max(1, budget)]
