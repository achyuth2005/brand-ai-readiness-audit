#!/usr/bin/env python3
"""Gate G2 -- INTERPRET. Does a machine know what the facts on the page mean?

Pure function of the evidence ledger. No network I/O.

Mechanism
---------
The brief's Appendix A, third step: the machine has to "pick out the specific fact
someone is looking for". Prose requires inference. Structured data states a fact in an
unambiguous, typed, machine-native form, which is why it survives chunking, translation
and summarisation intact.

Markup is walked up a **validity ladder**, and each rung is a distinct finding because
each has a distinct fix::

    L0 present?     G2.SD.ABSENT
    L1 parses?      G2.SD.JSONLD_SYNTAX_ERROR
    L2 typed?       G2.SD.MISSING_CONTEXT, G2.SD.UNKNOWN_TYPE
    L3 right type?  G2.SD.TYPE_MISMATCH
    L4 complete?    G2.SD.REQUIRED_PROP_MISSING, G2.SD.RECOMMENDED_PROP_MISSING
    L5 well-formed? G2.SD.PROP_TYPE_INVALID
    L6 connected?   G2.SD.ENTITY_GRAPH_UNLINKED
    L7 truthful?    G2.SD.MARKUP_TEXT_CONTRADICTION

Expectations are selected by the **detected site profile**, never by domain. The question
is never "does this page have Product markup" but "given the evidence says this is a
product page, does it have what a product page needs".

See references/schema-profiles.md and references/validation-rules.md.
"""

from __future__ import annotations

import re
import sys
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import analyzer, lexicons, profile as profile_mod, sdextract  # noqa: E402
from auditkit import textnorm, urlnorm  # noqa: E402
from auditkit.analyzer import Context, PageView  # noqa: E402
from auditkit.dom import selector_for  # noqa: E402
from auditkit.finding import (  # noqa: E402
    Finding, FindingsFile, Scope, SuggestedAction, dom_ref, jsonpath_ref, metric_ref,
)

SKILL_ID = "structured-data-audit"

# Page classes and the entity types that express them. Used by the type-mismatch rung.
PAGE_CLASS_TYPES: Dict[str, Tuple[str, ...]] = {
    "product": ("Product", "ProductGroup", "IndividualProduct", "ProductModel",
                "SoftwareApplication", "Vehicle", "Book"),
    "article": ("Article", "NewsArticle", "BlogPosting", "TechArticle", "Report",
                "ScholarlyArticle", "OpinionNewsArticle", "AnalysisNewsArticle"),
    "faq": ("FAQPage", "QAPage"),
    "event": ("Event", "BusinessEvent", "MusicEvent", "EducationEvent", "SocialEvent",
              "Festival", "ExhibitionEvent"),
}

# Which expected types a page class requires properties for, per profile.
REQUIRED_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    "Product": ("name", "image", "offers"),
    "Offer": ("price", "priceCurrency", "availability"),
    "AggregateOffer": ("lowPrice", "priceCurrency"),
    "Article": ("headline", "datePublished", "author"),
    "NewsArticle": ("headline", "datePublished", "author"),
    "BlogPosting": ("headline", "datePublished", "author"),
    "TechArticle": ("headline",),
    "LocalBusiness": ("name", "address", "telephone"),
    "Organization": ("name", "url"),
    "WebSite": ("name", "url"),
    "Event": ("name", "startDate", "location"),
    "FAQPage": ("mainEntity",),
    "BreadcrumbList": ("itemListElement",),
    "SoftwareApplication": ("name",),
    "Recipe": ("name", "image", "recipeIngredient"),
    "JobPosting": ("title", "datePosted", "hiringOrganization"),
}

RECOMMENDED_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    "Product": ("brand", "description", "sku", "aggregateRating"),
    "Offer": ("url", "priceValidUntil", "shippingDetails", "hasMerchantReturnPolicy"),
    "Article": ("dateModified", "publisher", "image"),
    "NewsArticle": ("dateModified", "publisher", "image"),
    "BlogPosting": ("dateModified", "publisher", "image"),
    "LocalBusiness": ("openingHoursSpecification", "geo", "url", "image"),
    "Organization": ("logo", "sameAs", "description"),
    "WebSite": ("potentialAction",),
    "Event": ("endDate", "offers", "eventStatus", "image"),
    "SoftwareApplication": ("offers", "applicationCategory", "operatingSystem"),
}

LOCAL_BUSINESS_SUBTYPES = frozenset(
    t for t in lexicons.SCHEMA_TYPES
    if t in ("LocalBusiness", "Restaurant", "Store", "Dentist", "Hotel", "BarOrPub",
             "CafeOrCoffeeShop", "AutoRepair", "HairSalon", "LegalService", "Plumber",
             "Electrician", "RealEstateAgent", "ProfessionalService", "MedicalClinic",
             "Bakery", "ClothingStore", "HealthClub", "Physician", "Pharmacy")
)

_PRICE_CLASS_RE = re.compile(r"price|amount|cost|money|sale", re.IGNORECASE)
_RATING_CLASS_RE = re.compile(r"rating|stars|score|review-average", re.IGNORECASE)
_DATE_CLASS_RE = re.compile(r"publish|posted|pubdate|date|byline|meta", re.IGNORECASE)
_CURRENCY_SWITCHER_RE = re.compile(r"currency|country-selector|locale|region-select",
                                   re.IGNORECASE)


# ========================================================================================
# helpers
# ========================================================================================

def _page(context: Context, url: str) -> Optional[PageView]:
    return next((p for p in context.pages if p.url == url), None)


def _type_nodes(page: PageView, types: Sequence[str]) -> List[sdextract.SDNode]:
    return [n for n in page.sd.nodes if n.has_type(*types)]


def _has_prop(node: sdextract.SDNode, prop: str) -> bool:
    value = node.get_path(prop)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _emit(context: Context, out: FindingsFile, check_id: str, group: Dict[str, Any],
          *, title: str, evidence: str, mechanism: str, action: SuggestedAction,
          refs: List[Any], confidence: str = "high", signal_count: int = 2,
          fact_class: str = "", total: Optional[int] = None,
          notes: Optional[List[str]] = None) -> None:
    total_pages = total if total is not None else len(context.content_pages())
    out.add(Finding(
        check_id=check_id, title=title, evidence=evidence, mechanism=mechanism,
        suggested_action=action, confidence=confidence, signal_count=signal_count,
        fact_class=fact_class, scope=analyzer.scope_from_group(group, total_pages),
        evidence_refs=refs, notes=notes or [],
    ))


_STOCKED_TYPES = ("Product", "ProductGroup", "IndividualProduct", "ProductModel", "Vehicle",
                  "Book")
_REFERENCE_TYPES = ("Organization", "LocalBusiness", "WebSite")
# Properties whose value names another entity rather than describing it: a paywalled
# article isPartOf a subscription Product that is not a product listing.
_REFERENCE_PROPERTIES = ("isPartOf", "isRelatedTo", "isSimilarTo", "subjectOf", "about",
                         "mentions", "sponsor", "funder")


def _consolidates_duplicate(context: Context, page: PageView, target_key: str,
                            floor: float) -> bool:
    """True when the canonical target was fetched and its main text nearly equals this page's."""
    other = next((p for p in context.pages
                  if p.normal is not None and urlnorm.strip_index(p.normal) == target_key), None)
    if other is None:
        return False
    mine = {w.lower() for w in textnorm.words(page.doc.main_text())}
    theirs = {w.lower() for w in textnorm.words(other.doc.main_text())}
    return bool(mine and theirs) and len(mine & theirs) / len(mine | theirs) >= floor


def _offers_a_product(nodes: Sequence[sdextract.SDNode], offer: sdextract.SDNode) -> bool:
    """True when an Offer sells a stocked product: nested under one, or beside one."""
    path = re.sub(r"\[\d+\]$", "", offer.json_path)
    if path.endswith(".offers"):
        parent_path = path[: -len(".offers")]
        parent = next((n for n in nodes if n.json_path == parent_path
                       and n.block_index == offer.block_index), None)
        return parent is not None and parent.has_type(*_STOCKED_TYPES)
    return any(n.has_type(*_STOCKED_TYPES) for n in nodes)


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _prefix(group: Dict[str, Any]) -> str:
    return (f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled pages"
            + (f" in template cluster {group['template_id']}"
               if group.get("template_id") else ""))


# ========================================================================================
# L0 -- present?
# ========================================================================================

