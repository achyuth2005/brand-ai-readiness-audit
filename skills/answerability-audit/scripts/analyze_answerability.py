#!/usr/bin/env python3
"""Gate G3 -- CITE. Can a fact on this page be lifted out and quoted?

Pure function of the evidence ledger. No network I/O.

Mechanism
---------
The brief's Appendix B: what ends up in an answer is what a machine could "easily quote a
clear fact from". A page can pass every earlier gate -- permitted, readable, well marked
up -- and still never be cited, because retrieval operates on **chunks**. A passage is
selected and reproduced more or less standalone, and prose written to flow top-to-bottom
for a human reader can be systematically unquotable:

* "It also integrates with over 40 tools" is useless alone; the referent was in the
  previous chunk. "Acme integrates with over 40 tools" is directly quotable.
* "What is Acme?" is the most common query shape about a company. With no plain
  definitional sentence to quote, an assistant synthesises one from marketing copy --
  which is how brands get *misrepresented* rather than merely missed.

Calibrated honesty
------------------
These are **content-form heuristics with stated thresholds and published vocabulary**,
not measurements of any assistant's behaviour. The lexicons are English; a page in
another language is skipped and recorded, never judged with the wrong word list
(rule R6).

See references/quotability-rules.md and references/question-coverage.md.
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

from auditkit import analyzer, dom as dom_mod, entity, lexicons, sdextract  # noqa: E402
from auditkit import textnorm  # noqa: E402
from auditkit.analyzer import Context, PageCheck, PageView  # noqa: E402
from auditkit.dom import selector_for  # noqa: E402
from auditkit.finding import (  # noqa: E402
    Finding, FindingsFile, Scope, SuggestedAction, dom_ref, metric_ref,
)

SKILL_ID = "answerability-audit"

# High-frequency English function words, used only to decide whether an undeclared-language
# page can be judged with the English lexicons at all.
_EN_STOPWORDS = frozenset("""
the a an and or but of to in on at for with by from as is are was were be been being it
its this that these those we our you your they their he she his her not no yes can will
would should could may might must do does did have has had which who whom what when where
why how than then there here so if about into over under more most also just only very
""".split())

_QUOTE_CONTAINER_RE = re.compile(r"testimonial|quote|review|blockquote|cite|pullquote",
                                 re.IGNORECASE)
_INTERROGATIVE_RE = re.compile(
    r"^(what|why|how|when|where|who|whom|whose|which|can|could|does|do|did|is|are|was|"
    r"were|should|will|would|may|shall|has|have)\b", re.IGNORECASE)
_PREAMBLE_RE = re.compile(
    r"^(great question|good question|before we|let's|let us|in this (section|article|"
    r"guide|post)|first,? let|to answer (this|that)|it depends|there are (many|a few|"
    r"several)|many people|you may be wondering|you might be wondering|we often get|"
    r"a common question|this is a common|well,|so,)", re.IGNORECASE)
_MID_PROPER_NOUN_RE = re.compile(r"\s[A-Z][a-z]{2,}")
_CURRENCY_RE = re.compile(r"[£€¥₹$]")
_SPEC_PATTERNS = [re.compile(p, re.IGNORECASE)
                  for p in lexicons.FACT_CLASS_VALUE_PATTERNS["spec"]
                  + lexicons.FACT_CLASS_VALUE_PATTERNS["price"][:2]]
_STEP_RE = re.compile(r"\b(step \d+|first,|second,|third,|then,|next,|finally,)",
                      re.IGNORECASE)


# ========================================================================================
# helpers
# ========================================================================================

def _english(context: Context, page: PageView) -> bool:
    lang = dom_mod.detect_language(page.doc)
    if lang:
        return lang in lexicons.SUPPORTED_LANGUAGES
    tokens = [t.lower() for t in textnorm.words(page.doc.main_text()[:6000])]
    if len(tokens) < 30:
        return False
    share = sum(1 for t in tokens if t in _EN_STOPWORDS) / len(tokens)
    return share >= float(context.threshold("answerability.english_stopword_share"))


def _prose_blocks(page: PageView) -> List[Tuple[Any, str, str]]:
    """``(node, text, nearest_heading)`` for prose blocks in the main region.

    Excludes navigation, footer, legal boilerplate and quoted testimonials: a customer
    quote beginning "It changed how we work" is not the brand's own prose, and scoring it
    would be a false positive.
    """
    doc = page.doc
    root = doc.main_node() or doc.soup.find("body")
    if root is None:
        return []
    out: List[Tuple[Any, str, str]] = []
    heading = ""
    for node in root.find_all(list(dom_mod.HEADING_TAGS) + ["p", "li", "dd"]):
        if node.name in dom_mod.HEADING_TAGS:
            heading = doc.visible_text(node)
            continue
        if doc.is_boilerplate(node) or doc._is_hidden(node):
            continue
        if node.find_parent(["blockquote", "figure", "nav", "footer"]) is not None:
            continue
        wrapper = node.find_parent(attrs={"class": _QUOTE_CONTAINER_RE})
        if wrapper is not None:
            continue
        if node.name == "li" and node.find("p") is not None:
            continue  # the nested <p> is scored instead, to avoid double counting
        text = doc.visible_text(node)
        if textnorm.word_count(text) >= 4:
            out.append((node, text, heading))
    return out


def _is_factual(sentence: str, brand: entity.Brand) -> bool:
    if re.search(r"\d", sentence) or _CURRENCY_RE.search(sentence):
        return True
    if _MID_PROPER_NOUN_RE.search(sentence):
        return True
    return bool(brand.known and entity.find_definitional_sentence([sentence], brand))


def _self_contained(sentence: str, brand: entity.Brand, heading: str) -> bool:
    tokens = sentence.split()
    if not tokens:
        return True
    if not lexicons.opener_is_dependent(" ".join(tokens[:3])):
        return True
    first = tokens[0].strip(",.;:").lower()
    # "We" is acceptable on a first-person brand section whose heading names the brand.
    if first in ("we", "our", "us") and brand.known and brand.mentioned_in(heading):
        return True
    if brand.known and brand.mentioned_in(sentence):
        return True
    # A proper noun later in the sentence re-anchors it well enough to attribute.
    return bool(_MID_PROPER_NOUN_RE.search(" " + " ".join(tokens[1:])))


def _is_question(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith("?") or bool(_INTERROGATIVE_RE.match(stripped))


# ========================================================================================
# site-level checks
# ========================================================================================

def check_self_containment(context: Context, out: FindingsFile, brand: entity.Brand,
                           pages: Sequence[PageView]) -> None:
    ratio_floor = float(context.threshold("answerability.self_containment_ratio"))
    min_sentences = int(context.threshold("answerability.min_sentences_for_self_containment"))
    page_share = float(context.threshold("answerability.self_containment_page_share"))
    margin = context.near_miss_margin()

    qualifying = 0
    failing: List[Tuple[str, Any]] = []
    for page in pages:
        sentences: List[Tuple[str, str, Any]] = []
        for node, text, heading in _prose_blocks(page):
            for sentence in textnorm.sentences(text):
                if textnorm.word_count(sentence) >= 4:
                    sentences.append((sentence, heading, node))
        if len(sentences) < min_sentences:
            continue
        qualifying += 1
        dependent = [(s, h, n) for s, h, n in sentences if not _self_contained(s, brand, h)]
        ratio = 1 - len(dependent) / len(sentences)
        if ratio < ratio_floor:
            failing.append((page.url, {
                "ratio": ratio, "sentences": len(sentences), "dependent": dependent[:3],
                "refs": [metric_ref(page.url_hash, "self_containment_ratio",
                                    round(ratio, 3), ratio_floor),
                         dom_ref(page.url_hash, selector_for(dependent[0][2]),
                                 textnorm.truncate(dependent[0][0], 160))],
            }))
        elif ratio < ratio_floor * (1 + margin):
            out.near_miss("G3.ANSW.LOW_SELF_CONTAINMENT", "self_containment_ratio",
                          round(ratio, 3), ratio_floor, scope_key=page.cluster_id,
                          detail=(f"{len(sentences)} prose sentences measured on "
                                  f"{page.url}; {len(dependent)} open with a pronoun or "
                                  f"connective and name nothing."),
                          examples=[textnorm.truncate(s, 120) for s, _h, _n in dependent[:3]])

    out.note_metric("self_containment_pages_measured", qualifying)
    if qualifying < context.min_sample() or len(failing) < page_share * qualifying \
            or len(failing) < 2:
        return
    for group in analyzer.group_by_template(context, failing):
        payload = group["payloads"][0]
        examples = "; ".join(
            f"\"{textnorm.truncate(s, 110)}\" (under \"{h or 'the page top'}\")"
            for s, h, _n in payload["dependent"])
        analyzer.emit_group(
            context, out, "G3.ANSW.LOW_SELF_CONTAINMENT", group, total=qualifying,
            title="Passages lose their subject when retrieved on their own",
            evidence=(f"{analyzer.prefix(group)} fall below the self-containment floor: "
                      f"on the example page only {payload['ratio']:.0%} of "
                      f"{payload['sentences']} prose sentences name their subject "
                      f"(threshold {ratio_floor:.0%}). Examples that begin with a pronoun "
                      f"or connective and name nothing: {examples}. Across the site "
                      f"{len(failing)} of {qualifying} measured pages fail. Navigation, "
                      f"footer text and quoted testimonials were excluded."),
            mechanism=("Retrieval splits a page into passages and reproduces one more or "
                       "less standalone. A passage that begins \"It also supports...\" has "
                       "lost its referent to the previous chunk, so it cannot be quoted "
                       "or attributed. The same sentence naming its subject is directly "
                       "quotable. This is a mechanical property of chunked retrieval, not "
                       "a style preference."),
            action=SuggestedAction(
                summary="Rewrite section-opening and paragraph-opening sentences to name "
                        "their subject.",
                steps=["Make the first sentence of every section and paragraph true and "
                       "complete on its own: replace 'It', 'This', 'They', 'Also' openers "
                       "with the product or brand name.",
                       "Start with the examples quoted in the evidence; they are the "
                       "passages most likely to be retrieved without context.",
                       "Add the rule to the content style guide: 'every paragraph must "
                       "still make sense if it is the only paragraph someone reads'."],
                effort="medium", owner_role="Content / Marketing",
                verification=("Read any single paragraph in isolation and confirm it names "
                              "what it is about; re-run the audit and confirm the ratio "
                              "exceeds the threshold."),
                expected_effect="Makes individual passages quotable and attributable when "
                                "retrieved alone.",
            ),
            refs=list(payload["refs"]), fact_class="secondary",
        )


def check_definition(context: Context, out: FindingsFile, brand: entity.Brand) -> None:
    home = context.homepage()
    if home is None:
        out.skip("G3.ANSW.NO_DEFINITIONAL_SENTENCE", reason="the homepage was not fetched")
        return
    if not brand.known:
        out.skip("G3.ANSW.NO_DEFINITIONAL_SENTENCE",
                 reason="the brand name could not be inferred from any naming surface, so "
                        "a definitional sentence about it cannot be recognised")
        return
    if not _english(context, home):
        out.skip("G3.ANSW.NO_DEFINITIONAL_SENTENCE",
                 reason="the homepage is not in a language with a supported lexicon")
        return

    count = int(context.threshold("answerability.definitional_paragraphs"))
    about = [p for p in context.content_pages() if p.has_path_class("about")]
    checked = [f"the first {count} paragraphs of the homepage"]
    texts = [analyzer.first_n_paragraph_text(home, count)]
    if about:
        texts.append(analyzer.first_n_paragraph_text(about[0], count))
        checked.append(f"the first {count} paragraphs of {about[0].url}")
    org_descriptions = [sdextract.scalar(n.get("description"))
                        for p in context.content_pages() for n in p.sd.nodes
                        if n.has_type(*entity.ORG_TYPES) and n.get("description")]
    if org_descriptions:
        texts.extend(org_descriptions[:2])
        checked.append("Organization.description in structured data")

    found = entity.find_definitional_sentence([t for t in texts if t], brand)
    if found:
        out.note_posture("definitional_sentence", found)
        return

    meta = home.doc.meta("description")
    meta_has = bool(meta and entity.find_definitional_sentence([meta], brand))
    out.add(Finding(
        check_id="G3.ANSW.NO_DEFINITIONAL_SENTENCE",
        title=f"No plain sentence says what {brand.name} is",
        evidence=(f"No sentence of the form \"{brand.name} is a ...\", \"{brand.name} "
                  f"provides ...\" or \"{brand.name}, the ...\" appears in "
                  f"{', '.join(checked)}. "
                  + ("The meta description contains one, but a meta description is not "
                     "visible page text and is not what a retrieval passage is drawn "
                     "from. " if meta_has else "")
                  + f"The brand name was inferred as \"{brand.name}\" "
                    f"({brand.confidence} confidence) from the site's own naming surfaces."),
        mechanism=("\"What is <brand>?\" is the most common query shape about a company, and "
                   "assistants answer it by quoting a definitional sentence. When none "
                   "exists in plain text, the assistant has to synthesise one from "
                   "taglines and marketing copy -- which is precisely how brands get "
                   "described wrongly rather than merely omitted."),
        # A definition cannot be looked for with certainty under a guessed brand name.
        confidence="medium" if brand.confidence == "low" else "high",
        signal_count=len(checked) + 1, fact_class="secondary",
        scope=Scope(level="site", pages_affected=1 + len(about[:1]),
                    pages_sampled=1 + len(about[:1]),
                    example_urls=[home.url] + [p.url for p in about[:1]]),
        evidence_refs=[metric_ref(home.url_hash, "definitional_sentences_found", 0, 1),
                       dom_ref(home.url_hash, home.doc.main_selector,
                               textnorm.truncate(texts[0], 160) or "homepage body")],
        suggested_action=SuggestedAction(
            summary=(f"Add one plain definitional sentence near the top of the homepage "
                     f"and about page, and mirror it in Organization.description."),
            steps=[(f"Write it as category + audience + differentiator: \"{brand.name} is "
                    f"a <category> that helps <audience> <do what>, <differentiator>.\""),
                   "Place it in the first visible paragraph of the homepage, as body text "
                   "rather than inside an image, a slider or a heading-only hero.",
                   "Repeat it verbatim as the first sentence of the about page.",
                   "Use the same sentence in Organization.description and as the press "
                   "boilerplate, so every copy of it agrees."],
            effort="trivial", owner_role="Brand / Content",
            verification=(f"Fetch the homepage with JavaScript disabled and confirm the "
                          f"sentence is in the first paragraph; ask an assistant \"What is "
                          f"{brand.name}?\" after re-indexing and compare the answer."),
            expected_effect="Gives assistants the brand's own words to quote for its most "
                            "common query.",
        ),
    ))


def check_question_headings(context: Context, out: FindingsFile,
                            pages: Sequence[PageView]) -> None:
    if not context.allows("G3.ANSW.NO_QUESTION_SHAPED_HEADINGS"):
        out.skip("G3.ANSW.NO_QUESTION_SHAPED_HEADINGS",
                 reason=f"not applicable to the detected "
                        f"{context.site_profile.detected_type} profile")
        return
    min_pages = int(context.threshold("answerability.question_heading_min_pages"))
    with_headings = [p for p in pages if len(p.doc.headings()) >= 2]
    if len(with_headings) < min_pages:
        return
    questions = [(p, t) for p in with_headings for lvl, t, _n in p.doc.headings()
                 if lvl >= 2 and _is_question(t)]
    has_faq = any(p.sd.of_type("FAQPage", "QAPage") for p in context.content_pages())
    heading_total = sum(len(p.doc.headings()) for p in with_headings)
    if questions or has_faq:
        out.note_posture("question_shaped_headings", len(questions))
        return
    page = with_headings[0]
    out.add(Finding(
        check_id="G3.ANSW.NO_QUESTION_SHAPED_HEADINGS",
        title="No content is organised around the questions users actually ask",
        evidence=(f"Across {len(with_headings)} sampled content pages carrying "
                  f"{heading_total} headings, none is question-shaped, and no FAQPage or "
                  f"QAPage structured data is published anywhere on the site."),
        mechanism=("User prompts are question-shaped. A heading phrased as the question, "
                   "with the answer immediately beneath it, aligns the retrievable chunk "
                   "to the query, so the passage selected for 'how much does X cost' "
                   "actually contains the cost."),
        confidence="high", signal_count=2, fact_class="secondary",
        scope=Scope(level="site", pages_affected=len(with_headings),
                    pages_sampled=len(with_headings),
                    example_urls=[p.url for p in with_headings[:3]]),
        evidence_refs=[metric_ref(page.url_hash, "question_shaped_headings", 0, 1)],
        suggested_action=SuggestedAction(
            summary="Add question-shaped headings with first-sentence answers for the "
                    "top customer questions.",
            steps=["Collect the ten most common sales and support questions, using the "
                   "site's own navigation labels and product names as the vocabulary.",
                   "Phrase each as an h2 or h3 question on the page where it belongs, and "
                   "answer it in the first sentence beneath.",
                   "Where several accumulate on one page, add FAQPage markup generated "
                   "from the visible questions and answers."],
            effort="low", owner_role="Content / Marketing",
            verification="Confirm each new heading is a question and the first sentence "
                         "beneath it answers it without preamble.",
            expected_effect="Aligns retrievable passages with the queries users type.",
        ),
    ))


def _link_text_share(page: PageView) -> float:
    """Share of the main region's text that sits inside links -- high on index pages."""
    main_chars = len(page.doc.main_text())
    if not main_chars:
        return 0.0
    linked = sum(len(link.text) for link in page.doc.links() if link.in_main)
    return min(1.0, linked / main_chars)


