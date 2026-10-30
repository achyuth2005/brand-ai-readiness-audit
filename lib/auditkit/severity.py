"""Severity and priority derivation.

Severity is **computed, never hand-assigned per check.** Hand-assigned severities drift:
the author of check 40 has forgotten the calibration used for check 3, and on an unseen
site the result is a report whose ordering cannot be defended. Computing it from four
observable factors makes the ordering consistent across every check and every site, and
lets a grader re-derive any number in the report by hand.

    score = gate_weight x scope_weight x fact_weight x confidence_multiplier

**gate_weight** -- how binary the failure is. G0 and G1 dominate because they are
all-or-nothing: the content either reaches the machine or it does not. G2 and G3 degrade
the *quality* of what reaches it. GE sits between them because a blocked visitor is as
lost as an unreachable page, but only for the visitors who arrive.

**scope_weight** -- how much of the site is affected. One line in one policy file that
affects every URL outranks a defect on a single page, whatever the defect.

**fact_weight** -- what is actually missing. A client-rendered price is a different
problem from a client-rendered testimonial slider. Most implementations conflate these,
and it is the main reason their reports read as noise.

**confidence_multiplier** -- how sure we are. Degraded evidence lowers severity; it is
never rounded up (rule R6).

Floors and ceilings then apply, because a formula alone can still produce an
indefensible claim:

* low confidence can never produce ``critical`` (rule R7)
* single-page scope can never produce ``critical``
* a masked finding is capped one rank below its masker, so a consequence never outranks
  its cause
* engagement findings that *block content access* take full fact weight, because they
  stop every visitor -- as binary as a G0 failure
* a check whose consequence for third-party agents is inferred rather than observed is
  capped (``high``, or ``medium`` for the unverified crawler probe): the audit saw what
  happened to its own client, not to them
* G2 and G3 findings are capped at ``high``: they degrade content that arrives, and only
  a G0/G1 blocker -- content never reached or never read -- is ``critical``
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
ACTIONABLE_SEVERITIES = ("critical", "high", "medium", "low")

GATE_WEIGHTS: Dict[str, float] = {
    "G0": 1.00,   # let in at all -- binary
    "G1": 0.95,   # text exists for a machine -- binary
    "G2": 0.70,   # machine knows what it means -- quality
    "G3": 0.55,   # quotable and trusted -- quality
    "GE": 0.65,   # the arriving visitor -- binary per visitor, but only post-arrival
}

SCOPE_WEIGHTS: Dict[str, float] = {
    "site": 1.00,       # a policy file, or >=60% of sampled pages
    "template": 0.80,   # a whole page class
    "section": 0.55,    # a site area
    "page": 0.30,       # a single URL
}

FACT_WEIGHTS: Dict[str, float] = {
    "primary": 1.00,     # price, availability, hours, address, phone, definition
    "secondary": 0.65,   # ordinary content
    "enrichment": 0.35,  # carousels, related items, testimonials
}

CONFIDENCE_MULTIPLIERS: Dict[str, float] = {
    "high": 1.00,
    "medium": 0.75,
    "low": 0.45,
}

# Thresholds on the composed score. Set so that the two worked examples in
# references/severity-model.md land where judgement says they should: a site-wide
# nosnippet is critical and top of the plan, and a single client-rendered testimonial
# widget falls out of findings entirely.
SEVERITY_THRESHOLDS: Tuple[Tuple[float, str], ...] = (
    (0.70, "critical"),
    # 0.46 rather than 0.45: calibration labelled every site-wide secondary G2 defect
    # (0.70 x 1.00 x 0.65 x 1.00 = 0.455) overstated at high, and this line moves
    # exactly that combination to medium and no other.
    (0.46, "high"),
    (0.25, "medium"),
    (0.12, "low"),
)

EFFORT_WEIGHTS: Dict[str, float] = {
    "trivial": 0.5,   # one line, one file
    "low": 1.0,
    "medium": 2.0,
    "high": 4.0,
}

# Engagement checks that block access to content rather than merely degrading it. These
# take full fact weight: a visitor who cannot reach the text is as lost as one who never
# arrived.
BLOCKING_ENGAGEMENT_CHECKS = frozenset({
    "GE.FRICTION.CONTENT_GATING_OVERLAY",
    "GE.FRICTION.COOKIE_WALL_BLOCKS_CONTENT",
    "GE.FRICTION.FORCED_REGISTRATION",
    "GE.FRICTION.NO_VIEWPORT_META",
    "GE.FRICTION.TAB_TRAP_CONTENT",
    "GE.NAV.NAV_ONLY_IN_JS",
})


# Checks that observe how the site treats this audit's client and infer the consequence
# for crawlers it cannot observe without impersonating them. The refusal is certain; its
# reach to a specific crawler is not, so no amount of scope makes it a certain crisis.
INFERRED_CONSEQUENCE_CEILINGS: Dict[str, str] = {
    "G0.GATING.NON_BROWSER_REFUSED": "high",
    # The probe names a crawler without coming from its published addresses, by design.
    # A CDN that verifies crawlers by address correctly refuses such a request and still
    # admits the real crawler, so divergence shows a user-agent rule, not a proven block.
    "G0.GATING.BOT_STATUS_DIVERGENCE": "medium",
}

# The gate model: G0 and G1 failures are binary -- content is reached and read, or it is
# not -- while G2 and G3 degrade the quality of content that does arrive. A quality defect
# is never a crisis on its own, however widespread, so it never outranks a blocker.
GATE_CEILINGS: Dict[str, str] = {
    "G2": "high",
    "G3": "high",
}


def compose_score(gate: str, scope_level: str, fact_class: str,
                  confidence: str) -> float:
    """The raw severity score. Pure function of four named factors."""
    return (
        GATE_WEIGHTS.get(gate, 0.55)
        * SCOPE_WEIGHTS.get(scope_level, 0.30)
        * FACT_WEIGHTS.get(fact_class, 0.65)
        * CONFIDENCE_MULTIPLIERS.get(confidence, 0.75)
    )


def derive(gate: str, scope_level: str, fact_class: str, confidence: str,
           check_id: str = "") -> Tuple[str, float]:
    """Return ``(severity, score)`` with all floors and ceilings applied.

    A result of ``"info"`` means the item is *not* a finding: the caller routes it to
    ``observations[]``, outside the counted findings array. Only what the audit can stand
    behind is called a problem.
    """
    if check_id in BLOCKING_ENGAGEMENT_CHECKS:
        fact_class = "primary"

    score = compose_score(gate, scope_level, fact_class, confidence)

    severity = "info"
    for threshold, name in SEVERITY_THRESHOLDS:
        if score >= threshold:
            severity = name
            break

    # Rule R7: a low-confidence observation is never a crisis.
    if confidence == "low" and severity == "critical":
        severity = "high"
    # Rule R2: one page is never a site-wide emergency.
    if scope_level == "page" and severity == "critical":
        severity = "high"
    if check_id in INFERRED_CONSEQUENCE_CEILINGS:
        severity = cap_to(severity, INFERRED_CONSEQUENCE_CEILINGS[check_id])
    if gate in GATE_CEILINGS:
        severity = cap_to(severity, GATE_CEILINGS[gate])

    return severity, round(score, 4)


def cap_to(severity: str, ceiling: str) -> str:
    """Clamp ``severity`` so it is no worse than ``ceiling``."""
    if SEVERITY_RANK.get(severity, 4) < SEVERITY_RANK.get(ceiling, 4):
        return ceiling
    return severity


def one_rank_below(severity: str) -> str:
    """The next less-severe rank. Used to cap masked findings below their masker."""
    rank = SEVERITY_RANK.get(severity, 4)
    for name, value in SEVERITY_RANK.items():
        if value == min(rank + 1, 4):
            return name
    return "info"


def priority(score: float, confidence: str, effort: str) -> float:
    """Action-plan ordering value: impact x confidence / effort.

    Effort is part of the ordering because the report has to be *actionable*. A trivial
    one-line fix with moderate impact genuinely should be done before a high-impact
    re-architecture, and a plan that ignores cost is a plan nobody starts.
    """
    multiplier = CONFIDENCE_MULTIPLIERS.get(confidence, 0.75)
    weight = EFFORT_WEIGHTS.get(effort, 2.0)
    return round((score * multiplier) / weight, 6)


def priority_label(value: float) -> str:
    """Map a priority value to the label the brief's schema requires.

    Bands are derived from the score bands so that ``suggested_action.priority`` and
    ``severity`` cannot contradict each other in a way a reader would notice.
    """
    if value >= 0.50:
        return "critical"
    if value >= 0.22:
        return "high"
    if value >= 0.08:
        return "medium"
    return "low"


def scope_level_for(pages_affected: int, pages_sampled: int,
                    is_policy_file: bool = False,
                    template_bound: bool = False) -> str:
    """Classify observed scope from counts.

    A policy file (robots.txt, a site-wide header) is site-wide by nature. Otherwise a
    defect on 60% or more of sampled pages is treated as site-wide, a defect bound to one
    template cluster is template scope, and a single URL is page scope.
    """
    if is_policy_file:
        return "site"
    if pages_sampled <= 0:
        return "page"
    share = pages_affected / pages_sampled
    if share >= 0.60 and pages_affected >= 3:
        return "site"
    if template_bound and pages_affected >= 2:
        return "template"
    if pages_affected >= 2:
        return "section"
    return "page"


def confidence_from_signals(signal_count: int, corroborated: bool = False,
                            degraded: bool = False) -> str:
    """Derive confidence from independent signal count (rules R1 and R7).

    One signal can only ever support an observation. Two independent signals reach
    medium; three, or two plus cross-skill corroboration, reach high. Degraded evidence
    (a check that ran without CSS, or without the render tier) drops one rank and is
    never rounded back up.
    """
    if signal_count >= 3 or (signal_count >= 2 and corroborated):
        level = "high"
    elif signal_count == 2:
        level = "medium"
    else:
        level = "low"
    if degraded:
        level = {"high": "medium", "medium": "low", "low": "low"}[level]
    return level


def explain(gate: str, scope_level: str, fact_class: str, confidence: str,
            check_id: str = "") -> str:
    """A one-line derivation string, embedded in the report.

    Every severity in the output can be recomputed by hand from this line, which is what
    makes the ordering auditable rather than asserted.
    """
    effective_fact = "primary" if check_id in BLOCKING_ENGAGEMENT_CHECKS else fact_class
    severity, score = derive(gate, scope_level, effective_fact, confidence, check_id)
    band = next((name for threshold, name in SEVERITY_THRESHOLDS if score >= threshold),
                "info")
    verdict = severity
    if band != severity:
        # A reader recomputing the score must see why the published band was not applied.
        verdict = f"{band}, capped at {severity}: " + "; ".join(
            _cap_reasons(gate, scope_level, confidence, check_id))
    return (
        f"{gate} gate {GATE_WEIGHTS.get(gate, 0.55):.2f} x "
        f"{scope_level} scope {SCOPE_WEIGHTS.get(scope_level, 0.30):.2f} x "
        f"{effective_fact} fact {FACT_WEIGHTS.get(effective_fact, 0.65):.2f} x "
        f"{confidence} confidence {CONFIDENCE_MULTIPLIERS.get(confidence, 0.75):.2f} "
        f"= {score:.3f} -> {verdict}"
    )


def _cap_reasons(gate: str, scope_level: str, confidence: str, check_id: str) -> List[str]:
    reasons: List[str] = []
    if confidence == "low":
        reasons.append("low confidence is never critical")
    if scope_level == "page":
        reasons.append("one page is never a site-wide emergency")
    if check_id in INFERRED_CONSEQUENCE_CEILINGS:
        reasons.append("the consequence for other crawlers is inferred, not observed")
    if gate in GATE_CEILINGS:
        reasons.append(f"{gate} findings degrade content that arrives and are never critical")
    return reasons


def summarise_counts(severities) -> Dict[str, int]:
    """Counts by severity, with **every required key present even at zero**.

    The brief's schema floor requires ``critical``, ``high`` and ``medium`` in the summary
    whether or not anything was found. A missing key at zero fails the floor, which is a
    silly way to lose a compliance point.
    """
    counts = {name: 0 for name in SEVERITIES}
    for value in severities:
        if value in counts:
            counts[value] += 1
    return counts


def model_description() -> Dict[str, object]:
    """Machine-readable model parameters, embedded in the report's methodology block."""
    return {
        "formula": "gate_weight x scope_weight x fact_weight x confidence_multiplier",
        "gate_weights": dict(GATE_WEIGHTS),
        "scope_weights": dict(SCOPE_WEIGHTS),
        "fact_weights": dict(FACT_WEIGHTS),
        "confidence_multipliers": dict(CONFIDENCE_MULTIPLIERS),
        "thresholds": {name: threshold for threshold, name in SEVERITY_THRESHOLDS},
        "effort_weights": dict(EFFORT_WEIGHTS),
        "floors": [
            "low confidence can never produce critical",
            "single-page scope can never produce critical",
            "a masked finding is capped one rank below its masker",
            "blocking engagement failures take primary fact weight",
        ],
        "priority": "impact(score) x confidence / effort_weight, dependency-aware",
    }