def check_absent(context: Context, out: FindingsFile) -> None:
    min_pages = int(context.threshold("structured_data.min_pages_for_absent_check"))
    pages = context.content_pages()
    hits = [(p.url, {}) for p in pages if not p.sd.has_any and not p.sd.broken_blocks]
    with_markup = [p for p in pages if p.sd.has_any]
    out.note_metric("pages_with_structured_data", len(with_markup))
    out.note_metric("pages_without_structured_data", len(hits))

    if with_markup:
        out.note_posture("structured_data_types_present",
                         sorted({t for p in with_markup for t in p.sd.types_present()}))

    for group in analyzer.group_by_template(context, hits, min_sample=min_pages):
        # Rule R2: "this page class has no markup" needs enough pages of that class.
        if group["pages_sampled"] < min_pages and len(hits) < min_pages:
            continue
        page = _page(context, group["example_urls"][0])
        if page is None:
            continue
        expected = context.site_profile.expected_types()
        path_class = group.get("path_class") or "content"
        _emit(
            context, out, "G2.SD.ABSENT", group,
            title=f"The {path_class.replace('_', ' ')} page class publishes no structured "
                  f"data",
            evidence=(
                f"{_prefix(group)} contain no JSON-LD, Microdata or RDFa of any kind, "
                f"and no JSON-LD block that failed to parse. The site was profiled as "
                f"{context.site_profile.detected_type}, for which the expected types are "
                f"{', '.join(expected)}. Affected: "
                f"{analyzer.summarise_urls(group['urls'])}."
            ),
            mechanism=(
                "Without structured data, every fact on the page -- what it is, what it "
                "costs, who made it, when -- has to be inferred from prose. Inference is "
                "where extraction errors come from. Typed markup states each fact once, "
                "unambiguously, in a form that survives chunking and summarisation."
            ),
            action=SuggestedAction(
                summary=(f"Add JSON-LD describing this page class, starting with "
                         f"{', '.join(expected[:2])}."),
                steps=[
                    f"Emit a server-rendered <script type=\"application/ld+json\"> block "
                    f"in the template for this page class, typed as "
                    f"{' or '.join(expected[:2])}.",
                    "Populate it from the same data source that renders the visible "
                    "page, so the markup cannot drift from the text.",
                    "Reference a single site-wide Organization node by @id from "
                    "publisher/brand/provider rather than repeating it.",
                    "Validate the output with a structured-data validator in CI.",
                ],
                effort="medium", owner_role="Frontend engineering / SEO",
                verification="Fetch an affected URL and confirm a JSON-LD block of the "
                             "expected type is present in the raw HTML and validates.",
                expected_effect="Gives consumers typed, unambiguous facts for this page "
                                "class instead of prose to infer from.",
            ),
            refs=[metric_ref(page.url_hash, "structured_data_nodes", 0, 1),
                  dom_ref(page.url_hash, "head", textnorm.truncate(page.doc.title(), 120)
                          or "document head")],
            signal_count=2,
            # Markup carries the primary facts (price, stock, hours, address) of shops
            # and local businesses; elsewhere its absence degrades secondary content.
            fact_class=("primary" if context.site_profile.detected_type in
                        ("ecommerce", "local_business") else "secondary"),
        )


# ========================================================================================
# L1, L2 -- parses? typed?
# ========================================================================================

def check_syntax_and_typing(context: Context, out: FindingsFile) -> None:
    syntax_hits: List[Tuple[str, Any]] = []
    context_hits: List[Tuple[str, Any]] = []
    type_hits: List[Tuple[str, Any]] = []

    for page in context.content_pages(include_utility=True):
        for block in page.sd.blocks:
            if block.syntax != "json-ld":
                continue
            if block.error:
                syntax_hits.append((page.url, {
                    "error": block.error, "position": block.error_position,
                    "line": block.line, "selector": block.selector,
                    "fragment": textnorm.truncate(block.raw, 160),
                }))
                continue
            if block.nodes and not block.has_schema_context:
                context_hits.append((page.url, {
                    "context": block.context, "selector": block.selector,
                    "types": sorted({t for n in block.nodes for t in n.types}),
                }))
            for node in block.nodes:
                for type_name in node.types:
                    if lexicons.schema_type_is_known(type_name):
                        continue
                    suggestion = lexicons.suggest_schema_type(type_name)
                    # Rule R6: an unrecognised but plausible type is silence, not a
                    # finding. Only a near-miss of a known type is reported.
                    if not suggestion or suggestion == type_name:
                        continue
                    type_hits.append((page.url, {
                        "type": type_name, "suggestion": suggestion,
                        "path": node.json_path, "selector": block.selector,
                    }))

    total = len(context.content_pages(include_utility=True))

    for group in analyzer.group_by_template(context, syntax_hits):
        payload = group["payloads"][0]
        page = _page(context, group["example_urls"][0])
        if page is None:
            continue
        where = payload["position"] or payload["error"]
        _emit(
            context, out, "G2.SD.JSONLD_SYNTAX_ERROR", group, total=total,
            title="A JSON-LD block does not parse, so consumers silently discard it",
            evidence=(
                f"{_prefix(group)} carry a JSON-LD block that is not valid JSON. Parser "
                f"message: \"{payload['error']}\" ({where}). The block begins at source "
                f"line {payload['line'] or 'unknown'}. Because the block never parses, "
                f"none of the facts inside it reach any consumer, even though the markup "
                f"is visibly present in the page source."
            ),
            mechanism=(
                "Consumers parse JSON-LD strictly and discard a block that fails, without "
                "reporting anything. The author sees the markup in the source and "
                "believes it works; the machine sees nothing. The usual causes are a "
                "trailing comma, an unescaped quote inserted by CMS templating, or a raw "
                "newline inside a string value."
            ),
            action=SuggestedAction(
                summary="Fix the JSON-LD so it parses, and generate it with a serializer "
                        "rather than string templating.",
                steps=[
                    f"Correct the error the parser reports: {where}.",
                    "Generate the block with a JSON serializer (json.dumps, "
                    "JSON.stringify, a framework helper) instead of concatenating "
                    "strings in a template, which is what lets quotes and newlines break "
                    "it.",
                    "Escape '</' inside string values so a value cannot terminate the "
                    "script element early.",
                    "Add a CI step that parses every emitted JSON-LD block.",
                ],
                effort="low", owner_role="Frontend engineering",
                verification="Extract the block from the raw HTML and parse it with "
                             "JSON.parse; it must succeed and validate.",
                expected_effect="Restores every fact inside the block, which currently "
                                "contributes nothing.",
            ),
            refs=[dom_ref(page.url_hash, payload["selector"] or "script",
                          payload["fragment"]),
                  metric_ref(page.url_hash, "jsonld_blocks_failing_to_parse",
                             group["pages_affected"], 0)],
            fact_class="primary",
        )

    for group in analyzer.group_by_template(context, context_hits):
        payload = group["payloads"][0]
        page = _page(context, group["example_urls"][0])
        if page is None:
            continue
        _emit(
            context, out, "G2.SD.MISSING_CONTEXT", group, total=total,
            title="JSON-LD declares no schema.org @context, so its terms mean nothing to "
                  "a consumer",
            evidence=(
                f"{_prefix(group)} carry a JSON-LD block typed "
                f"{', '.join(payload['types']) or '(untyped)'} whose @context is "
                f"{payload['context']!r} rather than a schema.org context. Without it, "
                f"keys such as `name` and `price` are plain strings, not vocabulary terms."
            ),
            mechanism=(
                "JSON-LD resolves every key through its @context. A missing or wrong "
                "context leaves the block syntactically valid but semantically empty, so "
                "consumers that expand JSON-LD see no schema.org entity at all."
            ),
            action=SuggestedAction(
                summary="Add \"@context\": \"https://schema.org\" to every JSON-LD block.",
                steps=[
                    "Set \"@context\": \"https://schema.org\" at the top level of the "
                    "block, or on the @graph container.",
                    "Use the https form consistently across templates.",
                ],
                effort="trivial", owner_role="Frontend engineering",
                verification="Validate the block and confirm the entity types are "
                             "recognised as schema.org types.",
                expected_effect="Turns an inert block into recognised schema.org "
                                "entities.",
            ),
            refs=[dom_ref(page.url_hash, payload["selector"] or "script",
                          f"@context={payload['context']!r}")],
            fact_class="primary",
        )

    for group in analyzer.group_by_template(context, type_hits):
        payload = group["payloads"][0]
        page = _page(context, group["example_urls"][0])
        if page is None:
            continue
        _emit(
            context, out, "G2.SD.UNKNOWN_TYPE", group, total=total,
            title=f"@type \"{payload['type']}\" is not a schema.org type",
            evidence=(
                f"{_prefix(group)} declare @type \"{payload['type']}\" at JSON path "
                f"{payload['path']}. That is not a schema.org type, and it is a near "
                f"match for the real type \"{payload['suggestion']}\". Types this audit "
                f"does not recognise and cannot match to a known type were deliberately "
                f"not reported, because schema.org is large and silence beats a wrong "
                f"claim."
            ),
            mechanism=(
                "A misspelled or localised type voids the whole node: consumers do not "
                "fuzzy-match type names, so every property on it is discarded along with "
                "the unrecognised type."
            ),
            action=SuggestedAction(
                summary=f"Change @type \"{payload['type']}\" to \"{payload['suggestion']}\".",
                steps=[
                    f"Replace \"{payload['type']}\" with \"{payload['suggestion']}\" in the "
                    f"template that emits this block.",
                    "Keep type names in their exact schema.org spelling and case; they "
                    "are not localised.",
                ],
                effort="trivial", owner_role="Frontend engineering",
                verification="Validate the block and confirm the type is recognised.",
                expected_effect="Restores every property on a node that is currently "
                                "discarded.",
            ),
            refs=[jsonpath_ref(page.url_hash, f"{payload['path']}.@type", payload["type"])],
            fact_class="secondary",
        )


# ========================================================================================
# L3, L4, L5 -- right type? complete? well-formed?
# ========================================================================================