def check_entity_anchor(context: Context, out: FindingsFile, brand: entity.Brand) -> None:
    if not brand.known or brand.confidence == "low":
        out.skip("G3.ANSW.PRONOUN_ONLY_ENTITY_REFERENCE",
                 reason="the brand name could not be inferred with enough confidence to "
                        "test for its absence")
        return
    min_words = int(context.threshold("answerability.min_words_for_prose_checks"))
    index_share = float(context.threshold("answerability.index_page_link_text_share"))
    candidates = [p for p in context.content_pages()
                  if p.path not in ("/", "") and not p.has_path_class("product")
                  and textnorm.word_count(p.doc.main_text()) >= min_words
                  and _link_text_share(p) < index_share]
    if len(candidates) < context.min_sample():
        return
    unanchored = []
    for page in candidates:
        h1 = " ".join(page.doc.h1s())
        if brand.mentioned_in(page.doc.main_text()) or brand.mentioned_in(h1):
            continue
        chrome = brand.mentioned_in(page.doc.title()) or \
            brand.mentioned_in(page.doc.boilerplate_text())
        unanchored.append((page.url, {
            "chrome": chrome,
            "words": textnorm.word_count(page.doc.main_text()),
            "refs": [metric_ref(page.url_hash, "brand_mentions_in_body", 0, 1)],
        }))
    if len(unanchored) * 2 < len(candidates):
        return
    for group in analyzer.group_by_template(context, unanchored):
        payload = group["payloads"][0]
        analyzer.emit_group(
            context, out, "G3.ANSW.PRONOUN_ONLY_ENTITY_REFERENCE", group,
            total=len(candidates),
            title=f"Content pages never name {brand.name} in their body text",
            evidence=(f"{analyzer.prefix(group)} carry {payload['words']}+ words of body "
                      f"text and an h1 without once naming \"{brand.name}\""
                      + (", which appears only in the title tag, logo or navigation"
                         if payload["chrome"] else "")
                      + f". Across the site {len(unanchored)} of {len(candidates)} "
                        f"measured content pages are unanchored. Product pages and the "
                        f"homepage were excluded."),
            mechanism=("A passage extracted from a page carries only its own words. If the "
                       "brand is named only in the logo and navigation, a quoted passage "
                       "has no entity anchor, so even a correctly quoted fact cannot be "
                       "attributed to the brand."),
            action=SuggestedAction(
                summary=f"Name {brand.name} in the body text of each content page, at "
                        f"least once near the top.",
                steps=[f"Add the brand name to the first paragraph of each affected page: "
                       f"\"{brand.name}'s <feature> ...\" rather than \"Our <feature> ...\".",
                       "Name the brand again in any section likely to be quoted on its own, "
                       "such as pricing, specifications and definitions."],
                effort="low", owner_role="Content",
                verification="Confirm the brand name appears in the main-region text of "
                             "each affected page's raw HTML.",
                expected_effect="Makes quoted passages attributable to the brand.",
            ),
            # Whether a topic page needs the brand named in its body depends on whether the
            # brand is its subject, which a word match cannot decide.
            refs=list(payload["refs"]), fact_class="secondary", confidence="medium",
        )


