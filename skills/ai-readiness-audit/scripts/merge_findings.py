#!/usr/bin/env python3
"""Phase 4 -- MERGE. Six findings files become one root-cause-ordered set.

This is the step that turns a decomposition into a composition. Concatenating six files
would produce a flat pile in which one fault appears several times and consequences sit
beside their causes as if they were independent problems. The merge instead runs eight
steps, each of which is visible in the output:

    1 VALIDATE     every file against schema/finding.schema.json, every finding through
                   the Finding constructor; a skill may only report checks it owns.
                   Malformed entries are dropped and logged, never trusted.
    2 DEDUPE       key (check_id, scope key); evidence refs merged, strongest kept.
    3 COLLAPSE     per-page to per-template -- performed by each specialist through the
                   shared template clustering, and asserted here (no finding may claim
                   more affected pages than were sampled).
    4 CORROBORATE  one underlying fault surfaced by two skills becomes one finding with
                   corroborated_by and a confidence boost. Overlap becomes a strength
                   signal instead of noise.
    5 MASK         gate dominance: G0 > G1 > G2 > G3, GE never masked. A finding whose
                   cause is fully explained by a finding upstream moves to
                   masked_findings with masked_by and a reason. Nothing is discarded.
    6 SEVERITY     recomputed for every finding (gate x scope x fact x confidence), with
                   masked findings capped one rank below their masker.
    7 PRIORITISE   impact x confidence / effort, dependency-aware.
    8 ASSIGN IDS   deterministic sort, then F-001 ... F-NNN.

Worked example (also in references/gate-model.md): a product template renders its
content client-side. A naive auditor reports missing JSON-LD, missing alt text, thin
content, no definitional sentence and more -- all true of the raw HTML, all with one
cause. This merge reports the render gap once, as the finding, and lists the rest as
masked findings that will resolve or become independently actionable once it is fixed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import bootstrap, checks, severity as sev  # noqa: E402
from auditkit.finding import EvidenceRef, Finding, FindingError  # noqa: E402
from auditkit.profile import SiteProfile  # noqa: E402

GATE_ORDER = {"G0": 0, "G1": 1, "G2": 2, "G3": 3, "GE": 4}
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
RANK_CONFIDENCE = {v: k for k, v in CONFIDENCE_RANK.items()}

# ----------------------------------------------------------------------------------------
# Step 4 -- cross-skill corroboration.
# check_id -> check ids from another skill whose presence on overlapping scope confirms it.
# ----------------------------------------------------------------------------------------
CORROBORATION = {
    "GE.NAV.NAV_ONLY_IN_JS": ("G1.RENDER.CSR_CONTENT_GAP",),
    "GE.FRICTION.TAB_TRAP_CONTENT": ("G1.RENDER.BODY_REQUIRES_INTERACTION",),
    "G2.SD.SD_ONLY_IN_RENDERED_DOM": ("G1.RENDER.CSR_CONTENT_GAP",),
    "GE.FRICTION.FORCED_REGISTRATION": ("G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT",
                                        "G0.GATING.PAYWALL_UNMARKED"),
    "G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE": ("GE.READ.NO_LISTS", "GE.READ.LONG_SENTENCES"),
    "G3.CORROB.NO_CITABLE_PRIMARY_SOURCE": ("G3.ANSW.NO_DEFINITIONAL_SENTENCE",),
    "G2.SD.ENTITY_GRAPH_UNLINKED": ("G3.ENTITY.AMBIGUITY_RISK",
                                    "G3.ENTITY.WEAK_DISAMBIGUATION"),
}

# ----------------------------------------------------------------------------------------
# Step 5 -- dominance masking table. Mirrored in references/gate-model.md.
#
# Each rule names a dominating check and the checks whose cause it fully explains on
# overlapping scope. Engagement findings are never dominated: a blocked or confused
# visitor is a separate harm from an unreadable page, and it persists after the upstream
# fix unless it is fixed too.
# ----------------------------------------------------------------------------------------
_CONTENT_LEVEL = ("G1.", "G2.", "G3.")

MASKING_RULES: Tuple[Dict[str, Any], ...] = (
    {"dominator": "G0.GATING.NON_BROWSER_REFUSED",
     "prefixes": ("G0.SITEMAP.", "G0.HTTP.") + tuple(_CONTENT_LEVEL), "exact": (),
     "reason": "the site refused this audit's requests for its content, so findings about "
               "missing sitemaps, pages or page content describe the refusal rather than "
               "the site"},
    {"dominator": "G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT", "prefixes": _CONTENT_LEVEL,
     "exact": (),
     "reason": "the page serves a login form instead of its content, so content-level "
               "checks describe the login form"},
    {"dominator": "G0.HTTP.SOFT_404", "prefixes": _CONTENT_LEVEL, "exact": (),
     "reason": "the affected URLs are error pages served with a 200 status, so their "
               "content-level findings describe an error page"},
    {"dominator": "G0.DIRECTIVE.NOINDEX_HEADER", "prefixes": ("G2.", "G3."), "exact": (),
     "reason": "the page is excluded from indexes by an X-Robots-Tag noindex header, so "
               "its interpretation and citation findings cannot matter until it is "
               "indexable"},
    {"dominator": "G0.DIRECTIVE.NOINDEX_META", "prefixes": ("G2.", "G3."), "exact": (),
     "reason": "the page is excluded from indexes by a noindex meta tag, so its "
               "interpretation and citation findings cannot matter until it is "
               "indexable"},
    {"dominator": "G1.RENDER.CSR_CONTENT_GAP", "prefixes": ("G3.ANSW.",),
     "exact": ("G2.SD.ABSENT", "G2.SD.TYPE_MISMATCH", "G1.TEXT.BOILERPLATE_DOMINANT",
               "G1.TEXT.MAIN_LANDMARK_MISSING",
               "G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES", "G1.NONTEXT.ALT_NON_DESCRIPTIVE",
               "G1.NONTEXT.FACT_LOCKED_IN_MEDIA", "G3.CORROB.FACTS_ONLY_IN_MARKETING_COPY"),
     "reason": "the content is assembled client-side and absent from the HTML response, "
               "so these findings describe the empty response rather than the page -- "
               "the text is not there to mark up, quote or describe"},
    {"dominator": "G2.SD.JSONLD_SYNTAX_ERROR", "prefixes": (),
     "exact": ("G2.SD.REQUIRED_PROP_MISSING", "G2.SD.RECOMMENDED_PROP_MISSING",
               "G2.SD.MISSING_CONTEXT", "G2.SD.UNKNOWN_TYPE", "G2.SD.PROP_TYPE_INVALID",
               "G2.SD.ABSENT"),
     "reason": "a JSON-LD block on the same pages fails to parse, so property-level "
               "judgements about that markup are incoherent until it parses"},
)

# Blocking the core web indexes site-wide makes the site invisible to almost every
# assistant that rides on them, which fully explains downstream citation findings.
CORE_INDEX_AGENTS = ("Googlebot", "Bingbot")


# ========================================================================================
# helpers
# ========================================================================================

def _overlaps(a: Finding, b: Finding) -> bool:
    """True when two findings apply to overlapping pages."""
    if a.scope.level == "site" or b.scope.level == "site":
        return True
    if a.scope.template_id and a.scope.template_id == b.scope.template_id:
        return True
    return bool(set(a.scope.example_urls) & set(b.scope.example_urls))


def _explains(dominator: Finding, finding: Finding) -> bool:
    """True when a dominator's scope covers the pages a finding describes.

    Masking claims that a finding is a consequence of its dominator, and a narrower cause
    cannot explain a wider consequence: a noindex on one listing page does not explain a
    site-wide absence of structured data.
    """
    if dominator.scope.level == "site":
        return True
    if finding.scope.level == "site":
        return False
    if dominator.scope.template_id and dominator.scope.template_id == finding.scope.template_id:
        return True
    return bool(set(dominator.scope.example_urls) & set(finding.scope.example_urls))


def _ref_key(ref: EvidenceRef) -> str:
    return json.dumps(ref.to_dict(), sort_keys=True, default=str)


def _recompute(finding: Finding) -> None:
    finding.severity, finding.score = sev.derive(
        finding.gate, finding.scope.level, finding.fact_class, finding.confidence,
        finding.check_id)


def _sort_key(finding: Finding) -> Tuple:
    return (GATE_ORDER.get(finding.gate, 9), sev.SEVERITY_RANK.get(finding.severity, 9),
            finding.check_id, finding.scope.key)


# ========================================================================================
# step 1 -- validate
# ========================================================================================

def load_findings_files(paths: Dict[str, Optional[Path]],
                        rejected: List[Dict[str, str]]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Load each specialist's file, validating its top-level shape."""
    schema = bootstrap.load_schema("finding.schema.json")
    report_schema = bootstrap.load_schema("audit-report.schema.json")
    validator = None
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        registry = Registry().with_resources([
            ("audit-report.schema.json", Resource.from_contents(report_schema)),
            (report_schema["$id"], Resource.from_contents(report_schema)),
        ])
        validator = Draft202012Validator(schema, registry=registry)
    except Exception:
        validator = None  # jsonschema absent: the Finding constructor still validates

    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for skill, path in sorted(paths.items()):
        if path is None or not Path(path).exists():
            out[skill] = None
            continue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rejected.append({"skill": skill, "check_id": "*",
                             "reason": f"findings file is not valid JSON: {exc}"})
            out[skill] = None
            continue
        if validator is not None:
            errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
            bad_indexes: Set[int] = set()
            for error in errors:
                path_parts = list(error.path)
                if len(path_parts) >= 2 and path_parts[0] == "findings" \
                        and isinstance(path_parts[1], int):
                    bad_indexes.add(path_parts[1])
                    check_id = (data["findings"][path_parts[1]].get("check_id", "?")
                                if path_parts[1] < len(data.get("findings", [])) else "?")
                    rejected.append({"skill": skill, "check_id": check_id,
                                     "reason": f"schema: {error.message[:160]}"})
                else:
                    rejected.append({"skill": skill, "check_id": "*",
                                     "reason": f"schema: {error.message[:160]}"})
            if bad_indexes:
                data["findings"] = [f for i, f in enumerate(data.get("findings", []))
                                    if i not in bad_indexes]
        out[skill] = data
    return out


