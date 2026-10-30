#!/usr/bin/env python3
"""Gates G2-G3 -- IDENTITY & TIME. Can a machine tell who this is, and trust when?

Pure function of the evidence ledger, with one declared exception handled without
breaking that property: the **two-pass off-site protocol**.

    pass 1   analyze --ledger L --emit-requests offsite_requests.json
             (declares which sameAs identifiers it wants resolved, then stops)
    orchestrator validates each request (SSRF, robots, budget) and fetches it through
             the single egress gateway, appending to the ledger
    pass 2   analyze --ledger L --out findings.json   (pure analysis again)

So the marketplace keeps exactly one network choke point, and this skill still has no
code path to a socket.

Mechanism
---------
The brief's Appendix D: a fact is believed when many independent sources agree, and
*mistaken identity* is the specific failure when several things share a name. We cannot
enumerate the world's namesakes offline, and must not pretend to. So this skill measures
**the site's own disambiguation posture** -- whether the site gives a machine enough to
tell this entity apart -- which is both honestly observable and exactly what the fix
addresses. Freshness is judged the same way: from the coherence of the site's own date
signals, never from guesses about the world.

See references/entity-disambiguation.md and references/freshness-signals.md.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import analyzer, entity, lexicons, sdextract, sitemap, textnorm  # noqa: E402
from auditkit import urlnorm  # noqa: E402
from auditkit.analyzer import Context, PageView  # noqa: E402
from auditkit.dom import Document, detect_language, selector_for  # noqa: E402
from auditkit.finding import (  # noqa: E402
    Finding, FindingsFile, Scope, SuggestedAction, dom_ref, jsonpath_ref, metric_ref,
)

SKILL_ID = "entity-trust-audit"

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_FOUNDED_RE = re.compile(
    r"\b(?:founded|established|est\.|since|incorporated)\s*(?:in\s*)?((?:18|19|20)\d{2})\b"
    # Narrative founding: "started Acme in December 2018", "was founded back in 2011".
    r"|\b(?:founded|co-founded|established|started|launched|began|incorporated|formed|"
    r"opened)\b[^.!?\n]{0,60}?\b(?:in|since)\s+(?:" + _MONTH + r"\s+)?((?:18|19|20)\d{2})\b"
    # Story form: "Back to 2012, when five entrepreneurs came together".
    r"|\b(?:in|since|back to|back in)\s+((?:18|19|20)\d{2}),?\s+when\b[^.!?\n]{0,80}?\b"
    r"(?:came together|founded|started|launched|set out|began|created)\b",
    re.IGNORECASE)
_LEADERSHIP_RE = re.compile(r"\b(?:ceo|founder|co-founder|chief executive|managing "
                            r"director|president|chairman|chairwoman|chair)\b",
                            re.IGNORECASE)
# A named person as the subject or agent of founding: "Jane Doe started Acme",
# "founded by Jane Doe". Case-sensitive on purpose: a personal name is capitalised.
_FOUNDER_NAME_RE = re.compile(
    r"\b(?P<name>[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){1,2})\s+(?:founded|co-founded|started|"
    r"created)\b|\b(?:[Ff]ounded|[Cc]o-founded|[Ss]tarted|[Cc]reated)\s+by\s+"
    r"(?P<agent>[A-Z][a-z'\-]+\s+[A-Z][a-z'\-]+)")
_HEADCOUNT_RE = re.compile(r"\b\d[\d,]*\+?\s*(?:employees|people|staff|team members|"
                           r"colleagues|engineers)\b", re.IGNORECASE)
_LOCATION_RE = re.compile(
    r"\b(?:headquartered|based|located|offices?)\s+in\s+(?:the\s+)?[A-Z]\w+"
    # "Chicago Headquarters", "Headquarters: Berlin", "HQ in Austin".
    r"|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:Headquarters|HQ|Head Office)\b"
    r"|\b(?:[Hh]eadquarters|[Hh]ead [Oo]ffice|HQ)\s*(?:in|:|-)\s*[A-Z]\w+")
_UPDATED_RE = re.compile(
    r"\b(?:last\s+)?(?:updated|modified|revised|reviewed)\s*(?:on|:)?\s*"
    r"((?:\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4})|(?:[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
    r"|(?:\d{4}-\d{2}-\d{2}))", re.IGNORECASE)
_PUBLISHED_RE = re.compile(
    r"\b(?:published|posted|written)\s*(?:on|:)?\s*"
    r"((?:\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4})|(?:[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
    r"|(?:\d{4}-\d{2}-\d{2}))", re.IGNORECASE)
_FUTURE_EVENT_RE = re.compile(
    r"\b(?:will be held|will take place|coming in|launching in|launches in|join us in|"
    r"see you in|opening in|arriving in)\b[^.]{0,60}?\b((?:19|20)\d{2})\b", re.IGNORECASE)
_COMING_SOON_RE = re.compile(r"\bcoming soon\b", re.IGNORECASE)
_PRESS_PATH_RE = re.compile(r"(^|/)(press|media|newsroom|news-room|press-kit|presskit|"
                            r"brand-assets|brand|media-kit|press-releases?)(/|$)",
                            re.IGNORECASE)
_NUMERIC_CLAIM_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent|x\b|times|million|"
                               r"billion|thousand)")

INCONCLUSIVE_STATUSES = frozenset({401, 403, 429, 451, 999})


def _now(context: Context) -> Optional[date]:
    """The audit's reference date, from the ledger -- never the wall clock.

    Staleness judged against ``datetime.now()`` would make two runs over the same frozen
    ledger disagree, which breaks the determinism guarantee.
    """
    raw = context.ledger.meta.get("audited_at")
    return textnorm.parse_date(str(raw)) if raw else None


def _is_org(node: sdextract.SDNode) -> bool:
    return node.has_type(*entity.ORG_TYPES)


def _is_article_page(page: PageView) -> bool:
    return page.has_path_class("article") or bool(page.sd.of_type(
        "Article", "NewsArticle", "BlogPosting", "TechArticle", "Report"))


# ========================================================================================
# identity
# ========================================================================================

def check_posture(context: Context, out: FindingsFile, brand: entity.Brand) -> None:
    """G3.ENTITY.AMBIGUITY_RISK / WEAK_DISAMBIGUATION -- the six-point posture score."""
    home = context.homepage()
    pages = context.content_pages()
    if home is None or not pages:
        out.skip("G3.ENTITY.AMBIGUITY_RISK", reason="the homepage was not fetched")
        return

    risk_max = int(context.threshold("entity_trust.ambiguity_risk_score"))
    weak_max = int(context.threshold("entity_trust.weak_disambiguation_score"))
    sameas_needed = int(context.threshold("entity_trust.authoritative_sameas_required"))
    short_chars = int(context.threshold("entity_trust.short_name_chars"))
    variant_limit = int(context.threshold("entity_trust.name_variant_count"))
    para_count = int(context.threshold("answerability.definitional_paragraphs"))

    org_nodes = [(p, n) for p in pages for n in p.sd.nodes if _is_org(n)]
    points: List[Tuple[str, bool, str]] = []

    # 1. stable @id, used consistently
    ids = Counter(n.node_id for _p, n in org_nodes if n.node_id)
    all_have_id = bool(org_nodes) and all(n.node_id for _p, n in org_nodes)
    stable = all_have_id and len(ids) == 1
    points.append(("stable Organization @id", stable,
                   f"one Organization @id ({next(iter(ids))}) used on "
                   f"{len({p.url for p, _ in org_nodes})} page(s)" if stable else
                   (f"{len(org_nodes)} Organization node(s) with {len(ids)} distinct @id "
                    f"value(s)" if org_nodes else "no Organization structured data")))

    # 2. authoritative sameAs
    same_as = sorted({u for _p, n in org_nodes for u in sdextract.all_scalars(n.get("sameAs"))})
    authoritative = [u for u in same_as
                     if any(h in u.lower() for h in lexicons.AUTHORITATIVE_SAMEAS_HOSTS)]
    social = [u for u in same_as
              if any(h in u.lower() for h in lexicons.SOCIAL_SAMEAS_HOSTS)]
    points.append(("authoritative sameAs identifiers", len(authoritative) >= sameas_needed,
                   f"{len(authoritative)} authoritative identifier(s) and {len(social)} "
                   f"social profile(s) in sameAs (need {sameas_needed} authoritative)"))

    # 3. entity home page
    about = [p for p in pages if p.has_path_class("about")]
    points.append(("an entity-home page", bool(about),
                   f"entity-home page at {about[0].url}" if about else
                   "no /about or /company page among fetched pages"))

    # 4. definitional sentence
    texts = [analyzer.first_n_paragraph_text(home, para_count)]
    texts += [analyzer.first_n_paragraph_text(p, para_count) for p in about[:1]]
    texts += [sdextract.scalar(n.get("description")) for _p, n in org_nodes[:3]]
    definition = entity.find_definitional_sentence([t for t in texts if t], brand)
    points.append(("a plain-text definitional sentence", bool(definition),
                   f"\"{definition}\"" if definition else
                   f"no sentence of the form '{brand.name or '<brand>'} is a ...' in the "
                   f"first {para_count} paragraphs of the homepage or about page, or in "
                   f"Organization.description"))

    # 5. disambiguating modifiers
    corpus = " ".join(texts) + " " + (" ".join(about[0].doc.paragraphs()[:8]) if about else "")
    modifiers: List[str] = []
    lowered = corpus.lower()
    if any(re.search(rf"\b{re.escape(noun)}\b", lowered) for noun in lexicons.CATEGORY_NOUNS):
        modifiers.append("category")
    if any(n.get("address") or n.get("areaServed") or n.get("location")
           for _p, n in org_nodes) or _LOCATION_RE.search(corpus) \
            or any(p.sd.of_type("PostalAddress") for p in pages):
        modifiers.append("geography")
    if any(n.get("foundingDate") for _p, n in org_nodes) or _FOUNDED_RE.search(corpus):
        modifiers.append("founding year")
    if any(n.get("parentOrganization") or n.get("subOrganization") for _p, n in org_nodes):
        modifiers.append("parent or subsidiary relation")
    points.append(("two or more disambiguating modifiers", len(modifiers) >= 2,
                   f"modifiers present: {', '.join(modifiers) or 'none'}"))

    # 6. naming consistency
    clusters = brand.clusters
    points.append(("consistent naming across surfaces", len(clusters) < variant_limit,
                   f"{len(clusters)} irreducible name variant(s) across surfaces"))

    score = sum(1 for _label, ok, _detail in points if ok)
    amplifiers = entity.is_common_word_name(brand, short_chars)
    effective = score - (1 if amplifiers else 0)

    out.note_posture("disambiguation_posture", {
        "score": score, "effective_score": effective, "amplifiers": amplifiers,
        "points": {label: ok for label, ok, _ in points},
        "authoritative_sameas": authoritative, "social_sameas": social,
        "organization_present": bool(org_nodes), "definitional_sentence": definition,
    })
    out.note_metric("brand_name_inferred", brand.name)

    if effective > weak_max:
        return

    # Rule R1: risk is an inference, so a weak posture needs a second signal -- a name
    # that collides -- before it is called a risk. Alone it is weak disambiguation.
    check_id = ("G3.ENTITY.AMBIGUITY_RISK" if effective <= risk_max and amplifiers
                else "G3.ENTITY.WEAK_DISAMBIGUATION")
    missing = [label for label, ok, _ in points if not ok]
    breakdown = "; ".join(f"[{'x' if ok else ' '}] {label}: {detail}"
                          for label, ok, detail in points)

    refs = [metric_ref(home.url_hash, "disambiguation_posture_score", score,
                       weak_max + 1)]
    org_page = next((p for p, _n in org_nodes), None)
    if org_page is not None:
        node = next(n for p, n in org_nodes if p is org_page)
        refs.append(jsonpath_ref(org_page.url_hash, f"{node.json_path}.name",
                                 sdextract.scalar(node.get("name"))))

    out.add(Finding(
        check_id=check_id,
        title=(f"A machine has little to distinguish {brand.name or 'this brand'} from "
               f"namesakes" if check_id == "G3.ENTITY.AMBIGUITY_RISK" else
               f"{brand.name or 'The brand'}'s identity signals are only partly "
               f"established"),
        evidence=(
            f"Disambiguation posture scores {score} of 6 from on-site evidence only: "
            f"{breakdown}."
            + (f" Ambiguity amplifiers: {'; '.join(amplifiers)}, so the score is treated "
               f"as {effective}." if amplifiers else "")
            + " This measures what the site gives a machine to resolve the entity; it "
              "does not claim any specific namesake exists."
        ),
        mechanism=(
            "When several things share a name, a machine attaches facts to whichever "
            "entity its evidence points at. Stable identifiers, authoritative sameAs "
            "links, a definitional sentence and concrete modifiers such as category, "
            "location and founding year are what let it attach this brand's facts to "
            "this brand -- without them, a brand gets merged with a namesake or its "
            "description is synthesised from whatever is nearby."
        ),
        # A low posture score makes misattribution likely; it does not observe one.
        confidence="medium" if check_id == "G3.ENTITY.AMBIGUITY_RISK" else "high",
        signal_count=3,
        # A partly established identity is an improvement to make, not a defect: calibration
        # found it on 88% of well-known brands.
        fact_class="primary" if check_id == "G3.ENTITY.AMBIGUITY_RISK" else "enrichment",
        scope=Scope(level="site", pages_affected=len(pages), pages_sampled=len(pages),
                    example_urls=[home.url]),
        evidence_refs=refs,
        suggested_action=SuggestedAction(
            summary=f"Publish an explicit identity for the brand: {', '.join(missing)}.",
            steps=[
                "Emit one Organization JSON-LD node site-wide with a stable @id "
                "(https://<domain>/#organization), legal and common name, url, logo, "
                "foundingDate, address and description.",
                f"Add sameAs links to at least {sameas_needed} authoritative identifiers "
                f"that already exist for the company: a Wikidata item, the official "
                f"company registry entry, a LinkedIn company page, a Crunchbase profile.",
                (f"Put one plain sentence near the top of the homepage and about page: "
                 f"\"{brand.name or '<Brand>'} is a <category> for <audience> based in "
                 f"<location>.\" Mirror it in Organization.description."),
                "State the disambiguating facts as text on the about page: category, "
                "headquarters, founding year, parent company if any.",
            ],
            effort="low", owner_role="Brand / Content ops, with SEO",
            verification=("Re-run the audit and confirm the posture score is 5 or more; "
                          "validate the Organization node and confirm each sameAs URL "
                          "resolves to a page naming this company."),
            expected_effect=("Gives machines the anchors needed to attach the brand's "
                             "facts to the right entity."),
        ),
    ))


def check_name_consistency(context: Context, out: FindingsFile,
                           brand: entity.Brand) -> None:
    limit = int(context.threshold("entity_trust.name_variant_count"))
    clusters = brand.clusters
    if len(clusters) < limit:
        if brand.known:
            out.note_posture("brand_name_consistent", brand.name)
        return
    variants = brand.variant_summary()
    refs = []
    for cluster in clusters[:3]:
        surface = cluster[0]
        if surface.ref_kind == "jsonpath":
            refs.append(jsonpath_ref(surface.url_hash, surface.path, surface.value))
        elif surface.url_hash:
            refs.append(dom_ref(surface.url_hash, surface.selector or "title",
                                surface.value))
    home = context.homepage()
    if home is not None:
        refs.append(metric_ref(home.url_hash, "irreducible_name_variants",
                               len(clusters), limit - 1))
    pages = context.content_pages()
    out.add(Finding(
        check_id="G3.ENTITY.NAME_INCONSISTENCY",
        title=f"The brand is named {len(clusters)} different ways across the site",
        evidence=(
            f"{len(clusters)} name variants that do not reduce to one another were found "
            f"across naming surfaces: "
            + "; ".join(f"\"{v['value']}\" in {', '.join(v['surfaces'])} "
                        f"({v['occurrences']}x, e.g. {v['example_url']})"
                        for v in variants[:5])
            + ". Long and short forms of the same name (\"Acme\" and \"Acme Corporation\") "
              "and legal-suffix differences were merged before counting and are not "
              "part of this finding."
        ),
        mechanism=(
            "Each irreducible variant is a separate candidate entity string. Consistency "
            "is what lets a machine merge mentions into one entity; a variant that names "
            "something else entirely -- a sub-brand, a former name, an agency's own name "
            "leaking into a template -- actively attaches the brand's content to a "
            "different entity."
        ),
        confidence="high", signal_count=len(clusters), fact_class="secondary",
        scope=Scope(level="site", pages_affected=len(pages), pages_sampled=len(pages),
                    example_urls=sorted({v["example_url"] for v in variants if
                                         v["example_url"]})[:3]),
        evidence_refs=refs,
        suggested_action=SuggestedAction(
            summary=f"Standardise on one brand name everywhere the site names itself.",
            steps=[
                f"Choose the canonical form (the structured data currently favours "
                f"\"{brand.name}\") and a single permitted short form.",
                "Update og:site_name, the title-tag suffix, Organization.name, "
                "WebSite.name, logo alt text and the footer copyright line to use it.",
                "Remove variants that name a different entity: old names, sub-brands "
                "used site-wide, or a developer's name in the template.",
                "List legitimate alternate names in Organization.alternateName rather "
                "than using them interchangeably on surfaces.",
            ],
            effort="low", owner_role="Brand / Frontend engineering",
            verification="Re-run the audit and confirm at most two name variants remain, "
                         "both reducing to the canonical name.",
            expected_effect="Lets machines merge every mention of the brand into one "
                            "entity.",
        ),
    ))


def emit_offsite_requests(context: Context, path: Path) -> None:
    """Pass 1 of the two-pass protocol: declare wanted fetches, perform none."""
    cap = int(context.threshold("entity_trust.max_sameas_to_check"))
    urls: List[str] = []
    for page in context.content_pages():
        for node in page.sd.nodes:
            if _is_org(node):
                urls.extend(sdextract.all_scalars(node.get("sameAs")))
    wanted = []
    for url in sorted(set(urls)):
        normal = urlnorm.try_normalise(url)
        if normal is None:
            continue
        # Authoritative identifiers first: they are the ones that matter for identity.
        rank = 0 if any(h in normal.host for h in lexicons.AUTHORITATIVE_SAMEAS_HOSTS) else 1
        wanted.append((rank, normal.url))
    requests = [{"url": url, "method": "GET", "purpose": "offsite",
                 "reason": "resolve a declared sameAs identifier"}
                for _rank, url in sorted(wanted)[:cap]]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"skill": SKILL_ID, "requests": requests}, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def check_sameas(context: Context, out: FindingsFile, brand: entity.Brand) -> None:
    declared: Dict[str, str] = {}
    org_page = None
    for page in context.content_pages():
        for node in page.sd.nodes:
            if not _is_org(node):
                continue
            for url in sdextract.all_scalars(node.get("sameAs")):
                normal = urlnorm.try_normalise(url)
                if normal:
                    declared.setdefault(normal.url, url)
                    org_page = org_page or page
    if not declared:
        return
    offsite = {e.url: e for e in context.ledger.entries(purpose="offsite")}
    if not offsite:
        for check_id in ("G3.ENTITY.SAMEAS_BROKEN", "G3.ENTITY.SAMEAS_MISMATCH"):
            out.skip(check_id,
                     reason=f"{len(declared)} sameAs identifier(s) are declared, but "
                            f"off-site corroboration was not run (disabled, or dropped by "
                            f"the time budget), so their resolution was not checked",
                     remedy="run the orchestrator with --offsite on")
        return

    broken, mismatched, inconclusive = [], [], []
    for key, original in sorted(declared.items()):
        entry = offsite.get(key)
        if entry is None:
            continue
        if entry.status in (404, 410) or (entry.outcome == "blocked_policy"
                                           and "does not resolve" in entry.reason):
            broken.append((original, entry))
        elif entry.status in INCONCLUSIVE_STATUSES or entry.status is None \
                or (entry.status and entry.status >= 500):
            inconclusive.append((original, entry))
        elif entry.ok and brand.known and \
                any(h in key for h in lexicons.AUTHORITATIVE_SAMEAS_HOSTS):
            text = context.ledger.text(entry)[:20000]
            if not brand.mentioned_in(text):
                mismatched.append((original, entry))
    out.note_metric("sameas_inconclusive", len(inconclusive))
    if org_page is None:
        return

    if broken:
        _, example = broken[0]
        out.add(Finding(
            check_id="G3.ENTITY.SAMEAS_BROKEN",
            title="Declared sameAs identifiers do not resolve",
            evidence=(f"{len(broken)} of {len(declared)} sameAs URLs in the "
                      f"Organization markup returned not-found or do not resolve: "
                      + "; ".join(f"{u} -> {e.status or e.reason[:60]}"
                                  for u, e in broken[:3])
                      + f". {len(inconclusive)} other identifier(s) refused automated "
                        f"requests (401/403/429/999) and were treated as inconclusive, "
                        f"not broken."),
            mechanism=("A sameAs link is a claim that this entity is the same as the one "
                       "at that identifier. A dead identifier is a trust signal pointing "
                       "at nothing, and a consumer that follows it gains no "
                       "corroboration."),
            confidence="high", signal_count=2, fact_class="secondary",
            scope=Scope(level="site", pages_affected=len(broken),
                        pages_sampled=len(declared), example_urls=[u for u, _ in broken[:3]]),
            evidence_refs=[metric_ref(example.entry_id, "sameas_http_status",
                                      example.status or 0, 200)],
            suggested_action=SuggestedAction(
                summary="Replace or remove sameAs identifiers that no longer resolve.",
                steps=["Find the current URL of each profile or registry entry and "
                       "update the markup.",
                       "Remove identifiers for profiles that no longer exist.",
                       "Add a scheduled link check over sameAs URLs, since profiles move."],
                effort="trivial", owner_role="SEO / Brand",
                verification="Request each sameAs URL and confirm a 200 page naming the "
                             "company.",
                expected_effect="Restores working corroboration anchors.",
            ),
        ))
    if mismatched:
        _, example = mismatched[0]
        out.add(Finding(
            check_id="G3.ENTITY.SAMEAS_MISMATCH",
            title="A sameAs identifier resolves to a page that does not name this brand",
            evidence=(f"{len(mismatched)} authoritative sameAs URL(s) resolved with 200 "
                      f"but their content never mentions \"{brand.name}\": "
                      + ", ".join(u for u, _ in mismatched[:3]) + "."),
            mechanism=("An identifier that points at a differently named entity actively "
                       "merges this brand with another one -- the mistaken-identity "
                       "failure, introduced by the brand's own markup."),
            confidence="medium", signal_count=2, fact_class="primary",
            scope=Scope(level="site", pages_affected=len(mismatched),
                        pages_sampled=len(declared),
                        example_urls=[u for u, _ in mismatched[:3]]),
            evidence_refs=[metric_ref(example.entry_id, "brand_mentions_on_identifier",
                                      0, 1)],
            suggested_action=SuggestedAction(
                summary="Point sameAs only at identifiers for this exact entity.",
                steps=["Open each flagged identifier and confirm which entity it "
                       "describes.",
                       "Replace it with the identifier for this company, or remove it.",
                       "If the identifier is correct but outdated (a rename), update the "
                       "external record so both names are listed."],
                effort="low", owner_role="Brand / SEO",
                verification="Confirm every sameAs page names the company as it appears "
                             "in Organization.name or alternateName.",
                expected_effect="Stops the brand's own markup from merging it with "
                                "another entity.",
            ),
        ))


# ========================================================================================
# freshness
# ========================================================================================

def _date_sources(page: PageView) -> Dict[str, List[Tuple[str, date, Any]]]:
    """Every date the page states, separated into published-type and modified-type.

    Like is compared with like: a visible "updated" date legitimately differs from
    datePublished, and treating the two as competing sources is a false positive.
    """
    doc = page.doc
    sources: Dict[str, List[Tuple[str, date, Any]]] = {"published": [], "modified": []}
    for node in page.sd.nodes:
        for prop, kind in (("datePublished", "published"), ("dateModified", "modified")):
            raw = sdextract.scalar(node.get(prop))
            parsed = textnorm.parse_date(raw)
            if parsed:
                sources[kind].append((f"structured data {prop} ({raw})", parsed,
                                      jsonpath_ref(page.url_hash,
                                                   f"{node.json_path}.{prop}", raw)))
    for node in doc.soup.find_all("time"):
        raw = node.get("datetime") or doc.visible_text(node)
        parsed = textnorm.parse_date(raw)
        if not parsed:
            continue
        context_text = " ".join([node.get("itemprop") or "",
                                 " ".join(node.get("class") or []),
                                 doc.visible_text(node.parent) if node.parent else ""]).lower()
        kind = "modified" if re.search(r"updat|modif|revis", context_text) else "published"
        sources[kind].append((f"<time> element ({raw})", parsed,
                              dom_ref(page.url_hash, selector_for(node), raw)))
    head_text = doc.main_text()[:3000]
    for pattern, kind in ((_UPDATED_RE, "modified"), (_PUBLISHED_RE, "published")):
        match = pattern.search(head_text)
        if match:
            parsed = textnorm.parse_date(match.group(1))
            if parsed:
                sources[kind].append((f"visible text \"{match.group(0).strip()}\"", parsed,
                                      dom_ref(page.url_hash,
                                              doc.main_selector,
                                              match.group(0).strip())))
    return sources


def check_freshness(context: Context, out: FindingsFile, brand: entity.Brand) -> None:
    skew = int(context.threshold("entity_trust.date_skew_days"))
    pages = context.content_pages()
    total = len(pages)
    site_maps = sitemap.collect(context.ledger)
    lastmod_urls = {urlnorm.try_normalise(u).url for u, _ in site_maps["lastmods"]
                    if urlnorm.try_normalise(u)}

    articles = [p for p in pages if _is_article_page(p)]
    all_dates: List[date] = []
    undated: List[Tuple[str, Any]] = []
    contradictions: List[Tuple[str, Any]] = []

    for page in pages:
        sources = _date_sources(page)
        dates = [d for kind in sources.values() for _l, d, _r in kind]
        all_dates.extend(dates)

        if page in articles and not dates and not page.entry.header("last-modified") \
                and page.url not in lastmod_urls:
            undated.append((page.url, {
                "observed": "no datePublished or dateModified in structured data, no "
                            "<time> element, no visible published or updated date, no "
                            "Last-Modified header and no sitemap lastmod",
                "ref": metric_ref(page.url_hash, "date_signals_found", 0, 1),
            }))

        problems = []
        for kind in ("published", "modified"):
            values = sources[kind]
            if len(values) >= 2:
                earliest = min(values, key=lambda v: v[1])
                latest = max(values, key=lambda v: v[1])
                if (latest[1] - earliest[1]).days > skew:
                    problems.append((f"two {kind} dates disagree by "
                                     f"{(latest[1] - earliest[1]).days} days: "
                                     f"{earliest[0]} vs {latest[0]}", earliest, latest))
        if sources["published"] and sources["modified"]:
            first_pub = min(sources["published"], key=lambda v: v[1])
            first_mod = min(sources["modified"], key=lambda v: v[1])
            if (first_pub[1] - first_mod[1]).days > skew:
                problems.append((f"the modified date precedes the published date by "
                                 f"{(first_pub[1] - first_mod[1]).days} days: "
                                 f"{first_mod[0]} vs {first_pub[0]}", first_mod, first_pub))
        if problems:
            text, a, b = problems[0]
            contradictions.append((page.url, {"observed": text, "refs": [a[2], b[2]]}))

    min_sample = context.min_sample()
    if len(articles) >= min_sample and len(undated) * 2 >= len(articles):
        for group in analyzer.group_by_template(context, undated):
            payload = group["payloads"][0]
            analyzer.emit_group(
                context, out, "G3.FRESH.NO_DATE_SIGNAL", group, total=len(articles),
                title="Dated content publishes no date anywhere",
                evidence=(f"{analyzer.prefix(group)} are article-class pages carrying "
                          f"{payload['observed']}. Evergreen pages such as pricing, "
                          f"about and product pages were excluded, because they do not "
                          f"need a publication date."),
                mechanism=("Consumers use dates to decide whether to prefer a source and "
                           "whether a fact is still true. An article with no date cannot "
                           "win on recency and is discounted for time-sensitive "
                           "questions, however current it actually is."),
                action=SuggestedAction(
                    summary="Publish the publication and last-updated dates as text and "
                            "in structured data.",
                    steps=["Show \"Published <date>\" and, when content changes, "
                           "\"Updated <date>\" near the headline, in a <time datetime> "
                           "element.",
                           "Emit datePublished and dateModified in ISO-8601 in the "
                           "Article JSON-LD, from the same stored timestamps.",
                           "Advance dateModified only when the content materially "
                           "changes."],
                    effort="low", owner_role="Content ops / Frontend engineering",
                    verification="Fetch an article and confirm a visible date, a <time> "
                                 "element and matching JSON-LD dates.",
                    expected_effect="Makes recency establishable for every article.",
                ),
                refs=[payload["ref"]], fact_class="secondary",
            )

    for group in analyzer.group_by_template(context, contradictions):
        payload = group["payloads"][0]
        analyzer.emit_group(
            context, out, "G3.FRESH.DATE_CONTRADICTION", group, total=total,
            title="A page's own date signals contradict each other",
            evidence=(f"{analyzer.prefix(group)}: {payload['observed']} (allowed skew "
                      f"{skew} days). Published-type and modified-type dates were "
                      f"compared only with their own kind."),
            mechanism=("When a page's dates disagree, a consumer cannot tell which is "
                       "true and either picks one arbitrarily or distrusts all of them. "
                       "A falsely fresh signal is worse than none: once detected, it "
                       "discounts the site's freshness claims everywhere."),
            action=SuggestedAction(
                summary="Derive every displayed and marked-up date from one stored "
                        "timestamp per kind.",
                steps=["Find which date source is stale: usually a template default, a "
                       "copied page, or a hard-coded 'updated' line.",
                       "Render the visible dates, <time> elements and JSON-LD dates from "
                       "the same two fields: created and last substantively modified.",
                       "Add a build check that JSON-LD dates equal the rendered dates."],
                effort="low", owner_role="Content ops / Frontend engineering",
                verification="Fetch an affected page and confirm every published-type "
                             "and modified-type date agrees within a day.",
                expected_effect="Makes the page's freshness claim coherent and trusted.",
            ),
            refs=list(payload["refs"]), fact_class="secondary",
        )

    _check_lastmod_uniform(context, out, site_maps, all_dates)
    _check_stale(context, out, articles)
    _check_updated_without_change(context, out)


def _check_lastmod_uniform(context: Context, out: FindingsFile, site_maps: Dict[str, Any],
                           page_dates: Sequence[date]) -> None:
    """The freshness-lie detector: lastmod stamped at deploy time."""
    minimum = int(context.threshold("entity_trust.lastmod_uniform_count"))
    share_floor = float(context.threshold("entity_trust.lastmod_uniform_share"))
    lastmods = site_maps["lastmods"]
    if len(lastmods) < minimum:
        return
    raw_counts = Counter(value for _u, value in lastmods)
    day_counts = Counter(str(textnorm.parse_date(value)) for _u, value in lastmods
                         if textnorm.parse_date(value))
    top_raw, raw_n = raw_counts.most_common(1)[0]
    top_day, day_n = day_counts.most_common(1)[0] if day_counts else ("", 0)
    share = max(raw_n, day_n) / len(lastmods)
    out.note_metric("sitemap_lastmod_top_share", round(share, 3))
    if share <= share_floor:
        return

    span_days = (max(page_dates) - min(page_dates)).days if len(page_dates) >= 2 else 0
    entry = site_maps["entries"][0]
    refs = [metric_ref(entry.entry_id, "lastmod_single_day_share", round(share, 3),
                       share_floor)]
    if span_days < 365:
        # Rule R1: uniform lastmod alone is normal on a new or statically built site.
        out.observe(
            "G3.FRESH.SITEMAP_LASTMOD_UNIFORM",
            "Sitemap lastmod values are nearly all identical",
            (f"{max(raw_n, day_n)} of {len(lastmods)} sitemap lastmod values fall on "
             f"{top_day or top_raw}. This is expected on a new site or a first static "
             f"build, and the crawled pages' own dates span only {span_days} days, so "
             f"there is no contradicting evidence and this is not reported as a finding."),
            evidence_refs=refs, confidence="medium",
        )
        return
    out.add(Finding(
        check_id="G3.FRESH.SITEMAP_LASTMOD_UNIFORM",
        title="Sitemap lastmod is stamped at deploy time, not when content changed",
        evidence=(f"{max(raw_n, day_n)} of {len(lastmods)} sitemap lastmod values "
                  f"({share:.0%}) fall on the same day ({top_day or top_raw}), while the "
                  f"crawled pages' own published and updated dates span {span_days} "
                  f"days. Content that visibly differs in age by years is all declared "
                  f"modified on one day."),
        mechanism=("lastmod is how a sitemap tells crawlers what changed and should be "
                   "re-fetched. Stamping every URL at build time makes the signal "
                   "worthless for prioritisation, and teaches crawlers to discount the "
                   "site's freshness claims -- including the genuine ones."),
        confidence="high", signal_count=2, fact_class="secondary",
        scope=Scope(level="site", pages_affected=max(raw_n, day_n),
                    pages_sampled=len(lastmods),
                    example_urls=[u for u, v in lastmods if v == top_raw][:3]),
        evidence_refs=refs,
        suggested_action=SuggestedAction(
            summary="Emit lastmod from each document's content-change time, or omit it.",
            steps=["Generate lastmod from the stored modification timestamp of each "
                   "document's content, not from the build or deploy time.",
                   "Exclude template-only or navigation changes from that timestamp.",
                   "If per-document times are not available, remove lastmod entirely: an "
                   "absent signal is better than a false one."],
            effort="low", owner_role="Backend engineering / SEO",
            verification="Regenerate the sitemap and confirm lastmod values vary and "
                         "match the pages' own updated dates.",
            expected_effect="Restores lastmod as a trustworthy re-crawl signal.",
        ),
    ))


def _check_stale(context: Context, out: FindingsFile, articles: Sequence[PageView]) -> None:
    now = _now(context)
    if now is None:
        out.skip("G3.FRESH.STALE_CONTENT_MARKERS",
                 reason="the ledger carries no audit reference date, so staleness cannot "
                        "be judged deterministically")
        return
    lag = int(context.threshold("entity_trust.copyright_lag_years"))
    stale_months = int(context.threshold("entity_trust.stale_content_months"))
    needed = int(context.threshold("false_positive_control.min_signals_for_inference"))
    pages = context.content_pages()
    markers: List[Tuple[str, Any]] = []

    years = entity.copyright_years(pages)
    if years:
        latest, page = max(years, key=lambda y: y[0])
        if now.year - latest >= lag:
            markers.append((f"the newest footer copyright year is {latest}, "
                            f"{now.year - latest} years before the audit date",
                            metric_ref(page.url_hash, "copyright_year", latest,
                                       now.year - lag + 1)))
    if context.site_profile.detected_type == "publisher" and articles:
        newest = None
        newest_page = None
        for page in articles:
            for kind in _date_sources(page).values():
                for _label, value, _ref in kind:
                    if newest is None or value > newest:
                        newest, newest_page = value, page
        if newest is not None:
            months = (now.year - newest.year) * 12 + (now.month - newest.month)
            if months > stale_months:
                markers.append((f"the newest dated article is from {newest}, {months} "
                                f"months before the audit date",
                                metric_ref(newest_page.url_hash, "months_since_newest_article",
                                           months, stale_months)))
    soon = [p for p in pages if _COMING_SOON_RE.search(p.doc.main_text())]
    if soon:
        markers.append((f"\"coming soon\" appears on {len(soon)} content page(s)",
                        dom_ref(soon[0].url_hash, soon[0].doc.main_selector, "coming soon")))
    for page in pages:
        match = _FUTURE_EVENT_RE.search(page.doc.main_text())
        if match and int(match.group(1)) < now.year:
            markers.append((f"future-tense wording refers to a past year: "
                            f"\"{textnorm.truncate(match.group(0), 90)}\"",
                            dom_ref(page.url_hash, page.doc.main_selector, match.group(0))))
            break

    if len(markers) < needed:
        if markers:
            out.note_posture("single_staleness_marker", markers[0][0])
        return
    home = context.homepage() or pages[0]
    out.add(Finding(
        check_id="G3.FRESH.STALE_CONTENT_MARKERS",
        title="Several independent signs indicate the site is not maintained",
        evidence=(f"{len(markers)} independent staleness markers were found: "
                  + "; ".join(m for m, _r in markers)
                  + f". A stale copyright year alone is never reported; at least {needed} "
                    f"independent markers are required."),
        mechanism=("Consumers weigh whether a source is current before repeating its "
                   "facts. Visible signs of neglect lower the credibility of every fact "
                   "on the site, including the ones that are still accurate."),
        confidence="high" if len(markers) >= 3 else "medium",
        signal_count=len(markers), fact_class="secondary",
        scope=Scope(level="site", pages_affected=len(pages), pages_sampled=len(pages),
                    example_urls=[home.url]),
        evidence_refs=[ref for _m, ref in markers],
        suggested_action=SuggestedAction(
            summary="Remove visible staleness and put a maintenance routine behind it.",
            steps=["Generate the footer copyright year from the current date.",
                   "Replace or remove 'coming soon' and past-dated future-tense copy.",
                   "Publish or update content on a stated cadence, and show updated "
                   "dates when you do.",
                   "Schedule a quarterly review of evergreen pages."],
            effort="low", owner_role="Content ops",
            verification="Re-run the audit and confirm fewer than two staleness markers "
                         "remain.",
            expected_effect="Restores the site's credibility as a current source.",
        ),
    ))


def _check_updated_without_change(context: Context, out: FindingsFile) -> None:
    prior_path = context.ledger.meta.get("prior_ledger")
    if not prior_path or not Path(str(prior_path)).exists():
        out.skip("G3.FRESH.UPDATED_WITHOUT_CHANGE",
                 reason="no prior ledger was supplied, so dateModified cannot be compared "
                        "across runs",
                 remedy="run the orchestrator with --prior-ledger <previous run's ledger>")
        return
    from auditkit.ledger import Ledger
    prior = Ledger.open(Path(str(prior_path)))
    bumped = []
    for page in context.content_pages():
        before = prior.get(page.url)
        if before is None or not before.content_hash:
            continue
        prior_doc = Document(prior.text(before))
        old_doc_text = textnorm.comparison_key(prior_doc.main_text())
        new_text = textnorm.comparison_key(page.doc.main_text())
        if old_doc_text != new_text:
            continue
        new_mod = [d for _l, d, _r in _date_sources(page)["modified"]]
        old_sd = sdextract.extract(prior_doc)
        old_mod = [textnorm.parse_date(sdextract.scalar(n.get("dateModified")))
                   for n in old_sd.nodes if n.get("dateModified")]
        old_mod = [d for d in old_mod if d]
        if new_mod and old_mod and max(new_mod) > max(old_mod):
            bumped.append((page, max(old_mod), max(new_mod)))
    if not bumped:
        return
    page, old, new = bumped[0]
    out.observe(
        "G3.FRESH.UPDATED_WITHOUT_CHANGE",
        "dateModified advanced while the content did not change",
        (f"{len(bumped)} page(s) advanced dateModified between runs with an identical "
         f"main-content text. Example: {page.url} moved from {old} to {new}. Refreshing "
         f"dates without changing content trains consumers to discount the signal."),
        evidence_refs=[metric_ref(page.url_hash, "date_modified_advanced_days",
                                  (new - old).days, 0)],
        confidence="medium",
    )


# ========================================================================================
# corroboration surface
# ========================================================================================

_LOCALE_SEGMENT_RE = re.compile(r"^/[a-z]{2}(?:[-_][a-z]{2})?/", re.IGNORECASE)


def _entity_home_page(about: Sequence[PageView], home: Optional[PageView]) -> PageView:
    """The about page in the homepage's language and default locale, where there is a choice."""
    home_lang = detect_language(home.doc) if home is not None else ""

    def rank(page: PageView) -> Tuple[int, int, int, str]:
        lang = detect_language(page.doc) or home_lang
        return (0 if lang == home_lang else 1,
                1 if _LOCALE_SEGMENT_RE.match(page.path) else 0, len(page.path), page.url)
    return sorted(about, key=rank)[0]