# ========================================================================================
# per-page checks
# ========================================================================================

def build_page_checks(context: Context, brand: entity.Brand) -> List[PageCheck]:
    min_words = int(context.threshold("answerability.min_words_for_prose_checks"))
    min_sentences = int(context.threshold("answerability.min_sentences_for_self_containment"))
    fluff_floor = float(context.threshold("answerability.fluff_ratio"))
    factual_floor = float(context.threshold("answerability.factual_ratio"))
    long_words = int(context.threshold("answerability.long_page_words"))
    proximity = int(context.threshold("answerability.answer_proximity_chars"))
    max_para = int(context.threshold("answerability.max_paragraph_words"))
    max_span = int(context.threshold("answerability.max_heading_free_words"))
    majority = float(context.threshold("false_positive_control.template_majority_share"))

    def prose(page: PageView) -> bool:
        return _english(context, page) and \
            textnorm.word_count(page.doc.main_text()) >= min_words

    def main_sel(page: PageView) -> str:
        return page.doc.main_selector

    # -- fluff ----------------------------------------------------------------------------
    def fluff(page: PageView) -> Optional[Dict[str, Any]]:
        sentences = [s for _n, t, _h in _prose_blocks(page) for s in textnorm.sentences(t)
                     if textnorm.word_count(s) >= 4]
        if len(sentences) < min_sentences:
            return None
        factual = [s for s in sentences if _is_factual(s, brand)]
        fluffy = [s for s in sentences
                  if lexicons.is_fluff_sentence(s.lower()) and not _is_factual(s, brand)]
        fluff_ratio = len(fluffy) / len(sentences)
        factual_ratio = len(factual) / len(sentences)
        if not (fluff_ratio > fluff_floor and factual_ratio < factual_floor):
            return None
        return {
            "observed": (f"{len(fluffy)} of {len(sentences)} prose sentences "
                         f"({fluff_ratio:.0%}, threshold {fluff_floor:.0%}) rest on an "
                         f"unquantified superlative with no number, name or verifiable "
                         f"object, and only {factual_ratio:.0%} (threshold "
                         f"{factual_floor:.0%}) state anything verifiable. Example: "
                         f"\"{textnorm.truncate(fluffy[0], 140)}\". Both conditions are "
                         f"required, so a page that is promotional and factual is not "
                         f"flagged"),
            "refs": [metric_ref(page.url_hash, "fluff_ratio", round(fluff_ratio, 3),
                                fluff_floor),
                     metric_ref(page.url_hash, "factual_ratio", round(factual_ratio, 3),
                                factual_floor)],
            "confidence": "medium",
        }

    # -- anchors --------------------------------------------------------------------------
    def anchors(page: PageView) -> Optional[Dict[str, Any]]:
        main = page.doc.main_node() or page.doc.soup.find("body")
        if main is None:
            return None
        headings = main.find_all(["h2", "h3"])
        if len(headings) < 3:
            return None
        addressable = [h for h in headings if (h.get("id") or "").strip()
                       or (h.parent is not None and h.parent.name == "section"
                           and (h.parent.get("id") or "").strip())
                       or h.find("a", attrs={"id": True}) is not None
                       or h.find("a", attrs={"name": True}) is not None]
        if addressable:
            return None
        return {
            "observed": (f"a {textnorm.word_count(page.doc.main_text())}-word page "
                         f"(threshold {long_words}) has {len(headings)} h2/h3 headings "
                         f"and none carries an id or an enclosing section id, so no part "
                         f"of it can be linked to directly"),
            "refs": [metric_ref(page.url_hash, "addressable_headings", 0, len(headings)),
                     dom_ref(page.url_hash, selector_for(headings[0]),
                             page.doc.visible_text(headings[0]))],
        }

    # -- buried answers --------------------------------------------------------------------
    def buried(page: PageView) -> Optional[Dict[str, Any]]:
        sections = [s for s in page.doc.sections() if _is_question(s.heading)]
        if len(sections) < 2:
            return None
        late: List[Tuple[Any, int]] = []
        for section in sections:
            offset = 0
            found = False
            for sentence in textnorm.sentences(section.text):
                if _is_question(sentence) or _PREAMBLE_RE.match(sentence) \
                        or textnorm.word_count(sentence) < 5:
                    offset = section.text.find(sentence) + len(sentence)
                    continue
                offset = section.text.find(sentence)
                found = True
                break
            if (found and offset > proximity) or (not found and len(section.text) > proximity):
                late.append((section, offset if found else len(section.text)))
        if len(late) / len(sections) < majority:
            return None
        section, offset = late[0]
        return {
            "observed": (f"{len(late)} of {len(sections)} question-shaped headings are "
                         f"followed by preamble before any answering sentence; under "
                         f"\"{section.heading}\" the first answer-like sentence starts "
                         f"{offset} characters in (threshold {proximity})"),
            "refs": [dom_ref(page.url_hash, section.selector, section.heading),
                     metric_ref(page.url_hash, "answer_offset_chars", offset, proximity)],
        }

    # -- walls of text --------------------------------------------------------------------
    def wall(page: PageView) -> Optional[Dict[str, Any]]:
        doc = page.doc
        main = doc.main_node() or doc.soup.find("body")
        if main is None:
            return None
        paragraphs = [textnorm.word_count(doc.visible_text(p)) for p in main.find_all("p")
                      if not doc.is_boilerplate(p)]
        paragraphs = [w for w in paragraphs if w]
        median_words = textnorm.median(paragraphs)
        span = longest = 0
        for node in main.find_all(list(dom_mod.HEADING_TAGS) + ["p", "li", "pre", "dd"]):
            if node.name in dom_mod.HEADING_TAGS:
                span = 0
                continue
            if node.name == "li" and node.find("p") is not None:
                continue
            span += textnorm.word_count(doc.visible_text(node))
            longest = max(longest, span)
        long_paras = len(paragraphs) >= 3 and median_words > max_para
        if not (long_paras or longest > max_span):
            return None
        parts = []
        if long_paras:
            parts.append(f"median paragraph length is {median_words:.0f} words across "
                         f"{len(paragraphs)} paragraphs (threshold {max_para})")
        if longest > max_span:
            parts.append(f"the longest stretch without a heading runs {longest} words "
                         f"(threshold {max_span})")
        return {
            "observed": " and ".join(parts),
            "refs": [metric_ref(page.url_hash, "median_paragraph_words",
                                round(median_words, 1), max_para),
                     metric_ref(page.url_hash, "longest_heading_free_words", longest,
                                max_span)],
        }

    # -- unlisted facts ---------------------------------------------------------------------
    def unlisted(page: PageView) -> Optional[Dict[str, Any]]:
        doc = page.doc
        main = doc.main_node() or doc.soup.find("body")
        if main is None:
            return None
        if doc.tables():
            return None
        if any(len(l.find_all("li")) >= 3 for l in doc.lists()):
            return None
        fact_sentences = []
        for p in main.find_all("p"):
            if doc.is_boilerplate(p):
                continue
            for sentence in textnorm.sentences(doc.visible_text(p)):
                if any(pattern.search(sentence) for pattern in _SPEC_PATTERNS) \
                        or _STEP_RE.search(sentence):
                    fact_sentences.append((sentence, p))
        if len(fact_sentences) < 3:
            return None
        sentence, node = fact_sentences[0]
        return {
            "observed": (f"{len(fact_sentences)} sentences state specifications, prices "
                         f"or steps inside running prose (e.g. \"{textnorm.truncate(sentence, 120)}\"), "
                         f"and the main region contains no table, definition list or "
                         f"list of three or more items"),
            "refs": [metric_ref(page.url_hash, "fact_sentences_in_prose",
                                len(fact_sentences), 2),
                     dom_ref(page.url_hash, selector_for(node), textnorm.truncate(sentence, 120))],
        }

    return [
        PageCheck(
            check_id="G3.ANSW.FLUFF_DOMINANT", detect=fluff, applies=prose,
            title="Promotional language leaves little that can be quoted as fact",
            mechanism=("Unquantified superlatives are not extractable claims. A page made of "
                       "them gives a retrieval system nothing to lift, and hands the answer "
                       "to a competitor whose page states a specific, checkable fact."),
            fix="Replace unquantified claims with the specific facts behind them.",
            steps=["For each superlative, ask what evidence it rests on -- a number, a "
                   "customer, a benchmark, a certification -- and state that instead.",
                   "Keep the brand voice; change 'industry-leading uptime' to '99.95% "
                   "uptime over the last 12 months'.",
                   "Where no evidence exists, cut the claim."],
            verification="Re-run the audit and confirm the factual-sentence share exceeds "
                         "the threshold on the affected template.",
            effort="medium", owner="Content / Marketing", fact_class="secondary",
            expected_effect="Gives retrieval systems concrete claims to quote.",
        ),
        PageCheck(
            check_id="G3.ANSW.NO_ANCHOR_ADDRESSABILITY", detect=anchors,
            applies=lambda p: textnorm.word_count(p.doc.main_text()) > long_words,
            title="Long pages offer no linkable section anchors",
            mechanism=("A heading id is what makes a fragment citable: a consumer can point "
                       "at the exact section that answers a question. Without ids the "
                       "whole page must be cited or nothing, and a long page cited whole "
                       "dilutes the passage that mattered."),
            fix="Give every h2 and h3 a stable slug id and add an in-page table of "
                "contents.",
            steps=["Generate an id from each heading's text (a stable slug) in the "
                   "template or markdown renderer.",
                   "Keep ids stable when headings are lightly edited, so external links "
                   "do not break.",
                   "Add a table of contents linking to the ids."],
            verification="Load an affected URL with #<heading-slug> appended and confirm "
                         "the browser scrolls to that section.",
            effort="trivial", owner="Frontend engineering", fact_class="enrichment",
            expected_effect="Makes individual sections citable by URL.",
        ),
        PageCheck(
            check_id="G3.ANSW.ANSWER_BURIED", detect=buried,
            applies=lambda p: _english(context, p),
            title="Question headings are followed by preamble instead of the answer",
            mechanism=("A retrieved chunk that contains the question and the preamble but "
                       "not the answer is useless for that question. Answering in the "
                       "first sentence puts the question and its resolution in the same "
                       "passage."),
            fix="Answer each question in the first sentence beneath its heading, then "
                "elaborate.",
            steps=["Move the direct answer to the first sentence under each question "
                   "heading.",
                   "Delete scene-setting openers ('Great question', 'Before we dive "
                   "in').",
                   "Put caveats and detail after the answer, not before it."],
            verification="Read the first sentence under each question heading and confirm "
                         "it answers the question on its own.",
            effort="low", owner="Content", fact_class="secondary",
            expected_effect="Puts each answer in the same passage as its question.",
        ),
        PageCheck(
            check_id="G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE", detect=wall,
            applies=lambda p: textnorm.word_count(p.doc.main_text()) >= min_words,
            title="Long unbroken prose gives neither chunkers nor readers a boundary",
            mechanism=("Chunkers split on structure. Long paragraphs and heading-free spans "
                       "produce passages that straddle several topics, so the retrieved "
                       "passage for any one question is diluted. The same structure is "
                       "unscannable for a human reader."),
            fix="Break long spans with descriptive subheadings and shorter paragraphs.",
            steps=["Insert an h2 or h3 at each change of topic, phrased as what the "
                   "section answers.",
                   "Keep paragraphs to one idea, roughly 40-80 words.",
                   "Convert enumerations inside prose into lists."],
            verification="Re-run the audit and confirm median paragraph length and the "
                         "longest heading-free span are under their thresholds.",
            effort="medium", owner="Content", fact_class="secondary",
            expected_effect="Produces topic-aligned passages and a scannable page.",
        ),
        PageCheck(
            check_id="G3.ANSW.KEY_FACTS_UNLISTED", detect=unlisted, applies=prose,
            title="Specifications, prices or steps are buried in prose",
            mechanism=("Facts embedded as clauses in sentences have to be parsed out; facts "
                       "in a table, definition list or ordered list survive extraction and "
                       "summarisation intact, label and value together."),
            fix="Present specifications, plans and steps as tables and lists.",
            steps=["Move specifications and prices into a <table> or <dl> with a label "
                   "and value per row.",
                   "Turn sequential instructions into an <ol>.",
                   "Keep a sentence of context above each table."],
            verification="Confirm the facts appear inside table, dl or list elements in "
                         "the raw HTML.",
            effort="low", owner="Content / Frontend engineering", fact_class="secondary",
            expected_effect="Makes key facts extractable as label-value pairs.",
        ),
    ]


