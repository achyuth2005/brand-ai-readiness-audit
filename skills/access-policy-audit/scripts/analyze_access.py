#!/usr/bin/env python3
"""Gate G0 -- REACH. Is the crawler let in, and is it allowed to quote what it finds?

Pure function of the evidence ledger. No network I/O.

Mechanism
---------
The brief's Appendix A gives the ordering: the crawler has to be let in *first*. This
gate is unique in that one line of text in one file can make an entire brand invisible to
one assistant while leaving it perfectly visible to Google -- which is exactly the "why
are we not cited" complaint an audit exists to answer. It is also the cheapest gate to
fix, so it dominates priority ordering.

The non-obvious insight the whole skill is built around: **AI crawlers are not one
population, and they do different jobs.** Blocking a training crawler has no effect on
citation. Blocking a *retrieval* crawler removes the brand from answers entirely. A site
that "blocked AI bots" on legal advice two years ago very often blocked both without
realising the second consequence.

See references/ai-crawler-agents.md, robots-and-directives.md, gating-patterns.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import analyzer, ledger as ledger_mod, lexicons, sitemap, textnorm  # noqa: E402
from auditkit import urlnorm  # noqa: E402
from auditkit.dom import Document, selector_for  # noqa: E402
from auditkit.severity import scope_level_for  # noqa: E402
from auditkit.analyzer import Context, PageView  # noqa: E402
from auditkit.finding import (  # noqa: E402
    Finding, FindingsFile, Scope, SuggestedAction, dom_ref, file_line_ref, header_ref,
    metric_ref,
)
from auditkit.robots import (  # noqa: E402
    STATE_MISSING, STATE_PRESENT, STATE_UNAVAILABLE, STATE_UNPARSEABLE, RobotsFile,
)

SKILL_ID = "access-policy-audit"

# ----------------------------------------------------------------------------------------
# The AI crawler table.
#
# `purpose` is the column that matters, and the one most implementations omit. A finding
# that reports "GPTBot: Disallow" as a citation blocker is wrong about the mechanism, and
# being wrong about the mechanism is worse than being silent.
#
# citation_impact: severe   = removes citation eligibility for that surface
#                  moderate = reduces it, or affects one surface only
#                  training = affects latent model knowledge, NOT live citation
# ----------------------------------------------------------------------------------------
AI_CRAWLERS: Tuple[Dict[str, str], ...] = (
    {"agent": "OAI-SearchBot", "operator": "OpenAI",
     "purpose": "Builds the search index ChatGPT retrieves citable sources from",
     "impact": "severe"},
    {"agent": "ChatGPT-User", "operator": "OpenAI",
     "purpose": "Live user-initiated fetch during a conversation",
     "impact": "severe"},
    {"agent": "GPTBot", "operator": "OpenAI",
     "purpose": "Model training corpus",
     "impact": "training"},
    {"agent": "Claude-SearchBot", "operator": "Anthropic",
     "purpose": "Search index for answer retrieval",
     "impact": "severe"},
    {"agent": "Claude-User", "operator": "Anthropic",
     "purpose": "Live user-initiated fetch during a conversation",
     "impact": "severe"},
    {"agent": "ClaudeBot", "operator": "Anthropic",
     "purpose": "Model training corpus",
     "impact": "training"},
    {"agent": "PerplexityBot", "operator": "Perplexity",
     "purpose": "Search and answer index",
     "impact": "severe"},
    {"agent": "Perplexity-User", "operator": "Perplexity",
     "purpose": "Live user-initiated fetch",
     "impact": "severe"},
    {"agent": "Googlebot", "operator": "Google",
     "purpose": "Core web index; many assistant surfaces ride on it",
     "impact": "severe"},
    {"agent": "Google-Extended", "operator": "Google",
     "purpose": "Gemini grounding and training opt-out control",
     "impact": "moderate"},
    {"agent": "Bingbot", "operator": "Microsoft",
     "purpose": "Core web index used by Copilot and others",
     "impact": "severe"},
    {"agent": "Applebot", "operator": "Apple",
     "purpose": "Search index for Siri and Spotlight",
     "impact": "moderate"},
    {"agent": "Applebot-Extended", "operator": "Apple",
     "purpose": "AI training opt-out control",
     "impact": "training"},
    # Common Crawl publishes periodic open snapshots of the web. They are a staple of
    # model-training corpora, but no assistant retrieves live citations from them, so a
    # CCBot block is a training-data decision, not a citation blocker.
    {"agent": "CCBot", "operator": "Common Crawl",
     "purpose": "Open web snapshots widely used as model-training corpora",
     "impact": "training"},
    {"agent": "Amazonbot", "operator": "Amazon",
     "purpose": "Search index for Alexa and shopping surfaces",
     "impact": "moderate"},
    {"agent": "meta-externalagent", "operator": "Meta",
     "purpose": "Search and AI corpus",
     "impact": "moderate"},
)

RETRIEVAL_AGENTS = tuple(c["agent"] for c in AI_CRAWLERS if c["impact"] == "severe")
TRAINING_AGENTS = tuple(c["agent"] for c in AI_CRAWLERS if c["impact"] == "training")

# Policy comments near a block that indicate deliberate intent. When these are present
# the finding is kept but reframed: "confirm this is still intended, and note the
# distinction you may not have meant" rather than "unblock". Respecting owner intent is
# part of being right about the mechanism.
DELIBERATE_POLICY_MARKERS = (
    "licens", "legal", "copyright", "scrap", "ai train", "ai-train", "training data",
    "opt out", "opt-out", "terms of use", "intellectual property", "permission",
    "do not train", "no ai", "gdpr", "policy",
)

_NOINDEX_TOKENS = ("noindex", "none")
_NOSNIPPET_TOKENS = ("nosnippet",)
_NOARCHIVE_TOKENS = ("noarchive", "nocache", "no-cache")


# ========================================================================================
# robots.txt
# ========================================================================================

def _load_robots(context: Context) -> Tuple[Optional[RobotsFile], Optional[Any]]:
    """Reconstruct the site's own robots policy from the ledger. Pure, no network.

    The ledger can hold several robots.txt files (host variants, off-site identifiers);
    the audited host's own file is preferred, then any same-site file.
    """
    host = context.site_host()
    origin = context.site_url()

    def rank(entry: Any) -> int:
        normal = urlnorm.try_normalise(entry.url.split("#")[0])
        if normal is None or not host:
            return 3
        if origin and normal.origin == origin:
            return 0  # the exact origin the crawl obeyed
        if normal.host == host:
            return 1
        return 2 if urlnorm.same_site(normal.host, host) else 3

    for entry in sorted(context.ledger.entries(purpose="robots"), key=rank):
        body = context.ledger.body(entry)
        timed_out = entry.outcome in (ledger_mod.OUTCOME_TIMEOUT,
                                      ledger_mod.OUTCOME_NETWORK_ERROR)
        robots = RobotsFile.from_response(
            entry.status, body if body else None,
            source_url=entry.final_url or entry.url, timed_out=timed_out,
        )
        return robots, entry
    return None, None


def _primary_content_paths(context: Context) -> List[str]:
    """The paths that matter for a citation-blocking judgement.

    Only real content paths count. A ``Disallow: /cart`` is correct engineering, and a
    check that treats it as a citation failure produces noise on every well-run shop.
    """
    paths = []
    for page in context.content_pages():
        paths.append(page.path)
    if "/" not in paths:
        paths.append("/")
    return sorted(set(paths))


def check_robots(context: Context, out: FindingsFile) -> None:
    robots, entry = _load_robots(context)
    if robots is None or entry is None:
        out.skip("G0.ROBOTS.AI_AGENT_BLOCKED",
                 reason="no robots.txt fetch is recorded in the evidence ledger",
                 remedy="run the orchestrator, which always fetches /robots.txt first")
        for check_id in ("G0.ROBOTS.TRAINING_AGENT_BLOCKED", "G0.ROBOTS.UNAVAILABLE",
                         "G0.SITEMAP.NOT_IN_ROBOTS"):
            out.skip(check_id, reason="no robots.txt fetch is recorded in the ledger")
        return

    url_hash = entry.entry_id
    out.note_metric("robots_state", robots.state)
    out.note_metric("robots_status", entry.status)

    # --- unavailable -------------------------------------------------------------------
    if robots.state == STATE_UNAVAILABLE:
        out.add(Finding(
            check_id="G0.ROBOTS.UNAVAILABLE",
            title="robots.txt is unavailable, so crawlers cannot establish permission",
            evidence=(
                f"A request for {entry.final_url or entry.url} returned "
                f"{entry.status or 'no response'} ({entry.reason or 'timeout'}). "
                f"RFC 9309 allows a crawler to treat an unreachable robots.txt as "
                f"disallow-all, and major crawlers do back off rather than assume "
                f"permission, so the whole site can become uncrawlable while every page "
                f"serves correctly to a browser."
            ),
            mechanism=(
                "A crawler fetches /robots.txt before anything else. A 5xx or a timeout "
                "is not the same as an absent file: an absent file grants access, while "
                "an erroring one is commonly treated as a refusal, because the crawler "
                "cannot tell whether it is being denied."
            ),
            confidence="high", signal_count=2,
            scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                        example_urls=[entry.final_url or entry.url]),
            evidence_refs=[metric_ref(url_hash, "robots_http_status",
                                      entry.status or 0, 200)],
            suggested_action=SuggestedAction(
                summary="Serve /robots.txt reliably with a 200 or a 404, never a 5xx.",
                steps=[
                    "Make /robots.txt a statically served file, or cache it at the edge "
                    "so it never depends on the application being healthy.",
                    "If no crawl policy is intended, return 404: an absent robots.txt "
                    "grants full access under RFC 9309 and is safer than an erroring one.",
                    "Add an uptime check on /robots.txt specifically, separate from the "
                    "site's own health check.",
                ],
                effort="trivial", owner_role="Infrastructure / SEO",
                verification="Request /robots.txt ten times and confirm every response "
                             "is 200 or 404 and none is 5xx or a timeout.",
                expected_effect="Restores a stable permission signal so crawlers stop "
                                "backing off from the whole origin.",
            ),
        ))
        return

    if robots.state == STATE_MISSING:
        out.observe(
            "G0.ROBOTS.UNAVAILABLE",
            "No robots.txt is published, which grants full access",
            f"A request for {entry.final_url or entry.url} returned {entry.status}. "
            f"Under RFC 9309 an absent robots.txt grants access to everything, so this "
            f"is not a defect. Recorded because publishing one lets the site declare a "
            f"sitemap location and differentiate crawler policy later.",
            confidence="high",
        )
    elif robots.state == STATE_UNPARSEABLE:
        out.observe(
            "G0.ROBOTS.UNAVAILABLE",
            "robots.txt contains no recognised directives",
            f"robots.txt returned {entry.status} but no parseable directive was found, "
            f"so it is treated as allow-all. Parser notes: "
            f"{'; '.join(robots.warnings[:3]) or 'none'}.",
            confidence="medium",
        )

    if robots.state != STATE_PRESENT:
        return

    for warning in robots.warnings[:5]:
        out.note_metric(f"robots_warning_{abs(hash(warning)) % 10000}", warning)

    content_paths = _primary_content_paths(context)
    audit_ua = entry.user_agent or "BrandAIReadinessAudit"

    # --- per-agent evaluation ----------------------------------------------------------
    blocked_retrieval: List[Dict[str, Any]] = []
    blocked_training: List[Dict[str, Any]] = []

    for crawler in AI_CRAWLERS:
        agent = crawler["agent"]
        blocked_paths: List[str] = []
        rule_line = 0
        rule_text = ""
        group_token = "*"
        for path in content_paths:
            verdict = robots.verdict_for(agent, path)
            if not verdict.allowed:
                blocked_paths.append(path)
                if verdict.rule is not None and not rule_line:
                    rule_line = verdict.rule.line_no
                    rule_text = verdict.rule.raw
                    group_token = verdict.group_agent
        if not blocked_paths:
            continue
        record = {
            "crawler": crawler,
            "blocked": blocked_paths,
            "line": rule_line,
            "rule": rule_text,
            "group": group_token,
            "explicit": robots.has_explicit_group(agent),
        }
        if crawler["impact"] == "training":
            blocked_training.append(record)
        else:
            blocked_retrieval.append(record)

    deliberate = _looks_deliberate(robots)
    total_paths = len(content_paths)

    # --- G0.ROBOTS.AI_AGENT_BLOCKED ----------------------------------------------------
    for record in blocked_retrieval:
        crawler = record["crawler"]
        agent = crawler["agent"]
        blocked = record["blocked"]
        share = len(blocked) / max(1, total_paths)

        refs = [metric_ref(url_hash, f"content_paths_blocked_for_{agent}",
                           len(blocked), 0)]
        if record["line"]:
            refs.append(file_line_ref(url_hash, "/robots.txt", record["line"],
                                      record["rule"]))

        permitted_note = _permitted_contrast(robots, content_paths, agent)
        evidence = (
            f"robots.txt "
            + (f"line {record['line']} declares `{record['rule']}` under the "
               f"`User-agent: {record['group']}` group, which"
               if record["line"] else "has no rule permitting this agent, so the "
                                      "effective policy")
            + f" disallows {len(blocked)} of {total_paths} sampled content paths for "
              f"{agent}. {agent} is operated by {crawler['operator']} and its job is: "
              f"{crawler['purpose'].lower()}. "
            + (f"{permitted_note} " if permitted_note else "")
            + (f"Blocked paths include {analyzer.summarise_urls(blocked)}."
               if blocked else "")
        )

        action = _unblock_action(agent, crawler, deliberate, record)
        severe = crawler["impact"] == "severe"
        out.add(Finding(
            check_id="G0.ROBOTS.AI_AGENT_BLOCKED",
            title=(f"robots.txt blocks {agent}, the retrieval crawler "
                   f"{crawler['operator']} uses to find citable sources" if severe else
                   f"robots.txt blocks {agent}, which feeds one {crawler['operator']} "
                   f"answer surface"),
            evidence=evidence,
            mechanism=(
                f"{agent} is not a training crawler. {crawler['purpose']}. When it is "
                f"disallowed, the site is not in the candidate set an assistant can "
                f"retrieve and cite at answer time, so the brand is absent from answers "
                f"no matter how good its content is. This is invisible in a browser and "
                f"invisible in analytics, which is why it survives for years."
                if severe else
                f"{agent} is not a training crawler. {crawler['purpose']}. Disallowing it "
                f"removes the site from that one surface while the main retrieval "
                f"crawlers can still reach it, so it narrows where the brand can be cited "
                f"rather than removing it from answers."
            ),
            confidence="high", signal_count=2,
            # Impact enters severity through the computed model, not a hand-set rank: a
            # crawler that feeds one surface is a narrower loss of the primary fact.
            fact_class="primary" if severe else "secondary",
            scope=Scope(level="site" if share >= 0.6 else "section",
                        pages_affected=len(blocked), pages_sampled=total_paths,
                        example_urls=blocked[:3]),
            evidence_refs=refs,
            suggested_action=action,
            notes=([f"A nearby comment in robots.txt suggests this block is deliberate "
                    f"policy. The finding is kept because the consequence for citation "
                    f"is frequently not the one intended, but the suggested action is "
                    f"framed as confirming intent rather than unblocking."]
                   if deliberate else []),
        ))

    # --- G0.ROBOTS.TRAINING_AGENT_BLOCKED ----------------------------------------------
    # Reported at info level with the correct mechanism. Calling this a citation blocker
    # would be the most common mechanism error in this problem space.
    for record in blocked_training:
        crawler = record["crawler"]
        out.observe(
            "G0.ROBOTS.TRAINING_AGENT_BLOCKED",
            f"robots.txt blocks {crawler['agent']}, a training-corpus crawler",
            (f"robots.txt "
             + (f"line {record['line']} (`{record['rule']}`) " if record["line"] else "")
             + f"disallows {crawler['agent']} ({crawler['operator']}: "
               f"{crawler['purpose'].lower()}). This does **not** affect whether the "
               f"site can be cited in a live answer, because citation depends on the "
               f"retrieval crawlers, not the training ones. Recorded so the distinction "
               f"is explicit: if the intention was to stay out of training data while "
               f"remaining citable, this rule is correct and should be kept."),
            confidence="high",
        )

    if not blocked_retrieval:
        out.note_posture("robots_permits_all_retrieval_agents", True)
        # Near miss: a site relying on a permissive "*" with no named groups is one
        # careless edit away from blocking retrieval crawlers.
        if not any(robots.has_explicit_group(a) for a in RETRIEVAL_AGENTS):
            out.near_miss(
                "G0.ROBOTS.AI_AGENT_BLOCKED", "named_retrieval_agent_groups", 0.0, 1.0,
                scope_key="site",
                detail=("Every retrieval crawler is currently permitted, but only via "
                        "the catch-all `User-agent: *` group. No retrieval agent is "
                        "named explicitly, so a future blanket `Disallow: /` added for "
                        "unrelated reasons would silently remove citation eligibility."),
            )


def _permitted_contrast(robots: RobotsFile, paths: Sequence[str],
                        blocked_agent: str) -> str:
    """Name an agent that *is* permitted, which is what makes the finding land.

    "OAI-SearchBot is blocked while Googlebot is permitted" tells the owner immediately
    that this is differential treatment they probably did not intend, rather than a
    site-wide crawl policy they chose.
    """
    for crawler in AI_CRAWLERS:
        agent = crawler["agent"]
        if agent == blocked_agent:
            continue
        if all(robots.verdict_for(agent, p).allowed for p in paths):
            return (f"{agent} is separately permitted on the same paths, so this is "
                    f"differential treatment rather than a site-wide crawl policy.")
    return ""


def _looks_deliberate(robots: RobotsFile) -> bool:
    """True when the file carries comments indicating a considered policy decision."""
    text = (robots.text or "").lower()
    comment_lines = [line for line in text.splitlines() if line.strip().startswith("#")]
    haystack = " ".join(comment_lines)
    if any(marker in haystack for marker in DELIBERATE_POLICY_MARKERS):
        return True
    # All AI agents blocked while search engines stay permitted is itself a deliberate
    # posture, whether or not it is commented.
    ai_blocked = sum(1 for c in AI_CRAWLERS
                     if c["impact"] in ("severe", "training", "moderate")
                     and robots.has_explicit_group(c["agent"])
                     and not robots.verdict_for(c["agent"], "/").allowed)
    return ai_blocked >= 4


def _unblock_action(agent: str, crawler: Dict[str, str], deliberate: bool,
                    record: Dict[str, Any]) -> SuggestedAction:
    if deliberate:
        return SuggestedAction(
            summary=(f"Confirm that blocking {agent} is still the intended policy, and "
                     f"note that it removes citation eligibility rather than only "
                     f"training use."),
            steps=[
                f"Decide the policy deliberately for the two cases separately: training "
                f"corpus use ({', '.join(TRAINING_AGENTS[:3])}) and live retrieval and "
                f"citation ({', '.join(RETRIEVAL_AGENTS[:3])}).",
                f"If the intention was to stay out of training data while remaining "
                f"citable, keep the training-agent rules and remove the disallow for "
                f"{agent} on public content paths.",
                "Keep utility paths (/cart, /checkout, /search, /admin, /api) "
                "disallowed for every agent; they are correctly excluded.",
                "Record the decision in a comment in robots.txt so the next person "
                "does not have to re-derive the intent.",
            ],
            effort="trivial", owner_role="Infrastructure / SEO, with brand sign-off",
            verification=(f"After the change, confirm with a robots.txt tester that "
                          f"{agent} is allowed on a public content path, and that the "
                          f"training-agent rules you intended to keep are still in force."),
            expected_effect=("Makes the training-versus-citation trade-off an explicit "
                             "decision rather than an accidental one."),
        )
    return SuggestedAction(
        summary=(f"Allow {agent} on public content paths in robots.txt, keeping any "
                 f"training-crawler policy as it is."),
        steps=[
            f"Add an explicit group for {agent} that permits public content:\n"
            f"    User-agent: {agent}\n    Allow: /\n    Disallow: /cart\n"
            f"    Disallow: /checkout\n    Disallow: /search\n    Disallow: /admin",
            (f"Remove or narrow the rule at line {record['line']} "
             f"(`{record['rule']}`) so it no longer covers content paths."
             if record["line"] else
             "Add the permissive group above so the agent no longer falls through to a "
             "restrictive catch-all group."),
            "Keep training-corpus agents at whatever policy the business has chosen; "
            "they are a separate decision and do not affect citation.",
            "Re-check that the catch-all `User-agent: *` group does not still disallow "
            "content, since agents with no named group fall through to it.",
        ],
        effort="trivial", owner_role="Infrastructure / SEO",
        verification=(f"Fetch a public content URL sending a user-agent containing "
                      f"`{agent}` and confirm a 200 with the page body; and confirm with "
                      f"a robots.txt tester that the path is allowed for {agent}."),
        expected_effect=(f"Restores the site's eligibility to be retrieved and cited by "
                         f"{crawler['operator']} surfaces."),
    )


def check_audit_path_disallowed(context: Context, out: FindingsFile) -> None:
    """G0.ROBOTS.AUDIT_PATH_DISALLOWED -- turn our own constraint into signal.

    The audit respects robots.txt for its own fetching and never fetches a disallowed
    path "to check it anyway". When content paths are disallowed for a generic,
    well-behaved, honestly-identified crawler, that is itself substantive evidence: a
    site treating a polite generic crawler that way is very likely treating assistants
    the same. So the limitation is converted into a finding instead of a silent gap.
    """
    blocked = [e for e in context.ledger.entries()
               if e.outcome == ledger_mod.OUTCOME_BLOCKED_ROBOTS]
    if not blocked:
        return

    site_host = context.site_host()
    content_blocked = []
    for entry in blocked:
        if entry.purpose != "page":
            continue
        normal = urlnorm.try_normalise(entry.url.split("#")[0])
        if normal is None or urlnorm.is_utility(normal):
            continue
        if site_host and not urlnorm.same_site(normal.host, site_host):
            # An off-site profile (a sameAs link being corroborated) is governed by that
            # host's robots.txt, which says nothing about this site's policy.
            continue
        if "robots.txt is unavailable" in entry.reason:
            # Refusals caused by an unavailable robots.txt belong to G0.ROBOTS.UNAVAILABLE.
            # They say nothing about what the site's rules permit.
            continue
        content_blocked.append(entry)
    if not content_blocked:
        out.note_posture("robots_disallows_only_utility_paths_for_audit", True)
        return

    min_signals = int(context.threshold(
        "false_positive_control.min_signals_for_inference", 2))
    if len(content_blocked) < min_signals:
        only = content_blocked[0]
        out.observe(
            "G0.ROBOTS.AUDIT_PATH_DISALLOWED",
            "robots.txt disallows one content URL for a generic crawler",
            (f"{only.url} was not fetched: {only.reason}. A single disallowed URL is "
             f"recorded as an observation; at least {min_signals} are required before a "
             f"catch-all policy is reported as restricting content."),
            confidence="medium",
        )
        return

    attempted = len([e for e in context.ledger.entries(purpose="page")
                     if (n := urlnorm.try_normalise(e.url.split("#")[0])) is not None
                     and (not site_host or urlnorm.same_site(n.host, site_host))])
    example = content_blocked[0]
    out.add(Finding(
        check_id="G0.ROBOTS.AUDIT_PATH_DISALLOWED",
        title="robots.txt disallows a generic, well-identified crawler on content paths",
        evidence=(
            f"{len(content_blocked)} of {attempted} attempted content URLs were not "
            f"fetched because robots.txt disallows this audit's own declared user-agent. "
            f"Example: {example.url} -- {example.reason} "
            f"Those paths were therefore skipped rather than fetched, and are recorded "
            f"in the report's coverage section. A site that disallows a generic "
            f"well-behaved crawler on content is very likely presenting the same policy "
            f"to assistant crawlers that fall through to the same catch-all group."
        ),
        mechanism=(
            "Crawlers that do not match a named User-agent group fall through to the "
            "catch-all `*` group. A restrictive catch-all therefore governs every agent "
            "the site has not explicitly named -- including assistant crawlers that did "
            "not exist when the file was written, and any that appear next year."
        ),
        confidence="high", signal_count=2,
        # Most content disallowed is a policy failure; a minority is a narrower loss.
        fact_class=("primary" if len(content_blocked) / max(1, attempted)
                    >= float(context.threshold("false_positive_control.site_wide_share", 0.6))
                    else "secondary"),
        scope=Scope(level="section", pages_affected=len(content_blocked),
                    pages_sampled=max(attempted, len(content_blocked)),
                    example_urls=[e.url for e in content_blocked[:3]]),
        evidence_refs=[metric_ref(example.entry_id, "content_urls_blocked_for_audit_ua",
                                  len(content_blocked), 0)],
        suggested_action=SuggestedAction(
            summary="Narrow the catch-all robots.txt group so it disallows utility paths "
                    "rather than content paths.",
            steps=[
                "Change the `User-agent: *` group from a broad content disallow to an "
                "explicit list of utility paths: /cart, /checkout, /search, /admin, "
                "/api, /account.",
                "Name the crawlers whose access you care about in their own groups, so "
                "their policy does not depend on the catch-all.",
                "Re-run this audit and confirm coverage.paths_skipped is empty.",
            ],
            effort="trivial", owner_role="Infrastructure / SEO",
            verification="Re-run the audit and confirm that coverage.paths_skipped no "
                         "longer lists content paths.",
            expected_effect="Makes public content reachable by every well-behaved "
                            "crawler, including assistant crawlers not named in the file.",
        ),
    ))


# ========================================================================================
# Indexing and snippet directives
# ========================================================================================

def _parse_directive_tokens(value: str) -> List[str]:
    return [token.strip().lower() for token in value.split(",") if token.strip()]


def check_directives(context: Context, out: FindingsFile) -> None:
    """G0.DIRECTIVE.* -- the silent citation killers.

    The most under-detected class in this problem space, because every one of these is
    invisible in a browser. ``nosnippet`` in particular deserves its own callout: a brand
    asking "why are we never quoted" while shipping ``nosnippet`` has a one-line fix and
    no idea it exists.
    """
    pages = context.content_pages()
    if not pages:
        out.skip("G0.DIRECTIVE.NOINDEX_META",
                 reason="no content pages were fetched")
        return

    hits: Dict[str, List[Tuple[str, Any]]] = {}

    def record(check_id: str, page: PageView, payload: Dict[str, Any]) -> None:
        hits.setdefault(check_id, []).append((page.url, payload))

    for page in pages:
        header_value = page.entry.header("x-robots-tag")
        meta_directives = page.doc.robots_meta()
        meta_all = ",".join(value for _name, value in meta_directives)

        header_tokens = _parse_directive_tokens(header_value)
        meta_tokens = _parse_directive_tokens(meta_all)
        combined = header_tokens + meta_tokens

        if any(t in _NOINDEX_TOKENS for t in header_tokens):
            record("G0.DIRECTIVE.NOINDEX_HEADER", page,
                   {"value": header_value, "source": "header"})
        for name, value in meta_directives:
            if any(t in _NOINDEX_TOKENS for t in _parse_directive_tokens(value)):
                record("G0.DIRECTIVE.NOINDEX_META", page,
                       {"value": value, "meta_name": name})
                break

        if any(t in _NOSNIPPET_TOKENS for t in combined):
            source = "X-Robots-Tag header" if any(
                t in _NOSNIPPET_TOKENS for t in header_tokens) else "robots meta tag"
            record("G0.DIRECTIVE.NOSNIPPET", page,
                   {"value": header_value or meta_all, "source": source})

        for token in combined:
            if token.replace(" ", "").startswith("max-snippet:"):
                try:
                    limit = int(token.split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    continue
                if limit == 0:
                    record("G0.DIRECTIVE.MAX_SNIPPET_ZERO", page,
                           {"value": token,
                            "source": "header" if token in header_tokens else "meta"})

        if any(t in _NOARCHIVE_TOKENS for t in combined):
            record("G0.DIRECTIVE.NOARCHIVE_NOCACHE", page,
                   {"value": header_value or meta_all})

        nosnippet_nodes = page.doc.data_nosnippet_nodes()
        if nosnippet_nodes:
            wrapped_words = sum(
                textnorm.word_count(page.doc.visible_text(n)) for n in nosnippet_nodes)
            if wrapped_words >= 10:
                record("G0.DIRECTIVE.DATA_NOSNIPPET", page, {
                    "nodes": len(nosnippet_nodes),
                    "words": wrapped_words,
                    "selector": selector_for(nosnippet_nodes[0]),
                    "excerpt": page.doc.visible_text(nosnippet_nodes[0]),
                })

        # Conflict: most restrictive wins, and it is usually unintended.
        header_indexable = "index" in header_tokens and not any(
            t in _NOINDEX_TOKENS for t in header_tokens)
        meta_noindex = any(t in _NOINDEX_TOKENS for t in meta_tokens)
        header_noindex = any(t in _NOINDEX_TOKENS for t in header_tokens)
        meta_indexable = "index" in meta_tokens and not meta_noindex
        if (header_indexable and meta_noindex) or (meta_indexable and header_noindex):
            record("G0.DIRECTIVE.CONFLICT", page,
                   {"header": header_value, "meta": meta_all})

    total = len(pages)
    for check_id, page_hits in sorted(hits.items()):
        _emit_directive(context, out, check_id, page_hits, total)


_DIRECTIVE_SPEC: Dict[str, Dict[str, str]] = {
    "G0.DIRECTIVE.NOINDEX_HEADER": {
        "title": "An X-Robots-Tag: noindex header removes pages from indexes",
        "mechanism": ("An X-Robots-Tag response header carries the same weight as a "
                      "robots meta tag but is invisible in page source and in a "
                      "browser's rendered view. A noindex here removes the page from "
                      "search and retrieval indexes entirely, so it cannot be surfaced "
                      "or cited however well it is written."),
        "fix": "Remove noindex from the X-Robots-Tag header on content pages.",
        "owner": "Infrastructure / Platform engineering",
        "effort": "trivial",
    },
    "G0.DIRECTIVE.NOINDEX_META": {
        "title": "A robots meta tag carries noindex on content pages",
        "mechanism": ("A noindex directive removes the page from indexes, so it is not "
                      "in the candidate set any assistant can retrieve. On content "
                      "pages this is very often a staging or template default that was "
                      "never removed."),
        "fix": "Remove the noindex directive from content page templates.",
        "owner": "Frontend engineering / SEO",
        "effort": "trivial",
    },
    "G0.DIRECTIVE.NOSNIPPET": {
        "title": "nosnippet explicitly forbids quoting these pages",
        "mechanism": ("nosnippet tells consumers they may index the page but may not "
                      "reproduce any text from it. The page can rank and still never be "
                      "excerpted or quoted, which is precisely the observable symptom "
                      "behind 'we are never quoted' -- and it has a one-line fix that "
                      "most owners do not know exists."),
        "fix": "Remove nosnippet from content pages so they can be quoted.",
        "owner": "SEO / Platform engineering",
        "effort": "trivial",
    },
    "G0.DIRECTIVE.MAX_SNIPPET_ZERO": {
        "title": "max-snippet:0 forbids quoting these pages numerically",
        "mechanism": ("max-snippet:0 caps the quotable extract at zero characters, "
                      "which has the same effect as nosnippet. It is often set "
                      "site-wide by a plugin default rather than deliberately."),
        "fix": "Remove max-snippet:0, or raise it to a real character budget.",
        "owner": "SEO / Platform engineering",
        "effort": "trivial",
    },
    "G0.DIRECTIVE.DATA_NOSNIPPET": {
        "title": "data-nosnippet wraps primary page content",
        "mechanism": ("A data-nosnippet attribute suppresses the wrapped text from "
                      "extracts. When it wraps a substantive region it removes exactly "
                      "the sentence a consumer would otherwise quote, while leaving the "
                      "page apparently healthy."),
        "fix": "Narrow data-nosnippet to the specific elements that must not be quoted.",
        "owner": "Frontend engineering",
        "effort": "low",
    },
    "G0.DIRECTIVE.NOARCHIVE_NOCACHE": {
        "title": "noarchive or nocache limits how assistants may use the page",
        "mechanism": ("Microsoft documents that content marked noarchive is not "
                      "included in Copilot answers or linked from them, and that "
                      "nocache limits Copilot to the URL, title and snippet. The page "
                      "stays indexed and ranking in search while being removed from, "
                      "or thinned on, an assistant surface -- rarely what an owner of "
                      "reference content intends."),
        "fix": "Remove noarchive from reference and evergreen content.",
        "owner": "SEO",
        "effort": "trivial",
    },
    "G0.DIRECTIVE.CONFLICT": {
        "title": "HTTP header and meta robots directives disagree",
        "mechanism": ("When a header and a meta tag both carry robots directives, "
                      "consumers apply the most restrictive combination. A page whose "
                      "meta tag says index while its header says noindex is not "
                      "indexed, and the author reading the page source has no way to "
                      "see why."),
        "fix": "Set robots directives in exactly one place per page.",
        "owner": "Platform engineering / SEO",
        "effort": "low",
    },
}


def _emit_directive(context: Context, out: FindingsFile, check_id: str,
                    page_hits: Sequence[Tuple[str, Any]], total_pages: int) -> None:
    spec = _DIRECTIVE_SPEC[check_id]
    groups = analyzer.group_by_template(context, list(page_hits))
    by_url = {url: payload for url, payload in page_hits}

    for group in groups:
        scope = analyzer.scope_from_group(group, total_pages)
        example_url = group["example_urls"][0]
        payload = by_url.get(example_url, {})
        page = next((p for p in context.pages if p.url == example_url), None)
        if page is None:
            continue

        refs = []
        if check_id == "G0.DIRECTIVE.NOINDEX_HEADER":
            refs.append(header_ref(page.url_hash, "x-robots-tag",
                                   page.entry.header("x-robots-tag")))
        elif check_id == "G0.DIRECTIVE.DATA_NOSNIPPET":
            nodes = page.doc.data_nosnippet_nodes()
            if nodes:
                refs.append(dom_ref(page.url_hash, selector_for(nodes[0]),
                                    page.doc.visible_text(nodes[0])[:200]))
        else:
            node = page.doc.soup.find("meta", attrs={"name": True, "content": True})
            directives = page.doc.robots_meta()
            if directives:
                target = page.doc.soup.find(
                    "meta", attrs={"name": directives[0][0]})
                if target is not None:
                    refs.append(dom_ref(page.url_hash, selector_for(target),
                                        str(target)[:200]))
            if not refs and page.entry.header("x-robots-tag"):
                refs.append(header_ref(page.url_hash, "x-robots-tag",
                                       page.entry.header("x-robots-tag")))
            if not refs and node is not None:
                refs.append(dom_ref(page.url_hash, selector_for(node), str(node)[:200]))
        refs.append(metric_ref(page.url_hash, "pages_with_directive",
                               group["pages_affected"], 0))

        value = payload.get("value") or payload.get("meta") or ""
        detail = ""
        if check_id == "G0.DIRECTIVE.DATA_NOSNIPPET":
            detail = (f"The attribute wraps {payload.get('nodes', 1)} element(s) "
                      f"carrying {payload.get('words', 0)} words of content. ")
        elif check_id == "G0.DIRECTIVE.CONFLICT":
            detail = (f"The X-Robots-Tag header says `{payload.get('header', '')}` "
                      f"while the robots meta tag says `{payload.get('meta', '')}`; the "
                      f"most restrictive combination applies. ")
        elif value:
            detail = f"The directive value is `{value}`. "

        out.add(Finding(
            check_id=check_id,
            title=spec["title"],
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"content pages"
                + (f" in template cluster {group['template_id']}"
                   if group.get("template_id") else "")
                + f" carry this directive. {detail}"
                + f"Affected pages include {analyzer.summarise_urls(group['urls'])}. "
                + "These pages are not utility surfaces: pagination, search results, "
                  "cart, login and print views were excluded before this check ran, "
                  "because a noindex on those is correct engineering."
            ),
            mechanism=spec["mechanism"],
            confidence="high", signal_count=2,
            scope=scope,
            evidence_refs=refs,
            suggested_action=SuggestedAction(
                summary=spec["fix"],
                steps=[
                    f"Locate where the directive is set: a template, a CMS field, a "
                    f"CDN rule, or an SEO plugin default. It applies to "
                    f"{group['pages_affected']} sampled page(s), so it is most likely "
                    f"set at the template or platform level rather than per page.",
                    "Remove it from content pages while keeping it on the utility pages "
                    "where it is correct: search results, faceted listings, cart and "
                    "checkout, thank-you pages and print views.",
                    "If the directive was intended for a subset only, scope it with an "
                    "explicit path rule rather than a global default.",
                ],
                effort=spec["effort"], owner_role=spec["owner"],
                verification=(
                    "Fetch an affected URL and inspect both the response headers and the "
                    "page source: neither should carry the directive. Then confirm in "
                    "a search console that the page is eligible for indexing and "
                    "snippets."
                ),
                expected_effect=(
                    f"Restores index and quote eligibility for "
                    f"{group['pages_affected']} sampled page(s) and any others sharing "
                    f"the same template."
                ),
            ),
        ))


# ========================================================================================
# Gating
# ========================================================================================

def check_gating(context: Context, out: FindingsFile) -> None:
    """G0.GATING.* -- served to humans, refused to machines."""
    probes = [e for e in context.ledger.entries(purpose="probe") if e.probe_agent]
    baseline = {p.url.split("#probe=")[0]: p for p in context.pages}

    if not probes:
        out.skip("G0.GATING.BOT_STATUS_DIVERGENCE",
                 reason="no user-agent divergence probes are recorded in the ledger",
                 remedy="run the orchestrator, which issues a small fixed probe set")
    else:
        _check_divergence(context, out, probes, baseline)

    if not _check_non_browser_refused(context, out):
        _check_rate_limited(context, out)
    _check_auth_wall(context, out)
    _check_paywall(context, out)


def _challenge_markers(text: str) -> List[str]:
    lowered = text.lower()
    return [marker for marker in lexicons.CHALLENGE_FINGERPRINTS if marker in lowered]


def _check_divergence(context: Context, out: FindingsFile,
                      probes: Sequence[Any], baseline: Dict[str, Any]) -> None:
    divergences: List[Dict[str, Any]] = []

    for probe in probes:
        base_url = probe.url.split("#probe=")[0]
        base_page = baseline.get(base_url)
        if base_page is None:
            continue
        base_entry = base_page.entry
        if not base_entry.ok:
            continue

        reasons: List[str] = []
        if probe.status != base_entry.status:
            reasons.append(
                f"status {probe.status or probe.outcome} for a user-agent naming "
                f"{probe.probe_agent} versus {base_entry.status} for the generic "
                f"audit agent")
        else:
            base_len = base_entry.content_length
            probe_len = probe.content_length
            # Only a materially *smaller* body suggests refusal: challenge and block pages
            # are small. A larger body is ordinary variance on a dynamic page.
            if base_len and probe_len and probe_len < base_len * 0.5:
                reasons.append(
                    f"body length drops materially ({probe_len} vs {base_len} bytes) "
                    f"at the same {probe.status} status")

        markers: List[str] = []
        if probe.content_hash:
            try:
                markers = _challenge_markers(context.ledger.text(probe))
            except Exception:
                markers = []
        base_markers = _challenge_markers(context.ledger.text(base_entry)) \
            if base_entry.content_hash else []
        new_markers = [m for m in markers if m not in base_markers]
        if new_markers:
            reasons.append(
                f"the response contains challenge-page wording "
                f"({', '.join(repr(m) for m in new_markers[:2])}) that the generic "
                f"agent's response did not")

        if reasons:
            divergences.append({
                "url": base_url, "agent": probe.probe_agent, "reasons": reasons,
                "probe": probe, "base": base_entry,
            })

    # Rule R1: a single URL diverging is never enough. Geographic and CDN variance is
    # real, and one-off differences are common.
    min_signals = int(context.threshold(
        "false_positive_control.min_signals_for_inference", 2))
    by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for item in divergences:
        by_agent.setdefault(item["agent"], []).append(item)

    fired = False
    for agent in sorted(by_agent):
        items = by_agent[agent]
        if len(items) < min_signals:
            if items:
                out.observe(
                    "G0.GATING.BOT_STATUS_DIVERGENCE",
                    f"Single-URL response difference for a user-agent naming {agent}",
                    (f"{items[0]['url']} responded differently: "
                     f"{items[0]['reasons'][0]}. Only {len(items)} of the probed URLs "
                     f"diverged, which is below the {min_signals} agreeing observations "
                     f"this check requires, so it is recorded as an observation rather "
                     f"than a finding. CDN and geographic variance produce differences "
                     f"of this kind routinely."),
                    confidence="low",
                )
            continue

        fired = True
        example = items[0]
        out.add(Finding(
            check_id="G0.GATING.BOT_STATUS_DIVERGENCE",
            title=(f"Pages are served to a generic agent but refused or challenged when "
                   f"the request names {agent}"),
            evidence=(
                f"{len(items)} probed URLs were requested twice, once with this audit's "
                f"generic user-agent and once with a truthful user-agent that also names "
                f"{agent}. In every case the second request was treated differently: "
                f"{example['reasons'][0]} (example: {example['url']}). "
                f"Both requests were identical apart from the user-agent string, used "
                f"the same IP address, sent no cookies, and were issued within the same "
                f"crawl. "
                f"Limitation stated: the probe names {agent} without coming from "
                f"{agent}'s published addresses, by design. A CDN that verifies crawlers "
                f"by address refuses unverified requests naming a crawler -- correct "
                f"spoofing protection that still admits the real {agent}. This result "
                f"shows a rule keyed on the user-agent string, which may or may not also "
                f"verify addresses; and a null result is not proof that no gating exists."
            ),
            mechanism=(
                "Bot-management rules at the CDN or WAF layer frequently match on "
                "user-agent substrings. A rule written to stop scrapers commonly catches "
                "the retrieval crawlers assistants use to fetch citable sources, so the "
                "site is visible to browsers and to Googlebot while being refused to the "
                "agent that would have quoted it. Nothing about this is visible in the "
                "site's own analytics, because the blocked requests never reach it."
            ),
            confidence="medium", signal_count=len(items), fact_class="primary",
            scope=Scope(level="site", pages_affected=len(items),
                        pages_sampled=len(probes),
                        example_urls=[i["url"] for i in items[:3]]),
            evidence_refs=[
                metric_ref(example["probe"].entry_id,
                           f"probe_status_{agent}",
                           example["probe"].status or 0,
                           example["base"].status or 200),
                metric_ref(example["base"].entry_id, "baseline_status",
                           example["base"].status or 0, 200),
            ],
            suggested_action=SuggestedAction(
                summary=(f"Allowlist declared retrieval crawlers at the CDN or WAF layer "
                         f"and serve them the same content a browser receives."),
                steps=[
                    f"Find the bot-management rule that matches on user-agent and "
                    f"identify why {agent} is caught by it. If that rule already admits "
                    f"requests from {agent}'s published IP ranges, the real crawler is "
                    f"let in and no change is needed.",
                    "Add an explicit allow rule for the retrieval crawlers you want to "
                    "be cited by, verifying them by their published IP ranges or reverse "
                    "DNS rather than by user-agent alone, which is spoofable.",
                    "Serve a 200 with the real content to non-JavaScript agents instead "
                    "of an interstitial challenge page; a challenge returns 200 with no "
                    "content, so consumers store an empty page rather than retrying.",
                    "Keep rate limiting in place: the goal is correct identification, "
                    "not unlimited access.",
                ],
                effort="medium", owner_role="Infrastructure / Security",
                verification=(f"Request the same URL with and without {agent} in the "
                              f"user-agent and confirm both return 200 with comparable "
                              f"body sizes and no challenge wording."),
                expected_effect=("Makes the site's content actually retrievable by the "
                                 "agents that select citable sources."),
            ),
        ))

    if not fired and probes:
        out.note_posture("bot_gating_not_detected", True)


def _bot_management_fingerprints(entry: Any) -> List[str]:
    headers = {str(k).lower(): str(v) for k, v in (entry.headers or {}).items()}
    found: List[str] = []
    for name, needle in lexicons.BOT_MANAGEMENT_HEADER_FINGERPRINTS:
        if name.endswith("-"):
            present = sorted(h for h in headers if h.startswith(name))
            if present:
                found.append(f"header {present[0]}")
        elif name in headers and (not needle or needle in headers[name]):
            found.append(f"cookie {needle.rstrip('=')}" if needle else f"header {name}")
    return sorted(set(found))


def _check_non_browser_refused(context: Context, out: FindingsFile) -> bool:
    """G0.GATING.NON_BROWSER_REFUSED -- content refused to a client that runs no challenge.

    Returns True when the finding was emitted, so the weaker rate-limit observation is not
    also recorded for the same refusals.
    """
    attempts = [e for e in context.ledger.entries(purpose="page")
                if e.status is not None or e.outcome == ledger_mod.OUTCOME_CIRCUIT_OPEN]
    refused = [e for e in attempts
               if e.status in lexicons.CONTENT_REFUSAL_STATUSES
               or e.outcome == ledger_mod.OUTCOME_CIRCUIT_OPEN]
    home = next((e for e in attempts if e.depth == 0), None)
    if home is None or home.status not in lexicons.CONTENT_REFUSAL_STATUSES:
        return False
    share = float(context.threshold("false_positive_control.site_wide_share", 0.6))
    if len(refused) / len(attempts) < share:
        return False

    home_normal = urlnorm.try_normalise(home.final_url or home.url)
    home_host = home_normal.host if home_normal else ""
    statuses = sorted({e.status for e in refused if e.status is not None})
    signals = [f"{len(refused)}/{len(attempts)} attempted content pages were refused "
               f"({', '.join(f'HTTP {s}' for s in statuses)}), including the homepage"]
    policy_served = [
        e for e in context.ledger.entries(purpose="robots") if e.status == 200
        and (n := urlnorm.try_normalise(e.final_url or e.url)) is not None
        and n.host == home_host
    ]
    if policy_served:
        signals.append(f"robots.txt on the same host returned 200 "
                       f"({policy_served[0].final_url or policy_served[0].url}), so the host "
                       f"answers machine clients and refuses them only for content")
    body = context.ledger.text(home) if home.content_hash else ""
    wording = _challenge_markers(body)
    fingerprints = _bot_management_fingerprints(home)
    if wording or fingerprints:
        parts = ([f"challenge wording {', '.join(repr(w) for w in wording[:2])}"]
                 if wording else []) + fingerprints[:3]
        signals.append(f"the refusal carries a bot-management fingerprint "
                       f"({'; '.join(parts)})")

    min_signals = int(context.threshold(
        "false_positive_control.min_signals_for_inference", 2))
    if len(signals) < min_signals:
        out.observe(
            "G0.GATING.NON_BROWSER_REFUSED",
            "Content pages were refused to this audit's client",
            (f"{signals[0]}, with no second signal: robots.txt was not served on the same "
             f"host and the refusal carries no bot-management fingerprint. A refusal "
             f"alone can be a temporary block or an origin fault, so it is recorded as an "
             f"observation rather than a finding."),
            confidence="low",
        )
        return False

    refs = [metric_ref(home.entry_id, "content_pages_refused", len(refused), 0)]
    server = home.header("server")
    if server:
        refs.append(header_ref(home.entry_id, "server", server))
    if body and home.is_html:
        title = Document(body, url=home.url).title()
        if title:
            refs.append(dom_ref(home.entry_id, "title", title))

    out.add(Finding(
        check_id="G0.GATING.NON_BROWSER_REFUSED",
        title="The site's bot protection refuses clients that do not run a browser",
        evidence=(
            f"Every signal below was observed on requests sent by this audit's honest, "
            f"cookie-less user-agent. " + "; ".join(signals) + f". Homepage: {home.url}. "
            f"Limitation stated: the refusal of this client is observed directly; whether "
            f"the provider's verified-bot allowlist admits a particular retrieval crawler "
            f"cannot be observed without impersonating that crawler, which this audit "
            f"never does."
        ),
        mechanism=(
            "Retrieval crawlers fetch raw HTML and do not execute challenge scripts. A "
            "bot-management layer that refuses or challenges every client without a "
            "browser refuses them too, unless it recognises and allowlists each crawler. "
            "The site loads normally in every browser, so nothing in the site's own "
            "analytics shows that the agents which select citable sources are turned away."
        ),
        confidence="medium", signal_count=len(signals), fact_class="primary",
        scope=Scope(level="site", pages_affected=len(refused), pages_sampled=len(attempts),
                    example_urls=sorted({e.url for e in refused})[:3]),
        evidence_refs=refs,
        suggested_action=SuggestedAction(
            summary=("Allow verified search and retrieval crawlers through the "
                     "bot-management layer and serve them the real HTML."),
            steps=[
                "Open the CDN or WAF bot-management settings and find the rule or managed "
                "challenge that answers non-browser clients with a 401/403/429/503.",
                "Enable the provider's verified-bot allowance and confirm the retrieval "
                "crawlers you want to be cited by are included; verify them by published "
                "IP ranges or reverse DNS, never by user-agent alone.",
                "Serve verified crawlers a 200 with the full HTML instead of a challenge "
                "page, and keep challenges and rate limits for unverified automation.",
            ],
            effort="low", owner_role="Infrastructure / Security",
            verification=("From a server with no browser, request the homepage with a plain "
                          "HTTP client and confirm a 200 with the page content, or confirm "
                          "in the provider's logs that requests from a crawler's published "
                          "IP ranges are allowed."),
            expected_effect=("Makes the site's content retrievable by the agents that "
                             "select and quote sources, which every later stage depends on."),
        ),
    ))
    return True


def _check_rate_limited(context: Context, out: FindingsFile) -> None:
    limited = [e for e in context.ledger.entries()
               if e.status in (429, 503) or e.outcome == ledger_mod.OUTCOME_CIRCUIT_OPEN]
    if not limited:
        return
    example = limited[0]
    out.observe(
        "G0.GATING.RATE_LIMITED",
        "The host rate-limited or refused a polite, low-rate crawl",
        (f"{len(limited)} request(s) returned 429/503 or were abandoned after repeated "
         f"refusals, during a crawl that issued at most "
         f"{context.threshold('crawl.per_host_concurrency', 2)} concurrent requests with "
         f"a minimum "
         f"{context.threshold('crawl.per_host_min_interval_seconds', 0.5)}s interval per "
         f"host. Example: {example.url} ({example.status or example.outcome}). "
         f"Aggressive limiting of well-behaved crawlers suppresses indexing without "
         f"blocking anything explicitly, and appears in no configuration as a block. "
         f"Recorded as an observation rather than a finding because transient capacity "
         f"issues produce the same signal."),
        confidence="medium",
    )


def _check_auth_wall(context: Context, out: FindingsFile) -> None:
    sitemap_urls = set(_sitemap_urls(context))
    hits: List[Tuple[str, Any]] = []

    for page in context.content_pages():
        main = page.doc.main_node()
        if main is None:
            continue
        forms = [f for f in page.doc.forms() if f["in_main"] and f["has_password"]]
        main_words = textnorm.word_count(page.doc.main_text())
        redirected_to_login = any(
            "login" in str(hop.get("to", "")).lower()
            or "signin" in str(hop.get("to", "")).lower()
            for hop in page.entry.redirects
        )
        in_sitemap = page.url in sitemap_urls or page.entry.final_url in sitemap_urls

        # Rule R1: a login link in the header is not a wall. The gate must be inside the
        # main region AND the main content must actually be missing.
        if (forms or redirected_to_login) and main_words < 120:
            hits.append((page.url, {
                "words": main_words, "forms": len(forms),
                "redirect": redirected_to_login, "in_sitemap": in_sitemap,
                "selector": forms[0]["selector"] if forms else "",
            }))

    if not hits:
        return
    total = len(context.content_pages())
    for group in analyzer.group_by_template(context, hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        refs = [metric_ref(page.url_hash, "main_region_words", payload["words"], 120)]
        if payload["selector"]:
            refs.append(dom_ref(page.url_hash, payload["selector"],
                                "authentication form in the main content region"))
        out.add(Finding(
            check_id="G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT",
            title="Pages advertised as public require authentication to read",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"pages contain an authentication form inside the main content region "
                f"(or redirect to a login URL) while carrying only "
                f"{payload['words']} words of main-region text. "
                + ("These URLs are listed in the public XML sitemap, so the site is "
                   "advertising them for indexing while refusing to serve them. "
                   if payload["in_sitemap"] else "")
                + f"Account and dashboard paths were excluded by path class before this "
                  f"check ran. Affected: "
                  f"{analyzer.summarise_urls(group['urls'])}."
            ),
            mechanism=(
                "A consumer fetching the URL receives the login form rather than the "
                "content, so what gets indexed and quoted is the login page. Publishing "
                "the URL in a sitemap compounds it: the site asks for the page to be "
                "crawled and then serves nothing, which wastes crawl budget and can be "
                "read as cloaking."
            ),
            confidence="high", signal_count=2, fact_class="primary",
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=refs,
            suggested_action=SuggestedAction(
                summary="Serve a substantive public portion of these pages, or remove "
                        "them from the public sitemap.",
                steps=[
                    "Decide per page class whether the content is meant to be public. "
                    "If it is not, remove those URLs from the XML sitemap and mark them "
                    "noindex.",
                    "If it is meant to be public, serve the content to unauthenticated "
                    "requests and gate only the interactive or personalised parts.",
                    "For genuinely partial access, publish the free portion as real text "
                    "and declare the gate with isAccessibleForFree: false plus a hasPart "
                    "block describing the free section, so consumers index what is free "
                    "instead of treating the page as empty.",
                ],
                effort="medium", owner_role="Product / Backend engineering",
                verification="Fetch the URL with no cookies and confirm the response "
                             "body contains the page's substantive text rather than a "
                             "login form.",
                expected_effect="Turns pages that currently contribute nothing into "
                                "indexable, quotable content.",
            ),
        ))


def _check_paywall(context: Context, out: FindingsFile) -> None:
    hits: List[Tuple[str, Any]] = []
    for page in context.content_pages():
        text_lower = page.doc.main_text().lower()
        markers = [m for m in lexicons.PAYWALL_FINGERPRINTS if m in text_lower]
        if not markers:
            continue
        cluster = context.cluster_of(page)
        median_words = cluster.median("main_words") if cluster else 0.0
        main_words = textnorm.word_count(page.doc.main_text())
        thin = bool(median_words and main_words < median_words * 0.5)

        declared = False
        for node in page.sd.nodes:
            value = node.get("isAccessibleForFree")
            if value is not None and str(value).lower() in ("false", "0", "no"):
                declared = True
            if node.get("hasPart") is not None:
                declared = True
        if declared:
            out.note_posture("paywall_declared_correctly", True)
            continue
        if markers and (thin or len(markers) >= 2):
            hits.append((page.url, {"markers": markers, "words": main_words,
                                    "median": median_words}))

    if not hits:
        return
    total = len(context.content_pages())
    for group in analyzer.group_by_template(context, hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        out.add(Finding(
            check_id="G0.GATING.PAYWALL_UNMARKED",
            title="Content is behind a subscription gate that is not declared in markup",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"pages show subscription wording "
                f"({', '.join(repr(m) for m in payload['markers'][:2])}) and carry "
                f"{payload['words']} words of main-region text against a template median "
                f"of {payload['median']:.0f}. No isAccessibleForFree or hasPart paywall "
                f"markup is present on any of them."
            ),
            mechanism=(
                "An undeclared paywall makes a truncated page look like a thin page, or "
                "like cloaking when a crawler receives less than a subscriber does. "
                "Declaring the gate explicitly is what lets a consumer index and quote "
                "the free portion instead of discounting the whole page -- so the "
                "business keeps the paywall and gains the citation."
            ),
            confidence="medium", signal_count=2,
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                metric_ref(page.url_hash, "main_words_vs_template_median",
                           payload["words"], payload["median"]),
                dom_ref(page.url_hash, "main", page.doc.main_text()[:200]),
            ],
            suggested_action=SuggestedAction(
                summary="Keep the paywall and declare it correctly so the free portion "
                        "is indexed and quotable.",
                steps=[
                    "Add isAccessibleForFree: false to the Article (or equivalent) node "
                    "on gated pages.",
                    "Add a hasPart block of type WebPageElement with "
                    "isAccessibleForFree: false and a cssSelector naming the gated "
                    "region, so consumers can tell which part is free.",
                    "Ensure the free portion is substantive real text in the response, "
                    "not a teaser rendered client-side.",
                    "Serve the same free portion to every agent; never vary content by "
                    "user-agent, which is cloaking and carries its own penalties.",
                ],
                effort="low", owner_role="Frontend engineering / SEO",
                verification="Validate the structured data and confirm the gated region's "
                             "selector matches the markup; then fetch the page with no "
                             "cookies and confirm the free portion is present as text.",
                expected_effect="Makes the free portion of gated content eligible for "
                                "indexing and quotation instead of being discounted.",
            ),
        ))


# ========================================================================================
# HTTP hygiene
# ========================================================================================

def check_http(context: Context, out: FindingsFile) -> None:
    _check_redirects(context, out)
    _check_canonical_host(context, out)
    _check_soft_404(context, out)
    _check_ttfb(context, out)


def _check_redirects(context: Context, out: FindingsFile) -> None:
    limit = int(context.threshold("http.max_redirect_hops", 2))
    offenders = []
    for entry in context.ledger.entries():
        if entry.purpose not in ("page", "sitemap"):
            continue
        hops = len(entry.redirects)
        if hops > limit or (hops and entry.status and entry.status >= 400):
            offenders.append(entry)
    if not offenders:
        return
    example = offenders[0]
    chain = " -> ".join([str(example.redirects[0].get("from", ""))]
                        + [str(h.get("to", "")) for h in example.redirects])
    out.add(Finding(
        check_id="G0.HTTP.REDIRECT_CHAIN",
        title="URLs reach their destination through long redirect chains",
        evidence=(
            f"{len(offenders)} of {len(context.ledger.entries())} fetched URLs "
            f"redirected more than {limit} times, or ended a chain on an error status. "
            f"Longest example: {chain} (final status {example.status})."
        ),
        mechanism=(
            "Fetchers have a hop budget and abandon chains that exceed it, so the "
            "destination is never read. Each hop also adds a full round trip, which "
            "matters most for live in-conversation retrieval where the fetch competes "
            "with a response deadline."
        ),
        confidence="high", signal_count=2,
        scope=Scope(level="site" if len(offenders) >= 3 else "section",
                    pages_affected=len(offenders),
                    pages_sampled=len(context.ledger.entries()),
                    example_urls=[e.url for e in offenders[:3]]),
        evidence_refs=[metric_ref(example.entry_id, "redirect_hops",
                                  len(example.redirects), limit)],
        suggested_action=SuggestedAction(
            summary="Collapse redirect chains so every URL reaches its destination in "
                    "one hop.",
            steps=[
                "List the redirect rules in order and find where they compound: "
                "http to https, non-www to www, trailing slash, and a legacy path "
                "rewrite each add a hop and are usually four separate rules.",
                "Rewrite the rules so the first response goes straight to the final "
                "canonical URL.",
                "Update internal links and sitemap entries to point at final URLs so no "
                "redirect is needed at all.",
            ],
            effort="low", owner_role="Infrastructure / SEO",
            verification="Follow each affected URL and confirm at most one redirect hop "
                         "to a 200 response.",
            expected_effect="Removes latency and hop-budget risk from every fetch of "
                            "these URLs.",
        ),
    ))


def _check_canonical_host(context: Context, out: FindingsFile) -> None:
    probes = [e for e in context.ledger.entries(purpose="probe") if not e.probe_agent]
    serving = [e for e in probes if e.status and 200 <= e.status < 300
               and not e.redirects]
    if len(serving) < 2:
        if not probes:
            out.skip("G0.HTTP.MIXED_CANONICAL_HOST",
                     reason="host-variant probes are not recorded in the ledger",
                     remedy="run the orchestrator, which probes the http/https and "
                            "www/non-www variants of the site root")
        else:
            out.note_posture("canonical_host_resolves_to_one_variant", True)
        return

    variants = sorted({e.final_url or e.url for e in serving})
    out.add(Finding(
        check_id="G0.HTTP.MIXED_CANONICAL_HOST",
        title="Several host variants serve the site without redirecting to one preferred "
              "host",
        evidence=(
            f"{len(serving)} host variants of the site root each returned 200 with no "
            f"redirect to a single preferred host: {', '.join(variants)}. Each is a "
            f"distinct URL to a consumer, so links, citations and any authority they "
            f"carry are split across them instead of accumulating on one."
        ),
        mechanism=(
            "Duplicate hosts fragment every signal that depends on URL identity. Two "
            "consumers can hold two different canonical URLs for the same page, and "
            "neither accumulates the full weight of references to the content."
        ),
        confidence="high", signal_count=len(serving), fact_class="primary",
        scope=Scope(level="site", pages_affected=len(serving),
                    pages_sampled=len(probes), example_urls=variants[:3]),
        evidence_refs=[metric_ref(serving[0].entry_id, "host_variants_serving_200",
                                  len(serving), 1)]
        + [header_ref(e.entry_id, "content-type", e.header("content-type"))
           for e in serving[:2]],
        suggested_action=SuggestedAction(
            summary="Pick one canonical host and 301-redirect every other variant to it.",
            steps=[
                f"Choose the preferred variant (typically https with your preferred www "
                f"convention): {variants[0]}.",
                "Add a single 301 redirect from every other variant to the preferred "
                "host, preserving the path and query.",
                "Make rel=canonical on every page point at the preferred host.",
                "Update the XML sitemap so every URL uses the preferred host.",
                "Confirm HSTS and any CDN host rules agree with the choice.",
            ],
            effort="low", owner_role="Infrastructure / SEO",
            verification=f"Request each of {', '.join(variants)} and confirm all but "
                         f"{variants[0]} answer with a single 301 to it.",
            expected_effect="Consolidates citation and link authority onto one URL per "
                            "page.",
        ),
    ))


def _check_soft_404(context: Context, out: FindingsFile) -> None:
    share_threshold = float(context.threshold("http.soft_404_text_share", 0.4))
    hits: List[Tuple[str, Any]] = []
    for page in context.content_pages():
        if not page.entry.status or page.entry.status != 200:
            continue
        text_lower = page.doc.main_text().lower()
        markers = [m for m in lexicons.SOFT_404_FINGERPRINTS if m in text_lower]
        if not markers:
            continue
        cluster = context.cluster_of(page)
        median_words = cluster.median("main_words") if cluster else 0.0
        main_words = textnorm.word_count(page.doc.main_text())
        thin = bool(median_words and main_words < median_words * share_threshold)
        # Rule R1: an article *about* 404 pages contains the wording without being one.
        # Both signals required.
        if markers and thin:
            hits.append((page.url, {"markers": markers, "words": main_words,
                                    "median": median_words}))
    if not hits:
        return
    total = len(context.content_pages())
    for group in analyzer.group_by_template(context, hits):
        payload = group["payloads"][0]
        page = next((p for p in context.pages if p.url == group["example_urls"][0]), None)
        if page is None:
            continue
        out.add(Finding(
            check_id="G0.HTTP.SOFT_404",
            title="Missing pages return 200 with a not-found body",
            evidence=(
                f"{analyzer.kn(group['pages_affected'], group['pages_sampled'])} sampled "
                f"URLs returned HTTP 200 while their main region contains not-found "
                f"wording ({', '.join(repr(m) for m in payload['markers'][:2])}) and "
                f"only {payload['words']} words against a template median of "
                f"{payload['median']:.0f}. Both signals are required for this check to "
                f"fire, so a page discussing error handling is not flagged."
            ),
            mechanism=(
                "A 200 status tells a consumer the content exists, so error pages are "
                "indexed as real content and can be retrieved and quoted as if they were "
                "the answer. It also hides genuine breakage from every monitoring tool "
                "that watches status codes."
            ),
            confidence="medium", signal_count=2,
            scope=analyzer.scope_from_group(group, total),
            evidence_refs=[
                metric_ref(page.url_hash, "main_words_vs_template_median",
                           payload["words"], payload["median"]),
                dom_ref(page.url_hash, "main", page.doc.main_text()[:200]),
            ],
            suggested_action=SuggestedAction(
                summary="Return a real 404 or 410 status for missing content.",
                steps=[
                    "Change the handler so unknown URLs respond 404 (or 410 for content "
                    "deliberately removed) instead of 200.",
                    "Keep the helpful error page body: the status code is what matters, "
                    "not the design.",
                    "Where the URL changed, 301-redirect to the new location rather than "
                    "serving an error page.",
                ],
                effort="low", owner_role="Backend engineering",
                verification="Request a deliberately invalid URL under the same path and "
                             "confirm the response status is 404 rather than 200.",
                expected_effect="Stops error pages accumulating in indexes as citable "
                                "content.",
            ),
        ))


def _check_ttfb(context: Context, out: FindingsFile) -> None:
    values = [p.entry.ttfb_ms for p in context.pages
              if p.entry.ttfb_ms is not None and p.entry.ok]
    if len(values) < 3:
        out.skip("G0.HTTP.SLOW_TTFB",
                 reason="fewer than three timed responses were recorded, so a percentile "
                        "would not be meaningful")
        return
    threshold = float(context.threshold("http.slow_ttfb_ms", 1500))
    p75 = textnorm.percentile(values, 75)
    out.note_metric("ttfb_p75_ms", round(p75, 1))
    if p75 <= threshold:
        if p75 >= threshold * (1 - context.near_miss_margin()):
            out.near_miss("G0.HTTP.SLOW_TTFB", "ttfb_p75_ms", round(p75, 1), threshold,
                          scope_key="site",
                          detail=f"75th-percentile time to first byte is {p75:.0f}ms "
                                 f"across {len(values)} sampled pages, within the "
                                 f"near-miss margin of the {threshold:.0f}ms threshold.")
        return
    out.observe(
        "G0.HTTP.SLOW_TTFB",
        "Server response time is slow enough to reduce crawl frequency",
        (f"75th-percentile time to first byte is {p75:.0f}ms across {len(values)} sampled "
         f"pages, against a {threshold:.0f}ms threshold. Slow origins are crawled less "
         f"often and risk timing out for live in-conversation fetches, which operate "
         f"under a response deadline. Recorded as an observation rather than a finding "
         f"because the measurement includes network distance between this audit and the "
         f"origin, which is not a property of the site."),
        confidence="medium",
    )


# ========================================================================================
# Sitemaps and machine-readable feeds
# ========================================================================================

def _sitemap_entries(context: Context) -> List[Any]:
    return context.ledger.entries(purpose="sitemap")


def _sitemap_urls(context: Context) -> List[str]:
    urls: List[str] = []
    for entry in _sitemap_entries(context):
        if not entry.ok:
            continue
        parsed = _parse_sitemap(context, entry)
        urls.extend(parsed["urls"])
    return urls


def _parse_sitemap(context: Context, entry: Any) -> Dict[str, Any]:
    """Parse one stored sitemap. Delegates to the shared, network-free parser."""
    try:
        return sitemap.parse(context.ledger.body(entry))
    except Exception as exc:
        return {"urls": [], "lastmods": [], "children": [], "error": f"body unavailable: {exc}"}


def check_sitemaps(context: Context, out: FindingsFile) -> None:
    entries = _sitemap_entries(context)
    robots, robots_entry = _load_robots(context)
    declared_in_robots = bool(robots and robots.sitemap_urls())

    usable = [e for e in entries if e.ok]
    if not usable and robots_entry is None and not entries and context.homepage() is None:
        out.skip("G0.SITEMAP.MISSING", reason="neither robots.txt, any sitemap location "
                                              "nor the homepage is recorded in the ledger")
        return
    if not usable:
        origin = context.site_url()
        refused = [e for e in entries if e.status in lexicons.CONTENT_REFUSAL_STATUSES]
        out.add(Finding(
            check_id="G0.SITEMAP.MISSING",
            title=("The site's sitemaps refuse non-browser clients" if refused else
                   "No XML sitemap was found at any conventional location"),
            evidence=(
                (f"The sitemap location(s) {', '.join(e.url for e in refused[:2])} "
                 f"answered HTTP {', '.join(sorted({str(e.status) for e in refused}))} to "
                 f"this audit's honest, cookie-less client, so a crawler receives a refusal "
                 f"where the site's URL inventory should be. " if refused else "")
                + f"No usable sitemap was retrieved. "
                f"{len(entries)} candidate location(s) were tried "
                f"(/sitemap.xml, /sitemap_index.xml and any Sitemap: directive in "
                f"robots.txt) and none returned a parseable urlset or sitemapindex. "
                f"robots.txt "
                + ("declares no Sitemap: directive." if not declared_in_robots
                   else f"declares {len(robots.sitemap_urls())} Sitemap: directive(s), "
                        f"none of which resolved.")
            ),
            mechanism=(
                "Without a sitemap a crawler discovers pages only by following links, so "
                "anything weakly linked is found late or not at all, and the site gives "
                "no signal about which URLs it considers canonical or how recently they "
                "changed."
            ),
            confidence="high", signal_count=2,
            scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                        example_urls=[f"{origin}/sitemap.xml"] if origin else []),
            evidence_refs=[metric_ref(
                (robots_entry or (entries[0] if entries else None)).entry_id
                if (robots_entry or entries) else context.homepage().url_hash,
                "usable_sitemaps_found", 0, 1)],
            suggested_action=SuggestedAction(
                summary="Publish an XML sitemap and declare it in robots.txt.",
                steps=[
                    "Generate a sitemap listing every canonical, indexable URL, with a "
                    "lastmod reflecting when each document's content last changed.",
                    "Serve it at /sitemap.xml, using a sitemapindex if the site exceeds "
                    "50,000 URLs or 50 MB.",
                    "Add `Sitemap: https://<host>/sitemap.xml` to robots.txt.",
                    "Exclude utility URLs (search, cart, checkout, filtered listings) so "
                    "the file states what you want crawled.",
                    "Regenerate it on publish rather than on deploy, so lastmod stays "
                    "meaningful.",
                ],
                effort="low", owner_role="Infrastructure / SEO",
                verification="Fetch /sitemap.xml and confirm a 200 with a valid urlset, "
                             "then confirm robots.txt names it.",
                expected_effect="Gives crawlers a complete, dated inventory of canonical "
                                "URLs instead of requiring link discovery.",
            ),
        ))
        return

    if not declared_in_robots and robots_entry is not None:
        out.add(Finding(
            check_id="G0.SITEMAP.NOT_IN_ROBOTS",
            title="A sitemap exists but robots.txt does not declare it",
            evidence=(
                f"A usable sitemap was found at {usable[0].final_url or usable[0].url}, "
                f"but robots.txt contains no `Sitemap:` directive, so a crawler has to "
                f"guess the location. robots.txt is the one file every crawler fetches "
                f"first, which makes it the cheapest place to advertise the sitemap."
            ),
            mechanism=(
                "The Sitemap directive in robots.txt is the standard discovery mechanism. "
                "Without it, a crawler finds the sitemap only by trying conventional "
                "paths, which not all do, and a non-standard location is never found."
            ),
            confidence="high", signal_count=2,
            scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                        example_urls=[usable[0].final_url or usable[0].url]),
            evidence_refs=[metric_ref(robots_entry.entry_id,
                                      "sitemap_directives_in_robots", 0, 1)],
            suggested_action=SuggestedAction(
                summary="Add a Sitemap: directive to robots.txt.",
                steps=[
                    f"Add `Sitemap: {usable[0].final_url or usable[0].url}` to "
                    f"robots.txt, as an absolute URL on its own line.",
                    "Place it outside any User-agent group; the directive is global to "
                    "the file.",
                ],
                effort="trivial", owner_role="Infrastructure / SEO",
                verification="Fetch /robots.txt and confirm the Sitemap: line is "
                             "present and the URL it names returns 200.",
                expected_effect="Makes the sitemap discoverable by every crawler on its "
                                "first request.",
            ),
        ))

    # A sitemap cut at this audit's read cap is not evidence of invalid XML: a file is
    # judged only when it was read whole.
    invalid = [(e, _parse_sitemap(context, e)) for e in usable if not e.truncated]
    out.note_metric("sitemaps_truncated_at_read_cap", len(usable) - len(invalid))
    broken = [(e, p) for e, p in invalid if p.get("error")]
    if broken:
        entry, parsed = broken[0]
        out.add(Finding(
            check_id="G0.SITEMAP.INVALID_XML",
            title="A sitemap does not parse and will be discarded by consumers",
            evidence=(
                f"{len(broken)} of {len(invalid)} fully retrieved sitemap(s) could not be "
                f"parsed. {entry.url}"
                + (f" (redirected to {entry.final_url})"
                   if entry.final_url and entry.final_url != entry.url else "")
                + f": {parsed['error']}. "
                f"A sitemap that does not parse is discarded silently, so the site "
                f"behaves as though it has none while the file appears to exist."
            ),
            mechanism=(
                "Consumers parse sitemaps strictly and discard invalid files without "
                "reporting anything. The author sees a file being served and assumes "
                "discovery is working."
            ),
            confidence="high", signal_count=1,
            scope=Scope(level="site", pages_affected=len(broken),
                        pages_sampled=len(usable),
                        example_urls=[e.final_url or e.url for e, _ in broken[:3]]),
            evidence_refs=[metric_ref(entry.entry_id, "sitemaps_failing_to_parse",
                                      len(broken), 0)],
            suggested_action=SuggestedAction(
                summary="Fix the sitemap so it is valid XML with a urlset or sitemapindex "
                        "root.",
                steps=[
                    f"Correct the reported problem: {parsed['error']}.",
                    "XML-escape every URL: raw &, <, > in query strings are the most "
                    "common cause.",
                    "Confirm the root element is <urlset> or <sitemapindex> with the "
                    "sitemaps.org namespace.",
                    "Serve it as application/xml or text/xml.",
                    "Validate the generated file in CI so a template change cannot break "
                    "it silently again.",
                ],
                effort="low", owner_role="Backend engineering / SEO",
                verification="Parse the sitemap with any XML parser and confirm it "
                             "validates and that the URL count matches expectations.",
                expected_effect="Restores sitemap-based discovery, which is currently "
                                "contributing nothing.",
            ),
        ))

    _check_sitemap_urls_broken(context, out)
    _check_sitemap_coverage(context, out)
    _check_feed(context, out)


def _check_sitemap_urls_broken(context: Context, out: FindingsFile) -> None:
    """Report sampled sitemap URLs that do not resolve, as k/n."""
    sitemap_urls = set(_sitemap_urls(context))
    if not sitemap_urls:
        return
    checked = []
    broken = []
    for entry in context.ledger.entries():
        if entry.purpose not in ("page", "asset"):
            continue
        candidate = entry.url.split("#")[0]
        if candidate not in sitemap_urls:
            continue
        if entry.status in lexicons.CONTENT_REFUSAL_STATUSES:
            continue  # a refusal says nothing about whether the URL exists
        checked.append(entry)
        if entry.status and entry.status >= 400:
            broken.append(entry)
    if len(checked) < 3:
        out.skip("G0.SITEMAP.URLS_BROKEN",
                 reason=f"only {len(checked)} sitemap URL(s) were fetched, which is too "
                        f"few for a meaningful k/n share")
        return
    if not broken:
        out.note_posture("sitemap_sampled_urls_all_resolve", True)
        return
    out.add(Finding(
        check_id="G0.SITEMAP.URLS_BROKEN",
        title="Sitemap URLs do not resolve",
        evidence=(
            f"{analyzer.kn(len(broken), len(checked))} sampled sitemap URLs returned an "
            f"error status. Examples: "
            + "; ".join(f"{e.url} -> {e.status}" for e in broken[:3]) + "."
        ),
        mechanism=(
            "A sitemap is a declaration of what the site wants crawled. Entries that "
            "error waste crawl budget and devalue the whole file as a signal, because a "
            "consumer that finds errors in the sample trusts the rest less."
        ),
        confidence="high", signal_count=2,
        # One dead entry in a sample of sixteen is a page-sized defect, not a site-wide one.
        scope=Scope(level=scope_level_for(len(broken), len(checked)),
                    pages_affected=len(broken), pages_sampled=len(checked),
                    example_urls=[e.url for e in broken[:3]]),
        evidence_refs=[metric_ref(broken[0].entry_id, "sitemap_urls_failing",
                                  len(broken), 0)],
        suggested_action=SuggestedAction(
            summary="Regenerate the sitemap from live canonical URLs only.",
            steps=[
                "Remove the erroring URLs from the sitemap.",
                "Generate the sitemap from the same source of truth that serves the "
                "routes, so deleted content cannot remain listed.",
                "Add a scheduled check that fetches a sample of sitemap URLs and alerts "
                "on error statuses.",
            ],
            effort="low", owner_role="Backend engineering / SEO",
            verification="Fetch every URL in the regenerated sitemap and confirm none "
                         "returns 4xx or 5xx.",
            expected_effect="Restores the sitemap's value as a trustworthy inventory.",
        ),
    ))


def _first_segment(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    return parts[0].lower() if parts else ""


def _check_sitemap_coverage(context: Context, out: FindingsFile) -> None:
    """G0.SITEMAP.STALE_OR_TRUNCATED -- the sitemap no longer describes the site.

    Two independent signs: a file whose URL count sits exactly on a round generator limit
    (the generator stopped, not the site), and whole content sections that crawled pages
    link to but the sitemap never lists.
    """
    min_urls = int(context.threshold("sitemap.coverage_min_urls"))
    round_limits = {int(v) for v in context.threshold("sitemap.round_limits")}
    entries = [e for e in _sitemap_entries(context) if e.ok]
    if not entries:
        return
    listed: List[str] = []
    truncated: List[Tuple[Any, int]] = []
    children: List[str] = []
    for entry in entries:
        parsed = _parse_sitemap(context, entry)
        if parsed.get("error"):
            continue
        listed.extend(parsed["urls"])
        children.extend(parsed.get("children", []))
        if len(parsed["urls"]) in round_limits:
            truncated.append((entry, len(parsed["urls"])))

    # A section can be called missing from the sitemap only when the whole sitemap was read:
    # an index child this audit did not fetch, or a file cut at the read cap, may hold it.
    read_whole = {n.url for n in (urlnorm.try_normalise(e.final_url or e.url) for e in entries)
                  if n is not None}
    unread = [c for c in children
              if (n := urlnorm.try_normalise(c)) is None or n.url not in read_whole]
    incomplete = bool(unread) or any(e.truncated for e in _sitemap_entries(context))
    if incomplete:
        out.note_metric("sitemap_section_coverage_not_judged",
                        f"{len(unread)} index child sitemap(s) unread; "
                        f"{sum(1 for e in _sitemap_entries(context) if e.truncated)} "
                        f"sitemap(s) cut at the read cap")

    omitted: Dict[str, List[PageView]] = {}
    if len(listed) >= min_urls and not incomplete:
        listed_keys = {n.url for n in (urlnorm.try_normalise(u) for u in listed) if n}
        sections = {_first_segment(urlnorm.try_normalise(u).path) for u in listed
                    if urlnorm.try_normalise(u)}
        for page in context.content_pages():
            if page.url in listed_keys or page.path in ("/", ""):
                continue
            if page.entry.referrer in ("", "sitemap", "entity-home guess"):
                continue  # only pages the site itself links to count as reachable
            section = _first_segment(page.path)
            # A section is a directory: a single script or file (/search.aspx) is a page,
            # and utility pages are not expected in a sitemap at all.
            if page.normal is not None and urlnorm.is_utility(page.normal):
                continue
            if section and "." not in section and section not in sections:
                omitted.setdefault(section, []).append(page)
        omitted = {s: p for s, p in omitted.items() if len(p) >= 2}

    if not truncated and not omitted:
        return
    parts: List[str] = []
    refs = []
    if truncated:
        entry, count = truncated[0]
        parts.append(f"{entry.final_url or entry.url} lists exactly {count} URLs, a round "
                     f"generator limit, which indicates the file was cut off rather than "
                     f"complete")
        refs.append(metric_ref(entry.entry_id, "sitemap_urls_listed", count, None))
    if omitted:
        section, pages = sorted(omitted.items())[0]
        parts.append(f"{sum(len(p) for p in omitted.values())} crawled, internally linked "
                     f"page(s) in section(s) {', '.join('/' + s for s in sorted(omitted))} "
                     f"appear in a sitemap of {len(listed)} URLs whose entries never cover "
                     f"those sections (e.g. {pages[0].url})")
        refs.append(metric_ref(pages[0].url_hash, "listed_in_sitemap", 0, 1))
    out.add(Finding(
        check_id="G0.SITEMAP.STALE_OR_TRUNCATED",
        title="The sitemap no longer describes the whole site",
        evidence="; and ".join(parts) + ".",
        mechanism=("A sitemap is the site's declared inventory. When it stops at a generator "
                   "limit or omits whole sections, crawlers treat what it lists as the "
                   "canonical set, and pages outside it are discovered late or not at all."),
        confidence="high" if truncated and omitted else "medium",
        signal_count=1 + int(bool(truncated)) + int(bool(omitted)),
        fact_class="secondary",
        scope=Scope(level="site", pages_affected=sum(len(p) for p in omitted.values()) or 1,
                    pages_sampled=max(1, len(context.content_pages())),
                    example_urls=[p.url for ps in omitted.values() for p in ps][:3]
                    or [entries[0].final_url or entries[0].url]),
        evidence_refs=refs,
        suggested_action=SuggestedAction(
            summary="Generate the sitemap from the full set of live canonical URLs, split "
                    "into an index rather than truncated.",
            steps=["Regenerate from the routing or content source of truth so every public "
                   "section is included.",
                   "Split large inventories into child sitemaps under a sitemapindex "
                   "instead of stopping at a count.",
                   "Add a check that each content section appears in the sitemap."],
            effort="low", owner_role="Backend engineering / SEO",
            verification="Confirm the sitemap URL count changes with the content and that "
                         "every public section has entries.",
            expected_effect="Restores the sitemap as a complete discovery inventory.",
        ),
    ))


def _check_feed(context: Context, out: FindingsFile) -> None:
    if not context.allows("G0.SITEMAP.NO_MACHINE_FEED"):
        out.skip("G0.SITEMAP.NO_MACHINE_FEED",
                 reason=f"not applicable to the detected "
                        f"{context.site_profile.detected_type} profile")
        return
    if context.site_profile.detected_type != "publisher":
        return
    feeds = [e for e in context.ledger.entries() if e.purpose == "feed" and e.ok]
    homepage = context.homepage()
    declared = []
    if homepage is not None:
        for node in homepage.doc.link_rel("alternate"):
            ctype = (node.get("type") or "").lower()
            if "rss" in ctype or "atom" in ctype or "json" in ctype:
                declared.append((node.get("href") or "").strip())
    if feeds or declared:
        out.note_posture("machine_feed_present", True)
        return
    if homepage is None:
        return
    out.add(Finding(
        check_id="G0.SITEMAP.NO_MACHINE_FEED",
        title="A publishing site offers no machine-readable feed",
        evidence=(
            "The site was classified as a publisher from on-page evidence "
            f"({'; '.join(context.site_profile.signals[:2])}), but no RSS, Atom or JSON "
            "feed was found: no feed responded at the conventional paths tried, and the "
            "homepage declares no rel=alternate feed link."
        ),
        mechanism=(
            "A feed is the cheapest machine-readable freshness channel a publisher can "
            "offer. It gives consumers a dated, structured list of new content without "
            "re-crawling, which raises the chance that recent articles are discovered "
            "while they are still timely."
        ),
        confidence="medium", signal_count=2, fact_class="enrichment",
        scope=Scope(level="site", pages_affected=1, pages_sampled=1,
                    example_urls=[homepage.url]),
        evidence_refs=[metric_ref(homepage.url_hash, "machine_feeds_found", 0, 1)],
        suggested_action=SuggestedAction(
            summary="Publish an RSS or Atom feed and declare it with rel=alternate.",
            steps=[
                "Generate a feed of the most recent 20 to 50 articles with title, link, "
                "publication date, author and a substantive summary or full content.",
                "Serve it at a stable path such as /feed.xml.",
                "Add `<link rel=\"alternate\" type=\"application/rss+xml\" "
                "href=\"/feed.xml\">` to every page's head.",
                "Emit per-item dates from content timestamps, not build time.",
            ],
            effort="low", owner_role="Backend engineering / Content ops",
            verification="Fetch the feed URL and confirm it parses as RSS or Atom with "
                         "correct per-item dates, and that the homepage declares it.",
            expected_effect="Adds a reliable freshness channel for newly published "
                            "content.",
        ),
    ))


# ========================================================================================
# entry point
# ========================================================================================

def detect(context: Context, out: FindingsFile) -> None:
    """Run every G0 check. Each is independently guarded and reports its own skips."""
    out.note_metric("pages_analyzed", len(context.content_pages()))
    out.note_metric("site_profile", context.site_profile.detected_type)

    check_robots(context, out)
    check_audit_path_disallowed(context, out)
    check_directives(context, out)
    check_gating(context, out)
    check_http(context, out)
    check_sitemaps(context, out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return analyzer.run(
        SKILL_ID,
        "Gate G0 (REACH): robots.txt evaluated per AI crawler, indexing and snippet "
        "directives, bot gating, sitemaps, redirect chains and canonical host "
        "duplication. Reads the evidence ledger only.",
        detect, argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