def check_types_and_properties(context: Context, out: FindingsFile) -> None:
    profile = context.site_profile
    pages = context.content_pages()
    total = len(pages)

    mismatch_hits: List[Tuple[str, Any]] = []
    required_hits: Dict[str, List[Tuple[str, Any]]] = {}
    recommended_hits: Dict[str, List[Tuple[str, Any]]] = {}
    invalid_hits: Dict[str, List[Tuple[str, Any]]] = {}

    for page in pages:
        types = page.sd.types_present()
        page_class = profile_mod.page_class_of(page.path_classes, types, profile)

        # --- L3 type mismatch ----------------------------------------------------------
        if not profile.is_ambiguous and context.allows("G2.SD.TYPE_MISMATCH"):
            evidenced_class = _evidenced_page_class(page, profile)
            wanted = PAGE_CLASS_TYPES.get(evidenced_class)
            if wanted and page.sd.has_any and not page.sd.of_type(*wanted):
                mismatch_hits.append((page.url, {
                    "class": evidenced_class, "declared": list(types)[:5],
                    "wanted": wanted[:3],
                }))

        # --- L4 completeness, per node of a type we hold requirements for --------------
        for node in page.sd.nodes:
            node_types = [t for t in node.types if t in REQUIRED_BY_TYPE
                          or t in LOCAL_BUSINESS_SUBTYPES]
            if not node_types:
                continue
            type_name = node_types[0]
            lookup = "LocalBusiness" if type_name in LOCAL_BUSINESS_SUBTYPES else type_name
            # An Offer nested inside a Product is validated as an Offer, and a Product's
            # "offers" requirement is satisfied by its presence.
            required = [p for p in REQUIRED_BY_TYPE.get(lookup, ()) if not _has_prop(node, p)]
            if node.parent_prop and (lookup in _REFERENCE_TYPES
                                     or node.parent_prop in _REFERENCE_PROPERTIES):
                # A nested Organization or WebSite (an article's author, an offer's seller,
                # a page's isPartOf) is a reference to another entity, identified there by
                # its name. Full requirements apply where the entity itself is described.
                required = [p for p in required if p == "name"]
            if lookup == "Offer" and "availability" in required \
                    and not _offers_a_product(page.sd.nodes, node):
                # Stock status is required of a product's offer. A job, a ticket or a
                # software plan is offered without stock, and demanding it is noise.
                required = [p for p in required if p != "availability"]
            if lookup == "Product" and not _has_prop(node, "offers"):
                # A product with no offer is legitimate on non-commerce sites; only require
                # it where the evidence says the site sells.
                if profile.detected_type != "ecommerce":
                    required = [p for p in required if p != "offers"]
            if required:
                required_hits.setdefault(lookup, []).append((page.url, {
                    "type": type_name, "missing": required, "path": node.json_path,
                }))
            recommended = [p for p in RECOMMENDED_BY_TYPE.get(lookup, ())
                           if not _has_prop(node, p)]
            if node.parent_prop and (lookup in _REFERENCE_TYPES
                                     or node.parent_prop in _REFERENCE_PROPERTIES):
                recommended = []
            if recommended and not required:
                recommended_hits.setdefault(lookup, []).append((page.url, {
                    "type": type_name, "missing": recommended, "path": node.json_path,
                }))

            # --- L5 well-formedness ----------------------------------------------------
            for problem in _property_problems(node):
                invalid_hits.setdefault(problem["kind"], []).append((page.url, problem))

    for group in analyzer.group_by_template(context, mismatch_hits):
        payload = group["payloads"][0]
        page = _page(context, group["example_urls"][0])
        if page is None:
            continue
        _emit(
            context, out, "G2.SD.TYPE_MISMATCH", group, total=total,
            title=f"{payload['class'].capitalize()} pages carry structured data of the "
                  f"wrong type",
            evidence=(
                f"{_prefix(group)} are evidenced as {payload['class']} pages "
                f"(path class and on-page controls) and publish structured data typed "
                f"{', '.join(payload['declared']) or '(none)'}, but no "
                f"{' / '.join(payload['wanted'])} node. The markup exists; it describes "
                f"the page generically instead of describing the thing the page is about."
            ),
            mechanism=(
                "A product page typed only as WebPage tells a consumer it is a page, not "
                "that it sells something at a price. The facts that make the page useful "
                "for a shopping or comparison answer have no typed home, so they are "
                "inferred from prose or missed."
            ),
            action=SuggestedAction(
                summary=f"Add a {payload['wanted'][0]} node describing the page's subject.",
                steps=[
                    f"Emit a {payload['wanted'][0]} node on this template in addition to "
                    f"any WebPage node, populated from the page's own data.",
                    "Link it to the WebPage with mainEntity / mainEntityOfPage.",
                    "Fill the required properties listed in "
                    "references/schema-profiles.md for that type.",
                ],
                effort="medium", owner_role="Frontend engineering / SEO",
                verification=f"Validate an affected URL and confirm a "
                             f"{payload['wanted'][0]} node is detected.",
                expected_effect="Makes the page's subject a typed entity rather than an "
                                "anonymous page.",
            ),
            refs=[metric_ref(page.url_hash, f"{payload['wanted'][0].lower()}_nodes", 0, 1)],
            confidence="medium", fact_class="primary",
        )

    for type_name, hits in sorted(required_hits.items()):
        for group in analyzer.group_by_template(context, hits):
            payload = group["payloads"][0]
            page = _page(context, group["example_urls"][0])
            if page is None:
                continue
            missing_all = sorted({m for p in group["payloads"] for m in p["missing"]})
            _emit(
                context, out, "G2.SD.REQUIRED_PROP_MISSING", group, total=total,
                title=f"{type_name} markup is missing required properties: "
                      f"{', '.join(missing_all)}",
                evidence=(
                    f"{_prefix(group)} publish {_article(payload['type'])} "
                    f"{payload['type']} node (JSON path "
                    f"{payload['path']}) that lacks {', '.join(missing_all)}. These are "
                    f"the properties consumers require before they will use a "
                    f"{type_name} node for the answers it exists to support."
                ),
                mechanism=(
                    f"{_article(type_name).capitalize()} {type_name} node without its "
                    f"required properties is ineligible "
                    f"for the features it was written for. It is present, parses, and is "
                    f"typed correctly, so it passes a casual check -- and is still "
                    f"ignored."
                ),
                action=SuggestedAction(
                    summary=f"Populate {', '.join(missing_all)} on every {type_name} node.",
                    steps=[
                        f"Map {', '.join(missing_all)} to fields in the data that renders "
                        f"this template, and emit them in the JSON-LD.",
                        "Use the value shapes schema.org expects: ISO-8601 dates, numeric "
                        "prices with a separate ISO 4217 currency, schema.org enum URLs "
                        "for availability.",
                        "Where a value genuinely does not exist for some items, omit the "
                        "node for those items rather than emitting an empty property.",
                    ],
                    effort="low", owner_role="Frontend engineering / SEO",
                    verification=f"Validate an affected URL and confirm no required "
                                 f"property is reported missing for {type_name}.",
                    expected_effect=f"Makes {type_name} markup eligible for the answers "
                                    f"it was written to support.",
                ),
                refs=[jsonpath_ref(page.url_hash, payload["path"], payload["type"]),
                      metric_ref(page.url_hash,
                                 f"{type_name.lower()}_required_props_missing",
                                 len(missing_all), 0)],
                fact_class="primary",
            )

    for type_name, hits in sorted(recommended_hits.items()):
        groups = analyzer.group_by_template(context, hits)
        # Recommended properties are only a finding at template scope. A single page
        # missing a recommended property is not worth the reader's attention.
        for group in [g for g in groups if g["template_bound"]]:
            payload = group["payloads"][0]
            page = _page(context, group["example_urls"][0])
            if page is None:
                continue
            missing_all = sorted({m for p in group["payloads"] for m in p["missing"]})
            out.note_posture(f"{type_name}_recommended_missing", missing_all)
            _emit(
                context, out, "G2.SD.RECOMMENDED_PROP_MISSING", group, total=total,
                title=f"{type_name} markup omits recommended properties: "
                      f"{', '.join(missing_all)}",
                evidence=(
                    f"{_prefix(group)} publish complete {type_name} nodes that omit the "
                    f"recommended properties {', '.join(missing_all)}."
                ),
                mechanism=(
                    f"The node works, but competes poorly against more completely "
                    f"described entities. Each recommended property is an additional "
                    f"fact a consumer can use to match the entity to a query or to "
                    f"distinguish it from similar ones."
                ),
                action=SuggestedAction(
                    summary=f"Add {', '.join(missing_all)} to the {type_name} template.",
                    steps=[f"Emit {prop} where the data exists." for prop in missing_all]
                    + ["Prefer omitting a property to emitting a placeholder value."],
                    effort="low", owner_role="Frontend engineering / SEO",
                    verification=f"Validate an affected URL and confirm the recommended "
                                 f"properties are present on {type_name}.",
                    expected_effect=f"Makes {type_name} entities more completely "
                                    f"described and easier to match.",
                ),
                refs=[jsonpath_ref(page.url_hash, payload["path"], payload["type"])],
                confidence="high", fact_class="secondary",
            )

    for kind, hits in sorted(invalid_hits.items()):
        for group in analyzer.group_by_template(context, hits):
            payload = group["payloads"][0]
            page = _page(context, group["example_urls"][0])
            if page is None:
                continue
            _emit(
                context, out, "G2.SD.PROP_TYPE_INVALID", group, total=total,
                title=payload["title"],
                evidence=(f"{_prefix(group)}: {payload['detail']} (JSON path "
                          f"{payload['path']}, value {payload['value']!r})."),
                mechanism=payload["mechanism"],
                action=SuggestedAction(
                    summary=payload["fix"],
                    steps=payload["steps"],
                    effort="low", owner_role="Frontend engineering",
                    verification="Validate an affected URL and confirm the property "
                                 "value is accepted in the expected format.",
                    expected_effect="Makes the value machine-usable instead of rejected.",
                ),
                refs=[jsonpath_ref(page.url_hash, payload["path"], payload["value"])],
                fact_class="primary",
            )


