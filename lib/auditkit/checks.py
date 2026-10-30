"""The closed check registry.

Every finding the marketplace can emit must name a ``check_id`` that exists here.
``auditkit.finding.Finding`` refuses to construct a finding for an unknown id, and
``verify_report.py`` re-validates the whole report against this registry before the
report is written. That is what makes "scripts author findings, the model never does"
an enforced property rather than a convention.

Check id grammar
----------------
``<GATE>.<AREA>.<NAME>`` -- e.g. ``G0.ROBOTS.AI_AGENT_BLOCKED``.

Ids are stable across versions. They are the join key between findings, the reference
files that document each check's mechanism, the test fixtures, and each skill's
"Checks performed" table. The ``F-###`` ids in a report are presentation only.

Registry fields
---------------
gate        G0 | G1 | G2 | G3 | GE  -- which gate the *cause* lives at, not the symptom
skill       the skill id that owns the check (one owner per check, always)
category    grouping used in the report and the human-readable audit.md
fact        default fact class for severity derivation: primary | secondary | enrichment
            (a detector may override per finding when it knows better)
reference   path, relative to the marketplace root, of the file documenting mechanism
            and false-positive guards for this check
summary     one line: what firing means. Used to generate the skills' check tables.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

GATES = ("G0", "G1", "G2", "G3", "GE")

# Fact classes feed severity derivation (see auditkit.severity).
FACT_PRIMARY = "primary"
FACT_SECONDARY = "secondary"
FACT_ENRICHMENT = "enrichment"

_ACCESS_REF = "skills/access-policy-audit/references"
_RENDER_REF = "skills/render-extraction-audit/references"
_SD_REF = "skills/structured-data-audit/references"
_ENTITY_REF = "skills/entity-trust-audit/references"
_ANSW_REF = "skills/answerability-audit/references"
_ENGAGE_REF = "skills/engagement-audit/references"


def _c(gate: str, skill: str, category: str, fact: str, reference: str, summary: str) -> Dict[str, str]:
    return {
        "gate": gate,
        "skill": skill,
        "category": category,
        "fact": fact,
        "reference": reference,
        "summary": summary,
    }


# --------------------------------------------------------------------------------------
# G0 -- REACH: permission and reachability.  Owner: access-policy-audit
# --------------------------------------------------------------------------------------
_G0 = {
    "G0.ROBOTS.AI_AGENT_BLOCKED": _c(
        "G0", "access-policy-audit", "robots", FACT_PRIMARY,
        f"{_ACCESS_REF}/ai-crawler-agents.md",
        "robots.txt disallows a retrieval/search crawler on the site's primary content "
        "paths, removing the site from the pool an assistant can cite.",
    ),
    "G0.ROBOTS.TRAINING_AGENT_BLOCKED": _c(
        "G0", "access-policy-audit", "robots", FACT_ENRICHMENT,
        f"{_ACCESS_REF}/ai-crawler-agents.md",
        "robots.txt disallows a training-corpus crawler. Recorded because owners often "
        "believe this affects citation; it does not. Informational, never a citation blocker.",
    ),
    "G0.ROBOTS.AUDIT_PATH_DISALLOWED": _c(
        "G0", "access-policy-audit", "robots", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "robots.txt disallows this audit's own declared user-agent on content paths, so "
        "those paths could not be examined -- and generic well-behaved crawlers are "
        "likely treated the same way.",
    ),
    "G0.ROBOTS.UNAVAILABLE": _c(
        "G0", "access-policy-audit", "robots", FACT_SECONDARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "robots.txt returned 5xx or timed out. Many crawlers back off entirely rather "
        "than assume permission.",
    ),
    "G0.DIRECTIVE.NOINDEX_HEADER": _c(
        "G0", "access-policy-audit", "directives", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "An X-Robots-Tag: noindex HTTP header removes the page from indexes. Invisible "
        "in page source, so it survives review for years.",
    ),
    "G0.DIRECTIVE.NOINDEX_META": _c(
        "G0", "access-policy-audit", "directives", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "A robots meta tag carries noindex on a content page -- frequently a staging leftover.",
    ),
    "G0.DIRECTIVE.NOSNIPPET": _c(
        "G0", "access-policy-audit", "directives", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "nosnippet explicitly forbids quoting the page. It can rank and still never be "
        "excerpted -- a direct answer to 'why are we never quoted'.",
    ),
    "G0.DIRECTIVE.MAX_SNIPPET_ZERO": _c(
        "G0", "access-policy-audit", "directives", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "max-snippet:0 forbids quoting numerically, with the same effect as nosnippet.",
    ),
    "G0.DIRECTIVE.DATA_NOSNIPPET": _c(
        "G0", "access-policy-audit", "directives", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "A data-nosnippet attribute wraps primary facts, suppressing exactly the "
        "sentence an assistant would otherwise quote.",
    ),
    "G0.DIRECTIVE.NOARCHIVE_NOCACHE": _c(
        "G0", "access-policy-audit", "directives", FACT_SECONDARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "noarchive keeps content out of Copilot answers and nocache limits Copilot to the "
        "URL, title and snippet, while the page stays indexed in search.",
    ),
    "G0.DIRECTIVE.CONFLICT": _c(
        "G0", "access-policy-audit", "directives", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "HTTP header and meta robots directives disagree. The most restrictive wins, "
        "which is usually not what the author intended.",
    ),
    "G0.GATING.BOT_STATUS_DIVERGENCE": _c(
        "G0", "access-policy-audit", "gating", FACT_PRIMARY,
        f"{_ACCESS_REF}/gating-patterns.md",
        "The same URL is served to a generic agent but refused or challenged when "
        "requested by an honestly declared AI crawler.",
    ),
    "G0.GATING.RATE_LIMITED": _c(
        "G0", "access-policy-audit", "gating", FACT_SECONDARY,
        f"{_ACCESS_REF}/gating-patterns.md",
        "The host returned 429/503 repeatedly. Aggressive rate limiting of well-behaved "
        "crawlers suppresses indexing without blocking anything explicitly.",
    ),
    "G0.GATING.NON_BROWSER_REFUSED": _c(
        "G0", "access-policy-audit", "gating", FACT_PRIMARY,
        f"{_ACCESS_REF}/gating-patterns.md",
        "Content pages are refused or challenged (401/403/405/406/429/503) for an honestly "
        "identified non-browser client, so crawlers that cannot run a browser challenge "
        "receive no content unless the CDN verifies and allowlists them.",
    ),
    "G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT": _c(
        "G0", "access-policy-audit", "gating", FACT_PRIMARY,
        f"{_ACCESS_REF}/gating-patterns.md",
        "A page advertised in the public sitemap requires authentication to read.",
    ),
    "G0.GATING.PAYWALL_UNMARKED": _c(
        "G0", "access-policy-audit", "gating", FACT_PRIMARY,
        f"{_ACCESS_REF}/gating-patterns.md",
        "Content is truncated behind a subscription gate without isAccessibleForFree / "
        "hasPart markup, so the whole page reads as thin or cloaked instead of partly free.",
    ),
    "G0.HTTP.REDIRECT_CHAIN": _c(
        "G0", "access-policy-audit", "http", FACT_SECONDARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "A URL redirects more than twice, or a chain terminates in an error status. "
        "Fetchers with low hop budgets give up.",
    ),
    "G0.HTTP.MIXED_CANONICAL_HOST": _c(
        "G0", "access-policy-audit", "http", FACT_PRIMARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "http/https or www/non-www variants all answer 200 without redirecting to one "
        "preferred host, splitting citation authority across duplicate URLs.",
    ),
    "G0.HTTP.SOFT_404": _c(
        "G0", "access-policy-audit", "http", FACT_SECONDARY,
        f"{_ACCESS_REF}/gating-patterns.md",
        "A 200 status is returned for a page whose body says the content does not exist, "
        "so error pages accumulate in indexes as real content.",
    ),
    "G0.HTTP.SLOW_TTFB": _c(
        "G0", "access-policy-audit", "http", FACT_ENRICHMENT,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "Time to first byte at the 75th percentile is over budget; slow hosts get "
        "crawled less often and time out for live fetchers.",
    ),
    "G0.SITEMAP.MISSING": _c(
        "G0", "access-policy-audit", "sitemap", FACT_SECONDARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "No XML sitemap was found at any conventional location or in robots.txt.",
    ),
    "G0.SITEMAP.NOT_IN_ROBOTS": _c(
        "G0", "access-policy-audit", "sitemap", FACT_ENRICHMENT,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "A sitemap exists but is not declared in robots.txt, so a crawler has to guess "
        "its location.",
    ),
    "G0.SITEMAP.INVALID_XML": _c(
        "G0", "access-policy-audit", "sitemap", FACT_SECONDARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "The sitemap does not parse as XML, or is not a urlset/sitemapindex, so it is "
        "discarded silently.",
    ),
    "G0.SITEMAP.URLS_BROKEN": _c(
        "G0", "access-policy-audit", "sitemap", FACT_SECONDARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "A sampled share of sitemap URLs do not resolve, which devalues the whole file "
        "as a discovery signal.",
    ),
    "G0.SITEMAP.STALE_OR_TRUNCATED": _c(
        "G0", "access-policy-audit", "sitemap", FACT_SECONDARY,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "The sitemap omits URL classes that are linked and reachable on the site, or is "
        "truncated at a round limit.",
    ),
    "G0.SITEMAP.NO_MACHINE_FEED": _c(
        "G0", "access-policy-audit", "sitemap", FACT_ENRICHMENT,
        f"{_ACCESS_REF}/robots-and-directives.md",
        "A publisher-profile site offers no RSS/Atom/JSON feed, so it has no cheap "
        "machine-readable freshness channel.",
    ),
}

# --------------------------------------------------------------------------------------
# G1 -- READ: does the text exist for a machine?  Owner: render-extraction-audit
# --------------------------------------------------------------------------------------
_G1 = {
    "G1.RENDER.CSR_CONTENT_GAP": _c(
        "G1", "render-extraction-audit", "render", FACT_PRIMARY,
        f"{_RENDER_REF}/csr-detection.md",
        "Primary page content is assembled client-side and absent from the HTML "
        "response, so fetchers that execute little or no JavaScript see an empty page.",
    ),
    "G1.RENDER.BODY_REQUIRES_INTERACTION": _c(
        "G1", "render-extraction-audit", "render", FACT_PRIMARY,
        f"{_RENDER_REF}/csr-detection.md",
        "Primary content sits only inside collapsed panels whose text is not in the "
        "response until a click populates it.",
    ),
    "G1.ENCODING.UNDECLARED_OR_WRONG": _c(
        "G1", "render-extraction-audit", "encoding", FACT_SECONDARY,
        f"{_RENDER_REF}/csr-detection.md",
        "No charset is declared, or the declared charset contradicts the detected "
        "encoding, corrupting extracted text.",
    ),
    "G1.TEXT.BOILERPLATE_DOMINANT": _c(
        "G1", "render-extraction-audit", "text", FACT_SECONDARY,
        f"{_RENDER_REF}/csr-detection.md",
        "A page's main region carries less text than its own navigation and footer, so "
        "extraction yields chrome rather than content.",
    ),
    "G1.TEXT.MAIN_LANDMARK_MISSING": _c(
        "G1", "render-extraction-audit", "text", FACT_ENRICHMENT,
        f"{_RENDER_REF}/csr-detection.md",
        "No main/article/role=main landmark, so an extractor has to guess which region "
        "is the content.",
    ),
    "G1.NONTEXT.FACT_LOCKED_IN_MEDIA": _c(
        "G1", "render-extraction-audit", "non-text", FACT_PRIMARY,
        f"{_RENDER_REF}/non-text-facts.md",
        "A section labelled with a fact class carries almost no text and one or more "
        "images, and that fact class appears nowhere in the page's text or markup.",
    ),
    "G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES": _c(
        "G1", "render-extraction-audit", "non-text", FACT_SECONDARY,
        f"{_RENDER_REF}/non-text-facts.md",
        "Informative images in the main region have no alt attribute at all (distinct "
        "from a correct decorative alt=\"\").",
    ),
    "G1.NONTEXT.ALT_NON_DESCRIPTIVE": _c(
        "G1", "render-extraction-audit", "non-text", FACT_ENRICHMENT,
        f"{_RENDER_REF}/non-text-facts.md",
        "Alt text is a filename, a dimension string, or a placeholder word, which "
        "describes nothing.",
    ),
    "G1.NONTEXT.SVG_TEXT_AS_PATHS": _c(
        "G1", "render-extraction-audit", "non-text", FACT_SECONDARY,
        f"{_RENDER_REF}/non-text-facts.md",
        "An inline SVG in a fact-bearing region has drawing paths but no title or text "
        "element, so its content is unreadable to a text extractor.",
    ),
    "G1.NONTEXT.PDF_ONLY_FACT": _c(
        "G1", "render-extraction-audit", "non-text", FACT_PRIMARY,
        f"{_RENDER_REF}/non-text-facts.md",
        "A fact class is reachable only through a linked PDF, not as HTML text.",
    ),
    "G1.NONTEXT.IFRAME_LOCKED_CONTENT": _c(
        "G1", "render-extraction-audit", "non-text", FACT_PRIMARY,
        f"{_RENDER_REF}/non-text-facts.md",
        "Primary content is delegated to a cross-origin iframe, so it does not belong to "
        "this page for any extractor.",
    ),
    "G1.NONTEXT.VIDEO_WITHOUT_TRANSCRIPT": _c(
        "G1", "render-extraction-audit", "non-text", FACT_SECONDARY,
        f"{_RENDER_REF}/non-text-facts.md",
        "Video or audio carries content with no captions track, transcript, or "
        "VideoObject.transcript, leaving nothing quotable.",
    ),
}

# --------------------------------------------------------------------------------------
# G2 -- INTERPRET: structured data and canonicalisation.  Owner: structured-data-audit
# --------------------------------------------------------------------------------------
_G2 = {
    "G2.SD.ABSENT": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/schema-profiles.md",
        "A page class carries no structured data in any syntax, so every fact on it has "
        "to be inferred from prose.",
    ),
    "G2.SD.JSONLD_SYNTAX_ERROR": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/validation-rules.md",
        "A JSON-LD block does not parse. Consumers discard it silently, so the author "
        "believes the markup is working.",
    ),
    "G2.SD.MISSING_CONTEXT": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/validation-rules.md",
        "A JSON-LD block has no usable @context, so its keys are not schema.org terms "
        "to a consumer.",
    ),
    "G2.SD.UNKNOWN_TYPE": _c(
        "G2", "structured-data-audit", "structured-data", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "A declared @type is not a schema.org type -- usually a spelling or "
        "localisation error that voids the whole node.",
    ),
    "G2.SD.TYPE_MISMATCH": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/schema-profiles.md",
        "The page's evidenced class has no matching entity type in its markup (for "
        "example a product page typed only as WebPage).",
    ),
    "G2.SD.REQUIRED_PROP_MISSING": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/schema-profiles.md",
        "A property that consumers require for this entity type is absent, so the node "
        "is ineligible for the answers it was written for.",
    ),
    "G2.SD.RECOMMENDED_PROP_MISSING": _c(
        "G2", "structured-data-audit", "structured-data", FACT_SECONDARY,
        f"{_SD_REF}/schema-profiles.md",
        "A recommended property is absent; the node works but competes poorly against "
        "more completely described entities.",
    ),
    "G2.SD.PROP_TYPE_INVALID": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/validation-rules.md",
        "A property value is the wrong shape -- a currency symbol inside price, a "
        "non-ISO date, an availability string that is not a schema.org enum.",
    ),
    "G2.SD.ENTITY_GRAPH_UNLINKED": _c(
        "G2", "structured-data-audit", "structured-data", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "Entities have no stable @id and are not linked to the site's Organization, so "
        "each page publishes an unconnected island instead of one entity graph.",
    ),
    "G2.SD.MARKUP_TEXT_CONTRADICTION": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/validation-rules.md",
        "Structured data states a value that the visible page contradicts. Worse than "
        "absent markup: it produces confidently wrong citations.",
    ),
    "G2.SD.DUPLICATE_CONFLICTING_ENTITIES": _c(
        "G2", "structured-data-audit", "structured-data", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "Two nodes of the same core type disagree about name or url on one page, forcing "
        "consumers to choose arbitrarily.",
    ),
    "G2.SD.FAQ_MARKUP_MISMATCH": _c(
        "G2", "structured-data-audit", "structured-data", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "FAQPage markup describes questions and answers that do not appear in the "
        "visible page -- a policy violation and a spam signal.",
    ),
    "G2.SD.BREADCRUMB_ABSENT_ON_DEEP_PAGES": _c(
        "G2", "structured-data-audit", "structured-data", FACT_ENRICHMENT,
        f"{_SD_REF}/schema-profiles.md",
        "Deep pages publish no BreadcrumbList, so their position in the site hierarchy "
        "is not machine-readable.",
    ),
    "G2.SD.SEARCHACTION_MISSING": _c(
        "G2", "structured-data-audit", "structured-data", FACT_ENRICHMENT,
        f"{_SD_REF}/schema-profiles.md",
        "A site with on-site search publishes no WebSite.potentialAction SearchAction.",
    ),
    "G2.SD.SD_ONLY_IN_RENDERED_DOM": _c(
        "G2", "structured-data-audit", "structured-data", FACT_PRIMARY,
        f"{_SD_REF}/validation-rules.md",
        "Structured data is injected by JavaScript or a tag manager, so it is absent "
        "from the raw response that most fetchers read.",
    ),
    "G2.SD.MICRODATA_ONLY_LEGACY": _c(
        "G2", "structured-data-audit", "structured-data", FACT_ENRICHMENT,
        f"{_SD_REF}/validation-rules.md",
        "Only Microdata or RDFa is present. It is valid, but JSON-LD is the "
        "better-supported and more robustly parsed carrier.",
    ),
    "G2.CANONICAL.MISSING": _c(
        "G2", "structured-data-audit", "canonical", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "No rel=canonical on indexable content pages, leaving duplicate resolution to "
        "the consumer.",
    ),
    "G2.CANONICAL.NON_SELF_REFERENTIAL": _c(
        "G2", "structured-data-audit", "canonical", FACT_PRIMARY,
        f"{_SD_REF}/validation-rules.md",
        "The served, linked page canonicalises to a different URL -- the most common way "
        "a page removes itself from an index by accident.",
    ),
    "G2.CANONICAL.CHAIN_OR_LOOP": _c(
        "G2", "structured-data-audit", "canonical", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "Canonical targets form a chain or a cycle, so no single preferred URL resolves.",
    ),
    "G2.CANONICAL.VS_OGURL_CONFLICT": _c(
        "G2", "structured-data-audit", "canonical", FACT_ENRICHMENT,
        f"{_SD_REF}/validation-rules.md",
        "rel=canonical and og:url name different URLs, so link-preview and index "
        "identity disagree.",
    ),
    "G2.CANONICAL.TO_NON_200": _c(
        "G2", "structured-data-audit", "canonical", FACT_PRIMARY,
        f"{_SD_REF}/validation-rules.md",
        "The canonical target does not return 200, pointing indexers at a URL that is "
        "not there.",
    ),
    "G2.CANONICAL.PARAM_DUPLICATES": _c(
        "G2", "structured-data-audit", "canonical", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "Tracking or sort parameters serve identical main content at distinct URLs with "
        "no canonical, splitting authority.",
    ),
    "G2.CANONICAL.HREFLANG_ASYMMETRIC": _c(
        "G2", "structured-data-audit", "canonical", FACT_SECONDARY,
        f"{_SD_REF}/validation-rules.md",
        "hreflang declarations are one-directional, so language selection is unreliable.",
    ),
    "G2.CANONICAL.PAGINATION_UNMARKED": _c(
        "G2", "structured-data-audit", "canonical", FACT_ENRICHMENT,
        f"{_SD_REF}/validation-rules.md",
        "Paginated sequences carry neither rel=next/prev nor self-referential canonicals, "
        "so pages compete with each other.",
    ),
}

# --------------------------------------------------------------------------------------
# G2-G3 -- ENTITY, IDENTITY & FRESHNESS.  Owner: entity-trust-audit
# --------------------------------------------------------------------------------------
_G3_ENTITY = {
    "G3.ENTITY.AMBIGUITY_RISK": _c(
        "G3", "entity-trust-audit", "entity", FACT_PRIMARY,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "The site gives a machine almost nothing to tell this entity apart from "
        "namesakes: no stable identifier, no authoritative sameAs, no definitional "
        "sentence, no disambiguating modifiers.",
    ),
    "G3.ENTITY.WEAK_DISAMBIGUATION": _c(
        "G3", "entity-trust-audit", "entity", FACT_ENRICHMENT,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "Partial disambiguation posture: some identity signals exist but not enough to "
        "resolve the entity confidently.",
    ),
    "G3.ENTITY.NAME_INCONSISTENCY": _c(
        "G3", "entity-trust-audit", "entity", FACT_SECONDARY,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "The brand name appears in three or more irreducible variants across title, "
        "og:site_name, markup, logo alt and footer, so each variant is a separate "
        "candidate entity.",
    ),
    "G3.ENTITY.SAMEAS_BROKEN": _c(
        "G3", "entity-trust-audit", "entity", FACT_SECONDARY,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "A declared sameAs identifier does not resolve -- a trust signal pointing at "
        "nothing.",
    ),
    "G3.ENTITY.SAMEAS_MISMATCH": _c(
        "G3", "entity-trust-audit", "entity", FACT_SECONDARY,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "A declared sameAs identifier resolves to a page naming a different entity, "
        "which actively merges this brand with another.",
    ),
    "G3.FRESH.NO_DATE_SIGNAL": _c(
        "G3", "entity-trust-audit", "freshness", FACT_SECONDARY,
        f"{_ENTITY_REF}/freshness-signals.md",
        "A dated content class publishes no date anywhere -- not in markup, not as "
        "visible text, not in headers or sitemap -- so recency cannot be established.",
    ),
    "G3.FRESH.DATE_CONTRADICTION": _c(
        "G3", "entity-trust-audit", "freshness", FACT_SECONDARY,
        f"{_ENTITY_REF}/freshness-signals.md",
        "Independent date sources for one page disagree by more than the allowed skew.",
    ),
    "G3.FRESH.SITEMAP_LASTMOD_UNIFORM": _c(
        "G3", "entity-trust-audit", "freshness", FACT_SECONDARY,
        f"{_ENTITY_REF}/freshness-signals.md",
        "Sitemap lastmod values are stamped at build time rather than content-change "
        "time, which makes the site's whole freshness claim worthless as a signal.",
    ),
    "G3.FRESH.STALE_CONTENT_MARKERS": _c(
        "G3", "entity-trust-audit", "freshness", FACT_SECONDARY,
        f"{_ENTITY_REF}/freshness-signals.md",
        "Several independent markers indicate the site has not been maintained: a stale "
        "copyright year, past-dated future-tense events, a long-dormant feed.",
    ),
    "G3.FRESH.UPDATED_WITHOUT_CHANGE": _c(
        "G3", "entity-trust-audit", "freshness", FACT_ENRICHMENT,
        f"{_ENTITY_REF}/freshness-signals.md",
        "dateModified advanced between runs while the main-content hash did not change, "
        "a trust-eroding pattern that trains consumers to discount the signal.",
    ),
    "G3.CORROB.NO_CITABLE_PRIMARY_SOURCE": _c(
        "G3", "entity-trust-audit", "corroboration", FACT_SECONDARY,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "No stable page states the brand's own canonical facts, so third parties have "
        "nothing consistent to repeat and corroboration cannot form.",
    ),
    "G3.CORROB.NO_PRESS_KIT": _c(
        "G3", "entity-trust-audit", "corroboration", FACT_ENRICHMENT,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "No press or media page with boilerplate and brand assets, so journalists and "
        "aggregators paraphrase instead of reproducing.",
    ),
    "G3.CORROB.FACTS_ONLY_IN_MARKETING_COPY": _c(
        "G3", "entity-trust-audit", "corroboration", FACT_SECONDARY,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "Canonical facts exist only inside superlative-laden prose, never as a plain "
        "list or table, so they cannot be lifted cleanly.",
    ),
    "G3.CORROB.NO_AUTHOR_IDENTITY": _c(
        "G3", "entity-trust-audit", "corroboration", FACT_SECONDARY,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "Articles carry bare byline strings with no Person entity or profile page, so "
        "expertise is unverifiable.",
    ),
    "G3.CORROB.NO_EXTERNAL_REFERENCES": _c(
        "G3", "entity-trust-audit", "corroboration", FACT_ENRICHMENT,
        f"{_ENTITY_REF}/entity-disambiguation.md",
        "Factual claims carry no outbound citations, a one-way trust posture that gives "
        "a consumer nothing to check against.",
    ),
}

# --------------------------------------------------------------------------------------
# G3 -- ANSWERABILITY: can a fact be lifted and quoted?  Owner: answerability-audit
# --------------------------------------------------------------------------------------
_G3_ANSW = {
    "G3.ANSW.LOW_SELF_CONTAINMENT": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/quotability-rules.md",
        "Section-opening sentences begin with pronouns or bare connectives, so a "
        "retrieved passage loses its subject and cannot be quoted standalone.",
    ),
    "G3.ANSW.NO_DEFINITIONAL_SENTENCE": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/quotability-rules.md",
        "No plain sentence states what the brand is. 'What is X?' is the most common "
        "query shape about a company; with nothing to quote, an assistant synthesises an "
        "answer from marketing copy -- which is how brands get misrepresented.",
    ),
    "G3.ANSW.FLUFF_DOMINANT": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/quotability-rules.md",
        "Unquantified superlatives dominate and verifiable statements are rare, leaving "
        "a retrieval system nothing extractable to lift.",
    ),
    "G3.ANSW.NO_QUESTION_SHAPED_HEADINGS": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/question-coverage.md",
        "No interrogative headings and no FAQ/QA markup, so no retrievable unit is "
        "aligned to the question shapes users actually type.",
    ),
    "G3.ANSW.NO_ANCHOR_ADDRESSABILITY": _c(
        "G3", "answerability-audit", "answerability", FACT_ENRICHMENT,
        f"{_ANSW_REF}/quotability-rules.md",
        "Long pages have no stable heading ids, so there is no deep-linkable fragment to "
        "cite -- the whole page must be cited, or nothing.",
    ),
    "G3.ANSW.ANSWER_BURIED": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/question-coverage.md",
        "A question-shaped heading is followed by preamble rather than the answer, so the "
        "retrieved chunk contains the question but not its resolution.",
    ),
    "G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/quotability-rules.md",
        "Very long paragraphs or heading-free spans give a chunker no natural boundary, "
        "so passages straddle topics. Also unscannable for humans.",
    ),
    "G3.ANSW.KEY_FACTS_UNLISTED": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/quotability-rules.md",
        "Specifications, plans or steps appear only as prose clauses, never as a table "
        "or list, and so survive extraction and summarisation poorly.",
    ),
    "G3.ANSW.PRONOUN_ONLY_ENTITY_REFERENCE": _c(
        "G3", "answerability-audit", "answerability", FACT_SECONDARY,
        f"{_ANSW_REF}/quotability-rules.md",
        "Content pages never name the brand in body text, so a passage extracted from "
        "them carries no entity anchor and the fact cannot be attributed.",
    ),
}

# --------------------------------------------------------------------------------------
# GE -- ENGAGEMENT: the deep, context-free arrival.  Owner: engagement-audit
# --------------------------------------------------------------------------------------
_GE = {
    "GE.ORIENT.NO_H1": _c(
        "GE", "engagement-audit", "orientation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "No h1: the arriving visitor gets no single statement of what the page is.",
    ),
    "GE.ORIENT.MULTIPLE_H1": _c(
        "GE", "engagement-audit", "orientation", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "Several competing h1 elements, so neither a reader nor an extractor can tell "
        "which is the page's subject.",
    ),
    "GE.ORIENT.HEADING_HIERARCHY_SKIP": _c(
        "GE", "engagement-audit", "orientation", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "Heading levels skip, so the document outline misrepresents the content "
        "structure for screen readers and chunkers alike.",
    ),
    "GE.ORIENT.GENERIC_H1": _c(
        "GE", "engagement-audit", "orientation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "The h1 is a placeholder like 'Home' or the bare brand name, answering none of "
        "what-is-this / who-is-it-for / what-next.",
    ),
    "GE.ORIENT.NO_VALUE_PROP": _c(
        "GE", "engagement-audit", "orientation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "Nothing above the fold says what the page offers in a full sentence.",
    ),
    "GE.ORIENT.NO_PRIMARY_CTA": _c(
        "GE", "engagement-audit", "orientation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "No actionable next step above the fold, or only non-specific labels such as "
        "'Learn more' with no object.",
    ),
    "GE.ORIENT.CTA_COMPETITION": _c(
        "GE", "engagement-audit", "orientation", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "Many equally-weighted calls to action above the fold produce choice paralysis.",
    ),
    "GE.ORIENT.TITLE_MISMATCH": _c(
        "GE", "engagement-audit", "orientation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "title, h1 and og:title describe materially different things, so the expectation "
        "set by an assistant's link preview is violated on landing.",
    ),
    "GE.ORIENT.DEEP_PAGE_NO_CONTEXT": _c(
        "GE", "engagement-audit", "orientation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "A deep page offers no breadcrumb, no parent link and no section label. This is "
        "the literal no-context-retention failure for a visitor who arrived from an "
        "assistant with no journey behind them.",
    ),
    "GE.FRICTION.CONTENT_GATING_OVERLAY": _c(
        "GE", "engagement-audit", "friction", FACT_PRIMARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "A viewport-covering overlay is present and not hidden by default, so the "
        "content the visitor was sent to read is behind a dismissal.",
    ),
    "GE.FRICTION.COOKIE_WALL_BLOCKS_CONTENT": _c(
        "GE", "engagement-audit", "friction", FACT_PRIMARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "A consent container combines with a scroll lock so content is unreachable "
        "without interaction -- which also blocks non-interactive fetchers.",
    ),
    "GE.FRICTION.FORCED_REGISTRATION": _c(
        "GE", "engagement-audit", "friction", FACT_PRIMARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Content is truncated behind a signup form rather than a paywall the site "
        "declares.",
    ),
    "GE.FRICTION.NO_VIEWPORT_META": _c(
        "GE", "engagement-audit", "friction", FACT_PRIMARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "No viewport meta tag, so mobile visitors get a desktop layout scaled down. Most "
        "assistant referrals are mobile, and the fix is one line.",
    ),
    "GE.FRICTION.CLS_RISK": _c(
        "GE", "engagement-audit", "friction", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Most main-region images declare neither dimensions nor aspect-ratio, so content "
        "jumps under the reader's finger as images load.",
    ),
    "GE.FRICTION.RENDER_BLOCKING_HEAD": _c(
        "GE", "engagement-audit", "friction", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Synchronous scripts in head block first paint, delaying the moment the arriving "
        "visitor sees anything.",
    ),
    "GE.FRICTION.HEAVY_PAGE": _c(
        "GE", "engagement-audit", "friction", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Declared transfer size for the document is far over budget for a mobile "
        "connection.",
    ),
    "GE.FRICTION.AUTOPLAY_MEDIA": _c(
        "GE", "engagement-audit", "friction", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Media autoplays without muted, which is hostile on arrival and blocked "
        "inconsistently across browsers.",
    ),
    "GE.FRICTION.LAYOUT_SHIFT_FONTS": _c(
        "GE", "engagement-audit", "friction", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Web fonts load without font-display, so text is invisible or reflows during the "
        "first seconds of the visit.",
    ),
    "GE.FRICTION.INTRUSIVE_STICKY": _c(
        "GE", "engagement-audit", "friction", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Stacked fixed-position chrome consumes a large share of a small viewport before "
        "any content is visible.",
    ),
    "GE.FRICTION.TAB_TRAP_CONTENT": _c(
        "GE", "engagement-audit", "friction", FACT_PRIMARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Primary content is reachable only through JavaScript-driven tabs whose panels "
        "are absent from the raw document.",
    ),
    "GE.FRICTION.NO_SKIP_LINK": _c(
        "GE", "engagement-audit", "friction", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "A large navigation block has no skip link, so keyboard and screen-reader users "
        "traverse it on every page.",
    ),
    "GE.FRICTION.UNLABELLED_FORM_FIELDS": _c(
        "GE", "engagement-audit", "friction", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Form inputs have no programmatic label, which measurably increases abandonment "
        "on the site's own conversion path.",
    ),
    "GE.FRICTION.LOW_CONTRAST_DECLARED": _c(
        "GE", "engagement-audit", "friction", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Statically resolvable foreground/background pairs fall below the WCAG AA "
        "contrast ratio. Only computed where both colours are literally declared.",
    ),
    "GE.NAV.DEAD_END_PAGE": _c(
        "GE", "engagement-audit", "navigation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "A content page has no in-body internal links, so a deep arrival has nowhere to "
        "go but back.",
    ),
    "GE.NAV.ORPHAN_PAGES": _c(
        "GE", "engagement-audit", "navigation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Pages are in the sitemap and reachable by URL but linked from no crawled page, "
        "so they have no internal discovery path.",
    ),
    "GE.NAV.NO_RELATED_CONTENT": _c(
        "GE", "engagement-audit", "navigation", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Article or product pages offer no related, next or previous module, so the "
        "single-page visit cannot become a session.",
    ),
    "GE.NAV.NO_SITE_SEARCH": _c(
        "GE", "engagement-audit", "navigation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "A large site offers no search input and no SearchAction, so a visitor who "
        "landed on the wrong page cannot self-rescue.",
    ),
    "GE.NAV.NAV_ONLY_IN_JS": _c(
        "GE", "engagement-audit", "navigation", FACT_PRIMARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Primary navigation is absent from the raw response, which breaks both crawl "
        "discovery and no-JavaScript visitors.",
    ),
    "GE.NAV.EXCESSIVE_DEPTH": _c(
        "GE", "engagement-audit", "navigation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Primary commercial pages sit deep from the homepage, diluting internal "
        "authority and lengthening every path to them.",
    ),
    "GE.NAV.BROKEN_INTERNAL_LINKS": _c(
        "GE", "engagement-audit", "navigation", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "A measured share of in-body internal links resolve to error statuses.",
    ),
    "GE.CONV.PATH_UNCLEAR": _c(
        "GE", "engagement-audit", "conversion", FACT_PRIMARY,
        f"{_ENGAGE_REF}/orientation-rules.md",
        "A page whose own intent implies a next step offers none -- a product page with "
        "no purchase or enquiry control, a pricing page with no signup.",
    ),
    "GE.CONV.FORM_FRICTION": _c(
        "GE", "engagement-audit", "conversion", FACT_SECONDARY,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "The primary conversion form demands more required fields than its stated action "
        "needs.",
    ),
    "GE.READ.NO_LISTS": _c(
        "GE", "engagement-audit", "readability", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Long content with no lists or tables at all, so nothing is scannable.",
    ),
    "GE.READ.LONG_SENTENCES": _c(
        "GE", "engagement-audit", "readability", FACT_ENRICHMENT,
        f"{_ENGAGE_REF}/friction-catalogue.md",
        "Mean sentence length is far above comfortable reading length for screen "
        "reading.",
    ),
}

REGISTRY: Dict[str, Dict[str, str]] = {}
for _group in (_G0, _G1, _G2, _G3_ENTITY, _G3_ANSW, _GE):
    REGISTRY.update(_group)

# Checks whose *cause* is owned by one skill but whose evidence is produced by another.
# The merge step (step 4, cross-skill reconcile) uses this to award a corroboration
# boost instead of emitting two peer findings for one fault.
CROSS_SKILL_CORROBORATION = {
    "GE.NAV.NAV_ONLY_IN_JS": ("render-extraction-audit",),
    "GE.FRICTION.TAB_TRAP_CONTENT": ("render-extraction-audit",),
    "G2.SD.SD_ONLY_IN_RENDERED_DOM": ("render-extraction-audit",),
    "GE.FRICTION.COOKIE_WALL_BLOCKS_CONTENT": ("render-extraction-audit",),
    "GE.FRICTION.FORCED_REGISTRATION": ("access-policy-audit",),
    "G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE": ("engagement-audit",),
}


def is_known(check_id: str) -> bool:
    """True when ``check_id`` exists in the closed registry."""
    return check_id in REGISTRY


def meta(check_id: str) -> Dict[str, str]:
    """Registry entry for ``check_id``. Raises ``KeyError`` when unknown."""
    try:
        return REGISTRY[check_id]
    except KeyError:
        raise KeyError(
            f"unknown check_id {check_id!r}: findings may only cite ids in the closed "
            f"registry (auditkit/checks.py). Add the check there, with a reference entry "
            f"and a positive fixture, before emitting it."
        ) from None


def gate_of(check_id: str) -> str:
    return meta(check_id)["gate"]


def skill_of(check_id: str) -> str:
    return meta(check_id)["skill"]


def for_skill(skill_id: str) -> List[str]:
    """Sorted check ids owned by ``skill_id``."""
    return sorted(cid for cid, m in REGISTRY.items() if m["skill"] == skill_id)


def for_gate(gate: str) -> List[str]:
    return sorted(cid for cid, m in REGISTRY.items() if m["gate"] == gate)


def all_ids() -> List[str]:
    return sorted(REGISTRY)


def skills() -> List[str]:
    return sorted({m["skill"] for m in REGISTRY.values()})


def counts_by_skill() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for m in REGISTRY.values():
        out[m["skill"]] = out.get(m["skill"], 0) + 1
    return dict(sorted(out.items()))


def markdown_table(check_ids: Iterable[str]) -> str:
    """Render a ``check_id | gate | what it detects`` table.

    Used to keep each skill's "Checks performed" section in its SKILL.md honest: the
    table is generated from the registry rather than maintained by hand.
    """
    rows = ["| `check_id` | Gate | What it detects |", "| :--- | :--- | :--- |"]
    for cid in sorted(check_ids):
        m = meta(cid)
        rows.append(f"| `{cid}` | {m['gate']} | {m['summary']} |")
    return "\n".join(rows)