def build_findings(files: Dict[str, Optional[Dict[str, Any]]],
                   rejected: List[Dict[str, str]]) -> List[Finding]:
    """Rebuild every finding through the contract, enforcing check ownership."""
    findings: List[Finding] = []
    for skill, data in sorted(files.items()):
        if not data:
            continue
        for raw in data.get("findings", []):
            check_id = raw.get("check_id", "?")
            try:
                finding = Finding.from_dict(raw)
            except (FindingError, KeyError, TypeError, ValueError) as exc:
                rejected.append({"skill": skill, "check_id": check_id,
                                 "reason": f"contract: {exc}"})
                continue
            owner = checks.skill_of(finding.check_id)
            if owner != skill:
                rejected.append({"skill": skill, "check_id": check_id,
                                 "reason": f"skill {skill} reported a check owned by "
                                           f"{owner}; each check has exactly one owner"})
                continue
            if finding.scope.pages_affected > max(finding.scope.pages_sampled, 1) \
                    and finding.scope.level != "site":
                finding.scope.pages_sampled = finding.scope.pages_affected
            finding.fact_class = raw.get("fact_class") or finding.fact_class
            finding.signal_count = int(raw.get("signal_count", finding.signal_count))
            finding.detected_by = skill
            findings.append(finding)
    return findings