def _evidenced_page_class(page: PageView, profile) -> str:
    """What a page is, from evidence other than its markup.

    Deliberately excludes the markup itself: asking "is the markup the right type" using
    the markup as the evidence would be circular.
    """
    classes = set(page.path_classes)
    doc = page.doc
    if "product" in classes and profile.detected_type == "ecommerce":
        has_price = bool(textnorm.find_all_money(doc.main_text()[:5000]))
        has_cart = bool(doc.select("[class*=add-to-cart],[name*=add-to-cart],"
                                   "form[action*=cart],[class*=add-to-bag]"))
        if has_price and has_cart:
            return "product"
    if "article" in classes and profile.detected_type == "publisher":
        if doc.select("time[datetime], [rel=author], [class*=byline]"):
            return "article"
    if "faq" in classes:
        questions = [t for lvl, t, _ in doc.headings() if t.strip().endswith("?")]
        if len(questions) >= 3:
            return "faq"
    return ""


def _property_problems(node: sdextract.SDNode) -> List[Dict[str, Any]]:
    """L5 checks. Each returned dict carries its own wording and fix."""
    problems: List[Dict[str, Any]] = []

    if node.has_type("Offer", "AggregateOffer"):
        for prop in ("price", "lowPrice", "highPrice"):
            raw = node.get(prop)
            value = sdextract.scalar(raw)
            if not value:
                continue
            if re.search(r"[^\d.,\s-]", value) or re.search(r"\d,\d{3}", value):
                problems.append({
                    "kind": "price_format", "path": f"{node.json_path}.{prop}",
                    "value": value,
                    "title": "Offer price is not a plain number",
                    "detail": (f"Offer.{prop} is {value!r}, which contains a currency "
                               f"symbol, text, or a thousands separator"),
                    "mechanism": ("schema.org price is a number with a separate ISO 4217 "
                                  "priceCurrency. A value like '£49.00' or '1,299' is "
                                  "rejected or misparsed, so the price is lost or read "
                                  "wrongly."),
                    "fix": f"Emit {prop} as a plain decimal and the currency in "
                           f"priceCurrency.",
                    "steps": [f"Change {prop} to a bare number such as \"49.00\" using "
                              f"a '.' decimal separator and no thousands separator.",
                              "Put the ISO 4217 code (GBP, EUR, USD) in priceCurrency."],
                })
        currency = sdextract.scalar(node.get("priceCurrency"))
        if currency and not re.fullmatch(r"[A-Z]{3}", currency):
            problems.append({
                "kind": "currency_format", "path": f"{node.json_path}.priceCurrency",
                "value": currency,
                "title": "priceCurrency is not an ISO 4217 code",
                "detail": f"priceCurrency is {currency!r}, not a three-letter ISO 4217 code",
                "mechanism": ("A symbol such as '$' is ambiguous across a dozen "
                              "currencies, and consumers expect the ISO code; the price "
                              "cannot be interpreted without it."),
                "fix": "Use the three-letter ISO 4217 code in priceCurrency.",
                "steps": ["Replace the value with the ISO 4217 code, e.g. USD, GBP, EUR."],
            })
        availability = sdextract.scalar(node.get("availability"))
        if availability:
            enum = lexicons.normalise_availability(availability)
            if enum not in lexicons.AVAILABILITY_ENUM:
                problems.append({
                    "kind": "availability_enum", "path": f"{node.json_path}.availability",
                    "value": availability,
                    "title": "Offer availability is not a schema.org ItemAvailability "
                             "value",
                    "detail": f"availability is {availability!r}, which is not an "
                              f"ItemAvailability enumeration member",
                    "mechanism": ("Availability is an enumeration. Free text such as "
                                  "'Available' or 'yes' is not recognised, so stock "
                                  "status is unknown to consumers answering shopping "
                                  "queries."),
                    "fix": "Use a schema.org ItemAvailability URL such as "
                           "https://schema.org/InStock.",
                    "steps": ["Map the internal stock status to an ItemAvailability "
                              "member: InStock, OutOfStock, PreOrder, BackOrder, "
                              "LimitedAvailability, Discontinued.",
                              "Emit it as the full URL form."],
                })

    for prop in ("datePublished", "dateModified", "startDate", "endDate", "datePosted",
                 "validThrough", "priceValidUntil", "foundingDate", "uploadDate"):
        value = sdextract.scalar(node.get(prop))
        if not value:
            continue
        if not textnorm.is_iso8601(value):
            problems.append({
                "kind": f"date_format_{prop}", "path": f"{node.json_path}.{prop}",
                "value": value,
                "title": f"{prop} is not an ISO-8601 date",
                "detail": f"{node.primary_type}.{prop} is {value!r}, not an ISO-8601 date",
                "mechanism": ("Dates in structured data must be ISO-8601. A "
                              "human-formatted date is rejected or parsed "
                              "inconsistently, so recency -- a primary signal for "
                              "whether a source is preferred -- cannot be established."),
                "fix": f"Emit {prop} in ISO-8601 form, e.g. 2026-03-15 or "
                       f"2026-03-15T09:00:00Z.",
                "steps": [f"Format {prop} as YYYY-MM-DD, or a full timestamp with a "
                          f"timezone offset.",
                          "Generate it from the stored timestamp, not from a display "
                          "string."],
            })

    rating = node.get("aggregateRating") if not node.has_type("AggregateRating") else node
    if rating is not None:
        rating_value = rating.get("ratingValue") if isinstance(rating, sdextract.SDNode) \
            else (rating.get("ratingValue") if isinstance(rating, dict) else None)
        best = (rating.get("bestRating") if isinstance(rating, (sdextract.SDNode, dict))
                else None)
        try:
            rv = float(sdextract.scalar(rating_value)) if rating_value is not None else None
            bv = float(sdextract.scalar(best)) if best is not None else 5.0
        except (TypeError, ValueError):
            rv, bv = None, 5.0
        if rv is not None and (rv < 0 or rv > bv):
            problems.append({
                "kind": "rating_range", "path": f"{node.json_path}.aggregateRating.ratingValue",
                "value": rv,
                "title": "ratingValue is outside its declared scale",
                "detail": f"ratingValue {rv} lies outside the 0-{bv:g} scale",
                "mechanism": ("An out-of-range rating is treated as invalid markup and "
                              "can cost the page rating eligibility entirely."),
                "fix": "Emit ratingValue on the declared scale, and declare bestRating "
                       "if the scale is not 1-5.",
                "steps": ["Normalise ratingValue to the scale in bestRating/worstRating.",
                          "Declare bestRating explicitly when it is not 5."],
            })
    return problems


# ========================================================================================
# L6 -- connected?
# ========================================================================================

def check_entity_graph(context: Context, out: FindingsFile) -> None:
    pages = [p for p in context.content_pages() if p.sd.jsonld_nodes()]
    if len(pages) < context.min_sample():
        out.skip("G2.SD.ENTITY_GRAPH_UNLINKED",
                 reason=f"only {len(pages)} page(s) carry JSON-LD, too few to judge "
                        f"site-wide entity linkage")
        return

    all_nodes = [n for p in pages for n in p.sd.jsonld_nodes()]
    graph = sdextract.node_id_graph(all_nodes)
    orgs = [n for n in all_nodes if n.has_type("Organization", "Corporation",
                                               "LocalBusiness", "NewsMediaOrganization",
                                               "OnlineStore")
            or any(t in LOCAL_BUSINESS_SUBTYPES for t in n.types)]
    org_ids = {n.node_id for n in orgs if n.node_id}
    referencing = [n for n in all_nodes if n.has_type(
        "Product", "Article", "NewsArticle", "BlogPosting", "WebSite", "WebPage",
        "SoftwareApplication", "Service", "Event")]
    linked = 0
    for node in referencing:
        for prop in ("publisher", "brand", "provider", "author", "manufacturer",
                     "seller", "organizer", "isPartOf"):
            value = node.get(prop)
            if value is None:
                continue
            ids = sdextract._collect_ids(value)
            if set(ids) & org_ids:
                linked += 1
                break

    out.note_posture("entity_graph", {
        "organization_nodes": len(orgs), "organization_ids": sorted(org_ids),
        "entities_linked_to_organization": linked, "entities_total": len(referencing),
    })

    if org_ids and (not referencing or linked / max(1, len(referencing)) >= 0.5):
        return
    if not orgs and not referencing:
        return

    example = pages[0]
    dangling = graph["dangling_references"]
    entity_types = sorted({t for n in referencing for t in n.types})
    if not orgs:
        detail = (f"no Organization node is published on any of the {len(pages)} pages "
                  f"carrying JSON-LD, so their {len(referencing)} content entities "
                  f"({', '.join(entity_types[:4])}) have no publisher or brand entity to "
                  f"reference")
    elif not org_ids:
        detail = (f"{len(orgs)} Organization-type node(s) appear across {len(pages)} "
                  f"pages carrying JSON-LD, and none declares a stable @id")
    else:
        detail = (f"an Organization @id is declared ({sorted(org_ids)[0]}), but only "
                  f"{linked} of {len(referencing)} content entities reference it through "
                  f"publisher, brand, provider or author")
    group = {"pages_affected": len(pages), "pages_sampled": len(context.content_pages()),
             "example_urls": [p.url for p in pages[:3]], "template_id": "",
             "template_bound": False, "path_class": ""}
    out.add(Finding(
        check_id="G2.SD.ENTITY_GRAPH_UNLINKED",
        title="Structured data publishes unconnected entity islands instead of one "
              "entity graph",
        evidence=(
            f"Across the site's structured data, {detail}. "
            + (f"{len(dangling)} @id reference(s) point to identifiers that are not "
               f"declared anywhere in the sampled pages. " if dangling else "")
            + "No single organisation entity connects the site's content, so each page's "
              "entities stand alone instead of accumulating onto one."
        ),
        mechanism=(
            "An @id is what lets a consumer know that the publisher of this article, the "
            "brand of that product and the organisation on the homepage are one entity. "
            "Without it every page publishes a separate candidate entity, and the "
            "brand's facts are scattered across unmerged nodes rather than accumulating "
            "on one."
        ),
        confidence="high", signal_count=2, fact_class="secondary",
        scope=Scope(level="site", pages_affected=len(pages),
                    pages_sampled=len(context.content_pages()),
                    example_urls=[p.url for p in pages[:3]]),
        evidence_refs=[metric_ref(example.url_hash, "entities_linked_to_organization",
                                  linked, len(referencing)),
                       metric_ref(example.url_hash, "organization_ids_declared",
                                  len(org_ids), 1)],
        suggested_action=SuggestedAction(
            summary="Declare one Organization node with a stable @id and reference it "
                    "from every other entity.",
            steps=[
                "Emit a single Organization node site-wide with \"@id\": "
                "\"https://<your-domain>/#organization\", plus name, url, logo and "
                "sameAs.",
                "Reference it by @id from publisher (articles), brand or manufacturer "
                "(products), provider (services) and isPartOf / publisher (WebSite).",
                "Give WebSite and each WebPage their own stable @id and link them with "
                "isPartOf, so the site forms one connected graph.",
            ],
            effort="low", owner_role="Frontend engineering / SEO",
            verification="Extract the JSON-LD from three different page classes and "
                         "confirm every content entity references the same "
                         "Organization @id.",
            expected_effect="Consolidates the brand's facts onto one machine-resolvable "
                            "entity.",
        ),
    ))


