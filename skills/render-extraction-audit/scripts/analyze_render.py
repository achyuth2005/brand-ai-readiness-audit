#!/usr/bin/env python3
"""Gate G1 -- READ. Does the page's text actually exist for a machine?

Pure function of the evidence ledger. No network I/O.

Mechanism
---------
The brief's Appendix C: "a page that looks complete to a person isn't always complete to
a machine ... some content is only assembled after a page loads, or lives in a form a
simple reader can't interpret." Many retrieval fetchers read the raw HTML response and
execute little or no JavaScript. If a fact only exists after hydration, it does not exist
for those fetchers -- and the page can be perfectly crawlable while contributing nothing
to an answer.

The headline detector works in **three tiers** and must not require a browser, because
the grading machine may not have one:

* **Tier 1, suspicion** -- structural signals from one fetch. Never above medium alone.
* **Tier 2, confirmation without a browser** -- the framework's own hydration payload is
  a ground-truth oracle for what the page *will* say once JavaScript runs. A
  natural-language span present in the payload and absent from the static text proves
  client-side rendering from a single static fetch. This is the key idea.
* **Tier 3, direct measurement** -- when Playwright is present, diff static against
  rendered text and classify which fact classes appear only after render.

Severity comes from **what** is missing, not how much. A site whose prices only exist
post-hydration is a different problem from one whose testimonial slider is
client-rendered, and conflating the two is what makes most implementations noisy.

See references/csr-detection.md and references/non-text-facts.md.
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

from auditkit import analyzer, dom as dom_mod, lexicons, textnorm  # noqa: E402
from auditkit.analyzer import Context, PageView  # noqa: E402
from auditkit.dom import selector_for  # noqa: E402
from auditkit.finding import (  # noqa: E402
    Finding, FindingsFile, Scope, SuggestedAction, dom_ref, jsonpath_ref, metric_ref,
)

SKILL_ID = "render-extraction-audit"

# Fact-class value patterns, compiled once.
_FACT_PATTERNS: Dict[str, List[re.Pattern]] = {
    fact: [re.compile(src, re.IGNORECASE) for src in sources]
    for fact, sources in lexicons.FACT_CLASS_VALUE_PATTERNS.items()
}

_ICON_HINT_RE = re.compile(
    r"(icon|sprite|logo|avatar|badge|bullet|arrow|chevron|spacer|pixel|divider|"
    r"separator|placeholder|loader|spinner)", re.IGNORECASE)

_DIMENSION_ALT_RE = re.compile(r"^\d+\s*[x×]\s*\d+$|^\d+(px|em|rem)?$")
_FILENAME_ALT_RE = re.compile(
    r"^[\w\-. ]+\.(jpg|jpeg|png|gif|webp|avif|svg|bmp|tiff)$", re.IGNORECASE)

_TRANSCRIPT_HINT_RE = re.compile(
    r"(transcript|captions?|subtitles?|full text|read the transcript)", re.IGNORECASE)


# ========================================================================================
# G1.RENDER.CSR_CONTENT_GAP
# ========================================================================================

def _suppressed_span(text: str) -> bool:
    """Guard G5: a delta made of consent, chat and ad text is not a content gap."""
    lowered = text.lower()
    return any(marker in lowered for marker in lexicons.RENDER_DELTA_SUPPRESSION)


def _classify_fact_classes(text: str) -> List[str]:
    """Which fact classes appear in ``text``. Drives severity escalation."""
    found: List[str] = []
    for fact, patterns in _FACT_PATTERNS.items():
        if any(p.search(text) for p in patterns):
            found.append(fact)
    return sorted(found)


def _tier1_signals(context: Context, page: PageView) -> Dict[str, Any]:
    """Structural suspicion signals. Cheap, single-fetch, never conclusive alone."""
    doc = page.doc
    thin_chars = int(context.threshold("render.thin_text_chars"))
    mount_chars = int(context.threshold("render.mount_node_text_chars"))
    ratio_floor = float(context.threshold("render.text_to_html_ratio"))
    script_floor = int(context.threshold("render.script_count"))
    script_bytes_floor = int(context.threshold("render.script_bytes"))

    main_text = doc.main_text()
    main_chars = len(main_text)
    ratio = doc.text_ratio()

    # S2 -- a mount node exists whose subtree is essentially empty. This is the signal
    # that separates a client-rendered page from a server-rendered one that merely uses
    # the same framework (rule R5).
    mount_empty = False
    mount_node = None
    for node in doc.mount_nodes():
        subtree_chars = len(doc.visible_text(node))
        if subtree_chars < mount_chars:
            mount_empty = True
            mount_node = node
            break

    noscript = doc.noscript_text().lower()
    s5 = any(phrase in noscript for phrase in
             ("enable javascript", "javascript is required", "javascript to run",
              "turn on javascript", "requires javascript", "doesn't work properly "
              "without javascript"))

    title_substantive = (
        textnorm.word_count(doc.title()) >= 3
        or textnorm.word_count(doc.og("description")) >= 8
        or textnorm.word_count(doc.meta("description")) >= 8
    )

    signals = {
        "S1_thin_main_text": main_chars < thin_chars,
        "S2_empty_mount_node": mount_empty,
        "S3_low_text_ratio": ratio < ratio_floor,
        "S4_script_heavy": (doc.script_count >= script_floor
                            or doc.script_bytes > script_bytes_floor),
        "S5_noscript_warning": s5,
        "S6_metadata_substantive_body_not": title_substantive and main_chars < thin_chars,
        "_main_chars": main_chars,
        "_thin_chars": thin_chars,
        "_ratio": ratio,
        "_ratio_floor": ratio_floor,
        "_script_count": doc.script_count,
        "_mount_node": mount_node,
        "_mount_chars": mount_chars,
    }
    # The composite condition from the detection spec: (S1 or S3) and S2 and (S4 or S5)
    # and S6. Requiring S2 and S6 together is what keeps correctly server-rendered
    # framework pages silent.
    signals["fires"] = bool(
        (signals["S1_thin_main_text"] or signals["S3_low_text_ratio"])
        and signals["S2_empty_mount_node"]
        and (signals["S4_script_heavy"] or signals["S5_noscript_warning"])
        and signals["S6_metadata_substantive_body_not"]
    )
    signals["count"] = sum(1 for k, v in signals.items()
                           if k.startswith("S") and v is True)
    return signals


def _tier2_blob_oracle(context: Context, page: PageView) -> Dict[str, Any]:
    """Confirm client-side rendering from the hydration payload. No browser needed."""
    doc = page.doc
    min_chars = int(context.threshold("render.blob_span_min_chars"))
    min_words = int(context.threshold("render.blob_span_min_words"))
    delta_floor = int(context.threshold("render.blob_delta_chars"))

    spans = doc.blob_text_spans(min_chars=min_chars, min_words=min_words)
    static_key = textnorm.comparison_key(doc.body_text())

    missing: List[Dict[str, Any]] = []
    for span in spans:
        # Guard G2: a span that IS present in the static text is proof of working SSR.
        if textnorm.comparison_key(span["text"]) in static_key:
            continue
        # Guard G5: consent, chat and ad text is not brand content.
        if _suppressed_span(span["text"]):
            continue
        missing.append(span)

    missing_chars = sum(len(s["text"]) for s in missing)
    combined = " ".join(s["text"] for s in missing)
    return {
        "spans_total": len(spans),
        "spans_missing": len(missing),
        "missing_chars": missing_chars,
        "delta_floor": delta_floor,
        "missing": missing,
        "fact_classes": _classify_fact_classes(combined),
        "confirmed": missing_chars > delta_floor,
    }


def _tier3_render_delta(context: Context, page: PageView) -> Optional[Dict[str, Any]]:
    """Direct measurement against the rendered DOM, when one was captured."""
    rendered = page.rendered_doc
    if rendered is None:
        return None
    ratio_floor = float(context.threshold("render.render_delta_ratio"))
    chars_floor = int(context.threshold("render.render_delta_chars"))

    static_text = page.doc.body_text()
    rendered_text = rendered.body_text()
    delta = dom_mod.sequence_delta(static_text, rendered_text)

    added = delta["added_text"]
    if _suppressed_span(added) and delta["added_chars"] < chars_floor * 2:
        # A delta that is mostly suppression-lexicon text is chrome, not content.
        delta["confirmed"] = False
        delta["suppressed"] = True
        delta["fact_classes"] = []
        return delta

    delta["suppressed"] = False
    delta["fact_classes"] = _classify_fact_classes(added)
    delta["confirmed"] = (delta["delta_ratio"] > ratio_floor
                          and delta["added_chars"] > chars_floor)
    delta["ratio_floor"] = ratio_floor
    delta["chars_floor"] = chars_floor
    return delta


def check_csr(context: Context, out: FindingsFile) -> None:
    pages = context.content_pages()
    if not pages:
        out.skip("G1.RENDER.CSR_CONTENT_GAP", reason="no content pages were fetched")
        return

    rendered_count = sum(1 for p in pages if p.rendered_doc is not None)
    if rendered_count == 0:
        # Rule R6: state the limitation rather than silently upgrading tier 1.
        out.skip(
            "G1.RENDER.CSR_CONTENT_GAP",
            reason=("tier 3 (direct browser measurement) did not run, so the render gap "
                    "was assessed from structural signals and hydration payloads only "
                    "(tiers 1-2). Tier 2 still yields high confidence where a framework "
                    "payload is present."),
            remedy="install Playwright and run `playwright install chromium` to enable "
                   "direct render measurement",
        )

    hits: List[Tuple[str, Any]] = []
    for page in pages:
        if page.truncated:
            # Guard G7: streaming server-rendered HTML genuinely looks thin mid-response.
            out.skip("G1.RENDER.CSR_CONTENT_GAP",
                     reason=f"the response body for {page.url} hit the size cap, so it "
                            f"was not judged for thinness")
            continue

        tier1 = _tier1_signals(context, page)
        tier2 = _tier2_blob_oracle(context, page)
        tier3 = _tier3_render_delta(context, page)

        # Tier 2 measures text a payload *carries*. Framework payloads routinely carry text
        # the page never shows -- other locales' navigation, unrendered variants, CMS
        # models -- so a payload delta alone cannot show the page *depends* on hydration
        # (rule R5). A thin static response (tier 1) or a browser-measured delta (tier 3)
        # must establish that; tier 2 then corroborates. Where the browser measured the
        # page, its measurement decides.
        if tier3 is not None:
            fires = confirmed = bool(tier3.get("confirmed"))
        else:
            fires = tier1["fires"]
            confirmed = fires and tier2["confirmed"]
        if not fires:
            if tier2["confirmed"]:
                out.note_metric(f"payload_text_not_rendered_chars:{page.url}",
                                tier2["missing_chars"])
            # A genuine near miss is a measured hydration delta just under its floor. A
            # merely short page is not one: short pages are normal, and turning them into
            # "advice" would generate recommendations from noise.
            floor = tier2["delta_floor"]
            if tier3 is None and tier2["missing_chars"] and \
                    floor * (1 - context.near_miss_margin()) <= tier2["missing_chars"] < floor:
                out.near_miss(
                    "G1.RENDER.CSR_CONTENT_GAP", "blob_text_missing_from_static_chars",
                    float(tier2["missing_chars"]), float(floor),
                    scope_key=page.cluster_id or page.url,
                    detail=(f"{page.url} ships {tier2['missing_chars']} characters of "
                            f"prose in its hydration payload that are absent from the "
                            f"static HTML, just under the {floor}-character confirmation "
                            f"floor. Some content on this template is already rendered "
                            f"client-side."),
                    examples=[page.url],
                )
            continue

        hits.append((page.url, {"tier1": tier1, "tier2": tier2, "tier3": tier3,
                                "confirmed": confirmed}))

    if not hits:
        out.note_posture("static_html_carries_main_content", True)
        return

    total = len(pages)
    for group in analyzer.group_by_template(context, hits):
        _emit_csr(context, out, group, total)


def _emit_csr(context: Context, out: FindingsFile, group: Dict[str, Any],
              total_pages: int) -> None:
    payloads = group["payloads"]
    example_url = group["example_urls"][0]
    page = next((p for p in context.pages if p.url == example_url), None)
    if page is None:
        return
    payload = next((p for u, p in zip(group["urls"], payloads) if u == example_url),
                   payloads[0])
    tier1, tier2, tier3 = payload["tier1"], payload["tier2"], payload["tier3"]

    confirmed_pages = sum(1 for p in payloads if p["confirmed"])
    fact_classes = sorted({f for p in payloads
                           for f in (p["tier2"]["fact_classes"]
                                     + ((p["tier3"] or {}).get("fact_classes") or []))})
    primary_facts = [f for f in fact_classes
                     if f in lexicons.PRIMARY_FACT_CLASSES
                     or f in context.site_profile.primary_fact_classes()]

    # Severity is driven by WHAT is missing, not how much.
    fact_class = "primary" if primary_facts else (
        "secondary" if fact_classes else "enrichment")

    tier_used = []
    signal_count = 0
    refs = [metric_ref(page.url_hash, "main_static_text_chars",
                       tier1["_main_chars"], tier1["_thin_chars"])]

    if tier1["fires"]:
        tier_used.append("tier 1 (structural signals)")
        signal_count += 2
    if tier2["confirmed"]:
        tier_used.append("tier 2 (hydration-payload oracle)")
        signal_count += 2
        first = tier2["missing"][0]
        refs.append(jsonpath_ref(page.url_hash, first["path"],
                                 textnorm.truncate(first["text"], 200)))
        refs.append(metric_ref(page.url_hash, "blob_text_missing_from_static_chars",
                               tier2["missing_chars"], tier2["delta_floor"]))
    if tier3 and tier3.get("confirmed"):
        tier_used.append("tier 3 (direct browser measurement)")
        signal_count += 2
        refs.append(metric_ref(page.url_hash, "render_delta_ratio",
                               round(tier3["delta_ratio"], 3), tier3["ratio_floor"]))
    refs.append(dom_ref(page.url_hash, page.doc.main_selector,
                        textnorm.truncate(page.doc.main_text(), 200)))

    confidence = "high" if (tier2["confirmed"] or (tier3 and tier3.get("confirmed"))) \
        else "medium"

    # Evidence describes observations, never inferences dressed as observations.
    parts = [
        f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled pages"
        + (f" in template cluster {group['template_id']}"
           if group.get("template_id") else "")
        + f" return a raw HTML response whose main region contains "
          f"{tier1['_main_chars']} characters of visible text "
          f"(threshold {tier1['_thin_chars']}), with a visible-text-to-HTML ratio of "
          f"{tier1['_ratio']:.4f} across {tier1['_script_count']} script elements."
    ]
    if tier1["_mount_node"] is not None:
        parts.append(
            f"A framework mount node `{dom_mod.node_identity(tier1['_mount_node'])}` is "
            f"present and its subtree holds under {tier1['_mount_chars']} characters of "
            f"text.")
    if tier2["confirmed"]:
        first = tier2["missing"][0]
        parts.append(
            f"{tier2['spans_missing']} natural-language span(s) totalling "
            f"{tier2['missing_chars']} characters are present in the page's own "
            f"hydration payload but absent from the static HTML. Example, at JSON path "
            f"{first['path']} in {first['blob']}: "
            f"\"{textnorm.truncate(first['text'], 160)}\". "
            f"This is measured from the single static response, with no browser "
            f"involved.")
    if tier3 and tier3.get("confirmed"):
        parts.append(
            f"Direct browser measurement confirms it: rendering adds "
            f"{tier3['added_tokens']} tokens ({tier3['added_chars']} characters), a "
            f"delta ratio of {tier3['delta_ratio']:.2f} against a "
            f"{tier3['ratio_floor']} threshold.")
    if fact_classes:
        parts.append(
            f"The text missing from the response carries these fact classes: "
            f"{', '.join(fact_classes)}"
            + (f" -- of which {', '.join(primary_facts)} "
               f"{'is' if len(primary_facts) == 1 else 'are'} primary for the detected "
               f"{context.site_profile.detected_type} profile."
               if primary_facts else "."))
    else:
        parts.append(
            "No primary commercial or definitional fact class was identified in the "
            "missing text, so severity is scoped to secondary content rather than "
            "escalated.")
    parts.append(f"Confirmed by: {', '.join(tier_used)}.")

    title = ("Page content is assembled client-side and absent from the HTML response"
             if not primary_facts else
             f"{(group.get('path_class') or 'page').replace('_', ' ').capitalize()} content "
             f"including {primary_facts[0]} is assembled client-side and absent from the "
             f"HTML response")

    out.add(Finding(
        check_id="G1.RENDER.CSR_CONTENT_GAP",
        title=title,
        evidence=" ".join(parts),
        mechanism=(
            "Many assistant retrieval fetchers take the raw HTML response and execute "
            "little or no JavaScript. A fact that only exists after hydration does not "
            "exist for those fetchers, so the page can be fully crawlable, fully "
            "permitted, and still contribute nothing to an answer. The page looks "
            "complete in a browser, which is why this survives review: the only way to "
            "see it is to read the response body."
        ),
        confidence=confidence, signal_count=max(signal_count, 2),
        fact_class=fact_class,
        scope=analyzer.scope_from_group(group, total_pages),
        evidence_refs=refs,
        suggested_action=SuggestedAction(
            summary=(f"Server-render or pre-render the primary content of this page "
                     f"class so it appears in the initial HTML response"
                     + (f", starting with {', '.join(primary_facts)}."
                        if primary_facts else ".")),
            steps=[
                (f"Move the data fetch for this template to the server so "
                 f"{', '.join(primary_facts) if primary_facts else 'the main content'} "
                 f"renders into the HTML response (getServerSideProps / "
                 f"generateStaticParams / loader / equivalent for your framework)."),
                "If a render change is not near-term, ship the same facts in JSON-LD "
                "server-side as an interim carrier: structured data is machine-readable "
                "regardless of hydration, so it restores the facts even while the DOM "
                "stays client-rendered. Treat this as a stopgap, not the fix.",
                "Where the page class is genuinely static, pre-render or "
                "static-generate it at build time instead.",
                ("Add a build-time assertion that a known value from this template "
                 "appears in the server response body, so the gap cannot silently "
                 "return."),
            ],
            effort="medium", owner_role="Frontend engineering",
            verification=(
                "Fetch the URL with JavaScript disabled, or run "
                "`curl -s <url> | grep -F '<a known value>'`. The fix is confirmed only "
                "when the fact appears in the raw response body -- not when it appears "
                "in the browser."
            ),
            expected_effect=(
                f"Makes the primary content of {group['pages_affected']} sampled page(s) "
                f"and every other page on this template available to retrieval "
                f"fetchers, and unblocks the structured-data and answerability checks "
                f"that are currently masked by it."
            ),
        ),
        notes=([f"{confirmed_pages} of {len(payloads)} affected pages reached tier-2 or "
                f"tier-3 confirmation; the remainder matched tier-1 structural signals "
                f"only."] if confirmed_pages < len(payloads) else []),
    ))


# ========================================================================================
# Related G1 render checks
# ========================================================================================

def check_interaction_required(context: Context, out: FindingsFile) -> None:
    """G1.RENDER.BODY_REQUIRES_INTERACTION -- content only inside collapsed panels."""
    floor = int(context.threshold("render.interaction_hidden_panel_chars"))
    hits: List[Tuple[str, Any]] = []

    for page in context.content_pages():
        doc = page.doc
        main = doc.main_node()
        if main is None:
            continue
        main_chars = len(doc.main_text())

        hidden_panels = []
        for node in main.find_all(["details", "div", "section"]):
            try:
                is_details_closed = node.name == "details" and not node.has_attr("open")
                role = (node.get("role") or "").lower()
                aria_hidden = (node.get("aria-hidden") or "").lower() == "true"
                is_tabpanel = role == "tabpanel"
                hidden_attr = node.has_attr("hidden")
            except (AttributeError, TypeError):
                continue
            if not (is_details_closed or (is_tabpanel and (hidden_attr or aria_hidden))
                    or (hidden_attr and dom_mod.looks_hidden_by_class(node))):
                continue
            # Text inside a closed <details> IS in the response, so it is readable. The
            # defect is a panel that is empty in the response and populated on click.
            panel_chars = len(doc.visible_text(node))
            if panel_chars < 40:
                hidden_panels.append((node, panel_chars))

        if not hidden_panels:
            continue
        # Rule R1: needs the panels to be empty AND the visible content to be thin.
        if main_chars >= floor:
            continue
        hits.append((page.url, {
            "panels": len(hidden_panels), "main_chars": main_chars,
            "selector": selector_for(hidden_panels[0][0]),
            "identity": dom_mod.node_identity(hidden_panels[0][0]),
        }))

    if not hits:
        return
    total = len(context.content_pages())
    for group in analyzer.group_by_template(context, hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        out.add(Finding(
            check_id="G1.RENDER.BODY_REQUIRES_INTERACTION",
            title="Primary content sits in panels that are empty until a click populates "
                  "them",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"pages contain {payload['panels']} collapsed panel(s) "
                f"(example `{payload['identity']}`) whose subtree holds under 40 "
                f"characters in the raw response, while the page's whole main region "
                f"carries only {payload['main_chars']} characters "
                f"(threshold {floor}). Panels whose text IS present in the response were "
                f"not counted, because that content is readable whether or not the panel "
                f"is open."
            ),
            mechanism=(
                "A consumer reads the response body and does not click. Content that is "
                "fetched or injected on interaction is absent at read time, so an "
                "accordion or tab set that lazily loads its panels hides exactly the "
                "detail a question would be answered from."
            ),
            confidence="medium", signal_count=2,
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                dom_ref(page.url_hash, payload["selector"], "empty collapsed panel"),
                metric_ref(page.url_hash, "main_region_chars", payload["main_chars"],
                           floor),
            ],
            suggested_action=SuggestedAction(
                summary="Ship panel content in the HTML response and let the panel "
                        "control only its visibility.",
                steps=[
                    "Render every panel's content into the response and collapse it with "
                    "CSS or the details element, rather than fetching it on click.",
                    "Prefer <details>/<summary>, whose content is in the document even "
                    "when closed.",
                    "If a panel is genuinely expensive, render a substantive summary in "
                    "the response and load only the remainder on demand.",
                ],
                effort="medium", owner_role="Frontend engineering",
                verification="Fetch the page and confirm the panel text is present in the "
                             "raw HTML with JavaScript disabled.",
                expected_effect="Makes the detail inside collapsed panels readable and "
                                "quotable.",
            ),
        ))


def check_encoding(context: Context, out: FindingsFile) -> None:
    """G1.ENCODING.UNDECLARED_OR_WRONG."""
    hits: List[Tuple[str, Any]] = []
    for page in context.content_pages():
        declared_meta = page.doc.charset_declared()
        declared_header = (page.entry.declared_encoding or "").lower()
        actual = (page.entry.encoding or "").lower()
        text = page.doc.body_text()
        # Replacement characters are direct evidence that decoding went wrong.
        mojibake = text.count("�")
        non_ascii = sum(1 for c in text[:20000] if ord(c) > 127)

        if not declared_meta and not declared_header and non_ascii > 20:
            hits.append((page.url, {
                "kind": "undeclared", "actual": actual, "mojibake": mojibake,
                "non_ascii": non_ascii,
            }))
        elif declared_meta and actual and declared_meta not in (actual, "utf8") \
                and not (declared_meta.replace("-", "") == actual.replace("-", "")):
            hits.append((page.url, {
                "kind": "mismatch", "declared": declared_meta, "actual": actual,
                "mojibake": mojibake, "non_ascii": non_ascii,
            }))
        elif mojibake > 3:
            hits.append((page.url, {
                "kind": "mojibake", "declared": declared_meta or declared_header,
                "actual": actual, "mojibake": mojibake, "non_ascii": non_ascii,
            }))

    if not hits:
        return
    total = len(context.content_pages())
    for group in analyzer.group_by_template(context, hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        if payload["kind"] == "undeclared":
            detail = (f"No charset is declared in a meta tag or the Content-Type header, "
                      f"while the page contains {payload['non_ascii']} non-ASCII "
                      f"characters that a consumer must therefore guess the encoding of.")
            summary = "Declare the character encoding explicitly."
        elif payload["kind"] == "mismatch":
            detail = (f"The page declares charset `{payload['declared']}` while the bytes "
                      f"decode as `{payload['actual']}`.")
            summary = "Correct the declared charset so it matches the bytes served."
        else:
            detail = (f"The decoded text contains {payload['mojibake']} Unicode "
                      f"replacement characters, which means some bytes could not be "
                      f"decoded under the declared encoding "
                      f"(`{payload['declared'] or 'none'}`).")
            summary = "Serve the page as UTF-8 and declare it."

        out.add(Finding(
            check_id="G1.ENCODING.UNDECLARED_OR_WRONG",
            title="Character encoding is undeclared or contradicts the bytes served",
            evidence=(f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} "
                      f"sampled pages are affected. {detail}"),
            mechanism=(
                "An extractor that decodes with the wrong encoding produces corrupted "
                "text. Corrupted text does not match a query, cannot be quoted, and can "
                "corrupt a brand or product name specifically -- the one string that most "
                "needs to be exact."
            ),
            confidence="high", signal_count=2,
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                metric_ref(page.url_hash, "unicode_replacement_characters",
                           payload["mojibake"], 0),
                metric_ref(page.url_hash, "non_ascii_characters",
                           payload["non_ascii"], 0),
            ],
            suggested_action=SuggestedAction(
                summary=summary,
                steps=[
                    "Serve every HTML response with `Content-Type: text/html; "
                    "charset=utf-8`.",
                    "Add `<meta charset=\"utf-8\">` as the first element in <head>, "
                    "within the first 1024 bytes where consumers look for it.",
                    "Confirm the database, template layer and response encoding all "
                    "agree on UTF-8, since a mismatch anywhere in the chain produces "
                    "this symptom.",
                ],
                effort="low", owner_role="Backend engineering",
                verification="Fetch the page and confirm the Content-Type header declares "
                             "utf-8 and that accented or non-Latin characters render "
                             "correctly in the raw bytes.",
                expected_effect="Makes the page's text decode correctly for every "
                                "consumer.",
            ),
        ))


def check_text_structure(context: Context, out: FindingsFile) -> None:
    """G1.TEXT.BOILERPLATE_DOMINANT and G1.TEXT.MAIN_LANDMARK_MISSING."""
    ratio_floor = float(context.threshold("render.boilerplate_dominance_ratio"))
    min_main = int(context.threshold("render.boilerplate_min_main_words"))
    index_share = float(context.threshold("answerability.index_page_link_text_share"))
    boilerplate_hits: List[Tuple[str, Any]] = []
    landmark_hits: List[Tuple[str, Any]] = []

    for page in context.content_pages():
        if page.truncated:
            continue
        doc = page.doc
        main_words = textnorm.word_count(doc.main_text())
        chrome_words = textnorm.word_count(doc.boilerplate_text())

        cluster = context.cluster_of(page)
        median_words = cluster.median("main_words") if cluster else 0.0

        # A near-empty main region is a render or thin-content problem, and an index page
        # made of links is meant to route; neither is content outweighed by chrome.
        main_chars = len(doc.main_text())
        linked = sum(len(link.text) for link in doc.links() if link.in_main)
        is_index = bool(main_chars) and linked / main_chars >= index_share
        if chrome_words and main_words >= min_main and not is_index \
                and chrome_words / main_words >= ratio_floor \
                and main_words < max(120, median_words * 0.6):
            boilerplate_hits.append((page.url, {
                "main_words": main_words, "chrome_words": chrome_words,
                "median": median_words,
            }))

        if not doc.has_main_landmark:
            landmark_hits.append((page.url, {"main_words": main_words}))

    total = len(context.content_pages())

    for group in analyzer.group_by_template(context, boilerplate_hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        out.add(Finding(
            check_id="G1.TEXT.BOILERPLATE_DOMINANT",
            title="Navigation and footer text outweighs the page's own content",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"pages carry {payload['main_words']} words in the main region against "
                f"{payload['chrome_words']} words of header, navigation, sidebar and "
                f"footer text"
                + (f", with a template median of {payload['median']:.0f} main-region "
                   f"words." if payload["median"] else ".")
            ),
            mechanism=(
                "Extractors that cannot identify the content region fall back to the "
                "whole document. When chrome outweighs content, the extracted passage is "
                "mostly navigation, so the page is represented to a consumer by its menu "
                "rather than by what it says."
            ),
            confidence="medium", signal_count=2,
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                metric_ref(page.url_hash, "main_region_words", payload["main_words"],
                           payload["chrome_words"]),
                metric_ref(page.url_hash, "boilerplate_words", payload["chrome_words"],
                           None),
            ],
            suggested_action=SuggestedAction(
                summary="Add substantive content to this page class, or reduce the chrome "
                        "rendered into it.",
                steps=[
                    "Decide whether this page class should exist as an indexable page at "
                    "all. If it is a thin index or a filter result, mark it noindex and "
                    "let the pages it links to carry the content.",
                    "If it should be substantive, add real content: a description, the "
                    "facts a visitor arrived for, and the entity name in body text.",
                    "Wrap the content region in <main> so extractors can separate it "
                    "from navigation.",
                ],
                effort="medium", owner_role="Content / Frontend engineering",
                verification="Re-measure: main-region word count should exceed the "
                             "combined header, nav and footer word count.",
                expected_effect="Makes extraction return the page's content rather than "
                                "its navigation.",
            ),
        ))

    if landmark_hits and len(landmark_hits) >= context.min_sample():
        for group in analyzer.group_by_template(context, landmark_hits):
            page = next((p for p in context.pages if p.url == group["example_urls"][0]),
                        None)
            if page is None:
                continue
            out.add(Finding(
                check_id="G1.TEXT.MAIN_LANDMARK_MISSING",
                title="No main content landmark, so extractors must guess the content "
                      "region",
                evidence=(
                    f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} "
                    f"sampled pages contain no <main>, <article> or role=\"main\" "
                    f"element. The content region had to be inferred heuristically for "
                    f"this audit's own analysis, which is exactly the guess every other "
                    f"extractor has to make."
                ),
                mechanism=(
                    "A main landmark tells an extractor which part of the document is the "
                    "content. Without one, boilerplate removal is heuristic and varies "
                    "between consumers, so what gets indexed and quoted from the page is "
                    "less predictable than it needs to be."
                ),
                confidence="high", signal_count=1, fact_class="enrichment",
                scope=analyzer.scope_from_group(group, total),
                evidence_refs=[
                    metric_ref(page.url_hash, "main_landmark_elements", 0, 1),
                    dom_ref(page.url_hash, "body",
                            textnorm.truncate(page.doc.body_text(), 150)),
                ],
                suggested_action=SuggestedAction(
                    summary="Wrap the content region of each template in a <main> element.",
                    steps=[
                        "Add exactly one <main> element per page, wrapping the content "
                        "region and excluding header, nav, sidebar and footer.",
                        "Use <article> inside it for self-contained items such as posts "
                        "or products.",
                        "Add a skip link targeting the main element, which also helps "
                        "keyboard users.",
                    ],
                    effort="trivial", owner_role="Frontend engineering",
                    verification="Confirm each template renders exactly one <main> "
                                 "element containing the page's content.",
                    expected_effect="Makes content-region detection deterministic for "
                                    "every extractor.",
                ),
            ))
            break  # one landmark finding is enough; it is a template-level omission


# ========================================================================================
# G1.NONTEXT.* -- facts locked in non-text carriers
# ========================================================================================

def _fact_class_of_label(text: str) -> str:
    lowered = textnorm.normalise_text(text).lower()
    best, best_len = "", 0
    for fact, labels in lexicons.FACT_CLASS_LABELS.items():
        for label in labels:
            if label in lowered and len(label) > best_len:
                best, best_len = fact, len(label)
    return best


def _fact_present_in_text(fact: str, text: str) -> bool:
    patterns = _FACT_PATTERNS.get(fact, [])
    return any(p.search(text) for p in patterns)


def check_nontext_fact_regions(context: Context, out: FindingsFile) -> None:
    """G1.NONTEXT.FACT_LOCKED_IN_MEDIA -- structural detection, deliberately no OCR.

    We never assert that an image *contains* a price: we have not read it. The evidence is
    phrased as absence -- a section labelled with a fact class, carrying almost no text,
    one or more content-sized images, and no table or list, with that fact class appearing
    nowhere in the page's text or structured data. That is the anti-hallucination
    discipline in miniature: describe the observation, never the inference dressed as an
    observation.
    """
    max_words = int(context.threshold("non_text.fact_region_max_words"))
    min_dimension = int(context.threshold("non_text.content_image_min_dimension"))
    hits: List[Tuple[str, Any]] = []

    for page in context.content_pages():
        doc = page.doc
        page_text = doc.body_text()
        sd_text = " ".join(
            str(v) for node in page.sd.nodes for v in node.props.values()
            if isinstance(v, (str, int, float))
        )
        haystack = page_text + " " + sd_text

        for section in doc.sections(min_level=2, max_level=3):
            fact = _fact_class_of_label(section.heading)
            if not fact:
                continue
            words = section.word_count
            if words >= max_words:
                continue
            node = section.node
            if node is None:
                continue

            # Collect the section subtree's media and structure.
            images: List[Any] = []
            iframes = 0
            pdf_links = 0
            tables = 0
            lists_ = 0
            svg_paths = 0
            for sibling in node.next_siblings:
                name = getattr(sibling, "name", None)
                if name in dom_mod.HEADING_TAGS:
                    break
                if name is None:
                    continue
                images.extend(sibling.find_all("img") if name != "img" else [sibling])
                iframes += len(sibling.find_all("iframe"))
                tables += len(sibling.find_all(["table", "dl"]))
                lists_ += len(sibling.find_all(["ul", "ol"]))
                for anchor in sibling.find_all("a"):
                    if ".pdf" in (anchor.get("href") or "").lower():
                        pdf_links += 1
                for svg in sibling.find_all("svg"):
                    if svg.find("title") is None and svg.find("text") is None:
                        svg_paths += 1

            content_images = []
            for img in images:
                src = (img.get("src") or "")
                if _ICON_HINT_RE.search(src) or _ICON_HINT_RE.search(
                        " ".join(img.get("class") or [])):
                    continue
                width = _as_int(img.get("width"))
                height = _as_int(img.get("height"))
                if width and height and (width < min_dimension
                                         and height < min_dimension):
                    continue
                content_images.append(img)

            has_media = bool(content_images) or iframes or pdf_links or svg_paths
            has_structure = tables or lists_
            # Belt-and-braces markup is good practice: if the fact IS in the text or the
            # structured data, an image duplicating it is not a defect.
            if _fact_present_in_text(fact, haystack):
                continue
            if not has_media or has_structure:
                continue

            strengtheners: List[str] = []
            for img in content_images:
                blob = f"{img.get('src') or ''} {img.get('alt') or ''}".lower()
                if any(label.replace(" ", "") in blob.replace(" ", "")
                       or label in blob
                       for label in lexicons.FACT_CLASS_LABELS[fact]):
                    strengtheners.append(
                        f"an image whose filename or alt text names the fact class "
                        f"(`{(img.get('src') or img.get('alt') or '')[:60]}`)")
                    break
            for img in content_images:
                width, height = _as_int(img.get("width")), _as_int(img.get("height"))
                if width and height and (width >= min_dimension
                                         and height >= min_dimension):
                    strengtheners.append(
                        f"a content-sized image ({width}x{height})")
                    break
            if pdf_links:
                strengtheners.append(f"{pdf_links} PDF link(s) in the section")
            if iframes:
                strengtheners.append(f"{iframes} iframe(s) in the section")

            hits.append((page.url, {
                "fact": fact, "heading": section.heading, "words": words,
                "images": len(content_images), "iframes": iframes,
                "pdf_links": pdf_links, "svg_paths": svg_paths,
                "selector": section.selector,
                "strengtheners": strengtheners,
                "example_src": ((content_images[0].get("src") or "")[:120]
                                if content_images else ""),
            }))

    if not hits:
        return
    total = len(context.content_pages())
    for group in analyzer.group_by_template(context, hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        fact = payload["fact"]
        is_primary = (fact in context.site_profile.primary_fact_classes()
                      or fact in lexicons.PRIMARY_FACT_CLASSES)
        confidence = "high" if len(payload["strengtheners"]) >= 2 else "medium"

        carriers = []
        if payload["images"]:
            carriers.append(f"{payload['images']} content-sized image(s)")
        if payload["iframes"]:
            carriers.append(f"{payload['iframes']} iframe(s)")
        if payload["pdf_links"]:
            carriers.append(f"{payload['pdf_links']} PDF link(s)")
        if payload["svg_paths"]:
            carriers.append(f"{payload['svg_paths']} SVG(s) with no title or text element")

        out.add(Finding(
            check_id="G1.NONTEXT.FACT_LOCKED_IN_MEDIA",
            title=f"The section covering {fact.replace('_', ' ')} carries almost no text",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"pages contain a section headed \"{payload['heading']}\" that carries "
                f"{payload['words']} words of text (threshold {max_words}), contains "
                f"{' and '.join(carriers)}, and contains no table, definition list or "
                f"list. No value matching the {fact.replace('_', ' ')} fact class appears "
                f"anywhere in the page's visible text or its structured data. "
                + (f"Strengthened by: {'; '.join(payload['strengtheners'])}. "
                   if payload["strengtheners"] else "")
                + "This audit does not read images, so it makes no claim about what the "
                  "image contains -- only that the fact is not available as text "
                  "anywhere on the page."
            ),
            mechanism=(
                "A fact rendered inside an image, an embedded viewer or a linked document "
                "is invisible to every text extractor, however well the page is crawled. "
                "The more explicitly and unambiguously a fact is stated in readable text, "
                "the more likely a consumer extracts it correctly; a fact that exists "
                "only as pixels is simply missed."
            ),
            confidence=confidence, signal_count=2 + len(payload["strengtheners"]),
            fact_class="primary" if is_primary else "secondary",
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                dom_ref(page.url_hash, payload["selector"], payload["heading"]),
                metric_ref(page.url_hash, f"{fact}_section_words", payload["words"],
                           max_words),
                metric_ref(page.url_hash, f"{fact}_values_in_page_text", 0, 1),
            ],
            suggested_action=SuggestedAction(
                summary=(f"Publish the {fact.replace('_', ' ')} information as HTML text "
                         f"-- a table or definition list -- alongside any image."),
                steps=[
                    (f"Render the {fact.replace('_', ' ')} values as a real HTML "
                     f"<table> or <dl>, with the labels and values as text. Keep the "
                     f"image if it is useful for design; the point is to add the text, "
                     f"not remove the picture."),
                    "Give the image descriptive alt text summarising what it shows.",
                    (f"Mirror the values into structured data where a schema.org property "
                     f"exists for them, so they are available in typed form as well."),
                    "If the values live only in a PDF or an embedded viewer, publish them "
                    "as an HTML page too and link the document as a supplement.",
                ],
                effort="low" if payload["images"] else "medium",
                owner_role="Content / Frontend engineering",
                verification=(
                    f"Fetch the page and grep the raw HTML for a known "
                    f"{fact.replace('_', ' ')} value. It should appear as text in the "
                    f"response body."
                ),
                expected_effect=(f"Makes the {fact.replace('_', ' ')} facts extractable "
                                 f"and quotable rather than locked in media."),
            ),
        ))


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip().replace("px", ""))
    except (TypeError, ValueError):
        return None


def check_alt_text(context: Context, out: FindingsFile) -> None:
    """G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES and ALT_NON_DESCRIPTIVE.

    Decorative images with ``alt=""`` are the *correct* pattern and are never flagged.
    That single guard is what separates a useful accessibility signal from the noise a
    naive image audit produces.
    """
    share_floor = float(context.threshold("non_text.alt_missing_share"))
    min_images = int(context.threshold("non_text.min_images_for_alt_check"))
    missing_hits: List[Tuple[str, Any]] = []
    weak_hits: List[Tuple[str, Any]] = []

    for page in context.content_pages():
        images = [i for i in page.doc.images() if i.in_main]
        informative = [i for i in images if not i.is_decorative]
        if len(informative) < min_images:
            continue

        absent = [i for i in informative if i.alt is None]
        weak = []
        for image in informative:
            if image.alt is None:
                continue
            alt = image.alt.strip()
            if not alt:
                continue
            lowered = alt.lower()
            if (lowered in lexicons.PLACEHOLDER_ALT_VALUES
                    or _FILENAME_ALT_RE.match(alt)
                    or _DIMENSION_ALT_RE.match(lowered)
                    or len(alt) <= 2):
                weak.append(image)

        share = len(absent) / len(informative)
        if share > share_floor:
            missing_hits.append((page.url, {
                "absent": len(absent), "informative": len(informative),
                "share": share, "selector": absent[0].selector if absent else "",
            }))
        if weak:
            weak_hits.append((page.url, {
                "weak": len(weak), "informative": len(informative),
                "examples": [i.alt for i in weak[:3]],
                "selector": weak[0].selector,
            }))

    total = len(context.content_pages())

    for group in analyzer.group_by_template(context, missing_hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        out.add(Finding(
            check_id="G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES",
            title="Informative images in the main content region have no alt attribute",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"pages have {payload['absent']} of {payload['informative']} "
                f"main-region informative images with no alt attribute at all "
                f"({payload['share']:.0%}, threshold {share_floor:.0%}). Images marked "
                f"decorative with alt=\"\" or role=\"presentation\" were excluded, "
                f"because that is the correct pattern for decoration and flagging it "
                f"would be wrong."
            ),
            mechanism=(
                "Alt text is the only textual representation of an image's content. "
                "Without it the image contributes nothing to what a consumer can extract "
                "from the page, and a screen-reader user is told nothing at all. An "
                "absent attribute is different from an empty one: empty declares "
                "'decorative', absent declares nothing."
            ),
            confidence="high", signal_count=2,
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                metric_ref(page.url_hash, "informative_images_without_alt",
                           payload["absent"], 0),
                metric_ref(page.url_hash, "alt_missing_share",
                           round(payload["share"], 3), share_floor),
            ] + ([dom_ref(page.url_hash, payload["selector"], "image with no alt "
                          "attribute")] if payload["selector"] else []),
            suggested_action=SuggestedAction(
                summary="Add descriptive alt text to informative images and alt=\"\" to "
                        "decorative ones.",
                steps=[
                    "For each informative image, write alt text stating what the image "
                    "communicates, not what it looks like. If the image carries a fact, "
                    "put the fact in the alt text.",
                    "For purely decorative images, set alt=\"\" explicitly so consumers "
                    "and screen readers skip them deliberately.",
                    "Make the alt attribute required in the CMS field or component props "
                    "so new images cannot ship without a decision.",
                ],
                effort="low", owner_role="Content / Frontend engineering",
                verification="Re-run this audit: the share of main-region informative "
                             "images without an alt attribute should be zero.",
                expected_effect="Adds a textual representation for image content that is "
                                "currently unreadable.",
            ),
        ))

    for group in analyzer.group_by_template(context, weak_hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        out.add(Finding(
            check_id="G1.NONTEXT.ALT_NON_DESCRIPTIVE",
            title="Alt text is a filename or placeholder rather than a description",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"pages carry {payload['weak']} image(s) whose alt text describes "
                f"nothing. Examples: "
                f"{', '.join(repr(a) for a in payload['examples'])}."
            ),
            mechanism=(
                "Alt text of 'image' or a filename occupies the attribute without "
                "conveying anything, so the image is effectively unlabelled while "
                "appearing compliant to an automated check."
            ),
            confidence="high", signal_count=1, fact_class="enrichment",
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                dom_ref(page.url_hash, payload["selector"],
                        f"alt={payload['examples'][0]!r}"),
                metric_ref(page.url_hash, "non_descriptive_alt_count",
                           payload["weak"], 0),
            ],
            suggested_action=SuggestedAction(
                summary="Replace placeholder alt text with a description of what the "
                        "image conveys.",
                steps=[
                    "Rewrite each alt value to state the image's content or purpose.",
                    "Where the image is decorative, use alt=\"\" instead of a "
                    "placeholder word.",
                    "Add a lint rule rejecting alt values that match the filename or a "
                    "placeholder word list.",
                ],
                effort="low", owner_role="Content",
                verification="Confirm no alt value equals a filename, a dimension string "
                             "or a placeholder word.",
                expected_effect="Turns existing alt attributes into usable text.",
            ),
        ))


def check_other_carriers(context: Context, out: FindingsFile) -> None:
    """SVG_TEXT_AS_PATHS, PDF_ONLY_FACT, IFRAME_LOCKED_CONTENT,
    VIDEO_WITHOUT_TRANSCRIPT."""
    total = len(context.content_pages())

    iframe_hits: List[Tuple[str, Any]] = []
    video_hits: List[Tuple[str, Any]] = []
    pdf_hits: List[Tuple[str, Any]] = []
    svg_hits: List[Tuple[str, Any]] = []

    site_host = context.site_host()

    for page in context.content_pages():
        doc = page.doc
        main = doc.main_node()
        main_chars = len(doc.main_text())

        # --- iframe-locked content ------------------------------------------------------
        for frame in doc.iframes():
            src = (frame.get("src") or frame.get("data-src") or "").strip()
            if not src or src.startswith(("about:", "javascript:")):
                continue
            if main is None or not dom_mod._is_within(frame, main):
                continue
            from auditkit import urlnorm
            target = urlnorm.try_normalise(src, base=page.entry.final_url or page.url)
            cross_origin = bool(target and site_host
                                and not urlnorm.same_site(target.host, site_host))
            # Rule R1: cross-origin AND the host page has little text of its own.
            if cross_origin and main_chars < 400:
                iframe_hits.append((page.url, {
                    "src": src[:120], "main_chars": main_chars,
                    "selector": selector_for(frame),
                }))
                break

        # --- video/audio without transcript ---------------------------------------------
        for media in doc.media():
            has_track = bool(media.find_all("track"))
            nearby = doc.visible_text(media.parent) if media.parent else ""
            has_transcript_link = bool(_TRANSCRIPT_HINT_RE.search(nearby))
            has_sd_transcript = any(
                node.get("transcript") or node.get("caption")
                for node in page.sd.of_type("VideoObject", "AudioObject")
            )
            if not (has_track or has_transcript_link or has_sd_transcript):
                video_hits.append((page.url, {
                    "tag": media.name, "selector": selector_for(media),
                    "src": (media.get("src") or "")[:100],
                }))
                break

        # --- PDF-only facts --------------------------------------------------------------
        page_text = doc.body_text()
        for anchor in (main.find_all("a") if main is not None else []):
            href = (anchor.get("href") or "").lower()
            if ".pdf" not in href:
                continue
            label = doc.visible_text(anchor)
            fact = _fact_class_of_label(label)
            if not fact:
                continue
            if _fact_present_in_text(fact, page_text):
                continue
            pdf_hits.append((page.url, {
                "fact": fact, "label": label[:80], "href": (anchor.get("href") or "")[:120],
                "selector": selector_for(anchor),
            }))
            break

        # --- SVG carrying text as paths in a fact region ---------------------------------
        for svg in doc.svgs():
            if main is None or not dom_mod._is_within(svg, main):
                continue
            if svg.find("title") is not None or svg.find("text") is not None:
                continue
            paths = len(svg.find_all(["path", "polygon", "polyline"]))
            if paths < 8:
                continue  # icons have few paths; lettering has many
            heading = _nearest_heading_text(doc, svg)
            fact = _fact_class_of_label(heading) if heading else ""
            if not fact or _fact_present_in_text(fact, page_text):
                continue
            svg_hits.append((page.url, {
                "paths": paths, "fact": fact, "heading": heading[:80],
                "selector": selector_for(svg),
            }))
            break

    _emit_simple(
        context, out, "G1.NONTEXT.IFRAME_LOCKED_CONTENT", iframe_hits, total,
        title="Primary content is delegated to a cross-origin iframe",
        evidence_fn=lambda p: (
            f"the page's own main region carries {p['main_chars']} characters of text and "
            f"embeds a cross-origin iframe (`{p['src']}`) that supplies the substance"),
        mechanism=("Content inside an iframe belongs to the framed document, not to the "
                   "page. An extractor reading this URL sees an almost empty page, and "
                   "the framed content -- if it is indexed at all -- is attributed to the "
                   "other origin, so neither URL represents what the visitor saw."),
        fix_summary="Render the primary content in the page itself rather than in an "
                    "embedded frame.",
        steps=[
            "Move the content into the page server-side, using the third-party service's "
            "API or data export rather than its embed widget.",
            "If the embed must stay, publish the same substance as HTML text on the page "
            "around it, so the URL carries the content it is about.",
            "Keep iframes for genuinely peripheral embeds such as maps and videos.",
        ],
        verification="Fetch the page and confirm its own response body contains the "
                     "substantive text, not just an iframe element.",
        effort="high", owner="Frontend engineering", fact_class="primary",
    )

    _emit_simple(
        context, out, "G1.NONTEXT.VIDEO_WITHOUT_TRANSCRIPT", video_hits, total,
        title="Video or audio content has no transcript or captions",
        evidence_fn=lambda p: (
            f"a <{p['tag']}> element is present with no <track> captions element, no "
            f"nearby transcript link, and no VideoObject.transcript in structured data"),
        mechanism=("Spoken content is invisible to text extraction. A transcript converts "
                   "the entire substance of a video into quotable text, and is usually "
                   "the single largest body of content on a media-led page."),
        fix_summary="Publish a transcript as page text and add a captions track.",
        steps=[
            "Generate a transcript and publish it as readable text on the same page, not "
            "only as a download.",
            "Add a <track kind=\"captions\"> element referencing a WebVTT file.",
            "Add VideoObject structured data with a transcript property and a "
            "description.",
        ],
        verification="Confirm the transcript text appears in the page's raw HTML and that "
                     "the captions track loads.",
        effort="medium", owner="Content", fact_class="secondary",
    )

    _emit_simple(
        context, out, "G1.NONTEXT.PDF_ONLY_FACT", pdf_hits, total,
        title="A fact class is available only through a linked PDF",
        evidence_fn=lambda p: (
            f"a link labelled \"{p['label']}\" points to a PDF (`{p['href']}`), and no "
            f"value matching the {p['fact'].replace('_', ' ')} fact class appears in the "
            f"page's own text"),
        mechanism=("A PDF is a separate document that many consumers do not parse, rank "
                   "differently, or cannot quote a specific passage from. A fact that "
                   "exists only inside one is effectively unavailable to the page that "
                   "links it."),
        fix_summary="Publish the document's key facts as an HTML page and keep the PDF as "
                    "a supplement.",
        steps=[
            "Create an HTML page carrying the document's substantive facts as text, "
            "tables and lists.",
            "Link the PDF from that page as a downloadable version rather than as the "
            "primary source.",
            "Where the PDF is the canonical artifact (a spec sheet, a report), publish an "
            "HTML summary with the key figures.",
        ],
        verification="Confirm the key facts appear as text in an HTML page's response "
                     "body.",
        effort="medium", owner="Content", fact_class="primary",
    )

    _emit_simple(
        context, out, "G1.NONTEXT.SVG_TEXT_AS_PATHS", svg_hits, total,
        title="An inline SVG in a fact-bearing section has no text or title element",
        evidence_fn=lambda p: (
            f"an inline SVG with {p['paths']} drawing paths and no <title> or <text> "
            f"element sits under the heading \"{p['heading']}\", and no value matching the "
            f"{p['fact'].replace('_', ' ')} fact class appears in the page's text"),
        mechanism=("Text converted to SVG paths is geometry, not characters. It renders "
                   "identically to a reader and is completely unreadable to an extractor, "
                   "which makes it one of the easiest ways to lose a fact without "
                   "noticing."),
        fix_summary="Add <title> and <text> elements to the SVG, or render the values as "
                    "HTML text.",
        steps=[
            "Add a <title> element as the SVG's first child, describing what it shows.",
            "Where the SVG contains lettering, use <text> elements rather than converting "
            "type to outlines.",
            "For data graphics, publish the underlying values as an adjacent HTML table.",
        ],
        verification="Confirm the SVG's text content appears in the page's raw HTML.",
        effort="low", owner="Design / Frontend engineering", fact_class="secondary",
    )


def _nearest_heading_text(doc: Any, node: Any) -> str:
    """Walk backwards to the nearest preceding heading, for section labelling."""
    current = node
    for _ in range(60):
        current = current.previous_element if hasattr(current, "previous_element") else None
        if current is None:
            return ""
        if getattr(current, "name", None) in dom_mod.HEADING_TAGS:
            return doc.visible_text(current)
    return ""


def _emit_simple(context: Context, out: FindingsFile, check_id: str,
                 hits: Sequence[Tuple[str, Any]], total_pages: int, *,
                 title: str, evidence_fn, mechanism: str, fix_summary: str,
                 steps: Sequence[str], verification: str, effort: str, owner: str,
                 fact_class: str) -> None:
    """Emit a template-collapsed finding for the simpler carrier checks."""
    if not hits:
        return
    for group in analyzer.group_by_template(context, list(hits)):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        out.add(Finding(
            check_id=check_id,
            title=title,
            evidence=(
                f"On {analyzer.kn(group['pages_affected'], group['pages_sampled'])} "
                f"sampled pages, {evidence_fn(payload)}. Affected: "
                f"{analyzer.summarise_urls(group['urls'])}."
            ),
            mechanism=mechanism,
            confidence="medium", signal_count=2, fact_class=fact_class,
            scope=analyzer.scope_from_group(group, total_pages),
            evidence_refs=[
                dom_ref(page.url_hash, payload.get("selector", "body"),
                        textnorm.truncate(str(payload), 200)),
                metric_ref(page.url_hash, f"{check_id.rsplit('.', 1)[-1].lower()}_pages",
                           group["pages_affected"], 0),
            ],
            suggested_action=SuggestedAction(
                summary=fix_summary, steps=list(steps), effort=effort,
                owner_role=owner, verification=verification,
                expected_effect="Converts content that is currently unreadable to text "
                                "extraction into quotable text.",
            ),
        ))


# ========================================================================================
# entry point
# ========================================================================================

def detect(context: Context, out: FindingsFile) -> None:
    out.note_metric("pages_analyzed", len(context.content_pages()))
    out.note_metric("pages_rendered",
                    sum(1 for p in context.pages if p.rendered_doc is not None))

    check_csr(context, out)
    check_interaction_required(context, out)
    check_encoding(context, out)
    check_text_structure(context, out)
    check_nontext_fact_regions(context, out)
    check_alt_text(context, out)
    check_other_carriers(context, out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return analyzer.run(
        SKILL_ID,
        "Gate G1 (READ): whether page text exists for a machine. Three-tier client-side "
        "render detection that works without a browser, plus facts locked in images, "
        "SVG, iframes, PDFs and video. Reads the evidence ledger only.",
        detect, argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