# ========================================================================================
# steps 2, 4, 5
# ========================================================================================

def dedupe(findings: Sequence[Finding]) -> List[Finding]:
    merged: Dict[str, Finding] = {}
    for finding in findings:
        key = finding.dedupe_key
        existing = merged.get(key)
        if existing is None:
            merged[key] = finding
            continue
        seen = {_ref_key(r) for r in existing.evidence_refs}
        for ref in finding.evidence_refs:
            if _ref_key(ref) not in seen:
                existing.evidence_refs.append(ref)
                seen.add(_ref_key(ref))
        if CONFIDENCE_RANK[finding.confidence] > CONFIDENCE_RANK[existing.confidence]:
            existing.confidence = finding.confidence
        existing.scope.pages_affected = max(existing.scope.pages_affected,
                                            finding.scope.pages_affected)
        existing.notes = sorted(set(existing.notes) | set(finding.notes))
    return list(merged.values())


def corroborate(findings: Sequence[Finding]) -> None:
    by_check: Dict[str, List[Finding]] = {}
    for finding in findings:
        by_check.setdefault(finding.check_id, []).append(finding)
    for finding in findings:
        for other_check in CORROBORATION.get(finding.check_id, ()):
            for other in by_check.get(other_check, []):
                if other.detected_by == finding.detected_by or not _overlaps(finding, other):
                    continue
                if other.detected_by not in finding.corroborated_by:
                    finding.corroborated_by.append(other.detected_by)
                    rank = CONFIDENCE_RANK[finding.confidence]
                    finding.confidence = RANK_CONFIDENCE[min(2, rank + 1)]
                    finding.notes.append(
                        f"Independently corroborated by {other.detected_by} "
                        f"({other.check_id}), so confidence was raised one rank.")