# ========================================================================================
# L7 -- truthful?
# ========================================================================================

def check_contradictions(context: Context, out: FindingsFile) -> None:
    """G2.SD.MARKUP_TEXT_CONTRADICTION.

    Comparison runs against the **raw response** only. If the visible value exists only
    after rendering, that is a G1 problem, not a contradiction -- reporting it here would
    blame the markup for a render gap.
    """
    price_tol = float(context.threshold("structured_data.price_match_tolerance"))
    rating_tol = float(context.threshold("structured_data.rating_match_tolerance"))
    hits: Dict[str, List[Tuple[str, Any]]] = {}
    corroborated = 0

    for page in context.content_pages():
        doc = page.doc
        main = doc.main_node() or doc.soup.find("body")
        if main is None:
            continue
        switcher = bool(doc.soup.find(attrs={"class": _CURRENCY_SWITCHER_RE})
                        or doc.soup.find("select", attrs={"name": _CURRENCY_SWITCHER_RE}))

        # --- price -------------------------------------------------------------------
        offers = [n for n in page.sd.nodes if n.has_type("Offer", "AggregateOffer")]
        markup_prices: List[textnorm.Money] = []
        for offer in offers:
            currency = sdextract.scalar(offer.get("priceCurrency")) or None
            for prop in ("price", "lowPrice", "highPrice"):
                value = sdextract.scalar(offer.get(prop))
                if not value:
                    continue
                parsed = textnorm.parse_money(value, default_currency=currency)
                if parsed is not None:
                    markup_prices.append(textnorm.Money(parsed.amount, currency or
                                                        parsed.currency, "", value))
        if markup_prices:
            labelled_nodes = [n for n in main.find_all(True)
                              if _PRICE_CLASS_RE.search(" ".join(
                                  [n.get("id") or ""] + list(n.get("class") or [])))
                              or (n.get("itemprop") or "").lower() == "price"]
            text_prices: List[Tuple[textnorm.Money, Any]] = []
            for node in labelled_nodes:
                for money in textnorm.find_all_money(doc.visible_text(node)):
                    text_prices.append((money, node))
            if text_prices:
                any_match = any(tp.matches(mp, tolerance=price_tol)
                                for tp, _n in text_prices for mp in markup_prices)
                if any_match:
                    corroborated += 1
                else:
                    competing, node = text_prices[0]
                    hits.setdefault("price", []).append((page.url, {
                        "field": "price",
                        "markup": ", ".join(f"{m.raw} {m.currency or ''}".strip()
                                            for m in markup_prices[:3]),
                        "text": competing.raw,
                        "selector": selector_for(node),
                        "path": offers[0].json_path + ".price",
                        "markup_value": markup_prices[0].raw,
                        "capped": switcher,
                    }))

        # --- availability ------------------------------------------------------------
        for offer in offers:
            enum = lexicons.normalise_availability(sdextract.scalar(
                offer.get("availability")))
            if enum not in lexicons.AVAILABILITY_ENUM:
                continue
            stock_nodes = [n for n in main.find_all(True) if re.search(
                r"stock|availability|inventory", " ".join(
                    [n.get("id") or ""] + list(n.get("class") or [])), re.IGNORECASE)]
            stock_text = " ".join(doc.visible_text(n) for n in stock_nodes).lower()
            text_enum = lexicons.availability_from_text(stock_text) if stock_text else ""
            if not text_enum:
                continue
            compatible = {frozenset({"InStock", "LimitedAvailability"}),
                          frozenset({"OutOfStock", "SoldOut"}),
                          frozenset({"PreOrder", "PreSale"})}
            if text_enum == enum or frozenset({text_enum, enum}) in compatible:
                corroborated += 1
                continue
            hits.setdefault("availability", []).append((page.url, {
                "field": "availability", "markup": enum, "text": text_enum,
                "selector": selector_for(stock_nodes[0]),
                "path": offer.json_path + ".availability", "markup_value": enum,
                "capped": False,
            }))
            break

        # --- telephone ---------------------------------------------------------------
        for node in page.sd.nodes:
            phone = sdextract.scalar(node.get("telephone"))
            if not phone:
                continue
            tel_links = [a for a in doc.soup.find_all("a")
                         if (a.get("href") or "").lower().startswith("tel:")]
            if not tel_links:
                continue
            numbers = [(a.get("href") or "")[4:] for a in tel_links]
            if any(textnorm.phones_match(phone, n) for n in numbers):
                corroborated += 1
                continue
            hits.setdefault("telephone", []).append((page.url, {
                "field": "telephone", "markup": phone, "text": numbers[0],
                "selector": selector_for(tel_links[0]),
                "path": node.json_path + ".telephone", "markup_value": phone,
                "capped": len(set(numbers)) > 1,
            }))
            break

        # --- datePublished -----------------------------------------------------------
        for node in page.sd.of_type("Article", "NewsArticle", "BlogPosting", "TechArticle"):
            published = textnorm.parse_date(sdextract.scalar(node.get("datePublished")))
            if published is None:
                continue
            time_nodes = [t for t in doc.soup.find_all("time")
                          if (t.get("itemprop") or "").lower() == "datepublished"
                          or _DATE_CLASS_RE.search(" ".join(
                              [t.get("class") and " ".join(t.get("class")) or "",
                               (t.parent.get("class") and " ".join(t.parent.get("class"))
                                or "") if t.parent is not None else ""]))]
            if not time_nodes:
                continue
            visible_dates = [textnorm.parse_date(t.get("datetime") or doc.visible_text(t))
                             for t in time_nodes]
            visible_dates = [d for d in visible_dates if d is not None]
            if not visible_dates:
                continue
            if any(textnorm.days_between(published, d) <= 1 for d in visible_dates):
                corroborated += 1
                continue
            # A visible "updated" date legitimately differs from datePublished.
            modified = textnorm.parse_date(sdextract.scalar(node.get("dateModified")))
            if modified and any(textnorm.days_between(modified, d) <= 1
                                for d in visible_dates):
                corroborated += 1
                continue
            hits.setdefault("datePublished", []).append((page.url, {
                "field": "datePublished", "markup": str(published),
                "text": str(visible_dates[0]), "selector": selector_for(time_nodes[0]),
                "path": node.json_path + ".datePublished",
                "markup_value": sdextract.scalar(node.get("datePublished")),
                "capped": False,
            }))
            break

        # --- rating ------------------------------------------------------------------
        for node in page.sd.nodes:
            rating = node.get("aggregateRating")
            if not isinstance(rating, (dict, sdextract.SDNode)):
                continue
            try:
                markup_rating = float(sdextract.scalar(rating.get("ratingValue")))
            except (TypeError, ValueError):
                continue
            rating_nodes = [n for n in main.find_all(True) if _RATING_CLASS_RE.search(
                " ".join([n.get("id") or ""] + list(n.get("class") or [])))]
            values = []
            for rn in rating_nodes:
                for match in re.finditer(r"\b(\d(?:[.,]\d{1,2})?)\s*(?:/|out of)\s*(\d+)",
                                         doc.visible_text(rn)):
                    try:
                        values.append(float(match.group(1).replace(",", ".")))
                    except ValueError:
                        continue
            if not values:
                continue
            if any(abs(v - markup_rating) <= rating_tol + 0.05 for v in values):
                corroborated += 1
                continue
            hits.setdefault("ratingValue", []).append((page.url, {
                "field": "aggregateRating.ratingValue", "markup": str(markup_rating),
                "text": str(values[0]), "selector": selector_for(rating_nodes[0]),
                "path": node.json_path + ".aggregateRating.ratingValue",
                "markup_value": markup_rating, "capped": False,
            }))
            break

    out.note_metric("markup_values_corroborated_by_text", corroborated)
    if corroborated and not hits:
        out.note_posture("markup_matches_visible_text", True)

    total = len(context.content_pages())
    for field_name, field_hits in sorted(hits.items()):
        for group in analyzer.group_by_template(context, field_hits):
            payload = group["payloads"][0]
            page = _page(context, group["example_urls"][0])
            if page is None:
                continue
            capped = any(p["capped"] for p in group["payloads"])
            _emit(
                context, out, "G2.SD.MARKUP_TEXT_CONTRADICTION", group, total=total,
                title=f"Structured data states a {field_name} that the visible page "
                      f"contradicts",
                evidence=(
                    f"{_prefix(group)} state {payload['field']} = {payload['markup']!r} "
                    f"in structured data while the page text, inside an element labelled "
                    f"for that field, shows {payload['text']!r}. "
                    + ("Where several offers or values were marked up, none matched the "
                       "visible value. " if payload["field"] == "price" else "")
                    + "Both values were read from the raw HTML response, so this is not a "
                      "rendering difference. "
                    + ("A currency or region switcher is present, so part of this "
                       "difference may be personalised pricing; confidence is capped at "
                       "medium accordingly." if capped else "")
                ),
                mechanism=(
                    "Markup that disagrees with the page is worse than no markup. A "
                    "consumer that trusts the typed value will quote it -- a stale price "
                    "or a wrong stock status repeated back to a customer -- and a "
                    "disagreement between markup and visible content is also treated as "
                    "a spam signal that can cost the site rich-result eligibility "
                    "entirely."
                ),
                action=SuggestedAction(
                    summary=(f"Generate {field_name} markup from the same data source "
                             f"that renders the visible value, so they cannot diverge."),
                    steps=[
                        f"Find where the markup's {field_name} comes from. A divergence "
                        f"like this almost always means the markup is populated from a "
                        f"cached, default or base-variant value while the page renders "
                        f"the live one.",
                        "Bind both to one field in the template, instead of maintaining "
                        "them separately.",
                        "Add a CI or build-time assertion that the rendered value equals "
                        "the markup value for a sample of items.",
                        "Where a page legitimately shows several values (variants, "
                        "ranges), mark each up as its own Offer or use AggregateOffer "
                        "with lowPrice/highPrice.",
                    ],
                    effort="medium", owner_role="Frontend engineering",
                    verification=(f"Fetch an affected URL, extract {payload['field']} "
                                  f"from the JSON-LD, and confirm it equals the value in "
                                  f"the labelled page element."),
                    expected_effect="Removes a source of confidently wrong citations and a "
                                    "spam signal. Being a process fix, it cannot regress.",
                ),
                refs=[jsonpath_ref(page.url_hash, payload["path"], payload["markup_value"]),
                      dom_ref(page.url_hash, payload["selector"], payload["text"])],
                confidence="medium" if capped else "high", signal_count=2,
                fact_class="primary",
            )