def _unjudgeable_about_page(context: Context, page: PageView) -> str:
    """Why an about page cannot be judged for facts, or "" when it can."""
    words = textnorm.word_count(page.doc.main_text())
    floor = int(context.threshold("entity_trust.about_page_min_words"))
    if words < floor:
        return (f"carries only {words} words of main text (threshold {floor}), as a "
                f"client-rendered shell or a placeholder does")
    lang = detect_language(page.doc)
    if lang and lang not in lexicons.SUPPORTED_LANGUAGES:
        return f"is in a language ({lang}) the fact patterns do not cover"
    return ""


_ABOUT_LINK_RE = re.compile(
    r"^\s*(?:about(?:\s+(?:us|the\s+[\w&.\-]+(?:\s+[\w&.\-]+)?|[\w&.\-]+))?|our story|"
    r"who we are|company|corporate)\s*$", re.IGNORECASE)


def _about_page_elsewhere(context: Context, pages: Sequence[PageView],
                          out: FindingsFile) -> bool:
    """Record, and return True, when the site links to its about page on another host.

    Large organisations often keep corporate information on a sister domain. "No page
    states the canonical facts" would then be false; what is true is that the page was
    out of this audit's scope.
    """
    site_host = context.site_host()
    if not site_host:
        return False
    elsewhere = sorted({
        target.url for page in pages for link in page.doc.links()
        if _ABOUT_LINK_RE.match(link.text or "")
        and (target := urlnorm.try_normalise(link.href, base=page.url)) is not None
        and not urlnorm.same_site(target.host, site_host)
    })
    if not elsewhere:
        return False
    out.observe(
        "G3.CORROB.NO_CITABLE_PRIMARY_SOURCE",
        "The brand's about page is on another host and was not examined",
        (f"No about or company page exists on this host, but the site links to "
         f"{elsewhere[0]} as its about page. That host is outside this audit's scope, so "
         f"whether it states the canonical facts was not measured."),
        confidence="medium",
    )
    return True