# ========================================================================================
# entry point
# ========================================================================================

def detect(context: Context, out: FindingsFile) -> None:
    pages = context.content_pages()
    brand = entity.infer_brand(pages, context.site_host())
    english = [p for p in pages if _english(context, p)]
    out.note_metric("pages_analyzed", len(pages))
    out.note_metric("pages_in_supported_language", len(english))
    out.note_metric("brand_name_inferred", brand.name)

    if pages and not english:
        for check_id in ("G3.ANSW.LOW_SELF_CONTAINMENT", "G3.ANSW.FLUFF_DOMINANT",
                         "G3.ANSW.ANSWER_BURIED", "G3.ANSW.KEY_FACTS_UNLISTED"):
            out.skip(check_id,
                     reason="no sampled page is in a language with a supported lexicon "
                            f"(supported: {', '.join(sorted(lexicons.SUPPORTED_LANGUAGES))}); "
                            "judging other languages with an English word list would "
                            "produce false findings")

    check_self_containment(context, out, brand, english)
    check_definition(context, out, brand)
    check_question_headings(context, out, pages)
    check_entity_anchor(context, out, brand)
    analyzer.run_page_checks(context, out, build_page_checks(context, brand))


def main(argv: Optional[Sequence[str]] = None) -> int:
    return analyzer.run(
        SKILL_ID,
        "Gate G3 (CITE): whether a fact can be lifted and quoted -- passage "
        "self-containment under chunked retrieval, definitional statements, question "
        "coverage, fragment addressability and extractable fact structure.",
        detect, argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
