"""Template clustering -- the highest-leverage false-positive control in the package.

The problem it solves
---------------------
A site has twelve product pages generated from one template. That template forgets its
``Product`` markup. A naive auditor reports twelve findings. The reader sees a
twelve-item list and has no idea it is one edit in one file.

Clustering pages by DOM *skeleton* turns that into one template-scoped finding carrying
``12/12`` evidence. The same mechanism supplies:

* ``k/n`` evidence strings, so every claim is quantitative rather than adjectival
* the per-template median baselines that "thin", "truncated" and "boilerplate-dominant"
  judgements are measured against -- a page is thin *relative to its own template*,
  which is the only comparison that generalizes
* the sampling guarantee of at least one page per cluster, so a defect confined to one
  page class is never missed because the crawl happened to sample elsewhere

Why structure and not text
--------------------------
The skeleton is tag names, depth, and *de-numbered* class tokens -- text content is
excluded entirely. Two product pages describing different products have identical
structure and different words; two pages built from different templates have the reverse.
Keying on structure is what makes the clustering work on a site nobody has seen, without
any URL-pattern guessing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Class-token noise that varies per page or per build and must be stripped before
# hashing, or every page lands in its own cluster and the whole mechanism is inert.
_DIGIT_RUN_RE = re.compile(r"\d+")
_HASHLIKE_RE = re.compile(r"^[a-f0-9]{6,}$", re.IGNORECASE)
_CSS_MODULE_RE = re.compile(r"^(.*?)[-_][a-zA-Z0-9]{5,}$")
_UTILITY_NOISE_RE = re.compile(
    r"^(is-|has-|js-|active|current|selected|open|closed|visible|hidden|"
    r"animate|animated|loaded|loading|in-view|aos-)", re.IGNORECASE
)

LANDMARK_TAGS = ("header", "nav", "main", "footer", "article", "section", "form",
                 "aside", "table", "ul", "ol", "figure")

DEFAULT_DEPTH = 6
DEFAULT_SHINGLE_K = 5
DEFAULT_JACCARD = 0.85


def _clean_class_token(token: str) -> str:
    """Reduce a class token to its stable stem.

    Strips digit runs (``col-6`` -> ``col-``), content hashes (``css-1x2y3z``), CSS-module
    suffixes (``Button_root__a1b2c``) and state modifiers (``is-active``). What remains is
    the part a template author wrote, which is what identifies the template.
    """
    token = token.strip()
    if not token:
        return ""
    if _UTILITY_NOISE_RE.match(token):
        return ""
    if _HASHLIKE_RE.match(token):
        return ""
    module = _CSS_MODULE_RE.match(token)
    if module and len(module.group(1)) >= 3:
        token = module.group(1)
    token = _DIGIT_RUN_RE.sub("", token)
    return token.lower().strip("-_")


def skeleton(document: Any, depth: int = DEFAULT_DEPTH) -> List[str]:
    """The structural fingerprint of a page, as an ordered list of step strings.

    Each step is ``tag@depth[classes]``. Text is excluded. Depth is capped so that deep
    content lists (which vary with how many items a page happens to hold) do not
    dominate the fingerprint.
    """
    body = document.soup.find("body") or document.soup
    steps: List[str] = []
    _walk(body, 0, depth, steps)

    counts = {tag: len(document.soup.find_all(tag)) for tag in LANDMARK_TAGS}
    steps.append("|landmarks:" + ",".join(
        f"{tag}={_bucket(counts[tag])}" for tag in LANDMARK_TAGS
    ))
    return steps


def _walk(node: Any, level: int, max_depth: int, out: List[str]) -> None:
    if level > max_depth or node is None:
        return
    for child in getattr(node, "children", ()):
        name = getattr(child, "name", None)
        if name is None or name in ("script", "style", "template", "noscript"):
            continue
        try:
            tokens = [_clean_class_token(c) for c in (child.get("class") or [])]
        except (AttributeError, TypeError):
            tokens = []
        tokens = sorted({t for t in tokens if t})[:4]
        out.append(f"{name}@{level}[{'.'.join(tokens)}]")
        _walk(child, level + 1, max_depth, out)


def _bucket(count: int) -> str:
    """Bucket a count so 11 and 12 list items do not split a cluster."""
    if count == 0:
        return "0"
    if count == 1:
        return "1"
    if count <= 3:
        return "2-3"
    if count <= 8:
        return "4-8"
    if count <= 20:
        return "9-20"
    return "20+"


def fingerprint(document: Any, depth: int = DEFAULT_DEPTH) -> str:
    """Exact-match structural hash. ``hashlib``, never ``hash()`` -- determinism."""
    joined = "\n".join(skeleton(document, depth))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def shingles(steps: Sequence[str], k: int = DEFAULT_SHINGLE_K) -> set:
    """K-gram shingles of a skeleton, for near-duplicate comparison."""
    if len(steps) < k:
        return {"|".join(steps)} if steps else set()
    return {"|".join(steps[i:i + k]) for i in range(len(steps) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Cluster:
    """A set of pages sharing one template."""

    id: str
    fingerprint: str
    urls: List[str] = field(default_factory=list)
    shingles: set = field(default_factory=set, repr=False)
    path_classes: Dict[str, int] = field(default_factory=dict)
    metrics: Dict[str, List[float]] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.urls)

    def record_metric(self, name: str, value: float) -> None:
        self.metrics.setdefault(name, []).append(float(value))

    def median(self, name: str) -> float:
        from . import textnorm
        return textnorm.median(self.metrics.get(name, []))

    def dominant_path_class(self) -> str:
        if not self.path_classes:
            return ""
        return max(sorted(self.path_classes.items()), key=lambda kv: kv[1])[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "fingerprint": self.fingerprint,
            "size": self.size,
            "urls": sorted(self.urls),
            "example_urls": sorted(self.urls)[:3],
            "dominant_path_class": self.dominant_path_class(),
            "path_classes": dict(sorted(self.path_classes.items())),
            "medians": {name: round(self.median(name), 2)
                        for name in sorted(self.metrics)},
        }


class ClusterSet:
    """All template clusters for one crawl, plus the lookups analyzers need."""

    def __init__(self, jaccard_threshold: float = DEFAULT_JACCARD,
                 shingle_k: int = DEFAULT_SHINGLE_K, depth: int = DEFAULT_DEPTH):
        self.jaccard_threshold = jaccard_threshold
        self.shingle_k = shingle_k
        self.depth = depth
        self.clusters: List[Cluster] = []
        self._by_url: Dict[str, str] = {}
        self._by_fingerprint: Dict[str, Cluster] = {}

    def add(self, url: str, document: Any, path_classes: Sequence[str] = ()) -> Cluster:
        """Assign ``url`` to a cluster, creating one when nothing matches.

        Exact fingerprint match first (cheap and the common case), then near-duplicate
        matching by shingle overlap, which catches templates that differ only by an
        optional module -- a product page with and without a reviews block.
        """
        steps = skeleton(document, self.depth)
        fp = hashlib.sha1("\n".join(steps).encode("utf-8")).hexdigest()[:16]

        cluster = self._by_fingerprint.get(fp)
        if cluster is None:
            page_shingles = shingles(steps, self.shingle_k)
            best, best_score = None, 0.0
            for candidate in self.clusters:
                score = jaccard(page_shingles, candidate.shingles)
                if score > best_score:
                    best, best_score = candidate, score
            if best is not None and best_score >= self.jaccard_threshold:
                cluster = best
            else:
                cluster = Cluster(
                    id=f"T{len(self.clusters) + 1}",
                    fingerprint=fp,
                    shingles=page_shingles,
                )
                self.clusters.append(cluster)
            self._by_fingerprint[fp] = cluster

        if url not in cluster.urls:
            cluster.urls.append(url)
        for path_class in path_classes:
            cluster.path_classes[path_class] = cluster.path_classes.get(path_class, 0) + 1
        self._by_url[url] = cluster.id
        return cluster

    # -- lookups ------------------------------------------------------------------------

    def cluster_for(self, url: str) -> Optional[Cluster]:
        cluster_id = self._by_url.get(url)
        if cluster_id is None:
            return None
        return self.by_id(cluster_id)

    def by_id(self, cluster_id: str) -> Optional[Cluster]:
        for cluster in self.clusters:
            if cluster.id == cluster_id:
                return cluster
        return None

    def cluster_id_for(self, url: str) -> str:
        return self._by_url.get(url, "")

    def __len__(self) -> int:
        return len(self.clusters)

    def __iter__(self):
        return iter(sorted(self.clusters, key=lambda c: (-c.size, c.id)))

    def representative_urls(self) -> List[str]:
        """One URL per cluster -- the sampling guarantee, and the render-tier subset."""
        return [sorted(c.urls)[0] for c in sorted(self.clusters, key=lambda c: (-c.size, c.id))
                if c.urls]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": len(self.clusters),
            "jaccard_threshold": self.jaccard_threshold,
            "clusters": [c.to_dict() for c in self],
        }


def collapse(observations: Sequence[Tuple[str, Any]], clusters: ClusterSet,
             min_sample: int = 3) -> List[Dict[str, Any]]:
    """Collapse per-page observations into template-scoped groups (rule R3).

    ``observations`` is ``(url, payload)``. The returned groups each carry the cluster,
    the affected and sampled counts, and enough examples to act on.

    A group is only promoted to template scope when the cluster has at least
    ``min_sample`` pages *and* the defect is on the majority of them. Below that the
    observation stays page-scoped, because "1 of 12 pages" is a page defect or noise --
    never a statement about the template (rule R2).
    """
    by_cluster: Dict[str, List[Tuple[str, Any]]] = {}
    orphans: List[Tuple[str, Any]] = []

    for url, payload in observations:
        cluster = clusters.cluster_for(url)
        if cluster is None:
            orphans.append((url, payload))
            continue
        by_cluster.setdefault(cluster.id, []).append((url, payload))

    groups: List[Dict[str, Any]] = []
    for cluster_id in sorted(by_cluster):
        cluster = clusters.by_id(cluster_id)
        if cluster is None:
            continue
        # Sorted so payloads[0] always belongs to example_urls[0]: evidence refs built
        # from a payload must point at the page the evidence names.
        hits = sorted(by_cluster[cluster_id], key=lambda h: h[0])
        # A page carrying two offending nodes is still one affected page.
        affected, sampled = len({url for url, _ in hits}), max(cluster.size, 1)
        template_bound = (sampled >= min_sample and affected / max(1, sampled) >= 0.5)
        groups.append({
            "level": "template" if template_bound else "page",
            "template_id": cluster.id,
            "pages_affected": affected,
            "pages_sampled": sampled,
            "example_urls": sorted({url for url, _ in hits})[:3],
            "path_class": cluster.dominant_path_class(),
            "payloads": [payload for _url, payload in hits],
            "urls": sorted({url for url, _ in hits}),
            "template_bound": template_bound,
        })

    for url, payload in sorted(orphans, key=lambda h: h[0]):
        groups.append({
            "level": "page",
            "template_id": "",
            "pages_affected": 1,
            "pages_sampled": 1,
            "example_urls": [url],
            "path_class": "",
            "payloads": [payload],
            "urls": [url],
            "template_bound": False,
        })
    return groups


def site_wide_fraction(affected_urls: Iterable[str], all_urls: Sequence[str]) -> float:
    """Share of sampled pages affected. Feeds the site-wide scope decision."""
    total = len(all_urls)
    if total == 0:
        return 0.0
    return len(set(affected_urls) & set(all_urls)) / total
