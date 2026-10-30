"""Evidence-based site-type classification.

**The site's type is never inferred from its domain name, brand, or TLD.** That rule is
the difference between a package that generalizes and one that has memorised examples.
A shop is a shop because it has ``Product`` markup, a cart path, price patterns across a
template cluster and add-to-cart controls -- not because its domain contains "shop".

Why classify at all
-------------------
Profile conditioning is what lets the audit ask the right question. It never asks "does
this page have ``Product`` markup"; it asks "given the evidence says this is a product
page, does it have what a product page needs". The profile selects:

* the expected structured-data types and their required properties
* which fact classes count as *primary* (a missing price is critical on a shop and not a
  finding on a documentation site)
* which checks do not apply at all (question-shaped headings are not a defect on a
  product detail page)

Refusing to guess is a feature
------------------------------
When the top two profiles score within a narrow margin, the result is ``ambiguous`` and
only the type-agnostic core checks run. The report says so. A confident wrong profile
produces a page of confident wrong findings, which is far worse than a smaller report
that states what it could not determine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from . import lexicons, textnorm

PROFILES = (
    "ecommerce", "publisher", "local_business", "saas_software",
    "docs_knowledge", "marketing_brochure", "marketplace_directory",
)
AMBIGUOUS = "ambiguous"

# Signal weights. Structured data is weighted highest because it is an explicit, authored
# declaration of what the site is; path and text signals are corroborating.
WEIGHT_SCHEMA = 3.0
WEIGHT_PATH = 1.5
WEIGHT_TEXT = 1.0
WEIGHT_STRUCTURE = 2.0

DEFAULT_AMBIGUITY_MARGIN = 0.15


@dataclass
class SiteProfile:
    """A classification plus the evidence that produced it."""

    detected_type: str
    confidence: str
    scores: Dict[str, float] = field(default_factory=dict)
    signals: List[str] = field(default_factory=list)
    runner_up: str = ""
    margin: float = 0.0

    @property
    def is_ambiguous(self) -> bool:
        return self.detected_type == AMBIGUOUS

    def expected_types(self) -> Tuple[str, ...]:
        entry = lexicons.PROFILE_EXPECTED_TYPES.get(self.detected_type)
        base = lexicons.PROFILE_EXPECTED_TYPES["_any"]["expected"]
        if entry is None:
            return tuple(base)
        return tuple(dict.fromkeys(tuple(entry["expected"]) + tuple(base)))

    def required_props(self) -> Tuple[str, ...]:
        entry = lexicons.PROFILE_EXPECTED_TYPES.get(self.detected_type)
        return tuple(entry["required"]) if entry else ()

    def recommended_props(self) -> Tuple[str, ...]:
        entry = lexicons.PROFILE_EXPECTED_TYPES.get(self.detected_type)
        return tuple(entry["recommended"]) if entry else ()

    def primary_fact_classes(self) -> Tuple[str, ...]:
        return lexicons.PROFILE_PRIMARY_FACTS.get(self.detected_type, ())

    def excluded_checks(self) -> Tuple[str, ...]:
        return lexicons.PROFILE_CHECK_EXCLUSIONS.get(self.detected_type, ())

    def allows(self, check_id: str) -> bool:
        """False when this check does not apply to this profile (a scoping FP guard)."""
        return check_id not in self.excluded_checks()

    def fact_weight_for(self, fact_class: str) -> str:
        """Map a fact class to a severity weight class, conditioned on the profile."""
        if fact_class in self.primary_fact_classes():
            return "primary"
        if fact_class in lexicons.PRIMARY_FACT_CLASSES:
            return "secondary"
        return "enrichment"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "detected_type": self.detected_type,
            "confidence": self.confidence,
            "signals": list(self.signals)[:12],
            "scores": {k: round(v, 2) for k, v in sorted(self.scores.items())},
            "runner_up": self.runner_up,
            "margin": round(self.margin, 3),
            "expected_schema_types": list(self.expected_types()),
            "primary_fact_classes": list(self.primary_fact_classes()),
            "checks_not_applicable": list(self.excluded_checks()),
            "method": ("scored from on-page evidence only: structured-data types, path "
                       "classes, page text patterns and interaction controls. The "
                       "domain name is never used."),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SiteProfile":
        return cls(
            detected_type=data.get("detected_type", AMBIGUOUS),
            confidence=data.get("confidence", "low"),
            scores={k: float(v) for k, v in (data.get("scores") or {}).items()},
            signals=list(data.get("signals") or []),
            runner_up=data.get("runner_up", ""),
            margin=float(data.get("margin", 0.0)),
        )


def classify(pages: Sequence[Dict[str, Any]],
             sitemap_urls: Sequence[str] = (),
             ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN) -> SiteProfile:
    """Score every profile from page evidence and return the winner, or ``ambiguous``.

    ``pages`` entries carry what the crawl already knows about each page::

        {"url", "path_classes", "schema_types", "text_lower", "has_price",
         "has_add_to_cart", "has_byline", "has_address", "has_hours",
         "has_code_block", "has_toc", "has_facets", "template_id"}

    Passing pre-extracted facts rather than documents keeps this function cheap and
    unit-testable, and keeps the extraction logic in one place.
    """
    scores: Dict[str, float] = {p: 0.0 for p in PROFILES}
    signals: Dict[str, List[str]] = {p: [] for p in PROFILES}

    all_urls = [p.get("url", "") for p in pages] + list(sitemap_urls)
    # Paths only. The host name is never evidence of what a site is: a site served from
    # "shop.example" must not score as e-commerce because "/shop" appears in its origin.
    joined_paths = " ".join(urlsplit(u).path for u in all_urls).lower()

    schema_types: Dict[str, int] = {}
    for page in pages:
        for type_name in page.get("schema_types") or ():
            schema_types[type_name] = schema_types.get(type_name, 0) + 1

    # --- structured-data signals -------------------------------------------------------
    for profile, types in lexicons.PROFILE_SCHEMA_SIGNALS.items():
        for type_name in types:
            count = schema_types.get(type_name, 0)
            if count:
                scores[profile] += WEIGHT_SCHEMA * min(count, 4) / 2.0
                signals[profile].append(f"{type_name} markup on {count} page(s)")

    # --- path signals ------------------------------------------------------------------
    for profile, paths in lexicons.PROFILE_PATH_SIGNALS.items():
        hits = [path for path in paths if path in joined_paths]
        if hits:
            scores[profile] += WEIGHT_PATH * min(len(hits), 6)
            signals[profile].append(f"path classes present: {', '.join(sorted(hits)[:4])}")

    # --- text signals ------------------------------------------------------------------
    corpus = " ".join((page.get("text_lower") or "")[:20000] for page in pages)
    for profile, phrases in lexicons.PROFILE_TEXT_SIGNALS.items():
        hits = [phrase for phrase in phrases if phrase in corpus]
        if hits:
            scores[profile] += WEIGHT_TEXT * min(len(hits), 8)
            signals[profile].append(
                f"page text contains: {', '.join(repr(h) for h in sorted(hits)[:3])}"
            )

    # --- structural / interaction signals ---------------------------------------------
    price_pages = [p for p in pages if p.get("has_price")]
    cart_pages = [p for p in pages if p.get("has_add_to_cart")]
    byline_pages = [p for p in pages if p.get("has_byline")]
    address_pages = [p for p in pages if p.get("has_address")]
    hours_pages = [p for p in pages if p.get("has_hours")]
    code_pages = [p for p in pages if p.get("has_code_block")]
    toc_pages = [p for p in pages if p.get("has_toc")]
    facet_pages = [p for p in pages if p.get("has_facets")]

    if cart_pages:
        scores["ecommerce"] += WEIGHT_STRUCTURE * min(len(cart_pages), 5)
        signals["ecommerce"].append(
            f"add-to-cart control on {len(cart_pages)} sampled page(s)")
    # A price pattern repeated across a template cluster is much stronger evidence than
    # a single price on one page (which every SaaS homepage has).
    price_clusters = {p.get("template_id") for p in price_pages if p.get("template_id")}
    if len(price_pages) >= 2:
        scores["ecommerce"] += WEIGHT_STRUCTURE * min(len(price_pages), 4) / 2.0
        scores["saas_software"] += WEIGHT_PATH
        signals["ecommerce"].append(
            f"price pattern on {len(price_pages)} page(s) across "
            f"{len(price_clusters)} template cluster(s)")
    if byline_pages:
        scores["publisher"] += WEIGHT_STRUCTURE * min(len(byline_pages), 5)
        signals["publisher"].append(f"author byline on {len(byline_pages)} page(s)")
    if address_pages and hours_pages:
        scores["local_business"] += WEIGHT_STRUCTURE * 3
        signals["local_business"].append(
            f"postal address on {len(address_pages)} page(s) and opening hours on "
            f"{len(hours_pages)}")
    elif address_pages:
        scores["local_business"] += WEIGHT_PATH
        signals["local_business"].append(f"postal address on {len(address_pages)} page(s)")
    if code_pages:
        scores["docs_knowledge"] += WEIGHT_STRUCTURE * min(len(code_pages), 5)
        signals["docs_knowledge"].append(f"code blocks on {len(code_pages)} page(s)")
    if toc_pages:
        scores["docs_knowledge"] += WEIGHT_PATH * min(len(toc_pages), 3)
        signals["docs_knowledge"].append(f"in-page table of contents on "
                                         f"{len(toc_pages)} page(s)")
    if facet_pages:
        scores["marketplace_directory"] += WEIGHT_STRUCTURE * min(len(facet_pages), 4)
        signals["marketplace_directory"].append(
            f"filter/facet controls on {len(facet_pages)} page(s)")

    # A small site with no commerce, article or docs signal is a brochure. Stated as a
    # weak positive rather than a fallback, so it has to actually win on evidence.
    total_urls = len(set(all_urls))
    if total_urls <= 25 and not cart_pages and not byline_pages and not code_pages:
        scores["marketing_brochure"] += WEIGHT_STRUCTURE
        signals["marketing_brochure"].append(
            f"small site ({total_urls} known URLs) with no commerce, article or "
            f"documentation signals")

    # --- decide ------------------------------------------------------------------------
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top, top_score = ranked[0]
    runner_up, runner_score = ranked[1] if len(ranked) > 1 else ("", 0.0)

    if top_score <= 0:
        return SiteProfile(
            detected_type=AMBIGUOUS, confidence="low", scores=scores,
            signals=["no profile signal reached its threshold; only type-agnostic "
                     "checks were run"],
            runner_up=runner_up, margin=0.0,
        )

    margin = (top_score - runner_score) / top_score if top_score else 0.0
    if margin < ambiguity_margin:
        return SiteProfile(
            detected_type=AMBIGUOUS,
            confidence="low",
            scores=scores,
            signals=[
                f"top two profiles scored within {margin:.0%} "
                f"({top} {top_score:.1f} vs {runner_up} {runner_score:.1f}), which is "
                f"inside the {ambiguity_margin:.0%} ambiguity margin, so no type was "
                f"assumed and only type-agnostic checks were run"
            ] + signals[top][:3] + signals[runner_up][:3],
            runner_up=runner_up,
            margin=margin,
        )

    # Confidence reflects both separation and absolute evidence, so a clear winner on
    # thin evidence does not read as certain.
    if margin >= 0.45 and top_score >= 12:
        confidence = "high"
    elif margin >= 0.25 and top_score >= 6:
        confidence = "medium"
    else:
        confidence = "low"

    return SiteProfile(
        detected_type=top, confidence=confidence, scores=scores,
        signals=signals[top][:8], runner_up=runner_up, margin=margin,
    )


# --------------------------------------------------------------------------------------
# Per-page evidence extraction
# --------------------------------------------------------------------------------------

_CART_SELECTORS = (
    "[name*=add-to-cart]", "[id*=add-to-cart]", "[class*=add-to-cart]",
    "[class*=addtocart]", "[data-add-to-cart]", "form[action*=cart]",
    "button[name=add]", "[class*=add-to-bag]", "[class*=add-to-basket]",
    "[class*=buy-now]", "[class*=product-form]",
)

_FACET_SELECTORS = (
    "[class*=facet]", "[class*=filter]", "[id*=filter]", "[class*=refine]",
    "[data-filter]", "[class*=sort-by]", "select[name*=sort]", "[class*=filters]",
)

_TOC_SELECTORS = (
    "[class*=toc]", "[id*=toc]", "[class*=table-of-contents]", "nav[aria-label*=able]",
    "[class*=on-this-page]", "[id*=on-this-page]", "[class*=page-nav]",
)

_BYLINE_SELECTORS = (
    "[rel=author]", "[class*=byline]", "[class*=author]", "[itemprop=author]",
    "[class*=post-meta]", "address[class*=author]",
)


def page_evidence(document: Any, url: str, path_classes: Sequence[str],
                  schema_types: Sequence[str], template_id: str = "") -> Dict[str, Any]:
    """Extract the observable facts :func:`classify` scores.

    All structural, all from the page itself. Text is lower-cased and capped so the
    corpus stays bounded on a large crawl.
    """
    main_text = document.main_text()
    body_lower = document.body_text().lower()

    has_price = bool(textnorm.find_all_money(main_text[:20000]))
    has_add_to_cart = any(document.select(sel) for sel in _CART_SELECTORS)
    has_facets = sum(1 for sel in _FACET_SELECTORS if document.select(sel)) >= 2
    has_toc = any(document.select(sel) for sel in _TOC_SELECTORS)
    has_byline = any(document.select(sel) for sel in _BYLINE_SELECTORS)
    has_code_block = bool(document.select("pre code")) or len(document.select("pre")) >= 2

    has_address = bool(document.select("[itemprop=address]")) or \
        any(pattern in body_lower for pattern in
            ("postcode", "post code", "zip code", "street", "avenue", "boulevard")) and \
        bool(document.select("address"))
    has_hours = any(pattern in body_lower for pattern in
                    ("opening hours", "opening times", "business hours", "hours of "
                     "operation", "mon-fri", "monday to friday", "mon - fri"))

    return {
        "url": url,
        "path_classes": tuple(path_classes),
        "schema_types": tuple(schema_types),
        "text_lower": body_lower[:20000],
        "has_price": has_price,
        "has_add_to_cart": has_add_to_cart,
        "has_byline": has_byline,
        "has_address": has_address,
        "has_hours": has_hours,
        "has_code_block": has_code_block,
        "has_toc": has_toc,
        "has_facets": has_facets,
        "template_id": template_id,
    }


def page_class_of(path_classes: Sequence[str], schema_types: Sequence[str],
                  profile: SiteProfile) -> str:
    """Classify one page within its site's profile.

    Used by ``G2.SD.TYPE_MISMATCH``, which needs to know what a page *is* before it can
    say the markup describes something else. Evidence order matters: explicit markup
    beats a path pattern, because markup is authored intent.
    """
    types = {t.lower() for t in schema_types}
    if types & {"product", "productgroup", "individualproduct"}:
        return "product"
    if types & {"article", "newsarticle", "blogposting", "techarticle"}:
        return "article"
    if types & {"faqpage", "qapage"}:
        return "faq"
    if types & {"localbusiness", "restaurant", "store", "hotel"}:
        return "local"
    if types & {"softwareapplication", "webapplication", "service"}:
        return "service"

    classes = set(path_classes)
    for candidate in ("home", "product", "article", "pricing", "docs", "faq", "about",
                      "contact", "service", "careers", "event"):
        if candidate in classes:
            return candidate
    return "generic"