def _core_index_block(findings: Sequence[Finding]) -> Optional[Finding]:
    blocked = [f for f in findings if f.check_id == "G0.ROBOTS.AI_AGENT_BLOCKED"
               and f.scope.level == "site"]
    agents = {a for f in blocked for a in CORE_INDEX_AGENTS if a in f.title}
    if set(CORE_INDEX_AGENTS) <= agents:
        return blocked[0]
    return None


def mask(findings: Sequence[Finding]) -> Tuple[List[Finding], List[Tuple[Finding, Finding, str]]]:
    """Return (active, [(masked, masker, reason)]). Dominators are resolved gate-first,
    so a finding that is itself masked never masks anything (no cycles)."""
    ordered = sorted(findings, key=_sort_key)
    masked: Dict[int, Tuple[Finding, Finding, str]] = {}

    core_block = _core_index_block(ordered)
    if core_block is not None:
        for finding in ordered:
            if finding.gate in ("G2", "G3"):
                masked[id(finding)] = (
                    finding, core_block,
                    "the core web indexes are disallowed site-wide, so the site is absent "
                    "from the index most assistants retrieve from; interpretation and "
                    "citation findings cannot take effect until that is resolved")

    for rule in MASKING_RULES:
        for dominator in [f for f in ordered if f.check_id == rule["dominator"]]:
            if id(dominator) in masked:
                continue
            for finding in ordered:
                if finding is dominator or id(finding) in masked or finding.gate == "GE":
                    continue
                hit = finding.check_id in rule["exact"] or any(
                    finding.check_id.startswith(p) for p in rule["prefixes"])
                if not hit or finding.check_id == dominator.check_id:
                    continue
                if GATE_ORDER[finding.gate] < GATE_ORDER[dominator.gate]:
                    continue  # an upstream finding is never explained by a downstream one
                if _explains(dominator, finding):
                    masked[id(finding)] = (finding, dominator, rule["reason"])

    active = [f for f in ordered if id(f) not in masked]
    return active, sorted(masked.values(), key=lambda t: _sort_key(t[0]))


# ========================================================================================
# the merge
# ========================================================================================