def check_corroboration(context: Context, out: FindingsFile, brand: entity.Brand) -> None:
    pages = context.content_pages()
    if not pages:
        return
    needed = int(context.threshold("entity_trust.about_fact_markers_required"))
    about = [p for p in pages if p.has_path_class("about")]
    home = context.homepage() or pages[0]

    def fact_markers(text: str, page: PageView) -> List[str]:
        found = []
        if _FOUNDED_RE.search(text) or any(n.get("foundingDate") for n in page.sd.nodes):
            found.append("founding date")
        if _LOCATION_RE.search(text) or page.sd.of_type("PostalAddress") \
                or page.doc.soup.find("address"):
            found.append("location")
        named_founders = [m.group("name") or m.group("agent")
                          for m in _FOUNDER_NAME_RE.finditer(text)]
        if _LEADERSHIP_RE.search(text) or any(
                textnorm.normalise_brand_name(name) != brand.normalised
                for name in named_founders):
            found.append("leadership")
        if _HEADCOUNT_RE.search(text):
            found.append("headcount")
        if entity.find_definitional_sentence([text], brand):
            found.append("category definition")
        return found

    # --- NO_CITABLE_PRIMARY_SOURCE --------------------------------------------------------
    attempted_about = [e for e in context.ledger.entries(purpose="page")
                       if (n := urlnorm.try_normalise(e.url)) is not None
                       and "about" in urlnorm.path_classes(n)]
    if about:
        page = _entity_home_page(about, home)
        markers = fact_markers(page.doc.main_text(), page)
        out.note_posture("about_page_fact_markers", markers)
        unjudgeable = _unjudgeable_about_page(context, page)
        if unjudgeable and len(markers) < needed:
            out.observe(
                "G3.CORROB.NO_CITABLE_PRIMARY_SOURCE",
                "The about page could not be judged for canonical facts",
                f"{page.url} {unjudgeable}, so whether it states the canonical facts was "
                f"not assessed.",
                confidence="medium",
            )
        elif len(markers) < needed:
            out.add(Finding(
                check_id="G3.CORROB.NO_CITABLE_PRIMARY_SOURCE",
                title="The about page does not state the brand's canonical facts",
                evidence=(f"The entity-home page {page.url} states "
                          f"{len(markers)} of the canonical fact types "
                          f"({', '.join(markers) or 'none'}); at least {needed} of "
                          f"founding date, location, leadership, headcount and a "
                          f"category definition are needed for a citable primary "
                          f"source."),
                mechanism=("Corroboration across the web requires something consistent to "
                           "corroborate. When the brand never states its own canonical "
                           "facts plainly, third parties paraphrase from marketing copy "
                           "and each other, and the descriptions assistants find "
                           "disagree."),
                confidence="high", signal_count=2, fact_class="secondary",
                scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                            example_urls=[page.url]),
                evidence_refs=[metric_ref(page.url_hash, "canonical_fact_types_stated",
                                          len(markers), needed)],
                suggested_action=_fact_sheet_action(brand),
            ))
    elif attempted_about and not _about_page_elsewhere(context, pages, out):
        example = attempted_about[0]
        out.add(Finding(
            check_id="G3.CORROB.NO_CITABLE_PRIMARY_SOURCE",
            title="The site has no page stating the brand's canonical facts",
            evidence=(f"No entity-home page was found: conventional about-page paths "
                      f"were requested ({', '.join(e.url for e in attempted_about[:3])}) "
                      f"and none returned a page, and no fetched page is an about or "
                      f"company page."),
            mechanism=("Without a stable primary source for founding, location, "
                       "leadership and category, third parties have nothing consistent to "
                       "repeat, so corroboration cannot form around the brand's own "
                       "description."),
            confidence="high", signal_count=2, fact_class="secondary",
            scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                        example_urls=[home.url]),
            evidence_refs=[metric_ref(example.entry_id, "entity_home_pages_found", 0, 1)],
            suggested_action=_fact_sheet_action(brand),
        ))

    # --- NO_PRESS_KIT --------------------------------------------------------------------
    if context.site_profile.detected_type not in ("local_business", "ambiguous") \
            and len(pages) >= context.min_sample():
        linked = any(_PRESS_PATH_RE.search(urlnorm.try_normalise(l.href, base=p.url).path)
                     for p in pages for l in p.doc.links()
                     if urlnorm.try_normalise(l.href, base=p.url) is not None)
        fetched = any(p.normal is not None and _PRESS_PATH_RE.search(p.path) for p in pages)
        if not linked and not fetched:
            out.add(Finding(
                check_id="G3.CORROB.NO_PRESS_KIT",
                title="No press or media page offers boilerplate and brand assets",
                evidence=(f"None of the {len(pages)} fetched pages is a press, media or "
                          f"newsroom page, and none of them links to one."),
                mechanism=("Journalists, directories and aggregators reproduce a supplied "
                           "boilerplate verbatim, but paraphrase when there is none. "
                           "Verbatim reproduction across independent sources is exactly "
                           "the agreement consumers treat as corroboration."),
                confidence="high", signal_count=2, fact_class="enrichment",
                scope=Scope(level="site", pages_affected=len(pages),
                            pages_sampled=len(pages), example_urls=[home.url]),
                evidence_refs=[metric_ref(home.url_hash, "press_pages_found", 0, 1)],
                suggested_action=SuggestedAction(
                    summary="Publish a press page with a boilerplate paragraph, fact "
                            "sheet and logo files.",
                    steps=["Create /press with a 50-100 word company boilerplate, a fact "
                           "list, leadership names, a media contact and downloadable "
                           "logos.",
                           "Link it from the footer.",
                           "Keep the boilerplate identical to Organization.description."],
                    effort="low", owner_role="Communications / Brand",
                    verification="Confirm /press returns 200, is linked from the footer, "
                                 "and its boilerplate matches the markup description.",
                    expected_effect="Gives third parties a verbatim description to repeat.",
                ),
            ))

    # --- FACTS_ONLY_IN_MARKETING_COPY ------------------------------------------------------
    if about:
        page = about[0]
        doc = page.doc
        listed = " ".join(doc.visible_text(n) for n in doc.tables() + doc.lists())
        prose = [s for s in doc.paragraphs()]
        fact_prose = [s for s in prose if fact_markers(s, page)]
        fluffy = [s for s in fact_prose if lexicons.is_fluff_sentence(s.lower())]
        if fact_prose and fluffy and not fact_markers(listed, page):
            out.add(Finding(
                check_id="G3.CORROB.FACTS_ONLY_IN_MARKETING_COPY",
                title="Canonical facts appear only inside promotional prose",
                evidence=(f"On {page.url}, {len(fact_prose)} paragraph(s) carry canonical "
                          f"facts, {len(fluffy)} of them wrapped in promotional language "
                          f"(e.g. \"{textnorm.truncate(fluffy[0], 140)}\"), and no table, "
                          f"list or definition list states those facts plainly."),
                mechanism=("A fact embedded in a superlative sentence has to be extracted "
                           "from it. Extraction loses or distorts facts, and the "
                           "surrounding claims are unverifiable, so the sentence is a poor "
                           "candidate to quote."),
                confidence="medium", signal_count=2, fact_class="secondary",
                scope=Scope(level="page", pages_affected=1, pages_sampled=1,
                            example_urls=[page.url]),
                evidence_refs=[metric_ref(page.url_hash, "fact_paragraphs_with_fluff",
                                          len(fluffy), 0),
                               dom_ref(page.url_hash, doc.main_selector, fluffy[0])],
                suggested_action=SuggestedAction(
                    summary="Add a plain 'at a glance' fact list to the about page.",
                    steps=["Add a definition list or table: founded, headquarters, "
                           "leadership, employees, category, markets.",
                           "Keep the narrative, but make every fact in it also appear "
                           "in the list."],
                    effort="trivial", owner_role="Content",
                    verification="Confirm each canonical fact appears in a <dl>, <table> "
                                 "or list in the raw HTML.",
                    expected_effect="Makes the brand's facts liftable verbatim.",
                ),
            ))

    # --- NO_AUTHOR_IDENTITY ----------------------------------------------------------------
    articles = [p for p in pages if _is_article_page(p)]
    if len(articles) >= context.min_sample():
        weak = []
        for page in articles:
            authors = []
            for node in page.sd.of_type("Article", "NewsArticle", "BlogPosting",
                                        "TechArticle"):
                value = node.get("author")
                if value is None:
                    continue
                values = value if isinstance(value, list) else [value]
                authors.extend(values)
            linked_profile = bool(page.doc.select("a[rel=author], [class*=author] a[href]"))
            strong = any(
                isinstance(a, (dict, sdextract.SDNode))
                and (a.get("url") or a.get("@id") or a.get("sameAs"))
                for a in authors)
            if not strong and not linked_profile:
                weak.append((page.url, {
                    "observed": ("the author is a bare name string or absent, with no "
                                 "Person entity carrying a url or sameAs and no linked "
                                 "author profile page"),
                    "ref": metric_ref(page.url_hash, "author_entities_with_profile", 0, 1),
                }))
        if len(weak) * 2 >= len(articles):
            for group in analyzer.group_by_template(context, weak):
                analyzer.emit_group(
                    context, out, "G3.CORROB.NO_AUTHOR_IDENTITY", group,
                    total=len(articles),
                    title="Articles carry no verifiable author identity",
                    evidence=f"{analyzer.prefix(group)}: {group['payloads'][0]['observed']}.",
                    mechanism=("Expertise is judged from who wrote something. A bare "
                               "byline cannot be connected to anything else the author "
                               "has written or to their credentials, so it adds no "
                               "credibility."),
                    action=SuggestedAction(
                        summary="Publish author profile pages and reference them as "
                                "Person entities.",
                        steps=["Create a profile page per author with bio, role, and "
                               "links to their external profiles.",
                               "Emit author as a Person with name, url (the profile page) "
                               "and sameAs.",
                               "Link the visible byline to the profile page."],
                        effort="low", owner_role="Content ops / Frontend engineering",
                        verification="Validate an article and confirm author is a Person "
                                     "with a resolvable url.",
                        expected_effect="Makes authorship and expertise verifiable.",
                    ),
                    refs=[group["payloads"][0]["ref"]], fact_class="secondary",
                )

    # --- NO_EXTERNAL_REFERENCES ------------------------------------------------------------
    min_words = int(context.threshold("entity_trust.external_reference_min_words"))
    site_host = context.site_host()
    candidates = [p for p in pages if (_is_article_page(p) or p.has_path_class("docs"))
                  and textnorm.word_count(p.doc.main_text()) >= min_words
                  and _NUMERIC_CLAIM_RE.search(p.doc.main_text())]
    if len(candidates) >= context.min_sample():
        uncited = []
        for page in candidates:
            outbound = [l for l in page.doc.links() if l.in_main and
                        (n := urlnorm.try_normalise(l.href, base=page.url)) is not None
                        and site_host and not urlnorm.same_site(n.host, site_host)]
            if not outbound:
                claims = len(_NUMERIC_CLAIM_RE.findall(page.doc.main_text()))
                uncited.append((page.url, {
                    "observed": (f"{claims} quantified claim(s) in the body and no "
                                 f"outbound link to any external source"),
                    "ref": metric_ref(page.url_hash, "outbound_citations", 0, 1),
                }))
        if len(uncited) * 2 >= len(candidates):
            for group in analyzer.group_by_template(context, uncited):
                analyzer.emit_group(
                    context, out, "G3.CORROB.NO_EXTERNAL_REFERENCES", group,
                    total=len(candidates),
                    title="Quantified claims are made without citing any source",
                    evidence=f"{analyzer.prefix(group)}: {group['payloads'][0]['observed']}.",
                    mechanism=("A claim with a source can be checked; a claim without one "
                               "has to be taken on trust. Linking to evidence places the "
                               "content inside a web of corroboration instead of standing "
                               "alone."),
                    action=SuggestedAction(
                        summary="Cite the source of each quantified claim.",
                        steps=["Link each statistic to its original study, dataset or "
                               "report.",
                               "Where the data is your own, link to a methodology page."],
                        effort="low", owner_role="Content",
                        verification="Confirm each numeric claim has an adjacent outbound "
                                     "or methodology link.",
                        expected_effect="Makes the content's claims verifiable.",
                    ),
                    refs=[group["payloads"][0]["ref"]], fact_class="enrichment",
                )