def check_duplicates_and_faq(context: Context, out: FindingsFile) -> None:
    faq_share = float(context.threshold("structured_data.faq_answer_match_share"))
    dup_hits: List[Tuple[str, Any]] = []
    faq_hits: List[Tuple[str, Any]] = []

    for page in context.content_pages():
        # Only entities that claim to *be* the site's organisation can conflict: root nodes
        # and the publisher. An Organization nested as a seller, brand, manufacturer or
        # affiliation names a different party by design -- a review listing three retailers
        # is not three rival publishers.
        orgs = [o for o in page.sd.of_type("Organization", "Corporation")
                if o.parent_prop in ("", "publisher")]
        names = {textnorm.normalise_brand_name(sdextract.scalar(o.get("name")))
                 for o in orgs if sdextract.scalar(o.get("name"))}
        urls = {sdextract.scalar(o.get("url")).rstrip("/") for o in orgs
                if sdextract.scalar(o.get("url"))}
        irreducible = []
        name_list = sorted(n for n in names if n)
        for i, a in enumerate(name_list):
            for b in name_list[i + 1:]:
                if not textnorm.name_variants_reduce(a, b):
                    irreducible.append((a, b))
        if irreducible or len(urls) > 1:
            dup_hits.append((page.url, {
                "names": name_list, "urls": sorted(urls), "count": len(orgs),
                "path": orgs[0].json_path if orgs else "$",
                # The evidence ref pairs a path with the value found *at that path*.
                "path_name": sdextract.scalar(orgs[0].get("name")) if orgs else "",
            }))

        page_key = textnorm.comparison_key(page.doc.body_text())
        for faq in page.sd.of_type("FAQPage"):
            entities = faq.get("mainEntity")
            entities = entities if isinstance(entities, list) else [entities]
            answers = []
            for entity in entities:
                if not isinstance(entity, (dict, sdextract.SDNode)):
                    continue
                accepted = entity.get("acceptedAnswer")
                text = sdextract.scalar(accepted.get("text")) if isinstance(
                    accepted, (dict, sdextract.SDNode)) else ""
                if text:
                    # Answers are commonly HTML with entities (&amp;, &#39;); the page
                    # text they are compared against is already decoded.
                    answers.append(unescape(re.sub(r"<[^>]+>", " ", text)))
            if not answers:
                continue
            present = sum(1 for a in answers
                          if textnorm.comparison_key(a)[:80] in page_key)
            if present / len(answers) < faq_share:
                faq_hits.append((page.url, {
                    "answers": len(answers), "present": present, "path": faq.json_path,
                    "example": textnorm.truncate(answers[0], 120),
                }))

    total = len(context.content_pages())
    for group in analyzer.group_by_template(context, dup_hits):
        payload = group["payloads"][0]
        page = _page(context, group["example_urls"][0])
        if page is None:
            continue
        _emit(
            context, out, "G2.SD.DUPLICATE_CONFLICTING_ENTITIES", group, total=total,
            title="One page publishes several Organization entities that disagree",
            evidence=(
                f"{_prefix(group)} carry {payload['count']} Organization nodes naming "
                f"{', '.join(repr(n) for n in payload['names']) or '(unnamed)'}"
                + (f" with URLs {', '.join(payload['urls'])}" if payload["urls"] else "")
                + ". The names do not reduce to one another as long and short forms of "
                  "the same name."
            ),
            mechanism=(
                "Two conflicting organisation nodes force a consumer to choose which one "
                "the page is about. The usual cause is a plugin, theme or agency template "
                "emitting its own organisation alongside the site's, which can attach the "
                "wrong name to the brand's content."
            ),
            action=SuggestedAction(
                summary="Emit exactly one Organization node per page and remove the "
                        "conflicting source.",
                steps=[
                    "Find which component emits each Organization node (theme, SEO "
                    "plugin, tag manager, template).",
                    "Remove the duplicate source and keep the one populated from the "
                    "brand's canonical facts.",
                    "Reference the surviving node by @id from other entities.",
                ],
                effort="low", owner_role="Frontend engineering / SEO",
                verification="Extract the page's JSON-LD and confirm a single Organization "
                             "node with the brand's canonical name.",
                expected_effect="Removes ambiguity about which entity publishes the "
                                "content.",
            ),
            refs=[jsonpath_ref(page.url_hash, payload["path"], payload["path_name"]),
                  metric_ref(page.url_hash, "organization_nodes_on_page",
                             payload["count"], 1)],
            fact_class="secondary",
        )

    for group in analyzer.group_by_template(context, faq_hits):
        payload = group["payloads"][0]
        page = _page(context, group["example_urls"][0])
        if page is None:
            continue
        _emit(
            context, out, "G2.SD.FAQ_MARKUP_MISMATCH", group, total=total,
            title="FAQPage markup describes answers that are not on the visible page",
            evidence=(
                f"{_prefix(group)} publish FAQPage markup with {payload['answers']} "
                f"answers, of which only {payload['present']} appear in the page's "
                f"visible text (threshold {faq_share:.0%}). Example marked-up answer not "
                f"found on the page: \"{payload['example']}\"."
            ),
            mechanism=(
                "FAQ markup is only valid for content the visitor can see. Markup written "
                "for machines only is a policy violation that can remove eligibility, "
                "and a consumer that quotes a hidden answer is quoting text the site does "
                "not actually show."
            ),
            action=SuggestedAction(
                summary="Publish the marked-up questions and answers as visible page "
                        "content, or remove them from the markup.",
                steps=[
                    "Render every FAQ question and answer as visible text on the page.",
                    "Generate the FAQPage markup from that same rendered content.",
                    "Remove markup entries that have no visible counterpart.",
                ],
                effort="low", owner_role="Content / Frontend engineering",
                verification="Confirm every acceptedAnswer text in the markup appears in "
                             "the page's raw HTML.",
                expected_effect="Makes FAQ markup valid and aligns what is quoted with "
                                "what is shown.",
            ),
            refs=[jsonpath_ref(page.url_hash, payload["path"], "FAQPage"),
                  metric_ref(page.url_hash, "faq_answers_visible", payload["present"],
                             payload["answers"])],
            fact_class="secondary",
        )


