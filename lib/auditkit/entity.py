"""Brand-name inference and definitional-sentence detection.

Two skills need to know what the brand is called: entity-trust (is the name consistent,
is the entity disambiguated?) and answerability (does any sentence say what the brand
*is*? is the brand ever named in body text?). The inference lives here, in the shared
library, so the two skills agree on the answer without importing each other -- which
would break test T4, swappability.

Inference is evidence-weighted across the surfaces where a site names itself, and never
uses the domain name except as a last-resort fallback. Every surface is kept with its
location, so a naming finding can cite exactly where each variant appears.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import lexicons, sdextract, textnorm, urlnorm
from .dom import selector_for

_TITLE_SEPARATOR_RE = re.compile(r"\s+[|\-–—:·•»/]\s+")
_COPYRIGHT_RE = re.compile(
    r"(?:©|\(c\)|copyright)\s*(?:(?:19|20)\d{2}\s*(?:[-–]\s*(?:19|20)\d{2})?)?"
    r"\s*,?\s*([A-Z][\w&.'\- ]{1,50}?)\s*(?:\.|,|all rights|\||$)",
    re.IGNORECASE,
)
_COPYRIGHT_YEAR_RE = re.compile(r"(?:©|\(c\)|copyright)[^0-9]{0,12}((?:19|20)\d{2})"
                                r"(?:\s*[-–]\s*((?:19|20)\d{2}))?", re.IGNORECASE)

ORG_TYPES = ("Organization", "Corporation", "LocalBusiness", "NewsMediaOrganization",
             "OnlineStore", "OnlineBusiness", "EducationalOrganization", "NGO",
             "ProfessionalService", "Store", "Restaurant")

# Surface weights for inference. Authored structured data is the most deliberate
# statement of identity; the domain label is a fallback because domains are frequently
# abbreviations, hyphenations or unrelated legacy names.
SURFACE_WEIGHTS: Dict[str, int] = {
    "schema:Organization.name": 5,
    "schema:WebSite.name": 3,
    "og:site_name": 3,
    "title-suffix": 2,
    "logo-alt": 2,
    "footer-copyright": 2,
    "meta:application-name": 1,
    "domain": 1,
}

_COPULA_WORDS = sorted({c.split()[0] for c in lexicons.DEFINITIONAL_COPULAS}, key=len,
                       reverse=True)
_DEFINITION_AFTER_RE = re.compile(
    r"^[\s,'’s]*(?:\([^)]{1,40}\)\s*)?(?:(?:" + "|".join(map(re.escape, _COPULA_WORDS))
    + r")\b|,\s+(?:a|an|the)\s+\w+)",
    re.IGNORECASE,
)


@dataclass
class NameSurface:
    """One place the site names itself."""

    surface: str
    value: str
    url: str
    url_hash: str
    ref_kind: str = "dom"        # dom | jsonpath
    selector: str = ""
    path: str = ""


@dataclass
class Brand:
    """The inferred brand name, with the evidence behind it."""

    name: str
    normalised: str
    confidence: str
    surfaces: List[NameSurface] = field(default_factory=list)
    clusters: List[List[NameSurface]] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return bool(self.normalised)

    @property
    def tokens(self) -> List[str]:
        return [t for t in self.normalised.split() if len(t) >= 2]

    def mentioned_in(self, text: str) -> bool:
        """True when ``text`` names the brand, case- and punctuation-insensitively."""
        if not self.normalised:
            return False
        haystack = " " + textnorm.normalise_brand_name(text) + " "
        if f" {self.normalised} " in haystack:
            return True
        # A multi-word brand is also anchored by its distinctive first token
        # ("Acme" for "Acme Tools"), as long as that token is not a common word.
        tokens = self.tokens
        if len(tokens) >= 2 and len(tokens[0]) >= 4 \
                and tokens[0] not in lexicons.COMMON_WORD_BRAND_RISK:
            return f" {tokens[0]} " in haystack
        return False

    def variant_summary(self) -> List[Dict[str, Any]]:
        out = []
        for cluster in self.clusters:
            values = Counter(s.value for s in cluster)
            out.append({
                "value": values.most_common(1)[0][0],
                "surfaces": sorted({s.surface for s in cluster}),
                "occurrences": len(cluster),
                "example_url": sorted(s.url for s in cluster)[0],
            })
        return out


def _clean(value: str) -> str:
    text = textnorm.normalise_text(value or "")
    text = re.sub(r"\b(logo|home|homepage)\b", "", text, flags=re.IGNORECASE)
    return textnorm.collapse_space(text.strip(" -|:,."))


def collect_surfaces(pages: Sequence[Any], site_host: str = "") -> List[NameSurface]:
    """Gather every naming surface from the crawled pages."""
    surfaces: List[NameSurface] = []
    title_segments: Counter = Counter()
    segment_pages: Dict[str, Any] = {}

    for page in pages:
        doc = page.doc
        for node in page.sd.nodes:
            if node.has_type(*ORG_TYPES):
                surface = "schema:Organization.name"
            elif node.has_type("WebSite"):
                surface = "schema:WebSite.name"
            else:
                continue
            name = _clean(sdextract.scalar(node.get("name")))
            if name:
                surfaces.append(NameSurface(surface, name, page.url, page.url_hash,
                                            "jsonpath", path=f"{node.json_path}.name"))

        for prop, surface in (("site_name", "og:site_name"),):
            value = _clean(doc.og(prop))
            if value:
                node = doc.soup.find("meta", attrs={"property": "og:site_name"})
                surfaces.append(NameSurface(surface, value, page.url, page.url_hash,
                                            selector=selector_for(node) if node else "head"))
        app_name = _clean(doc.meta("application-name"))
        if app_name:
            node = doc.soup.find("meta", attrs={"name": "application-name"})
            surfaces.append(NameSurface("meta:application-name", app_name, page.url,
                                        page.url_hash,
                                        selector=selector_for(node) if node else "head"))

        title = doc.title()
        parts = [p for p in _TITLE_SEPARATOR_RE.split(title) if p.strip()]
        if len(parts) >= 2:
            for segment in (parts[0], parts[-1]):
                cleaned = _clean(segment)
                if cleaned and textnorm.word_count(cleaned) <= 5:
                    title_segments[cleaned] += 1
                    segment_pages.setdefault(cleaned, page)

        for img in doc.soup.find_all("img"):
            ident = " ".join([img.get("id") or "", " ".join(img.get("class") or []),
                              img.get("src") or "", img.get("alt") or ""]).lower()
            if "logo" not in ident:
                continue
            if not doc.is_boilerplate(img) and img.find_parent("a") is None:
                continue
            alt = _clean(img.get("alt") or "")
            if alt and textnorm.word_count(alt) <= 5:
                surfaces.append(NameSurface("logo-alt", alt, page.url, page.url_hash,
                                            selector=selector_for(img)))
            break

        footer = doc.soup.find("footer")
        if footer is not None:
            match = _COPYRIGHT_RE.search(doc.visible_text(footer))
            if match:
                holder = _clean(match.group(1))
                if holder and textnorm.word_count(holder) <= 6:
                    surfaces.append(NameSurface("footer-copyright", holder, page.url,
                                                page.url_hash,
                                                selector=selector_for(footer)))

    # A title segment only counts as a naming surface when it repeats across pages; a
    # one-off segment is a page title, not a site name.
    for segment, count in sorted(title_segments.items()):
        if count >= 2 or len(pages) == 1:
            page = segment_pages[segment]
            surfaces.append(NameSurface("title-suffix", segment, page.url, page.url_hash,
                                        selector="title"))

    if site_host:
        label = urlnorm.registrable_hint(site_host).split(".")[0]
        if label:
            surfaces.append(NameSurface("domain", label.replace("-", " "), "", "",
                                        ref_kind="none"))
    return surfaces


def cluster_surfaces(surfaces: Sequence[NameSurface]) -> List[List[NameSurface]]:
    """Group surfaces whose names reduce to one another (long/short forms, suffixes)."""
    clusters: List[List[NameSurface]] = []
    for surface in sorted(surfaces, key=lambda s: (-SURFACE_WEIGHTS.get(s.surface, 1),
                                                   s.value, s.url)):
        placed = False
        for cluster in clusters:
            if textnorm.name_variants_reduce(surface.value, cluster[0].value):
                cluster.append(surface)
                placed = True
                break
        if not placed:
            clusters.append([surface])
    return clusters


def infer_brand(pages: Sequence[Any], site_host: str = "") -> Brand:
    """Infer the brand name from the heaviest-weighted cluster of naming surfaces."""
    surfaces = collect_surfaces(pages, site_host)
    named = [s for s in surfaces if s.surface != "domain"]
    clusters = cluster_surfaces(named)

    if not clusters:
        domain = [s for s in surfaces if s.surface == "domain"]
        if domain:
            return Brand(name=domain[0].value.title(),
                         normalised=textnorm.normalise_brand_name(domain[0].value),
                         confidence="low", surfaces=surfaces, clusters=[])
        return Brand(name="", normalised="", confidence="low", surfaces=surfaces)

    def weight(cluster: List[NameSurface]) -> Tuple[int, int]:
        return (sum(SURFACE_WEIGHTS.get(s.surface, 1) for s in cluster), len(cluster))

    ranked = sorted(clusters, key=lambda c: (-weight(c)[0], -weight(c)[1], c[0].value))
    best = ranked[0]
    preferred = [s for s in best if s.surface == "schema:Organization.name"] or best
    # Display the shortest common form: "Acme" rather than "Acme Tools Ltd".
    values = Counter(s.value for s in preferred)
    display = sorted(values, key=lambda v: (-values[v], len(v), v))[0]
    if _TITLE_SEPARATOR_RE.search(display):
        # "Acme | Tools for makers" is a name plus a tagline, even when the site's own
        # markup says it. Keep the segment the site also uses on its own, else the first.
        segments = [p.strip() for p in _TITLE_SEPARATOR_RE.split(display) if p.strip()]
        standalone = {textnorm.normalise_brand_name(s.value) for s in surfaces
                      if not _TITLE_SEPARATOR_RE.search(s.value)}
        display = next((seg for seg in segments
                        if textnorm.normalise_brand_name(seg) in standalone), segments[0])

    distinct_surfaces = {s.surface for s in best}
    confidence = "high" if len(distinct_surfaces) >= 3 else (
        "medium" if len(distinct_surfaces) == 2 else "low")
    return Brand(
        name=display, normalised=textnorm.normalise_brand_name(display),
        confidence=confidence, surfaces=surfaces,
        clusters=sorted(clusters, key=lambda c: (-weight(c)[0], c[0].value)),
    )


def find_definitional_sentence(texts: Sequence[str], brand: Brand) -> str:
    """Return the first sentence that says what the brand *is*, or ``""``.

    Matches "<Brand> is a ...", "<Brand> provides ...", "<Brand> helps ...", and the
    appositive "<Brand>, the ... company". The brand must be the subject: a sentence that
    merely mentions the brand somewhere is not a definition.
    """
    if not brand.known:
        return ""
    for text in texts:
        for sentence in textnorm.sentences(text):
            folded = textnorm.normalise_brand_name(sentence)
            position = folded.find(brand.normalised)
            if position < 0 or position > 40:
                continue
            lowered = textnorm.normalise_text(sentence).lower()
            anchor = brand.name.lower()
            index = lowered.find(anchor)
            if index < 0:
                index = lowered.find(brand.tokens[0]) if brand.tokens else -1
                anchor = brand.tokens[0] if brand.tokens else anchor
            if index < 0:
                continue
            after = lowered[index + len(anchor): index + len(anchor) + 80]
            after = re.sub(r"^\s*(inc|ltd|llc|gmbh|plc|corp|co)\.?", "", after)
            if _DEFINITION_AFTER_RE.match(after):
                return textnorm.truncate(sentence, 240)
    return ""


def copyright_years(pages: Sequence[Any]) -> List[Tuple[int, Any]]:
    """``(latest_year, page)`` for every page whose footer carries a copyright year."""
    out: List[Tuple[int, Any]] = []
    for page in pages:
        footer = page.doc.soup.find("footer")
        text = page.doc.visible_text(footer) if footer is not None else \
            page.doc.body_text()[-600:]
        match = _COPYRIGHT_YEAR_RE.search(text)
        if match:
            years = [int(y) for y in match.groups() if y]
            out.append((max(years), page))
    return out


def is_common_word_name(brand: Brand, short_chars: int) -> List[str]:
    """Ambiguity amplifiers that are observable without any network access."""
    amplifiers: List[str] = []
    if not brand.known:
        return amplifiers
    compact = brand.normalised.replace(" ", "")
    if brand.normalised in lexicons.COMMON_WORD_BRAND_RISK or (
            len(brand.tokens) == 1 and brand.tokens[0] in lexicons.COMMON_WORD_BRAND_RISK):
        amplifiers.append(f"the name '{brand.name}' is a common English word")
    if len(compact) <= short_chars:
        amplifiers.append(f"the name '{brand.name}' is {len(compact)} characters long, "
                          f"which collides heavily across the web")
    return amplifiers