def _fact_sheet_action(brand: entity.Brand) -> SuggestedAction:
    return SuggestedAction(
        summary="Publish a canonical fact sheet on the about page, as text and "
                "Organization markup.",
        steps=[
            (f"State plainly on /about: legal name and common name variants, one-sentence "
             f"definition (\"{brand.name or '<Brand>'} is a ...\"), founding year, "
             f"headquarters, leadership, headcount band, category and markets served."),
            "Present them as a definition list or table, not only inside narrative "
            "paragraphs.",
            "Mirror each fact in the Organization JSON-LD with a stable @id.",
            "Link the page from the footer of every page.",
        ],
        effort="low", owner_role="Brand / Content ops",
        verification="Fetch /about with JavaScript disabled and confirm every canonical "
                     "fact appears as text and in the Organization node.",
        expected_effect="Gives third parties one consistent source to repeat, which is "
                        "what corroboration forms around.",
    )


# ========================================================================================
# entry point
# ========================================================================================

def detect(context: Context, out: FindingsFile) -> None:
    pages = context.content_pages()
    brand = entity.infer_brand(pages, context.site_host())
    out.note_metric("pages_analyzed", len(pages))
    out.note_metric("brand_name_confidence", brand.confidence)

    if context.args.emit_requests:
        emit_offsite_requests(context, context.args.emit_requests)
        out.note_metric("pass", "1 (off-site requests declared, no analysis)")
        return

    check_posture(context, out, brand)
    check_name_consistency(context, out, brand)
    check_sameas(context, out, brand)
    check_freshness(context, out, brand)
    check_corroboration(context, out, brand)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return analyzer.run(
        SKILL_ID,
        "Gates G2-G3 (IDENTITY & TIME): entity disambiguation posture, brand-name "
        "consistency, sameAs corroboration via the two-pass off-site protocol, date "
        "coherence including build-time lastmod stamping, and the corroboration surface.",
        detect, argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