def check_breadcrumbs_search_rendered(context: Context, out: FindingsFile) -> None:
    deep_depth = int(context.threshold("structured_data.deep_page_depth"))
    pages = context.content_pages()
    total = len(pages)

    deep = [p for p in pages if p.depth >= deep_depth]
    lacking = [(p.url, {}) for p in deep if not p.sd.of_type("BreadcrumbList")]
    if len(deep) >= context.min_sample() and len(lacking) == len(deep):
        page = deep[0]
        visual = bool(page.doc.select("[class*=breadcrumb], nav[aria-label*=readcrumb]"))
        out.add(Finding(
            check_id="G2.SD.BREADCRUMB_ABSENT_ON_DEEP_PAGES",
            title="Deep pages publish no BreadcrumbList markup",
            evidence=(
                f"{analyzer.kn(len(lacking), len(deep))} sampled pages at path depth "
                f"{deep_depth} or more carry no BreadcrumbList structured data. "
                + ("A visual breadcrumb is present on the page, so the hierarchy exists "
                   "for readers and only needs to be expressed in markup."
                   if visual else "No visual breadcrumb was found either.")
            ),
            mechanism=(
                "BreadcrumbList states a page's position in the site hierarchy in typed "
                "form. It lets a consumer understand that a page belongs to a category, "
                "and gives link previews a readable path instead of a raw URL."
            ),
            confidence="high", signal_count=2, fact_class="enrichment",
            scope=Scope(level="site" if len(lacking) >= 3 else "section",
                        pages_affected=len(lacking), pages_sampled=len(deep),
                        example_urls=[u for u, _ in lacking[:3]]),
            evidence_refs=[metric_ref(page.url_hash, "deep_pages_with_breadcrumb_markup",
                                      0, len(deep))],
            suggested_action=SuggestedAction(
                summary="Emit BreadcrumbList JSON-LD on every page below the top level.",
                steps=[
                    "Generate a BreadcrumbList from the same data that builds the visual "
                    "breadcrumb or the page's parent chain.",
                    "Give each ListItem a position, name and item URL.",
                ],
                effort="low", owner_role="Frontend engineering",
                verification="Validate a deep page and confirm a BreadcrumbList with "
                             "ordered ListItems.",
                expected_effect="Makes site hierarchy machine-readable.",
            ),
        ))

    home = context.homepage()
    if home is not None:
        has_search = bool(home.doc.select("input[type=search], form[role=search], "
                                          "input[name=q], input[name=s], "
                                          "input[name=query]"))
        websites = home.sd.of_type("WebSite")
        has_action = any(_has_prop(w, "potentialAction") for w in websites)
        if has_search and not has_action:
            out.add(Finding(
                check_id="G2.SD.SEARCHACTION_MISSING",
                title="The site has search but publishes no SearchAction",
                evidence=(
                    "The homepage contains a search input, but no WebSite node with a "
                    "potentialAction SearchAction is present in its structured data "
                    f"({len(websites)} WebSite node(s) found)."
                ),
                mechanism=(
                    "A SearchAction tells a consumer how to query the site directly, "
                    "which lets a brand-name search surface a site search box and lets "
                    "an agent construct a search URL instead of guessing."
                ),
                confidence="high", signal_count=2, fact_class="enrichment",
                scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                            example_urls=[home.url]),
                evidence_refs=[metric_ref(home.url_hash, "website_search_actions", 0, 1)],
                suggested_action=SuggestedAction(
                    summary="Add a WebSite node with a SearchAction potentialAction.",
                    steps=[
                        "Emit on the homepage: WebSite with url, name, and potentialAction "
                        "of type SearchAction whose target is the search URL template, "
                        "e.g. https://<domain>/search?q={search_term_string}.",
                        "Set query-input to \"required name=search_term_string\".",
                    ],
                    effort="trivial", owner_role="Frontend engineering / SEO",
                    verification="Validate the homepage and confirm the SearchAction "
                                 "target URL returns results when filled in.",
                    expected_effect="Makes site search addressable by machines.",
                ),
            ))

    rendered_pages = [p for p in pages if p.rendered_doc is not None]
    if not rendered_pages:
        out.skip("G2.SD.SD_ONLY_IN_RENDERED_DOM",
                 reason="no rendered DOM is available, so markup injected by JavaScript "
                        "cannot be distinguished from markup that is absent",
                 remedy="install Playwright to enable the render tier")
    else:
        injected: List[Tuple[str, Any]] = []
        for page in rendered_pages:
            raw_types = set(page.sd.types_present())
            rendered_sd = sdextract.extract(page.rendered_doc)
            added = sorted(set(rendered_sd.types_present()) - raw_types)
            if added:
                injected.append((page.url, {"types": added}))
        for group in analyzer.group_by_template(context, injected):
            payload = group["payloads"][0]
            page = _page(context, group["example_urls"][0])
            if page is None:
                continue
            _emit(
                context, out, "G2.SD.SD_ONLY_IN_RENDERED_DOM", group, total=total,
                title="Structured data is injected by JavaScript and absent from the raw "
                      "response",
                evidence=(
                    f"{_prefix(group)} publish {', '.join(payload['types'])} markup only "
                    f"after JavaScript runs: the types are present in the rendered DOM "
                    f"and absent from the HTML response."
                ),
                mechanism=(
                    "Markup injected client-side, typically through a tag manager, is "
                    "invisible to every consumer that reads the raw response. It is the "
                    "markup equivalent of a render gap, and it survives review because "
                    "browser-based validators run the JavaScript."
                ),
                action=SuggestedAction(
                    summary="Emit structured data server-side in the HTML response.",
                    steps=[
                        "Move JSON-LD generation from the tag manager or client code into "
                        "the server-rendered template.",
                        "Remove the client-side injection once the server version ships, "
                        "to avoid duplicate nodes.",
                    ],
                    effort="medium", owner_role="Frontend engineering",
                    verification="curl the URL and confirm the JSON-LD block is present "
                                 "in the raw response.",
                    expected_effect="Makes the markup visible to non-rendering consumers.",
                ),
                refs=[metric_ref(page.url_hash, "types_only_in_rendered_dom",
                                 len(payload["types"]), 0)],
                fact_class="primary",
            )

    legacy = [p for p in pages if p.sd.has_any and not p.sd.jsonld_nodes()]
    if legacy and len(legacy) >= context.min_sample():
        out.observe(
            "G2.SD.MICRODATA_ONLY_LEGACY",
            "Structured data uses only Microdata or RDFa",
            (f"{analyzer.kn(len(legacy), len(pages))} sampled pages carry structured data "
             f"exclusively as {', '.join(sorted({s for p in legacy for s in p.sd.syntaxes}))}"
             f". This is valid, but JSON-LD is the better-supported carrier: it is parsed "
             f"more robustly, is independent of the HTML structure so a redesign cannot "
             f"break it, and is the syntax consumers document first."),
            confidence="high",
        )


# ========================================================================================
# canonicalisation
# ========================================================================================

