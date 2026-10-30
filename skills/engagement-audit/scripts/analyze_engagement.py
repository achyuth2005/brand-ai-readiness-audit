#!/usr/bin/env python3
"""Gate GE -- ENGAGE. Does the visitor who arrives stay?

Pure function of the evidence ledger. No network I/O.

Mechanism
---------
A visitor sent by an assistant is a **deep, context-free arrival**. They were sent to one
specific page to satisfy one specific intent, they have no homepage journey behind them,
and they have near-zero patience. The brief names the failures: "weak on-site
orientation / no context retention". So this skill is not a generic UX review; it asks
three things of every sampled page, in the order an arrival experiences them:

1. **Orientation** -- above the fold, without interaction: what is this, who is it for,
   what do I do next?
2. **Friction** -- is anything between the visitor and the content they came for?
3. **Context retention** -- can they situate themselves and go somewhere next? This half
   is computed over the *crawled link graph*, not page by page, which is part of what
   makes this a distinct evidence domain from the render and answerability skills.

Honest limits, stated rather than implied: there is no layout engine here. "Above the
fold" is an explicit document-order proxy, overlay geometry is read from statically
declared CSS only, and contrast is computed only where both colours are literal. Where
the evidence is not available the check is skipped or its confidence capped (rule R6).

See references/orientation-rules.md and references/friction-catalogue.md.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import analyzer, cssutil, dom as dom_mod, entity, lexicons  # noqa: E402
from auditkit import sitemap, textnorm, urlnorm  # noqa: E402
from auditkit.analyzer import Context, PageCheck, PageView  # noqa: E402
from auditkit.dom import selector_for  # noqa: E402
from auditkit.finding import (  # noqa: E402
    Finding, FindingsFile, Scope, SuggestedAction, dom_ref, header_ref, metric_ref,
)

SKILL_ID = "engagement-audit"

_CONTENT_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset("the and for with from your you our this that are was were how what "
                  "why who when where which into about all any can will has have page "
                  "home welcome".split())
_EYEBROW_RE = re.compile(r"eyebrow|kicker|overline|category|section-label|breadcrumb|"
                         r"tagline-top|pre-title|pretitle|subtitle-top", re.IGNORECASE)
# Checks that read what the server sends whatever the rendering path: head metadata,
# declared CSS and response size. They remain valid on a client-rendered shell. Every
# other engagement check judges what a human sees, and is not run against an empty shell.
_SERVED_AS_IS = frozenset({
    "GE.FRICTION.NO_VIEWPORT_META", "GE.FRICTION.RENDER_BLOCKING_HEAD",
    "GE.FRICTION.HEAVY_PAGE", "GE.FRICTION.LAYOUT_SHIFT_FONTS",
    "GE.FRICTION.CONTENT_GATING_OVERLAY", "GE.FRICTION.COOKIE_WALL_BLOCKS_CONTENT",
    "GE.FRICTION.INTRUSIVE_STICKY",
})


def _is_shell(context: Context, page: PageView) -> bool:
    """True when the raw response is an empty client-rendered shell.

    A visitor with JavaScript sees this page's h1, calls to action and links; the raw
    response does not contain them. Judging orientation from the shell would report the
    render gap a second time, disguised as an engagement problem -- a false positive. The
    gap itself is reported once, at G1, by render-extraction-audit.
    """
    doc = page.doc
    thin = int(context.threshold("render.thin_text_chars"))
    mount = int(context.threshold("render.mount_node_text_chars"))
    return len(doc.main_text()) < thin and any(
        len(doc.visible_text(node)) < mount for node in doc.mount_nodes())


_CONVERSION_RE = re.compile(
    r"\b(add to (cart|bag|basket)|buy|order|purchase|checkout|enquire|inquire|request "
    r"(a )?(quote|demo|pricing)|get (a )?quote|contact (us|sales)|sign ?up|start|try|"
    r"book|reserve|subscribe|get started|talk to)\b", re.IGNORECASE)


# ========================================================================================
# shared helpers
# ========================================================================================

class _Styles:
    """Per-page static CSS: inline <style> blocks plus stylesheets already in the ledger."""

    def __init__(self, context: Context):
        self.context = context
        self._cache: Dict[str, cssutil.Stylesheet] = {}

    def for_page(self, page: PageView) -> cssutil.Stylesheet:
        if page.url in self._cache:
            return self._cache[page.url]
        sources = list(page.doc.inline_styles())
        unavailable: List[str] = []
        for href in page.doc.stylesheet_hrefs():
            normal = urlnorm.try_normalise(href, base=page.entry.final_url or page.url)
            entry = self.context.ledger.get(normal.url) if normal else None
            if entry is not None and entry.ok and entry.content_hash:
                sources.append(self.context.ledger.text(entry))
            else:
                unavailable.append(href)
        sheet = cssutil.parse(sources, unavailable=unavailable)
        self._cache[page.url] = sheet
        return sheet


def _ident(node: Any) -> Tuple[str, List[str]]:
    try:
        return (node.get("id") or "").strip(), [c for c in (node.get("class") or []) if c]
    except (AttributeError, TypeError):
        return "", []


def _declarations(sheet: cssutil.Stylesheet, node: Any) -> Dict[str, str]:
    node_id, classes = _ident(node)
    merged = sheet.declarations_for(tag=node.name, ident=node_id, classes=classes, node=node)
    merged.update(cssutil.parse_inline(node.get("style") or ""))
    return merged


def _declared_hidden(sheet: cssutil.Stylesheet, node: Any) -> bool:
    if node.has_attr("hidden") or (node.get("aria-hidden") or "").lower() == "true":
        return True
    if dom_mod.looks_hidden_by_class(node):
        return True
    return cssutil.is_hidden_by_css(_declarations(sheet, node))[0]


def _scroll_locked(sheet: cssutil.Stylesheet, page: PageView) -> Tuple[bool, str]:
    locked, rule = sheet.body_scroll_locked(page.doc.soup)
    if locked:
        return locked, rule
    for tag in ("body", "html"):
        node = page.doc.soup.find(tag)
        style = (node.get("style") or "").lower().replace(" ", "") if node else ""
        if "overflow:hidden" in style:
            return True, f"<{tag} style=\"overflow:hidden\">"
    return False, ""


def _internal_links(context: Context, page: PageView, main_only: bool) -> List[Any]:
    host = context.site_host() or (page.normal.host if page.normal else "")
    out = []
    for link in page.doc.links():
        if main_only and not link.in_main:
            continue
        if link.href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        target = urlnorm.try_normalise(link.href, base=page.entry.final_url or page.url)
        if target is None or not host or not urlnorm.same_site(target.host, host):
            continue
        out.append((link, target))
    return out


def _content_words(text: str) -> set:
    return {w for w in _CONTENT_WORD_RE.findall(text.lower()) if w not in _STOP}


def _above_fold_ctas(page: PageView, limit: int) -> List[Tuple[str, str, Any]]:
    """``(label, target, node)`` for links and buttons in the above-fold proxy."""
    out = []
    for node in page.doc.above_fold_nodes(limit):
        if node.name not in ("a", "button"):
            continue
        if node.name == "input" and (node.get("type") or "") not in ("submit", "button"):
            continue
        label = textnorm.normalise_text(page.doc.visible_text(node)
                                        or node.get("aria-label") or "")
        if not label:
            continue
        target = (node.get("href") or node.get("formaction") or label).strip()
        out.append((label, target, node))
    return out


def _is_specific_cta(label: str) -> bool:
    lowered = label.lower().strip(" !.")
    if lowered in lexicons.WEAK_CTA_LABELS or not lowered:
        return False
    first = lowered.split()[0]
    return first in lexicons.ACTION_VERBS and len(lowered.split()) >= 2


# ========================================================================================
# per-page checks
# ========================================================================================

def build_page_checks(context: Context, brand: entity.Brand,
                      styles: _Styles) -> List[PageCheck]:
    fold = int(context.threshold("engagement.above_fold_nodes"))
    value_words = int(context.threshold("engagement.min_value_prop_words"))
    max_ctas = int(context.threshold("engagement.max_above_fold_ctas"))
    overlap_min = float(context.threshold("engagement.title_overlap_min"))
    deep_depth = int(context.threshold("engagement.deep_page_min_depth"))
    cls_share = float(context.threshold("engagement.cls_image_share"))
    cls_min = int(context.threshold("engagement.min_images_for_cls"))
    max_blocking = int(context.threshold("engagement.max_blocking_head_scripts"))
    heavy = int(context.threshold("engagement.heavy_page_bytes"))
    sticky_px = float(context.threshold("engagement.sticky_height_px"))
    max_required = int(context.threshold("engagement.max_form_required_fields"))
    nav_links_min = int(context.threshold("engagement.min_nav_links_for_skip_link"))
    skip_scan = int(context.threshold("engagement.skip_link_scan_nodes"))
    min_headings = int(context.threshold("engagement.min_headings_for_skip_check"))
    max_sentence = int(context.threshold("engagement.max_reading_sentence_words"))
    long_words = int(context.threshold("answerability.long_page_words"))
    min_words = int(context.threshold("answerability.min_words_for_prose_checks"))
    min_sentences = int(context.threshold("answerability.min_sentences_for_self_containment"))
    thin_share = float(context.threshold("false_positive_control.thin_relative_to_template"))

    # Paths known to be pages: fetched, or declared in a sitemap. A parent link is only
    # expected to an ancestor that is itself a page.
    known_paths = {p.path.rstrip("/") or "/" for p in context.pages}
    known_paths |= {n.path.rstrip("/") or "/" for u in sitemap.collect(context.ledger)["urls"]
                    if (n := urlnorm.try_normalise(u)) is not None}

    landing = lambda p: p.path in ("/", "") or p.has_path_class("service", "pricing")  # noqa: E731
    actionable = lambda p: landing(p) or p.has_path_class("product")  # noqa: E731

    def main_ref(page: PageView, excerpt: str = "") -> Any:
        return dom_ref(page.url_hash, page.doc.main_selector,
                       excerpt or textnorm.truncate(page.doc.main_text(), 120))

    # -------------------------------------------------------------------- orientation --
    def no_h1(page):
        if page.doc.h1s():
            return None
        return {"observed": "the page has no h1 element, so no single statement says "
                            "what the page is",
                "refs": [metric_ref(page.url_hash, "h1_count", 0, 1), main_ref(page)]}

    def multiple_h1(page):
        h1s = page.doc.h1s()
        if len(h1s) < 2:
            return None
        return {"observed": f"the page has {len(h1s)} h1 elements "
                            f"({', '.join(repr(textnorm.truncate(h, 40)) for h in h1s[:3])})",
                "refs": [metric_ref(page.url_hash, "h1_count", len(h1s), 1)]}

    def hierarchy(page):
        main = page.doc.main_node()
        headings = page.doc.headings(main) if main is not None else page.doc.headings()
        if len(headings) < min_headings:
            return None
        previous = headings[0][0]
        for level, text, node in headings[1:]:
            if level > previous + 1:
                return {"observed": (f"heading levels jump from h{previous} to h{level} at "
                                     f"\"{textnorm.truncate(text, 60)}\""),
                        "refs": [dom_ref(page.url_hash, selector_for(node), text)]}
            previous = level
        return None

    def generic_h1(page):
        h1s = page.doc.h1s()
        if not h1s:
            return None
        h1 = textnorm.normalise_text(h1s[0]).lower().strip(" !.")
        bare_brand = brand.known and textnorm.normalise_brand_name(h1) == brand.normalised
        if h1 not in lexicons.GENERIC_H1_VALUES and not bare_brand:
            return None
        node = page.doc.soup.find("h1")
        return {"observed": (f"the h1 is \"{h1s[0]}\"" + (", the bare brand name," if
                                                           bare_brand else ",")
                             + " which says nothing about what the page offers or who it "
                               "is for"),
                "refs": [dom_ref(page.url_hash, selector_for(node), h1s[0])]}

    def value_prop(page):
        text = page.doc.above_fold_text(fold)
        heading_text = " ".join(t for _l, t, _n in page.doc.headings()[:3])
        for sentence in textnorm.sentences(text) + [heading_text]:
            words = textnorm.words(sentence)
            if len(words) >= value_words and \
                    _content_words(sentence) & lexicons.CATEGORY_NOUNS:
                return None
        return {"observed": (f"no sentence of {value_words} or more words naming a "
                             f"category of product, service or organisation appears in "
                             f"the first {fold} elements of the main region"),
                "refs": [metric_ref(page.url_hash, "above_fold_value_prop_sentences", 0, 1),
                         main_ref(page, textnorm.truncate(text, 140))]}

    def primary_cta(page):
        ctas = _above_fold_ctas(page, fold)
        if any(_is_specific_cta(label) for label, _t, _n in ctas):
            return None
        labels = sorted({label for label, _t, _n in ctas})[:5]
        return {"observed": ("no specific call to action appears in the first "
                             f"{fold} elements of the main region"
                             + (f"; the only actions offered are "
                                f"{', '.join(repr(l) for l in labels)}, which name no "
                                f"object" if labels else "")),
                "refs": [metric_ref(page.url_hash, "specific_above_fold_ctas", 0, 1)]}

    def cta_competition(page):
        targets = {t for label, t, _n in _above_fold_ctas(page, fold)
                   if _is_specific_cta(label)}
        if len(targets) < max_ctas:
            return None
        return {"observed": (f"{len(targets)} distinct call-to-action targets compete in "
                             f"the first {fold} elements of the main region (threshold "
                             f"{max_ctas})"),
                "refs": [metric_ref(page.url_hash, "distinct_above_fold_cta_targets",
                                    len(targets), max_ctas)],
                "confidence": "medium"}

    def title_mismatch(page):
        h1s = page.doc.h1s()
        title = page.doc.title()
        if not h1s or not title:
            return None
        title_core = _content_words(title)
        if brand.known:
            title_core -= set(brand.normalised.split())
        h1_words = _content_words(h1s[0])
        if len(title_core) < 2 or len(h1_words) < 2:
            return None
        overlap = len(title_core & h1_words) / len(title_core | h1_words)
        if overlap >= overlap_min:
            return None
        return {"observed": (f"the title \"{textnorm.truncate(title, 70)}\" and the h1 "
                             f"\"{textnorm.truncate(h1s[0], 70)}\" share {overlap:.0%} of "
                             f"their content words (threshold {overlap_min:.0%})"),
                "refs": [metric_ref(page.url_hash, "title_h1_word_overlap",
                                    round(overlap, 3), overlap_min),
                         dom_ref(page.url_hash, "title", title)],
                "confidence": "medium"}

    def deep_context(page):
        doc = page.doc
        if doc.select(",".join(f"[class*={p}],[id*={p}]" for p in
                               lexicons.BREADCRUMB_PATTERNS[:4])) \
                or doc.select("nav[aria-label*=readcrumb]") \
                or page.sd.of_type("BreadcrumbList"):
            return None
        parts = [s for s in page.path.split("/") if s]
        ancestors = {"/" + "/".join(parts[:i]) for i in range(1, len(parts))} & known_paths
        if not ancestors:
            # No ancestor is a page -- on many platforms /pages/ or /l/ is a routing
            # prefix, not a section -- so there is no parent to link to or name.
            return None
        ancestors |= {a + "/" for a in ancestors}
        for _link, target in _internal_links(context, page, main_only=False):
            if target.path in ancestors:
                return None
        for node in doc.above_fold_nodes(fold):
            node_id, classes = _ident(node)
            if _EYEBROW_RE.search(" ".join([node_id] + classes)):
                return None
        return {"observed": (f"a page at path depth {page.depth} has no breadcrumb (visual "
                             f"or BreadcrumbList), no link to any parent path "
                             f"({', '.join(sorted(a for a in ancestors if not a.endswith('/'))) or 'none'}), "
                             f"and no section label above the fold"),
                "refs": [metric_ref(page.url_hash, "context_signals_found", 0, 1),
                         main_ref(page)]}

    # ----------------------------------------------------------------------- friction --
    def overlay(page):
        sheet = styles.for_page(page)
        doc = page.doc
        for node in doc.soup.find_all(["div", "section", "aside", "dialog", "form"]):
            node_id, classes = _ident(node)
            ident = " ".join([node_id] + classes).lower()
            role = (node.get("role") or "").lower()
            modal = (node.get("aria-modal") or "").lower() == "true"
            named = any(p in ident for p in lexicons.OVERLAY_NAME_PATTERNS)
            if not (role in ("dialog", "alertdialog") or modal or named
                    or node.name == "dialog"):
                continue
            if any(p in ident for p in lexicons.CONSENT_NAME_PATTERNS):
                continue
            if node.name == "dialog" and not node.has_attr("open"):
                continue
            if _declared_hidden(sheet, node):
                continue
            covering, reasons = cssutil.is_viewport_covering(_declarations(sheet, node))
            if not covering:
                continue
            locked, lock_rule = _scroll_locked(sheet, page)
            confidence = "high" if locked and sheet.complete else "medium"
            return {
                "observed": (f"`{dom_mod.node_identity(node)}` is named or roled as an "
                             f"overlay, is not hidden by any declared attribute, class "
                             f"convention or CSS rule, and declares covering geometry "
                             f"({'; '.join(reasons)})"
                             + (f", while {lock_rule} locks page scrolling" if locked else
                                ", with no scroll lock detected")
                             + ("" if sheet.complete else
                                f". {len(sheet.unavailable)} linked stylesheet(s) were not "
                                f"available, so a hiding rule could exist there and "
                                f"confidence is capped at medium")),
                "refs": [dom_ref(page.url_hash, selector_for(node),
                                 dom_mod.node_identity(node)),
                         metric_ref(page.url_hash, "overlay_signals", 3 + int(locked), 3)],
                "confidence": confidence, "signal_count": 3 + int(locked),
            }
        return None

    def cookie_wall(page):
        sheet = styles.for_page(page)
        locked, lock_rule = _scroll_locked(sheet, page)
        if not locked:
            return None
        for node in page.doc.soup.find_all(["div", "section", "aside", "dialog"]):
            node_id, classes = _ident(node)
            ident = " ".join([node_id] + classes).lower()
            if not any(p in ident for p in lexicons.CONSENT_NAME_PATTERNS):
                continue
            if _declared_hidden(sheet, node):
                continue
            covering, reasons = cssutil.is_viewport_covering(_declarations(sheet, node))
            return {
                "observed": (f"consent container `{dom_mod.node_identity(node)}` is visible "
                             f"by default and {lock_rule} locks page scrolling"
                             + (f", with covering geometry ({'; '.join(reasons)})"
                                if covering else "")),
                "refs": [dom_ref(page.url_hash, selector_for(node),
                                 dom_mod.node_identity(node))],
                "confidence": "high" if covering and sheet.complete else "medium",
                "signal_count": 2 + int(covering),
            }
        return None

    def forced_registration(page):
        text = page.doc.main_text().lower()
        markers = [m for m in lexicons.REGISTRATION_WALL_FINGERPRINTS if m in text]
        if not markers:
            return None
        forms = [f for f in page.doc.forms() if f["in_main"]
                 and (f["has_email"] or f["has_password"])]
        cluster = context.cluster_of(page)
        median = cluster.median("main_words") if cluster else 0.0
        words = textnorm.word_count(page.doc.main_text())
        if not forms or (median and words >= median * thin_share):
            return None
        return {"observed": (f"the main region shows registration wording "
                             f"({', '.join(repr(m) for m in markers[:2])}), contains a "
                             f"signup form, and carries {words} words against a template "
                             f"median of {median:.0f}"),
                "refs": [dom_ref(page.url_hash, forms[0]["selector"], "signup form"),
                         metric_ref(page.url_hash, "main_words", words, round(median, 1))]}

    def viewport(page):
        if page.doc.has_viewport_meta():
            return None
        return {"observed": "the document declares no <meta name=\"viewport\">",
                "refs": [metric_ref(page.url_hash, "viewport_meta_tags", 0, 1),
                         dom_ref(page.url_hash, "head", page.doc.title() or "head")]}

    def cls(page):
        images = [i for i in page.doc.images() if i.in_main]
        if len(images) < cls_min:
            return None
        undimensioned = [i for i in images if not i.has_dimensions]
        share = len(undimensioned) / len(images)
        if share <= cls_share:
            return None
        return {"observed": (f"{len(undimensioned)} of {len(images)} main-region images "
                             f"({share:.0%}, threshold {cls_share:.0%}) declare neither "
                             f"width and height attributes nor an inline aspect-ratio. "
                             f"Dimensions set only in external CSS cannot be seen here, so "
                             f"confidence is capped at medium"),
                "refs": [metric_ref(page.url_hash, "images_without_dimensions_share",
                                    round(share, 3), cls_share),
                         dom_ref(page.url_hash, undimensioned[0].selector,
                                 undimensioned[0].src[:100])],
                "confidence": "medium"}

    def blocking(page):
        scripts = page.doc.blocking_head_scripts
        if len(scripts) <= max_blocking:
            return None
        return {"observed": (f"{len(scripts)} synchronous external scripts in <head> "
                             f"carry neither defer, async nor type=module (threshold "
                             f"{max_blocking})"),
                "refs": [metric_ref(page.url_hash, "blocking_head_scripts", len(scripts),
                                    max_blocking),
                         dom_ref(page.url_hash, selector_for(scripts[0]),
                                 scripts[0].get("src") or "script")]}

    def heavy_page(page):
        size = page.entry.declared_length or page.entry.content_length
        if not size or size <= heavy:
            return None
        return {"observed": (f"the HTML document alone is {size / 1048576:.1f} MB "
                             f"(threshold {heavy / 1048576:.1f} MB), before any images, "
                             f"scripts or styles"),
                "refs": [metric_ref(page.url_hash, "document_bytes", size, heavy)]
                + ([header_ref(page.url_hash, "content-length",
                               page.entry.header("content-length"))]
                   if page.entry.header("content-length") else [])}

    def autoplay(page):
        for media in page.doc.media():
            if media.has_attr("autoplay") and not media.has_attr("muted"):
                return {"observed": f"a <{media.name}> element autoplays without muted",
                        "refs": [dom_ref(page.url_hash, selector_for(media),
                                         dom_mod.node_identity(media))]}
        return None

    def fonts(page):
        missing = styles.for_page(page).font_display_missing()
        if not missing:
            return None
        return {"observed": (f"{len(missing)} @font-face rule(s) declare no font-display "
                             f"({', '.join(sorted(set(missing))[:3])})"),
                "refs": [metric_ref(page.url_hash, "font_faces_without_font_display",
                                    len(missing), 0)]}

    def sticky(page):
        sheet = styles.for_page(page)
        total = 0.0
        parts = []
        for node in page.doc.soup.find_all(["header", "div", "nav", "aside", "section"]):
            decl = _declarations(sheet, node)
            if (decl.get("position") or "").lower() not in ("fixed", "sticky"):
                continue
            if _declared_hidden(sheet, node):
                continue
            height = cssutil.fixed_height_px(decl)
            if height:
                total += height
                parts.append(f"{dom_mod.node_identity(node)} {height:.0f}px")
        if total <= sticky_px:
            return None
        return {"observed": (f"fixed-position elements with declared heights stack to "
                             f"{total:.0f}px (threshold {sticky_px:.0f}px): "
                             f"{', '.join(parts[:4])}"),
                "refs": [metric_ref(page.url_hash, "fixed_chrome_height_px", round(total),
                                    sticky_px)],
                "confidence": "medium"}

    def tab_trap(page):
        panels = page.doc.select("[role=tabpanel]")
        tabs = page.doc.select("[role=tab]")
        if len(panels) < 2 or len(tabs) < 2:
            return None
        empty = [p for p in panels if len(page.doc.visible_text(p)) < 40]
        if not empty:
            return None
        return {"observed": (f"a tab interface with {len(tabs)} tabs has {len(empty)} of "
                             f"{len(panels)} panels empty in the raw document, so their "
                             f"content exists only after a script runs on click"),
                "refs": [dom_ref(page.url_hash, selector_for(empty[0]), "empty tabpanel"),
                         metric_ref(page.url_hash, "empty_tab_panels", len(empty), 0)],
                "confidence": "medium"}

    def skip_link(page):
        nav_links = [l for l in page.doc.links() if l.in_nav]
        if len(nav_links) < nav_links_min:
            return None
        body = page.doc.soup.find("body")
        if body is None:
            return None
        scanned = 0
        for node in body.descendants:
            if getattr(node, "name", None) is None:
                continue
            scanned += 1
            if scanned > skip_scan:
                break
            if node.name == "a":
                href = (node.get("href") or "").lower()
                ident = " ".join(_ident(node)[1]).lower()
                label = page.doc.visible_text(node).lower()
                if href.startswith("#") and ("skip" in label or
                                             any(p in ident for p in lexicons.SKIP_LINK_PATTERNS)):
                    return None
        return {"observed": (f"navigation carries {len(nav_links)} links and no skip link "
                             f"appears among the first {skip_scan} elements of the body"),
                "refs": [metric_ref(page.url_hash, "navigation_links", len(nav_links),
                                    nav_links_min)]}

    def unlabelled(page):
        forms = [f for f in page.doc.forms() if f["unlabelled"]]
        if not forms:
            return None
        count = sum(len(f["unlabelled"]) for f in forms)
        field = forms[0]["unlabelled"][0]
        return {"observed": (f"{count} form field(s) across {len(forms)} form(s) have no "
                             f"programmatic label (no <label>, aria-label, "
                             f"aria-labelledby or title)"),
                "refs": [dom_ref(page.url_hash, field["selector"],
                                 f"{field['type']} field {field['name']}".strip()),
                         metric_ref(page.url_hash, "unlabelled_fields", count, 0)]}

    def contrast(page):
        sheet = styles.for_page(page)
        pairs = []
        for node in page.doc.soup.find_all(style=True):
            decl = cssutil.parse_inline(node.get("style") or "")
            pairs.append((decl, selector_for(node), dom_mod.node_identity(node)))
        for rule in sheet.rules:
            if rule.in_media or any(s.startswith("@") for s in rule.selectors):
                continue
            pairs.append((rule.declarations, "", ", ".join(rule.selectors)[:60]))
        for decl, selector, label in pairs:
            fg = cssutil.parse_color(decl.get("color", ""))
            bg = cssutil.parse_color(decl.get("background-color", "")
                                     or decl.get("background", ""))
            if fg is None or bg is None:
                continue
            ratio = cssutil.contrast_ratio(fg, bg)
            if cssutil.meets_aa(ratio):
                continue
            return {"observed": (f"`{label}` declares color {decl.get('color')} on "
                                 f"background {decl.get('background-color') or decl.get('background')}, "
                                 f"a contrast ratio of {ratio}:1 against the WCAG AA "
                                 f"minimum of 4.5:1"),
                    "refs": ([dom_ref(page.url_hash, selector, label)] if selector else [])
                    + [metric_ref(page.url_hash, "declared_contrast_ratio", ratio, 4.5)]}
        return None

    # --------------------------------------------------------------- context retention --
    def dead_end(page):
        if page.path in ("/", ""):
            return None
        links = _internal_links(context, page, main_only=True)
        if links:
            return None
        return {"observed": ("the main content region contains no link to any other page "
                             "on the site"),
                "refs": [metric_ref(page.url_hash, "internal_links_in_main", 0, 1),
                         main_ref(page)]}

    def related(page):
        doc = page.doc
        for node in doc.soup.find_all(True):
            node_id, classes = _ident(node)
            if any(p in " ".join([node_id] + classes).lower()
                   for p in lexicons.RELATED_CONTENT_PATTERNS):
                return None
        if doc.link_rel("next") or doc.link_rel("prev") or \
                any("next" in l.rel or "prev" in l.rel for l in doc.links()):
            return None
        return {"observed": "no related, recommended, next or previous content module is "
                            "present",
                "refs": [metric_ref(page.url_hash, "related_content_modules", 0, 1)]}

    def path_unclear(page):
        for node in page.doc.soup.find_all(["a", "button", "input"]):
            label = page.doc.visible_text(node) or node.get("value") or \
                node.get("aria-label") or ""
            if _CONVERSION_RE.search(label):
                return None
        for form in page.doc.forms():
            if form["in_main"] and form["field_count"]:
                return None
        kind = "product" if page.has_path_class("product") else "pricing"
        return {"observed": (f"a {kind} page offers no purchase, enquiry, signup or "
                             f"contact control anywhere on the page"),
                "refs": [metric_ref(page.url_hash, "conversion_controls", 0, 1)]}

    def form_friction(page):
        for form in page.doc.forms():
            if not form["in_main"] or form["field_count"] <= 1:
                continue
            required = form["required_count"] or 0
            if required > max_required:
                return {"observed": (f"the main form requires {required} fields "
                                     f"(threshold {max_required}) out of "
                                     f"{form['field_count']}"),
                        "refs": [dom_ref(page.url_hash, form["selector"], "form"),
                                 metric_ref(page.url_hash, "required_form_fields",
                                            required, max_required)]}
        return None

    def no_lists(page):
        if page.doc.lists() or page.doc.tables():
            return None
        return {"observed": (f"a {textnorm.word_count(page.doc.main_text())}-word page "
                             f"contains no list, table or definition list"),
                "refs": [metric_ref(page.url_hash, "lists_and_tables", 0, 1)]}

    def long_sentences(page):
        sentences = [s for s in textnorm.sentences(page.doc.main_text())
                     if textnorm.word_count(s) >= 3]
        if len(sentences) < min_sentences:
            return None
        mean = sum(textnorm.word_count(s) for s in sentences) / len(sentences)
        if mean <= max_sentence:
            return None
        return {"observed": (f"mean sentence length is {mean:.0f} words across "
                             f"{len(sentences)} sentences (threshold {max_sentence})"),
                "refs": [metric_ref(page.url_hash, "mean_sentence_words", round(mean, 1),
                                    max_sentence)],
                "confidence": "medium"}

    def spec(check_id, detect, applies, title, mechanism, fix, steps, verification,
             effort="low", owner="Frontend engineering", fact="", template_only=False,
             expected=""):
        if check_id not in _SERVED_AS_IS:
            applies = (lambda page, base=applies: base(page)
                       and not _is_shell(context, page))
        return PageCheck(check_id=check_id, detect=detect, applies=applies, title=title,
                         mechanism=mechanism, fix=fix, steps=steps,
                         verification=verification, effort=effort, owner=owner,
                         fact_class=fact, template_only=template_only,
                         expected_effect=expected)

    everywhere = lambda p: True  # noqa: E731
    return [
        spec("GE.ORIENT.NO_H1", no_h1, everywhere,
             "Pages have no h1 stating what they are",
             "A visitor arriving deep in the site reads the h1 first to confirm they are "
             "in the right place. Without one, the page never states its subject, and the "
             "same gap leaves extractors guessing the page's topic.",
             "Give every page exactly one h1 that names its subject.",
             ["Render the page's primary subject as the h1 in each template.",
              "Style it however the design needs; the element is what matters."],
             "Confirm each affected template renders exactly one h1.",
             effort="trivial", fact="secondary"),
        spec("GE.ORIENT.MULTIPLE_H1", multiple_h1, everywhere,
             "Pages carry several competing h1 elements",
             "Multiple h1s leave neither a reader nor an extractor sure which statement "
             "is the page's subject.",
             "Keep one h1 per page and demote the others to h2.",
             ["Demote logo, section and widget headings to h2 or non-heading elements."],
             "Confirm exactly one h1 per affected page.", effort="trivial",
             fact="enrichment", template_only=True),
        spec("GE.ORIENT.HEADING_HIERARCHY_SKIP", hierarchy, everywhere,
             "Heading levels skip, breaking the document outline",
             "Screen readers navigate by heading level and chunkers split on it. A skipped "
             "level misrepresents which sections belong to which.",
             "Nest headings sequentially without skipping levels.",
             ["Change skipped levels so each heading is at most one level below its "
              "parent; use CSS for visual size."],
             "Inspect the heading outline and confirm no level is skipped.",
             effort="trivial", fact="enrichment", template_only=True),
        spec("GE.ORIENT.GENERIC_H1", generic_h1, everywhere,
             "The h1 is a placeholder or the bare brand name",
             "An h1 of 'Home' or just the brand name answers none of the three questions "
             "a deep arrival asks: what is this, who is it for, what do I do next.",
             "Rewrite the h1 to state what the page offers and for whom.",
             ["Replace the h1 with a specific statement, e.g. '<Category> for <audience>'.",
              "Keep the brand in the logo and title; the h1 is for the offer."],
             "Confirm the h1 names a category and an audience or outcome.",
             effort="trivial", owner="Content / Brand", fact="secondary"),
        spec("GE.ORIENT.NO_VALUE_PROP", value_prop, landing,
             "Nothing above the fold says what the page offers",
             "A visitor decides within seconds whether they are in the right place. With "
             "no full-sentence statement of the offer near the top, they have to scroll or "
             "infer -- and a context-free arrival mostly leaves instead.",
             "Add a one-sentence value proposition above the fold naming the category and "
             "audience.",
             ["Write one sentence: what it is, who it is for, what outcome it gives.",
              "Place it directly under the h1 as real text, not inside an image or "
              "carousel."],
             "Fetch the page and confirm the sentence appears within the first elements "
             "of the main region.", owner="Content / Marketing", fact="secondary"),
        spec("GE.ORIENT.NO_PRIMARY_CTA", primary_cta, actionable,
             "No clear next step is offered above the fold",
             "Every arrival needs an obvious next action. Generic labels such as 'Learn "
             "more' name no object, so the visitor cannot tell where they lead.",
             "Add one specific, verb-led call to action above the fold.",
             ["Label it with a verb and an object: 'Start a free trial', 'Book a demo', "
              "'Add to cart'.",
              "Replace generic 'Learn more' links with the destination they lead to."],
             "Confirm a verb-plus-object action appears near the top of each affected "
             "page.", owner="Product / Marketing", fact="secondary"),
        spec("GE.ORIENT.CTA_COMPETITION", cta_competition, actionable,
             "Many equally weighted calls to action compete above the fold",
             "A visitor with one intent faced with many equal choices hesitates or leaves "
             "(choice overload).",
             "Promote one primary action and demote the rest.",
             ["Keep one visually primary action per page; style others as secondary "
              "links."], "Confirm one dominant action above the fold.",
             owner="Product / Design", fact="enrichment"),
        spec("GE.ORIENT.TITLE_MISMATCH", title_mismatch, everywhere,
             "The title tag and the h1 describe different things",
             "An assistant's link preview shows the title. When the page's h1 describes "
             "something else, the arriving visitor's expectation is broken on landing, "
             "which drives immediate bounces.",
             "Align the title tag and h1 on the same subject.",
             ["Generate the title from the same field as the h1, plus the brand suffix."],
             "Compare title and h1 on affected pages; they should name the same subject.",
             owner="SEO / Content", fact="secondary", template_only=True),
        spec("GE.ORIENT.DEEP_PAGE_NO_CONTEXT", deep_context,
             lambda p: p.depth >= deep_depth,
             "Deep pages give an arriving visitor no way to situate themselves",
             "A visitor sent straight to a deep page by an assistant has no journey behind "
             "them. With no breadcrumb, parent link or section label, they cannot tell "
             "where they are or find related material -- the literal failure of context "
             "retention.",
             "Add a breadcrumb and a link to the parent section on deep pages.",
             ["Render a visual breadcrumb reflecting the page's place in the hierarchy.",
              "Emit matching BreadcrumbList structured data.",
              "Add a short section label above the h1."],
             "Open an affected page directly and confirm the breadcrumb links to each "
             "ancestor.", owner="UX / Frontend engineering", fact="secondary"),
        spec("GE.FRICTION.CONTENT_GATING_OVERLAY", overlay, everywhere,
             "An overlay covers the page content on arrival",
             "An overlay that is visible by default stands between the visitor and the "
             "content they were sent to read. For a context-free arrival the first "
             "interaction is a dismissal, and many simply leave.",
             "Stop showing covering overlays on arrival.",
             ["Trigger newsletter or promotional modals on exit intent or after "
              "meaningful engagement, never on load.",
              "Hide overlays by default in the HTML/CSS and reveal them only via script.",
              "Never lock scrolling behind a promotional overlay."],
             "Load an affected page and confirm the content is readable without closing "
             "anything.", effort="low", owner="Product / Marketing", fact="primary"),
        spec("GE.FRICTION.COOKIE_WALL_BLOCKS_CONTENT", cookie_wall, everywhere,
             "A consent banner locks the page until the visitor interacts",
             "A consent layer combined with a scroll lock makes content unreachable "
             "without interaction. It blocks the arriving visitor and, when the lock is "
             "in the served markup, non-interactive fetchers too.",
             "Present consent without locking scrolling or hiding content.",
             ["Use a non-blocking banner or panel; do not set overflow:hidden on body.",
              "Keep content readable while the consent choice is pending, deferring only "
              "the non-essential scripts."],
             "Load an affected page and confirm it scrolls with the banner still open.",
             owner="Legal / Frontend engineering", fact="primary"),
        spec("GE.FRICTION.FORCED_REGISTRATION", forced_registration, everywhere,
             "Content is truncated behind a signup form",
             "Requiring an account to read content turns away most visitors and serves "
             "non-authenticated fetchers only the teaser.",
             "Show the substantive content without registration; gate only tools or "
             "downloads.",
             ["Render the full content, or a substantive free portion, without an "
              "account.",
              "If gating is essential, declare it with isAccessibleForFree markup."],
             "Fetch an affected URL with no cookies and confirm the content is present.",
             effort="medium", owner="Product", fact="primary"),
        spec("GE.FRICTION.NO_VIEWPORT_META", viewport, everywhere,
             "Pages declare no viewport, so mobile visitors get a scaled-down desktop "
             "layout",
             "Without a viewport meta tag, mobile browsers render at desktop width and "
             "shrink it. Most assistant referrals arrive on mobile, and the text is too "
             "small to read without zooming.",
             "Add <meta name=\"viewport\" content=\"width=device-width, "
             "initial-scale=1\">.",
             ["Add the viewport meta tag to the shared <head> template."],
             "Open an affected page on a phone and confirm it renders at device width.",
             effort="trivial", fact="primary"),
        spec("GE.FRICTION.CLS_RISK", cls, everywhere,
             "Images reserve no space, so content jumps as they load",
             "Images without declared dimensions push text down as they load, moving the "
             "paragraph the visitor was reading.",
             "Declare width and height (or aspect-ratio) on every content image.",
             ["Emit width and height attributes from the image's intrinsic size in the "
              "template or CMS image component."],
             "Load the page on a slow connection and confirm text does not shift as "
             "images appear.", fact="secondary", template_only=True),
        spec("GE.FRICTION.RENDER_BLOCKING_HEAD", blocking, everywhere,
             "Synchronous scripts in the head delay first paint",
             "Every synchronous script in <head> must download and execute before "
             "anything is shown, extending the blank-screen time in which an arrival "
             "decides to leave.",
             "Add defer or async to head scripts, or move them to the end of the body.",
             ["Mark non-critical scripts defer.", "Load third-party tags asynchronously."],
             "Confirm no external script in <head> lacks defer, async or type=module.",
             fact="secondary", template_only=True),
        spec("GE.FRICTION.HEAVY_PAGE", heavy_page, everywhere,
             "The HTML document is heavy enough to stall on mobile",
             "A multi-megabyte HTML document -- usually inlined data or markup bloat -- "
             "delays first render on mobile networks before any other asset loads.",
             "Reduce the HTML payload.",
             ["Remove inlined hydration data the page does not need.",
              "Paginate or lazy-load long lists.", "Enable compression."],
             "Confirm the document size falls under the threshold.",
             effort="medium", fact="secondary"),
        spec("GE.FRICTION.AUTOPLAY_MEDIA", autoplay, everywhere,
             "Media autoplays with sound",
             "Unmuted autoplay is hostile on arrival and is blocked inconsistently across "
             "browsers, so the experience differs unpredictably.",
             "Mute autoplaying media or require a click to play.",
             ["Add muted and playsinline to autoplaying video, or remove autoplay."],
             "Load the page and confirm nothing plays with sound unprompted.",
             effort="trivial", fact="enrichment"),
        spec("GE.FRICTION.LAYOUT_SHIFT_FONTS", fonts, everywhere,
             "Web fonts load without font-display",
             "Without font-display, text can stay invisible while fonts load, then "
             "reflow -- blank or jumping text in the first seconds of the visit.",
             "Add font-display: swap (or optional) to every @font-face.",
             ["Set font-display in each @font-face rule or font provider URL."],
             "Confirm each @font-face declares font-display.", effort="trivial",
             fact="enrichment", template_only=True),
        spec("GE.FRICTION.INTRUSIVE_STICKY", sticky, everywhere,
             "Stacked fixed headers and bars consume the small-screen viewport",
             "Sticky header, promo bar, cookie bar and chat widget together can leave a "
             "fraction of a phone screen for content.",
             "Reduce fixed chrome to a single slim bar on small screens.",
             ["Collapse the header on scroll.", "Remove promo bars on mobile.",
              "Dock chat to a small launcher."],
             "On a 375x667 viewport, confirm fixed chrome uses under a quarter of the "
             "height.", owner="Design / Frontend engineering", fact="secondary"),
        spec("GE.FRICTION.TAB_TRAP_CONTENT", tab_trap, everywhere,
             "Primary content is hidden in tabs that are empty until clicked",
             "Tab panels populated by script on click are empty for anyone who does not "
             "click -- non-interactive fetchers and many skimming visitors alike.",
             "Render every tab panel's content in the HTML and use tabs only to toggle "
             "visibility.",
             ["Server-render all panels; hide inactive ones with CSS or the hidden "
              "attribute.", "Consider stacking sections on mobile instead of tabs."],
             "Fetch the page and confirm each panel's text is in the raw HTML.",
             effort="medium", fact="primary"),
        spec("GE.FRICTION.NO_SKIP_LINK", skip_link, everywhere,
             "Large navigation has no skip link",
             "Keyboard and screen-reader users must tab through every navigation link on "
             "every page before reaching content.",
             "Add a 'Skip to content' link as the first focusable element.",
             ["Add <a class=\"skip-link\" href=\"#main\">Skip to content</a> before the "
              "header, visible on focus."],
             "Press Tab once on page load and confirm the skip link appears and works.",
             effort="trivial", fact="enrichment", template_only=True),
        spec("GE.FRICTION.UNLABELLED_FORM_FIELDS", unlabelled, everywhere,
             "Form fields have no accessible label",
             "Fields identified only by placeholder text lose their label once the "
             "visitor types, and are unnamed for screen readers -- friction on the site's "
             "own conversion path.",
             "Give every form field a programmatic label.",
             ["Associate a <label for> with each field, or add aria-label where a "
              "visible label is impossible."],
             "Confirm every input, select and textarea has an accessible name.",
             fact="secondary", owner="Frontend engineering"),
        spec("GE.FRICTION.LOW_CONTRAST_DECLARED", contrast, everywhere,
             "Declared text colours fall below readable contrast",
             "Low-contrast text is hard to read on mobile screens outdoors and for "
             "low-vision visitors, which increases abandonment.",
             "Raise text contrast to at least 4.5:1.",
             ["Darken the foreground or lighten the background in the flagged rule."],
             "Recheck the colour pair with a WCAG contrast calculator.",
             effort="trivial", owner="Design", fact="secondary", template_only=True),
        spec("GE.NAV.DEAD_END_PAGE", dead_end,
             lambda p: textnorm.word_count(p.doc.main_text()) >= min_words,
             "Content pages link nowhere else on the site",
             "A deep arrival on a page with no in-body links has nowhere to go but back. "
             "The visit ends on that page.",
             "Add contextual links from the body to related pages and next steps.",
             ["Link key terms to their explanatory pages.",
              "End each page with a relevant next step: a related guide, the product, "
              "contact."],
             "Confirm each affected page has in-body links to other site pages.",
             owner="Content", fact="secondary"),
        spec("GE.NAV.NO_RELATED_CONTENT", related,
             lambda p: p.has_path_class("article", "product"),
             "Article and product pages offer no related or next content",
             "Without a related, next or previous module, a single-page visit cannot turn "
             "into a session, and a visitor who did not find what they needed has no "
             "alternative offered.",
             "Add a related-content or next/previous module to article and product "
             "templates.",
             ["Show 3-4 related items chosen by category or tag.",
              "Add next and previous links within series or categories."],
             "Confirm the module renders in the server response on affected templates.",
             owner="Product / Frontend engineering", fact="enrichment",
             template_only=True),
        spec("GE.CONV.PATH_UNCLEAR", path_unclear,
             lambda p: p.has_path_class("product", "pricing"),
             "Pages whose purpose implies a next step offer none",
             "A product or pricing page exists to move a visitor toward buying or "
             "enquiring. When it offers no control to do so, the intent the visitor "
             "arrived with has nowhere to go.",
             "Add the conversion control the page's intent implies.",
             ["Product pages: add-to-cart, buy, or enquire.",
              "Pricing pages: start trial, sign up, or contact sales, per plan."],
             "Confirm each affected page has a visible conversion control in its HTML.",
             owner="Product", fact="primary"),
        spec("GE.CONV.FORM_FRICTION", form_friction, everywhere,
             "The conversion form demands more fields than its action needs",
             "Each extra required field measurably reduces completion; fields that are not "
             "needed for the stated action are pure loss.",
             "Cut required fields to what the action strictly needs.",
             ["Keep name and email for a contact request; ask the rest later.",
              "Make optional fields visibly optional."],
             "Confirm the required field count falls within the threshold.",
             owner="Product / Marketing", fact="secondary"),
        spec("GE.READ.NO_LISTS", no_lists,
             lambda p: textnorm.word_count(p.doc.main_text()) >= long_words,
             "Long pages contain no lists or tables at all",
             "Readers scan before they read. A long page with no list or table offers "
             "nothing scannable, so visitors cannot find the part they came for.",
             "Break enumerations and comparisons out into lists and tables.",
             ["Turn any 'there are three ways...' paragraph into a list.",
              "Put comparisons in a table."],
             "Confirm affected pages contain lists or tables.", owner="Content",
             fact="enrichment", template_only=True),
        spec("GE.READ.LONG_SENTENCES", long_sentences,
             lambda p: textnorm.word_count(p.doc.main_text()) >= min_words,
             "Sentences are too long for comfortable screen reading",
             "Very long sentences sharply reduce comprehension on small screens, where "
             "only a few words are visible per line.",
             "Shorten sentences to one idea each.",
             ["Split sentences at conjunctions.", "Aim for an average under 20 words."],
             "Re-run the audit and confirm mean sentence length is under the threshold.",
             owner="Content", fact="enrichment", template_only=True),
    ]


# ========================================================================================
# graph-level checks
# ========================================================================================

def check_graph(context: Context, out: FindingsFile,
                shells: Sequence[PageView] = ()) -> None:
    pages = context.content_pages()
    if not pages:
        return
    home = context.homepage() or pages[0]
    site_maps = sitemap.collect(context.ledger)
    sitemap_keys = {urlnorm.try_normalise(u).url for u in site_maps["urls"]
                    if urlnorm.try_normalise(u)}

    inbound: Dict[str, int] = {}
    for page in pages:
        for _link, target in _internal_links(context, page, main_only=False):
            key = urlnorm.strip_index(target)
            if page.normal and key == urlnorm.strip_index(page.normal):
                continue
            inbound[key] = inbound.get(key, 0) + 1

    # --- orphans ------------------------------------------------------------------------
    from_sitemap = [p for p in pages if p.url in sitemap_keys and p.path not in ("/", "")]
    if shells:
        out.skip("GE.NAV.ORPHAN_PAGES",
                 reason=f"{len(shells)} crawled page(s) are client-rendered shells with no "
                        f"links in their raw response, so inbound-link counts would be "
                        f"understated and orphans overstated")
    elif len(from_sitemap) >= context.min_sample():
        orphans = [p for p in from_sitemap
                   if p.normal and inbound.get(urlnorm.strip_index(p.normal), 0) == 0]
        if orphans:
            out.add(Finding(
                check_id="GE.NAV.ORPHAN_PAGES",
                title="Sitemap pages are linked from nowhere on the site",
                evidence=(f"{analyzer.kn(len(orphans), len(from_sitemap))} fetched sitemap "
                          f"pages received no internal link from any of the "
                          f"{len(pages)} crawled pages. Examples: "
                          f"{analyzer.summarise_urls([p.url for p in orphans])}. This is "
                          f"measured over the crawled sample, so a link from an uncrawled "
                          f"page would not be seen."),
                mechanism=("A page reachable only through the sitemap has no navigational "
                           "path for a visitor and receives no internal authority. A "
                           "visitor who lands elsewhere will never find it, and crawlers "
                           "treat unlinked pages as unimportant."),
                confidence="medium", signal_count=2, fact_class="secondary",
                scope=Scope(level="site" if len(orphans) / len(from_sitemap) >= 0.6
                            else "section", pages_affected=len(orphans),
                            pages_sampled=len(from_sitemap),
                            example_urls=[p.url for p in orphans[:3]]),
                evidence_refs=[metric_ref(orphans[0].url_hash, "internal_inbound_links",
                                          0, 1)],
                suggested_action=SuggestedAction(
                    summary="Link orphaned pages from their parent section and related "
                            "content.",
                    steps=["Add each orphan to the relevant category or section index.",
                           "Link to it from related articles or products.",
                           "If a page is not worth linking, consider whether it should be "
                           "in the sitemap at all."],
                    effort="low", owner_role="Content / UX",
                    verification="Re-run the audit and confirm each page has at least one "
                                 "internal inbound link.",
                    expected_effect="Gives orphaned content a discovery path.",
                ),
            ))

    # --- site search ----------------------------------------------------------------------
    threshold = int(context.threshold("engagement.site_search_url_threshold"))
    if context.allows("GE.NAV.NO_SITE_SEARCH") and len(site_maps["urls"]) > threshold:
        has_input = bool(home.doc.select("input[type=search], form[role=search], "
                                         "input[name=q], input[name=s], input[name=query]"))
        has_action = any(n.get("potentialAction") for n in home.sd.of_type("WebSite"))
        if not has_input and not has_action:
            out.add(Finding(
                check_id="GE.NAV.NO_SITE_SEARCH",
                title="A large site offers no search",
                evidence=(f"The sitemap lists {len(site_maps['urls'])} URLs (threshold "
                          f"{threshold}), and the homepage has no search input and no "
                          f"WebSite SearchAction."),
                mechanism=("On a large site a visitor who arrived on the wrong page cannot "
                           "browse their way to the right one. Search is their only "
                           "self-rescue, and without it the visit ends."),
                confidence="high", signal_count=2, fact_class="secondary",
                scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                            example_urls=[home.url]),
                evidence_refs=[metric_ref(home.url_hash, "search_inputs", 0, 1)],
                suggested_action=SuggestedAction(
                    summary="Add site search reachable from every page's header.",
                    steps=["Add a search field to the global header.",
                           "Publish a WebSite SearchAction pointing at the results URL."],
                    effort="medium", owner_role="Product / Frontend engineering",
                    verification="Confirm a search input appears in the header of every "
                                 "template.",
                    expected_effect="Lets mis-landed visitors find what they came for.",
                ),
            ))

    # --- navigation only in JavaScript ------------------------------------------------------
    nav_links = [l for l, _t in _internal_links(context, home, main_only=False)
                 if l.in_nav]
    all_links = _internal_links(context, home, main_only=False)
    empty_mount = any(len(home.doc.visible_text(n)) < 200 for n in home.doc.mount_nodes())
    rendered = home.rendered_doc
    rendered_links = 0
    if rendered is not None:
        rendered_links = sum(1 for l in rendered.links() if not l.href.startswith("#"))
    if len(all_links) < 3 and (empty_mount or rendered_links >= len(all_links) + 5):
        out.add(Finding(
            check_id="GE.NAV.NAV_ONLY_IN_JS",
            title="Site navigation is absent from the HTML response",
            evidence=(f"The homepage's raw HTML contains {len(all_links)} internal link(s) "
                      f"({len(nav_links)} in navigation)"
                      + (f", while the rendered page contains {rendered_links}"
                         if rendered is not None else
                         ", and a framework mount node is present with an essentially "
                         "empty subtree")
                      + ". Navigation is therefore constructed by JavaScript."),
            mechanism=("Crawlers discover pages by following links in the response. "
                       "Navigation built in the browser leaves them with no path beyond "
                       "the homepage, and it also fails visitors on slow devices or with "
                       "scripts blocked."),
            confidence="high" if rendered is not None else "medium", signal_count=2,
            fact_class="primary",
            scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                        example_urls=[home.url]),
            evidence_refs=[metric_ref(home.url_hash, "internal_links_in_raw_html",
                                      len(all_links), 3)],
            suggested_action=SuggestedAction(
                summary="Server-render the primary navigation as plain <a href> links.",
                steps=["Render header and footer navigation in the server response.",
                       "Use real anchors with href, not click handlers on divs."],
                effort="medium", owner_role="Frontend engineering",
                verification="curl the homepage and confirm the navigation links are "
                             "present as <a href> elements.",
                expected_effect="Restores crawl paths and no-JavaScript navigation.",
            ),
        ))

    # --- excessive depth ------------------------------------------------------------------
    depth_limit = int(context.threshold("engagement.excessive_depth"))
    commercial = [p for p in pages if p.has_path_class("product", "pricing")
                  and p.entry.depth is not None]
    if not any(p.entry.depth is not None for p in pages):
        out.skip("GE.NAV.EXCESSIVE_DEPTH",
                 reason="click depth was not recorded for the crawled pages")
    elif commercial:
        deep = [p for p in commercial if p.entry.depth >= depth_limit]
        if deep and len(deep) * 2 >= len(commercial):
            out.add(Finding(
                check_id="GE.NAV.EXCESSIVE_DEPTH",
                title="Primary commercial pages sit many clicks from the homepage",
                evidence=(f"{analyzer.kn(len(deep), len(commercial))} sampled product and "
                          f"pricing pages were first reached at click depth "
                          f"{depth_limit} or more. Examples: "
                          f"{analyzer.summarise_urls([p.url for p in deep])}."),
                mechanism=("Every click between the homepage and a key page dilutes the "
                           "internal authority passed to it and lengthens the path a "
                           "visitor must find."),
                confidence="medium", signal_count=2, fact_class="secondary",
                scope=Scope(level="section", pages_affected=len(deep),
                            pages_sampled=len(commercial),
                            example_urls=[p.url for p in deep[:3]]),
                evidence_refs=[metric_ref(deep[0].url_hash, "click_depth",
                                          deep[0].entry.depth, depth_limit)],
                suggested_action=SuggestedAction(
                    summary="Link key commercial pages within two clicks of the homepage.",
                    steps=["Link top products and pricing from the homepage and main "
                           "navigation.", "Add category hubs that link directly to "
                                          "products."],
                    effort="low", owner_role="UX / Content",
                    verification="Re-run the audit and confirm key pages sit at depth 3 "
                                 "or less.",
                    expected_effect="Shortens paths to the pages that convert.",
                ),
            ))

    # --- broken internal links ---------------------------------------------------------------
    min_links = int(context.threshold("engagement.min_links_for_broken_check"))
    share_limit = float(context.threshold("engagement.broken_link_share"))
    host = context.site_host()
    checked = [e for e in context.ledger.entries()
               if e.referrer and e.purpose in ("page", "asset") and e.status
               and (n := urlnorm.try_normalise(e.url.split("#")[0])) is not None
               and host and urlnorm.same_site(n.host, host)]
    if len(checked) < min_links:
        out.skip("GE.NAV.BROKEN_INTERNAL_LINKS",
                 reason=f"only {len(checked)} internal link target(s) were requested, "
                        f"too few for a meaningful share")
    else:
        broken = [e for e in checked if e.status >= 400]
        share = len(broken) / len(checked)
        if broken and share > share_limit:
            out.add(Finding(
                check_id="GE.NAV.BROKEN_INTERNAL_LINKS",
                title="Internal links lead to error pages",
                evidence=(f"{analyzer.kn(len(broken), len(checked))} requested internal "
                          f"link targets ({share:.0%}, threshold {share_limit:.0%}) "
                          f"returned an error. Examples: "
                          + "; ".join(f"{e.url} -> {e.status} (linked from {e.referrer})"
                                      for e in broken[:3]) + "."),
                mechanism=("A broken link is a dead end at the moment a visitor chose to "
                           "go deeper, and it wastes crawl budget on errors."),
                confidence="high", signal_count=2, fact_class="secondary",
                scope=Scope(level="site" if share >= 0.3 else "section",
                            pages_affected=len(broken), pages_sampled=len(checked),
                            example_urls=[e.referrer for e in broken[:3]]),
                evidence_refs=[metric_ref(broken[0].entry_id, "internal_link_status",
                                          broken[0].status, 200)],
                suggested_action=SuggestedAction(
                    summary="Fix or redirect broken internal link targets.",
                    steps=["Update each link to the current URL.",
                           "301-redirect moved content from its old URL.",
                           "Add a scheduled internal link check."],
                    effort="low", owner_role="Content / Engineering",
                    verification="Request every internal link target and confirm none "
                                 "returns 4xx or 5xx.",
                    expected_effect="Removes dead ends from the site's own navigation.",
                ),
            ))


# ========================================================================================
# entry point
# ========================================================================================

def detect(context: Context, out: FindingsFile) -> None:
    pages = context.content_pages()
    brand = entity.infer_brand(pages, context.site_host())
    styles = _Styles(context)
    out.note_metric("pages_analyzed", len(pages))

    if pages and not any(styles.for_page(p).rules for p in pages):
        out.skip("GE.FRICTION.LOW_CONTRAST_DECLARED",
                 reason="no statically declared CSS was available, so no colour pair "
                        "could be resolved; contrast is never guessed")

    checks_to_run = build_page_checks(context, brand, styles)
    shells = [p for p in pages if _is_shell(context, p)]
    out.note_metric("client_rendered_shells", len(shells))
    if shells:
        for check in checks_to_run:
            if check.check_id not in _SERVED_AS_IS:
                out.skip(check.check_id,
                         reason=f"{len(shells)} page(s) are client-rendered shells whose "
                                f"raw response lacks the content a visitor sees; they "
                                f"were not judged here (the render gap is reported by "
                                f"render-extraction-audit)")
    analyzer.run_page_checks(context, out, checks_to_run)
    check_graph(context, out, shells)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return analyzer.run(
        SKILL_ID,
        "Gate GE (ENGAGE): the deep, context-free arrival -- above-the-fold "
        "orientation, content-blocking friction, navigation and context retention over "
        "the crawled link graph, conversion clarity and readability.",
        detect, argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