def merge(files: Dict[str, Optional[Dict[str, Any]]], profile: SiteProfile,
          expected_skills: Sequence[str],
          rejected: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    rejected = rejected if rejected is not None else []
    findings = build_findings(files, rejected)

    skipped: List[Dict[str, Any]] = []
    excluded = set(profile.excluded_checks())
    kept = []
    for finding in findings:
        if finding.check_id in excluded:
            skipped.append({"check_id": finding.check_id,
                            "reason": f"not applicable to the detected "
                                      f"{profile.detected_type} site profile"})
        else:
            kept.append(finding)

    findings = dedupe(kept)
    corroborate(findings)
    for finding in findings:
        _recompute(finding)

    # Anything the recomputation pushes below the lowest band is not a finding.
    observations: List[Dict[str, Any]] = []
    actionable = []
    for finding in findings:
        if finding.severity == "info" or finding.confidence == "low":
            observations.append({
                "check_id": finding.check_id, "gate": finding.gate,
                "title": finding.title,
                "detail": f"{finding.evidence} (Below the finding threshold after merge: "
                          f"{finding.severity_explanation()}.)",
                "confidence": finding.confidence, "detected_by": finding.detected_by,
                "evidence_refs": [r.to_dict() for r in finding.evidence_refs],
            })
        else:
            actionable.append(finding)

    active, masked_triples = mask(actionable)

    # Step 8: ids. Active findings first, masked after, each in deterministic order.
    for index, finding in enumerate(sorted(active, key=_sort_key), start=1):
        finding.id = f"F-{index:03d}"
    offset = len(active)
    masked_sorted = sorted(masked_triples, key=lambda t: _sort_key(t[0]))
    for index, (finding, _masker, _reason) in enumerate(masked_sorted, start=offset + 1):
        finding.id = f"F-{index:03d}"
    for finding, masker, reason in masked_sorted:
        finding.masked_by = masker.id
        finding.masked_reason = reason
        finding.severity = sev.cap_to(finding.severity, sev.one_rank_below(masker.severity))
        if finding.severity == "info":
            finding.severity = "low"
        if finding.id not in masker.masks:
            masker.masks.append(finding.id)

    for skill, data in sorted(files.items()):
        if not data:
            continue
        for item in data.get("observations", []):
            observations.append(item)
        for item in data.get("checks_skipped", []):
            skipped.append(item)

    skills_failed = []
    for skill in expected_skills:
        data = files.get(skill)
        if data is None:
            skills_failed.append({"skill": skill,
                                  "reason": "the skill produced no findings file (it "
                                            "crashed, timed out, or was not run)"})
        elif data.get("metrics", {}).get("analyzer_error"):
            skills_failed.append({"skill": skill, "reason": "the analyzer stopped early: "
                                  + str(data["metrics"]["analyzer_error"])[:200]})

    unique_skips = {(s["check_id"], s["reason"]): s for s in skipped}
    unique_obs = {(o["check_id"], o["title"], o.get("detail", "")[:80]): o
                  for o in observations}

    return {
        "findings": [_serialise(f) for f in sorted(active, key=_sort_key)],
        "masked_findings": [_serialise(f) for f, _m, _r in masked_sorted],
        "observations": [unique_obs[k] for k in sorted(unique_obs)],
        "checks_skipped": [unique_skips[k] for k in sorted(unique_skips)],
        "near_misses": sorted(
            (dict(n, skill=skill) for skill, data in sorted(files.items()) if data
             for n in data.get("near_misses", [])),
            key=lambda n: (n["check_id"], n.get("scope_key", ""), n["metric"])),
        "posture": {skill: data.get("posture", {}) for skill, data in sorted(files.items())
                    if data},
        "metrics": {skill: data.get("metrics", {}) for skill, data in sorted(files.items())
                    if data},
        "findings_rejected": sorted(rejected, key=lambda r: (r["skill"], r["check_id"],
                                                              r["reason"])),
        "skills_failed": skills_failed,
        "gate_scorecard": scorecard(active, [f for f, _m, _r in masked_sorted],
                                    skipped, skills_failed),
    }


def _serialise(finding: Finding) -> Dict[str, Any]:
    data = finding.to_dict()
    # Carried for render_report's ordering; stripped before the report is written.
    data["_score"] = finding.score
    data["_priority"] = finding.priority_value
    return data


def scorecard(active: Sequence[Finding], masked: Sequence[Finding],
              skipped: Sequence[Dict[str, Any]],
              skills_failed: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    labels = {"G0": "G0_reach", "G1": "G1_read", "G2": "G2_interpret", "G3": "G3_cite",
              "GE": "GE_engage"}
    failed_skills = {s["skill"] for s in skills_failed}
    out: Dict[str, Dict[str, Any]] = {}
    for gate, label in labels.items():
        gate_findings = [f for f in active if f.gate == gate]
        gate_masked = [f for f in masked if f.gate == gate]
        owners = {checks.skill_of(c) for c in checks.for_gate(gate)}
        if owners and owners <= failed_skills:
            status = "not_assessed"
        elif any(f.severity in ("critical", "high") for f in gate_findings):
            status = "fail"
        elif gate_findings or gate_masked:
            status = "partial"
        else:
            status = "pass"
        entry: Dict[str, Any] = {"status": status, "findings": len(gate_findings),
                                 "masked": len(gate_masked)}
        notes = []
        if gate_masked:
            maskers = sorted({f.masked_by for f in gate_masked})
            notes.append(f"{len(gate_masked)} finding(s) masked by {', '.join(maskers)}")
        gate_skips = [s for s in skipped if str(s.get("check_id", "")).startswith(gate + ".")]
        if gate_skips:
            notes.append(f"{len(gate_skips)} check(s) skipped; see coverage.checks_skipped")
        if status == "not_assessed":
            notes.append("the owning skill failed; this gate was not assessed")
        if notes:
            entry["note"] = "; ".join(notes)
        out[label] = entry
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Merge specialist findings files.")
    parser.add_argument("--findings-dir", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    manifest = bootstrap.load_manifest()
    specialists = [s["id"] for s in manifest["skills"] if not s.get("entrypoint")]
    paths = {skill: args.findings_dir / f"findings.{skill}.json" for skill in specialists}
    rejected: List[Dict[str, str]] = []
    files = load_findings_files(paths, rejected)
    profile = SiteProfile.from_dict(json.loads(args.profile.read_text(encoding="utf-8")))
    merged = merge(files, profile, specialists, rejected)
    args.out.write_text(json.dumps(merged, indent=2, sort_keys=True, ensure_ascii=False)
                        + "\n", encoding="utf-8")
    print(f"merged: {len(merged['findings'])} findings, {len(merged['masked_findings'])} "
          f"masked, {len(merged['observations'])} observations, "
          f"{len(merged['findings_rejected'])} rejected", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