def check_canonical(context: Context, out: FindingsFile) -> None:
    pages = context.content_pages()
    total = len(pages)
    by_url = {urlnorm.strip_index(p.normal): p for p in pages if p.normal}
    ledger_by_key = {}
    for entry in context.ledger.entries():
        n = urlnorm.try_normalise(entry.url.split("#")[0])
        if n:
            ledger_by_key[urlnorm.strip_index(n)] = entry

    missing: List[Tuple[str, Any]] = []
    non_self: List[Tuple[str, Any]] = []
    dup_floor = float(context.threshold("structured_data.canonical_duplicate_similarity"))
    og_conflict: List[Tuple[str, Any]] = []
    to_non_200: List[Tuple[str, Any]] = []
    pagination: List[Tuple[str, Any]] = []
    chains: List[Tuple[str, Any]] = []

    for page in pages:
        if page.normal is None:
            continue
        own = urlnorm.strip_index(page.normal)
        canonical_raw = page.doc.canonical()
        if not canonical_raw:
            missing.append((page.url, {}))
            continue
        target = urlnorm.try_normalise(canonical_raw, base=page.entry.final_url)
        if target is None:
            continue
        target_key = urlnorm.strip_index(target)
        link = page.doc.link_rel("canonical")[0]

        if target_key != own:
            cross_domain = not urlnorm.same_site(target.host, page.normal.host)
            only_params_differ = (target.path.rstrip("/") == page.normal.path.rstrip("/"))
            is_paginated = "paginated" in page.path_classes
            if is_paginated:
                pagination.append((page.url, {"canonical": target.url,
                                              "selector": selector_for(link)}))
            elif not cross_domain and not only_params_differ \
                    and not _consolidates_duplicate(context, page, target_key, dup_floor):
                # FP guards: intentional cross-domain canonicals (syndication), canonicals from
                # a parameter variant to its clean URL, and duplicates consolidated onto a
                # near-identical canonical copy (an old docs version, a legacy path) are
                # all correct.
                non_self.append((page.url, {"canonical": target.url,
                                            "selector": selector_for(link)}))
            target_entry = ledger_by_key.get(target_key)
            if target_entry is not None:
                if target_entry.status and target_entry.status >= 300 \
                        and not target_entry.redirects:
                    to_non_200.append((page.url, {"canonical": target.url,
                                                  "status": target_entry.status,
                                                  "selector": selector_for(link)}))
                target_page = by_url.get(target_key)
                if target_page is not None:
                    second = target_page.doc.canonical()
                    second_n = urlnorm.try_normalise(second, base=target_page.url) \
                        if second else None
                    if second_n is not None and urlnorm.strip_index(second_n) != target_key:
                        chains.append((page.url, {"canonical": target.url,
                                                  "second": second_n.url,
                                                  "selector": selector_for(link)}))

        og_url = page.doc.og("url")
        og_n = urlnorm.try_normalise(og_url, base=page.entry.final_url) if og_url else None
        if og_n is not None and urlnorm.strip_index(og_n) != target_key \
                and og_n.path.rstrip("/") != target.path.rstrip("/"):
            og_conflict.append((page.url, {"canonical": target.url, "og": og_n.url,
                                           "selector": selector_for(link)}))

    if len(missing) >= context.min_sample():
        for group in analyzer.group_by_template(context, missing):
            page = _page(context, group["example_urls"][0])
            if page is None or not group["template_bound"]:
                continue
            _emit(
                context, out, "G2.CANONICAL.MISSING", group, total=total,
                title="Indexable content pages declare no canonical URL",
                evidence=(f"{_prefix(group)} carry no rel=canonical link. Affected: "
                          f"{analyzer.summarise_urls(group['urls'])}."),
                mechanism=(
                    "Without a canonical, each URL variant a page is reachable at -- "
                    "tracking parameters, trailing slashes, sort orders -- is a separate "
                    "candidate, and the consumer decides which one represents the page."
                ),
                action=SuggestedAction(
                    summary="Emit a self-referential rel=canonical on every indexable page.",
                    steps=["Add <link rel=\"canonical\" href=\"<absolute clean URL>\"> in "
                           "the head of the template.",
                           "Use the preferred host and strip tracking and sort parameters "
                           "from the canonical value."],
                    effort="trivial", owner_role="Frontend engineering / SEO",
                    verification="Fetch an affected URL with a tracking parameter appended "
                                 "and confirm the canonical still names the clean URL.",
                    expected_effect="Consolidates URL variants onto one canonical page.",
                ),
                refs=[metric_ref(page.url_hash, "canonical_links", 0, 1)],
                fact_class="secondary",
            )

    specs = [
        ("G2.CANONICAL.NON_SELF_REFERENTIAL", non_self,
         "Served pages canonicalise to a different URL, removing themselves from indexes",
         lambda p: (f"the page declares rel=canonical {p['canonical']}, a different "
                    f"same-site URL that is not merely a parameter-free version of it"),
         ("A canonical is an instruction to index a different URL instead of this one. "
          "When the served, internally-linked page points elsewhere, it asks to be "
          "dropped -- the most common way a page removes itself from an index by "
          "accident, frequently from a template that hard-codes one URL."),
         "Make each page's canonical reference its own clean URL.", "primary"),
        ("G2.CANONICAL.CHAIN_OR_LOOP", chains,
         "Canonical targets point onward to further canonicals",
         lambda p: (f"the canonical target {p['canonical']} itself declares canonical "
                    f"{p['second']}, forming a chain"),
         ("Consumers do not reliably follow canonical chains. A chain or loop means no "
          "single preferred URL resolves, so the choice falls back to the consumer's "
          "own heuristics."),
         "Point every canonical directly at the final preferred URL.", "secondary"),
        ("G2.CANONICAL.TO_NON_200", to_non_200,
         "Canonical targets do not return 200",
         lambda p: (f"the canonical target {p['canonical']} returned HTTP {p['status']}"),
         ("A canonical pointing at an error or a redirect sends indexers to a URL that "
          "does not serve the content, so the page's own content is at risk of being "
          "dropped in favour of nothing."),
         "Point canonicals only at live URLs that return 200.", "primary"),
        ("G2.CANONICAL.VS_OGURL_CONFLICT", og_conflict,
         "rel=canonical and og:url name different URLs",
         lambda p: (f"rel=canonical names {p['canonical']} while og:url names {p['og']}"),
         ("Link previews and shares use og:url while indexers use the canonical. When "
          "they disagree, shared links accumulate on a different URL from the one being "
          "indexed, splitting the signals that should reinforce each other."),
         "Generate og:url from the same value as rel=canonical.", "enrichment"),
        ("G2.CANONICAL.PAGINATION_UNMARKED", pagination,
         "Paginated pages canonicalise to the first page",
         lambda p: (f"a paginated page declares canonical {p['canonical']} rather than "
                    f"its own URL"),
         ("Pointing page 2 onward at page 1 tells consumers the later pages are "
          "duplicates, so every item listed only on later pages loses its discovery "
          "path."),
         "Give each paginated page a self-referential canonical.", "enrichment"),
    ]
    for check_id, hits, title, evidence_fn, mechanism, fix, fact in specs:
        for group in analyzer.group_by_template(context, hits):
            payload = group["payloads"][0]
            page = _page(context, group["example_urls"][0])
            if page is None:
                continue
            _emit(
                context, out, check_id, group, total=total, title=title,
                evidence=(f"On {_prefix(group)}, {evidence_fn(payload)}. Affected: "
                          f"{analyzer.summarise_urls(group['urls'])}."),
                mechanism=mechanism,
                action=SuggestedAction(
                    summary=fix,
                    steps=[fix,
                           "Generate the canonical from the request's resolved clean URL "
                           "in the template rather than from a stored or hard-coded value.",
                           "Keep deliberate cross-domain canonicals (syndicated copies) "
                           "as they are; they are correct."],
                    effort="low", owner_role="Frontend engineering / SEO",
                    verification="Fetch an affected URL and confirm the canonical link "
                                 "names the intended URL and that URL returns 200.",
                    expected_effect="Keeps the served page eligible to be indexed under "
                                    "its own URL.",
                ),
                refs=[dom_ref(page.url_hash, payload["selector"],
                              f"rel=canonical {payload.get('canonical', '')}")],
                fact_class=fact,
            )

    _check_param_duplicates(context, out)
    _check_hreflang(context, out)


def _check_param_duplicates(context: Context, out: FindingsFile) -> None:
    groups = context.ledger.content_hash_groups()
    dupes = []
    for _hash, urls in groups.items():
        normals = [urlnorm.try_normalise(u.split("#")[0]) for u in urls]
        paths = {n.path.rstrip("/") for n in normals if n}
        if len(paths) != 1 or len(urls) < 2:
            continue
        pages = [p for p in context.pages if p.url in urls]
        if not pages or any(p.doc.canonical() for p in pages):
            continue
        dupes.append((urls, pages[0]))
    if not dupes:
        return
    urls, page = dupes[0]
    out.add(Finding(
        check_id="G2.CANONICAL.PARAM_DUPLICATES",
        title="Parameter variants serve identical content with no canonical",
        evidence=(f"{len(dupes)} set(s) of URLs differing only by query parameters returned "
                  f"byte-identical responses and declare no canonical. Example set: "
                  f"{', '.join(sorted(urls)[:3])}."),
        mechanism=("Identical content at several URLs splits links and citations across "
                   "them, and each variant competes with the others for the same "
                   "queries."),
        confidence="high", signal_count=2, fact_class="secondary",
        scope=Scope(level="section", pages_affected=len(urls), pages_sampled=len(urls),
                    example_urls=sorted(urls)[:3]),
        evidence_refs=[metric_ref(page.url_hash, "identical_parameter_variants",
                                  len(urls), 1)],
        suggested_action=SuggestedAction(
            summary="Add a canonical from every parameter variant to the clean URL.",
            steps=["Emit rel=canonical naming the parameter-free URL on all variants.",
                   "Avoid generating internal links that carry sort or tracking "
                   "parameters."],
            effort="low", owner_role="Frontend engineering / SEO",
            verification="Fetch a variant URL and confirm its canonical names the clean "
                         "URL.",
            expected_effect="Consolidates duplicate variants onto one URL.",
        ),
    ))


def _check_hreflang(context: Context, out: FindingsFile) -> None:
    pages = context.content_pages()
    declared: Dict[str, Dict[str, str]] = {}
    for page in pages:
        if page.normal is None:
            continue
        entries = {}
        for node in page.doc.link_rel("alternate"):
            lang = (node.get("hreflang") or "").strip().lower()
            href = (node.get("href") or "").strip()
            if lang and href:
                n = urlnorm.try_normalise(href, base=page.entry.final_url)
                if n:
                    entries[lang] = urlnorm.strip_index(n)
        if entries:
            declared[urlnorm.strip_index(page.normal)] = entries
    if len(declared) < 2:
        return
    asymmetric = []
    for source, alternates in declared.items():
        for lang, target in alternates.items():
            if target == source or target not in declared:
                continue
            if source not in declared[target].values():
                asymmetric.append((source, target, lang))
    if not asymmetric:
        out.note_posture("hreflang_reciprocal", True)
        return
    source, target, lang = asymmetric[0]
    page = next((p for p in pages if p.normal and urlnorm.strip_index(p.normal) == source),
                pages[0])
    out.add(Finding(
        check_id="G2.CANONICAL.HREFLANG_ASYMMETRIC",
        title="hreflang alternates are not reciprocal",
        evidence=(f"{len(asymmetric)} hreflang declaration(s) point to a page that does not "
                  f"point back. Example: {source} declares {target} as hreflang={lang}, "
                  f"but {target} does not list {source}."),
        mechanism=("hreflang is only honoured when both pages confirm the relationship. "
                   "One-directional declarations are ignored, so visitors are served the "
                   "wrong language version."),
        confidence="high", signal_count=2, fact_class="secondary",
        scope=Scope(level="section", pages_affected=len(asymmetric),
                    pages_sampled=len(declared), example_urls=[source]),
        evidence_refs=[metric_ref(page.url_hash, "non_reciprocal_hreflang",
                                  len(asymmetric), 0)],
        suggested_action=SuggestedAction(
            summary="Make every hreflang set complete and reciprocal on all its pages.",
            steps=["Generate the full alternate set from one source for each piece of "
                   "content, and emit the identical set on every language version.",
                   "Include a self-referencing entry and an x-default."],
            effort="low", owner_role="Frontend engineering / Localisation",
            verification="For each page in a set, confirm it lists every other page and "
                         "is listed by each of them.",
            expected_effect="Makes language targeting reliable.",
        ),
    ))


# ========================================================================================
# entry point
# ========================================================================================

def detect(context: Context, out: FindingsFile) -> None:
    out.note_metric("pages_analyzed", len(context.content_pages()))
    out.note_metric("extraction_backend",
                    sorted({p.sd.backend for p in context.content_pages()}) or ["builtin"])
    if context.site_profile.is_ambiguous:
        for check_id in context.site_profile.excluded_checks():
            if check_id.startswith("G2."):
                out.skip(check_id, reason="the site profile is ambiguous, so "
                                          "type-conditional expectations were not applied")

    check_absent(context, out)
    check_syntax_and_typing(context, out)
    check_types_and_properties(context, out)
    check_entity_graph(context, out)
    check_contradictions(context, out)
    check_duplicates_and_faq(context, out)
    check_breadcrumbs_search_rendered(context, out)
    check_canonical(context, out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return analyzer.run(
        SKILL_ID,
        "Gate G2 (INTERPRET): JSON-LD, Microdata and RDFa up an eight-rung validity "
        "ladder, profile-conditional coverage, entity-graph linkage, markup that "
        "contradicts the visible page, and canonicalisation. Reads the ledger only.",
        detect, argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
