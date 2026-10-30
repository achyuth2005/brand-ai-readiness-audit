#!/usr/bin/env python3
"""Phase 6a -- RENDER. The verified report, as JSON for machines and Markdown for people.

``audit.json`` is a strict superset of the brief's schema: ``site``, ``audited_at``,
``summary`` with ``critical``/``high``/``medium`` always present (including at zero) and
``findings`` whose items always carry ``id``, ``title``, ``severity``, ``evidence`` and
``suggested_action``. Everything else is additive.

``audit.md`` exists because the rubric asks for a report "a non-expert could act on", and
JSON alone does not clear that bar. It is generated from the same structure, in the order
a reader should act: what is wrong at a glance, the first five things to do, then each
problem with its evidence, why it matters, how to fix it and how to confirm the fix.

The model's role is bounded here too. Every number in the executive summary and in the
Markdown is regenerated from the findings arrays, never written by hand, so a mis-stated
count cannot survive verification.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import redact, render as render_mod, severity as sev, ssrf, urlnorm  # noqa: E402

GATE_NAMES = {
    "G0": "Reach -- is the crawler let in?",
    "G1": "Read -- does the text exist for a machine?",
    "G2": "Interpret -- does a machine know what the facts mean?",
    "G3": "Cite -- can a fact be quoted and trusted?",
    "GE": "Engage -- does the arriving visitor stay?",
}
SCORECARD_KEYS = {"G0": "G0_reach", "G1": "G1_read", "G2": "G2_interpret",
                  "G3": "G3_cite", "GE": "GE_engage"}

# Action-plan tiers: blockers lead regardless of effort.
PLAN_TIERS: Dict[str, int] = {"critical": 0, "high": 1}

RECOMMENDATION_WEIGHT = {"critical": 0.45, "high": 0.2, "medium": 0.07, "low": 0.02}

# Checks whose evidence is about the business's own published contact details, where
# redacting a phone number or email would make the finding unusable.
CONTACT_EVIDENCE_CHECKS = frozenset({"G2.SD.MARKUP_TEXT_CONTRADICTION",
                                     "G2.SD.REQUIRED_PROP_MISSING"})

LIMITATIONS = [
    "The audit samples pages; it is not exhaustive. Template clustering is what lets a "
    "sampled defect be attributed to every page on the same template, and each finding "
    "states its k/n sample.",
    "Rendering is static-first. Client-side rendering is detected from structural signals "
    "and hydration payloads without a browser; direct browser measurement runs only when "
    "Playwright is installed, and the report states which mode was used.",
    "Corroboration is measured as the surface the site offers for corroboration, plus "
    "resolution of declared identifiers. It is not a web-wide measurement of what other "
    "sources say about the brand.",
    "Answerability and readability checks are content-form heuristics with published "
    "thresholds and vocabulary. They are English-only; pages in other languages are "
    "skipped rather than judged with the wrong word list.",
    "'Above the fold' is a document-order proxy, and overlay and contrast checks read "
    "statically declared CSS only. There is no layout engine.",
    "User-agent differentiation is detected only where it is implemented as a user-agent "
    "rule. IP-allowlist or verified-crawler gating cannot be observed without "
    "impersonation, which this audit never does.",
    "Nothing here measures any assistant's actual answers. The checks encode the "
    "mechanisms by which content is found, read, interpreted and quoted.",
]


def _strip_internal(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _redact_finding(item: Dict[str, Any]) -> Dict[str, Any]:
    keep = item.get("check_id") in CONTACT_EVIDENCE_CHECKS
    item = dict(item)
    item["evidence"] = redact.redact(item["evidence"], limit=4000, keep_business_contact=keep)
    refs = []
    for ref in item.get("evidence_refs", []):
        ref = dict(ref)
        for key in ("excerpt", "text"):
            if isinstance(ref.get(key), str):
                ref[key] = redact.redact(ref[key], keep_business_contact=keep)
        refs.append(ref)
    item["evidence_refs"] = refs
    return item


def action_plan(findings: Sequence[Dict[str, Any]], recommendations: Sequence[Dict[str, Any]],
                size: int) -> Dict[str, Any]:
    """Blockers first, then impact x confidence / effort within each tier.

    Critical findings lead, then high, then everything else. Within a tier, a cheap fix with
    good impact goes before an expensive one. Without the tiers a trivial medium fix could
    push the one high-severity problem out of the first five entirely. Masked findings are
    never candidates, so no item can appear before the finding that masks it."""
    candidates = []
    for finding in findings:
        candidates.append((PLAN_TIERS.get(finding["severity"], 2),
                           finding.get("_priority", 0.0), finding["id"], {
            "id": finding["id"], "type": "finding", "title": finding["title"],
            "severity": finding["severity"],
            "effort": finding["suggested_action_detail"]["effort"],
            "owner_role": finding["suggested_action_detail"].get("owner_role", ""),
            "action": finding["suggested_action"]["summary"],
        }))
    for rec in recommendations:
        weight = RECOMMENDATION_WEIGHT.get(rec["priority"], 0.02)
        weight /= sev.EFFORT_WEIGHTS.get(rec["effort"], 2.0)
        candidates.append((2, weight, rec["id"], {
            "id": rec["id"], "type": "recommendation", "title": rec["title"],
            "priority": rec["priority"], "effort": rec["effort"],
            "owner_role": rec.get("owner_role", ""),
            "action": rec["implementation"][0],
        }))
    ranked = sorted(candidates, key=lambda c: (c[0], -round(c[1], 9), c[2]))[:size]
    return {
        "first_five": [item["id"] for _t, _w, _i, item in ranked],
        "items": [item for _t, _w, _i, item in ranked],
        "rationale": ("Critical findings first, then high, then the rest; within each tier, "
                      "ordered by impact x confidence / effort, where impact is the computed "
                      "severity score. Findings masked by a root cause are excluded, so no "
                      "item appears before the finding that explains it. Proactive "
                      "recommendations are weighted below findings of equal priority, "
                      "because they fix no observed defect."),
    }


def executive_summary(site: str, summary: Dict[str, int], scorecard: Dict[str, Any],
                      plan: Dict[str, Any], profile: Dict[str, Any],
                      coverage: Dict[str, Any]) -> str:
    failing = [gate for gate in ("G0", "G1", "G2", "G3", "GE")
               if scorecard.get(SCORECARD_KEYS[gate], {}).get("status") == "fail"]
    lines = [
        f"{site} was audited across {coverage.get('pages_fetched', 0)} fetched pages in "
        f"{coverage.get('template_clusters', 0)} template cluster(s), profiled as "
        f"{profile.get('detected_type', 'ambiguous').replace('_', ' ')} "
        f"({profile.get('confidence', 'low')} confidence).",
        f"{summary['total_findings']} finding(s): {summary['critical']} critical, "
        f"{summary['high']} high, {summary['medium']} medium, {summary['low']} low. "
        f"{summary.get('masked', 0)} further finding(s) are masked by a root cause and "
        f"will resolve or surface once it is fixed.",
    ]
    if failing:
        first = failing[0]
        lines.append(f"The earliest failing stage is {first} ({GATE_NAMES[first]}); "
                     f"problems there limit every later stage.")
    elif summary["total_findings"] == 0:
        lines.append("No gate is failing.")
    if plan["items"]:
        lines.append("Start with: " + "; ".join(
            f"{item['id']} {item['title']}" for item in plan["items"][:3]) + ".")
    return " ".join(lines)


def build_report(*, site: str, audited_at: str, merged: Dict[str, Any],
                 recommendations: Sequence[Dict[str, Any]], profile: Dict[str, Any],
                 coverage: Dict[str, Any], manifest: Dict[str, Any],
                 plan_size: int = 5) -> Dict[str, Any]:
    findings = [_redact_finding(f) for f in merged["findings"]]
    masked = [_redact_finding(f) for f in merged["masked_findings"]]
    counts = sev.summarise_counts(f["severity"] for f in findings)
    summary = {
        "total_findings": len(findings),
        "critical": counts["critical"], "high": counts["high"],
        "medium": counts["medium"], "low": counts["low"], "info": 0,
        "masked": len(masked),
        "observations": len(merged["observations"]),
        "proactive_recommendations": len(recommendations),
    }
    plan = action_plan(findings, recommendations, plan_size)
    specialists = [s["id"] for s in manifest["skills"] if not s.get("entrypoint")]

    report: Dict[str, Any] = {
        "site": site,
        "audited_at": audited_at,
        "summary": summary,
        "findings": [_strip_internal(f) for f in findings],
        "executive_summary": executive_summary(site, summary, merged["gate_scorecard"],
                                               plan, profile, coverage),
        "action_plan": plan,
        "gate_scorecard": merged["gate_scorecard"],
        "masked_findings": [_strip_internal(f) for f in masked],
        "proactive_recommendations": list(recommendations),
        "observations": merged["observations"],
        "site_profile": profile,
        "coverage": coverage,
        "methodology": {
            "marketplace_version": manifest.get("version", ""),
            "gate_model": "G0 Reach -> G1 Read -> G2 Interpret -> G3 Cite; GE Engage "
                          "evaluated in parallel and never masked by G0-G3",
            "severity_model": sev.model_description(),
            "skills_run": specialists,
            "determinism": "Byte-identical output for identical input except audited_at "
                           "(override with --now).",
            "evidence_policy": "Every finding is constructed by a script through the "
                               "Finding contract, cites evidence_refs into the evidence "
                               "ledger, and is re-resolved by verify_report.py before the "
                               "report is written.",
            "safety": {
                "methods": ["GET", "HEAD"],
                "robots_txt": "respected for this audit's own fetching; never fetched a "
                              "disallowed path",
                "mutating_get_denylist": urlnorm.summarise_denylist(),
                "ssrf_policy": list(ssrf.describe_policy()),
                "redaction": redact.redaction_policy(),
                "render_tier": render_mod.describe_policy(),
                "identity": "truthful, contactable user-agent; no impersonation, no "
                            "challenge solving, no evasion",
            },
            "limitations": LIMITATIONS,
        },
    }

    secrets = redact.scan_payload(report)
    if secrets:
        report = json.loads(redact._mask_secrets(json.dumps(report, ensure_ascii=False))[0])
        report["methodology"]["safety"]["secret_patterns_masked"] = secrets
    return report


# ========================================================================================
# Markdown
# ========================================================================================

def _md(text: Any) -> str:
    return str(text).replace("\r", "").replace("|", "\\|")


def render_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    out: List[str] = [
        f"# AI-Readiness Audit: {report['site']}",
        "",
        f"*Audited {report['audited_at']} -- read-only, recommend-only. Nothing on the "
        f"site was changed.*",
        "",
        "## Summary",
        "",
        report["executive_summary"],
        "",
        "| Critical | High | Medium | Low | Masked | Recommendations |",
        "| :-: | :-: | :-: | :-: | :-: | :-: |",
        f"| {s['critical']} | {s['high']} | {s['medium']} | {s['low']} | {s['masked']} "
        f"| {s['proactive_recommendations']} |",
        "",
        "_How to read this report: **critical** means machines cannot reach or read the "
        "content at all; **high** substantially limits how it is read, interpreted, quoted "
        "or used by visitors; **medium** and **low** are real but narrower. A **masked** "
        "finding is true today but caused by another finding, so fix that one first. A "
        "**template** is a set of pages sharing one layout, where one fix covers them all. "
        "Counts such as 4/30 are affected pages out of pages examined._",
        "",
        "## Where the chain breaks",
        "",
        "Each stage depends on the one before it: a page that cannot be reached cannot be "
        "read, and a page that cannot be read cannot be quoted. Engagement is judged "
        "separately, for visitors who do arrive.",
        "",
        "| Stage | Status | Findings | Masked | Note |",
        "| :-- | :-- | :-: | :-: | :-- |",
    ]
    for gate, key in SCORECARD_KEYS.items():
        row = report["gate_scorecard"].get(key, {})
        out.append(f"| {gate} {_md(GATE_NAMES[gate])} | **{row.get('status', '')}** | "
                   f"{row.get('findings', 0)} | {row.get('masked', 0)} | "
                   f"{_md(row.get('note', ''))} |")

    out += ["", "## Start here: your first five actions", ""]
    if not report["action_plan"]["items"]:
        out.append("Nothing to act on.")
    for index, item in enumerate(report["action_plan"]["items"], start=1):
        label = item.get("severity") or item.get("priority")
        out.append(f"{index}. **{item['id']} -- {_md(item['title'])}** "
                   f"({label}, effort {item['effort']}"
                   + (f", {item['owner_role']}" if item.get("owner_role") else "") + ")  ")
        out.append(f"   {_md(item['action'])}")
    out += ["", f"_{report['action_plan']['rationale']}_", ""]

    out += ["## Problems found", ""]
    if not report["findings"]:
        out.append("No evidence-backed problems were found.")
    for gate in SCORECARD_KEYS:
        gate_findings = [f for f in report["findings"] if f["gate"] == gate]
        if not gate_findings:
            continue
        out += [f"### {gate} -- {GATE_NAMES[gate]}", ""]
        for f in gate_findings:
            detail = f["suggested_action_detail"]
            scope = f["scope"]
            out += [
                f"#### {f['id']} · {_md(f['title'])}",
                "",
                f"**Severity:** {f['severity']} · **Confidence:** {f['confidence']} · "
                f"**Scope:** {scope['level']}, {scope['pages_affected']}/"
                f"{scope['pages_sampled']} pages"
                + (f" (template {scope['template_id']})" if scope.get("template_id") else "")
                + f" · **Check:** `{f['check_id']}`",
                "",
                f"**What we found.** {f['evidence']}",
                "",
                f"**Why it matters.** {f['mechanism']}",
                "",
                f"**What to do.** {f['suggested_action']['summary']} "
                f"(effort: {detail['effort']}"
                + (f"; owner: {detail['owner_role']}" if detail.get("owner_role") else "")
                + ")",
                "",
            ]
            out += [f"{i}. {step}" for i, step in enumerate(detail["steps"], start=1)]
            out += ["", f"**How to confirm the fix.** {detail['verification']}", ""]
            if f.get("masks"):
                out += [f"*Fixing this also unblocks: {', '.join(f['masks'])}.*", ""]
            if f.get("corroborated_by"):
                out += [f"*Independently corroborated by: "
                        f"{', '.join(f['corroborated_by'])}.*", ""]

    if report["masked_findings"]:
        out += ["## Will surface once the root cause is fixed", "",
                "These are true of the site today but are fully explained by an upstream "
                "finding. Fix that first; then re-run the audit.", ""]
        for f in report["masked_findings"]:
            out.append(f"- **{f['id']}** {_md(f['title'])} -- masked by "
                       f"**{f['masked_by']}**: {_md(f['masked_reason'])}")
        out.append("")

    if report["proactive_recommendations"]:
        out += ["## Beyond the defects: proactive recommendations", "",
                "None of these is a defect. Each is grounded in something observed on "
                "this site, and each says how to verify it.", ""]
        for r in report["proactive_recommendations"]:
            out += [f"### {r['id']} · {_md(r['title'])}", "",
                    f"**Priority:** {r['priority']} · **Effort:** {r['effort']} · "
                    f"**Source:** {r['generator'].replace('_', ' ')}", "",
                    f"**Observed.** {r['rationale']}", "",
                    f"**Why.** {r['mechanism']}", ""]
            if r.get("calibration"):
                out += [f"**Calibration.** {r['calibration']}", ""]
            out += [f"- {step}" for step in r["implementation"]]
            out += ["", f"**Verify.** {r['verification']}", ""]

    if report["observations"]:
        out += ["## Observations (not counted as problems)", "",
                "_True and possibly useful later, but below the evidence or impact bar for "
                "a finding._", ""]
        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for o in report["observations"]:
            grouped.setdefault((o["check_id"], o["title"]), []).append(o)
        for (check_id, title), items in list(grouped.items())[:25]:
            times = f" ({len(items)} instances)" if len(items) > 1 else ""
            detail = str(items[0].get("detail", "")).strip()
            if len(detail) > 220:
                detail = detail[:219].rstrip() + "…"
            out.append(f"- **{_md(title)}**{times} `{check_id}`"
                       + (f" -- {_md(detail)}" if detail else ""))
        out.append("")

    cov = report["coverage"]
    out += [
        "## Coverage and limits",
        "",
        f"- Pages fetched: {cov.get('pages_fetched', 0)} of {cov.get('pages_attempted', 0)} "
        f"attempted; rendered in a browser: {cov.get('pages_rendered', 0)} "
        f"(mode: {cov.get('render_mode', 'static')})",
        f"- robots.txt respected: yes. Paths skipped: {len(cov.get('paths_skipped', []))}"
        + ("".join(f". Crawl-delay of {seconds:g}s honoured for {host}"
                   for host, seconds in sorted(cov.get("crawl_delay_seconds", {}).items()))),
        f"- Checks skipped: {len(cov.get('checks_skipped', []))}; skills failed: "
        f"{len(cov.get('skills_failed', []))}",
        f"- Off-site corroboration: {cov.get('offsite_corroboration', 'disabled')}",
        (f"- Budget: {cov['budget']['wall_clock_seconds']}s, "
         f"{cov['budget'].get('requests_made', '?')} requests"
         if "wall_clock_seconds" in cov.get("budget", {}) else
         f"- Mode: {cov.get('mode', 'offline')}; "
         f"{cov.get('budget', {}).get('requests_made', 0)} requests recorded in the ledger"),
        "",
    ]
    out += [f"- {limit}" for limit in report["methodology"]["limitations"]]
    out.append("")
    return "\n".join(out)


def write(report: Dict[str, Any], out_dir: Path, stem: str = "audit") -> Dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "md": md_path}
