#!/usr/bin/env python3
"""The entrypoint: audit a website and emit a verified report.

    python skills/ai-readiness-audit/scripts/run_audit.py --url https://example.com \\
        --out ./audit-output --contact achyuthanoop@gmail.com

Phases
------
    0   PREFLIGHT       normalise the target; refuse unsafe input (exit 3)
    1   DISCOVERY       robots.txt first, sitemaps, homepage links, stratified frontier
    2   CRAWL           the single polite crawl into the evidence ledger (only network phase)
    2.5 PROFILE         template clusters and evidence-based site type
    3   FAN-OUT         six specialists as separate read-only processes; entity-trust runs
                        the two-pass off-site protocol
    4   MERGE           validate, dedupe, corroborate, mask, score, prioritise, assign ids
    5   RECOMMEND       four proactive generators, structurally separate from findings
    6   RENDER+VERIFY   audit.json and audit.md are published only if every evidence
                        reference re-resolves against the ledger (exit 2 otherwise)
    7   TEARDOWN        request log written; ledger kept unless --no-evidence

Offline mode: ``--ledger PATH`` skips phases 0-2 and runs everything else over an existing
evidence ledger. That is how the audit is regression-tested with no network, and how its
determinism is checked: two runs over one ledger with the same ``--now`` produce
byte-identical reports.

Exit codes: 0 ok (findings are not failures) | 2 verification failed | 3 unsafe input |
4 target unreachable | 5 budget exhausted before minimum coverage | 6 a required package
is missing.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditkit import analyzer, bootstrap, budget as budget_mod, lexicons  # noqa: E402
from auditkit import ledger as ledger_mod, netgate, ratelimit, urlnorm  # noqa: E402
from auditkit.dom import Document  # noqa: E402
from auditkit.ledger import Ledger  # noqa: E402
from auditkit.profile import SiteProfile  # noqa: E402

import crawl  # noqa: E402
import discover  # noqa: E402
import merge_findings  # noqa: E402
import recommend  # noqa: E402
import render_report  # noqa: E402
import verify_report  # noqa: E402

EXIT_OK = 0
EXIT_VERIFY = 2
EXIT_UNSAFE = 3
EXIT_UNREACHABLE = 4
EXIT_BUDGET = 5
EXIT_DEPENDENCY = 6

# Packages the audit cannot run without (requirements.txt). Checked before any work, so a
# clean machine gets one actionable message instead of a traceback halfway through.
REQUIRED_PACKAGES = (("bs4", "beautifulsoup4"), ("jsonschema", "jsonschema"))
LIVE_REQUIRED_PACKAGES = (("certifi", "certifi"),)

ENTITY_SKILL = "entity-trust-audit"


class RunLog:
    """Phase timings and notes, written to run.log. Never part of the report itself,
    because wall-clock values would break byte-identical output."""

    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.started = time.monotonic()
        self.lines: List[str] = []

    def __call__(self, message: str) -> None:
        line = f"+{time.monotonic() - self.started:7.2f}s  {message}"
        self.lines.append(line)
        if not self.quiet:
            print(line, file=sys.stderr)

    def write(self, path: Path, extra: Sequence[str] = ()) -> None:
        path.write_text("\n".join(self.lines + [""] + list(extra)) + "\n", encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a website for AI-discoverability and on-site-engagement "
                    "problems. Read-only and recommend-only.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="URL or bare domain to audit")
    target.add_argument("--ledger", type=Path,
                        help="offline mode: analyse an existing evidence ledger, no network")
    parser.add_argument("--out", type=Path, default=Path("audit-output"))
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--render", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--offsite", choices=("on", "off"), default="on")
    parser.add_argument("--budget-seconds", type=float, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--now", default=None,
                        help="override audited_at (ISO-8601 UTC, e.g. 2026-09-20T14:32:00Z)")
    parser.add_argument("--contact",
                        default=os.environ.get(netgate.CONTACT_ENV, netgate.DEFAULT_CONTACT),
                        help="URL or email a site operator can use to reach you; included "
                             f"in the user-agent string (default {netgate.DEFAULT_CONTACT})")
    parser.add_argument("--prior-ledger", type=Path, default=None,
                        help="a previous run's ledger, to detect dates advanced without "
                             "content changes")
    evidence = parser.add_mutually_exclusive_group()
    evidence.add_argument("--keep-evidence", dest="keep_evidence", action="store_true",
                          default=True)
    evidence.add_argument("--no-evidence", dest="keep_evidence", action="store_false")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def audited_at(args: argparse.Namespace, meta: Dict[str, Any]) -> str:
    if args.now:
        return args.now
    if args.ledger and meta.get("audited_at"):
        return str(meta["audited_at"])
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ========================================================================================
# phases 0-2 (live mode)
# ========================================================================================

def live_crawl(args: argparse.Namespace, config: Dict[str, Any], out: Path, now: str,
               log: RunLog) -> Dict[str, Any]:
    try:
        root = discover.preflight(args.url)
    except discover.UnsafeTarget as exc:
        log(f"refused: {exc}")
        raise SystemExit(EXIT_UNSAFE)
    except discover.UnreachableTarget as exc:
        log(f"unreachable: {exc}")
        raise SystemExit(EXIT_UNREACHABLE)
    log(f"phase 0 preflight: {root.url} validated")

    ledger_path = out / "ledger"
    if ledger_path.exists():
        shutil.rmtree(ledger_path)
    ledger = Ledger.create(ledger_path, meta={
        "site_origin": root.origin, "site_host": root.host, "audited_at": now,
        "marketplace_version": bootstrap.load_manifest().get("version", ""),
        "prior_ledger": str(args.prior_ledger.resolve()) if args.prior_ledger else "",
    })

    run_budget = budget_mod.from_config(config, args.budget_seconds, args.max_pages)
    limiter = ratelimit.RateLimiter(
        min_interval=float(bootstrap.threshold(config, "crawl.per_host_min_interval_seconds", 0.5)),
        max_concurrency=int(bootstrap.threshold(config, "crawl.per_host_concurrency", 2)),
        hard_concurrency_cap=int(bootstrap.threshold(config, "crawl.hard_concurrency_cap", 4)),
        breaker_threshold=int(bootstrap.threshold(config, "crawl.breaker_threshold", 3)),
        max_backoff=float(bootstrap.threshold(config, "crawl.max_backoff_seconds", 30)),
    )
    user_agent = netgate.honest_user_agent(contact=args.contact)
    resolver = discover.RobotsResolver(root.host, limiter, user_agent)
    os.environ[netgate.ORCHESTRATOR_ENV_TOKEN] = "1"
    try:
        gate = netgate.NetGate(
            ledger, run_budget, limiter, resolver, user_agent=user_agent,
            contact=args.contact,
            max_bytes=int(bootstrap.threshold(config, "crawl.max_response_bytes", 5242880)),
            max_redirects=int(bootstrap.threshold(config, "crawl.max_redirects", 5)),
            connect_timeout=float(bootstrap.threshold(config, "crawl.connect_timeout_seconds", 5)),
            read_timeout=float(bootstrap.threshold(config, "crawl.read_timeout_seconds", 10)),
            max_decompress_ratio=int(bootstrap.threshold(config, "crawl.max_decompress_ratio", 50)),
        )
    finally:
        os.environ.pop(netgate.ORCHESTRATOR_ENV_TOKEN, None)
    resolver.bind(gate)

    rules = resolver(root.origin)
    log(f"phase 1 robots.txt: {rules.state} ({len(rules.groups)} groups, "
        f"{len(rules.sitemaps)} sitemap directives)")
    robots_entry = ledger.get(urlnorm.normalise(root.origin + "/robots.txt").url)
    if robots_entry is not None and robots_entry.outcome in (
            ledger_mod.OUTCOME_NETWORK_ERROR, ledger_mod.OUTCOME_TIMEOUT):
        # No response at all is not a site policy to report on. Continuing would turn a
        # connectivity problem on the auditing side into findings about the site.
        if "CERTIFICATE_VERIFY_FAILED" in robots_entry.reason:
            log(f"TLS certificate verification failed for {root.host}: "
                f"{robots_entry.reason}. Either this machine's trust store lacks CA "
                f"certificates (pip install certifi) or the site serves an incomplete "
                f"certificate chain, which non-browser fetchers also reject.")
        else:
            log(f"{root.host} did not respond ({robots_entry.reason}); target unreachable")
        raise SystemExit(EXIT_UNREACHABLE)
    home = gate.fetch(root.url, purpose="page", depth=0)
    if home.outcome in (ledger_mod.OUTCOME_NETWORK_ERROR, ledger_mod.OUTCOME_TIMEOUT,
                        ledger_mod.OUTCOME_BLOCKED_POLICY) or \
            (home.status is not None and home.status >= 500
             and home.status not in lexicons.CONTENT_REFUSAL_STATUSES):
        log(f"homepage unreachable: {home.outcome} {home.reason}")
        raise SystemExit(EXIT_UNREACHABLE)
    home_doc = Document(ledger.text(home), url=home.url) if home.ok and home.is_html else None

    sitemap_urls = discover.discover_sitemaps(gate, rules, root.origin, config, root.host)
    discover.probe_well_known(gate, root.origin)
    frontier = discover.build_frontier(home.final_url or home.url, home_doc, sitemap_urls,
                                       root.host, root.origin, run_budget.max_pages,
                                       set(ledger.keys()))
    log(f"phase 1 discovery: {len(sitemap_urls)} sitemap URLs, frontier of {len(frontier)}")
    run_budget.apply_ladder()

    workers = int(bootstrap.threshold(config, "crawl.per_host_concurrency", 2))
    wave = crawl.fetch_pages(gate, frontier, workers)
    remaining = run_budget.max_pages - run_budget.pages_fetched
    if remaining > 0 and run_budget.may_request():
        extra = crawl.second_wave(gate, [home] + wave, root.host, remaining)
        wave += crawl.fetch_pages(gate, extra, workers)
    log(f"phase 2 crawl: {run_budget.pages_fetched} pages fetched")
    run_budget.apply_ladder()

    page_entries = [home] + [e for e in wave if e is not None]
    crawl.probe_host_variants(gate, root)
    agent = crawl.probe_user_agents(gate, rules, page_entries, config)
    if not run_budget.has_degraded(budget_mod.DEGRADE_ASSETS):
        sheets = crawl.fetch_stylesheets(gate, page_entries, root.host, config)
        links = crawl.sample_internal_links(gate, page_entries, root.host, config)
        log(f"phase 2 probes: ua-probe agent={agent or 'none permitted'}, "
            f"{sheets} stylesheet(s), {links} link HEAD(s)")
    run_budget.apply_ladder()

    return {"ledger_path": ledger_path, "gate": gate, "budget": run_budget,
            "root": root, "ledger": ledger}


# ========================================================================================
# phases 2.5-3
# ========================================================================================

def build_profile(ledger_path: Path, config_path: Optional[Path], work: Path) -> Dict[str, Any]:
    namespace = argparse.Namespace(ledger=ledger_path, profile=None, config=config_path,
                                   emit_requests=None, quiet=True, out=None)
    context = analyzer.build_context(namespace)
    profile = context.site_profile.to_dict()
    profile["template_clusters"] = [c.to_dict() for c in context.clusters]
    profile["pages"] = [{"url": p.url, "template_id": p.cluster_id,
                         "path_classes": list(p.path_classes)} for p in context.pages]
    (work / "profile.json").write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return profile


def run_skill(skill: str, ledger_path: Path, work: Path, config_path: Optional[Path],
              timeout: float, extra: Sequence[str] = ()) -> Dict[str, Any]:
    script = next((bootstrap.skill_dir(skill) / "scripts").glob("analyze_*.py"))
    command = [sys.executable, str(script), "--ledger", str(ledger_path),
               "--profile", str(work / "profile.json"),
               "--out", str(work / f"findings.{skill}.json"), "--quiet", *extra]
    if config_path:
        command += ["--config", str(config_path)]
    # Invariant B: specialists never inherit the orchestrator token, so none of them can
    # construct an egress gateway even by accident.
    env = {k: v for k, v in os.environ.items() if k != netgate.ORCHESTRATOR_ENV_TOKEN}
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env,
                                timeout=max(10.0, timeout))
        return {"skill": skill, "exit": result.returncode,
                "seconds": round(time.monotonic() - started, 2),
                "stderr": result.stderr.strip()[-600:]}
    except subprocess.TimeoutExpired:
        return {"skill": skill, "exit": None, "seconds": round(time.monotonic() - started, 2),
                "stderr": f"timed out after {timeout:.0f}s"}


def fan_out(ledger_path: Path, work: Path, config: Dict[str, Any], config_path: Optional[Path],
            specialists: Sequence[str], offsite: Optional[Any], log: RunLog) -> Dict[str, Any]:
    cap = float(bootstrap.threshold(config, "crawl.analyzer_timeout_seconds", 90))
    outcomes: Dict[str, Dict[str, Any]] = {}
    offsite_state = "disabled"
    with ThreadPoolExecutor(max_workers=len(specialists)) as pool:
        futures = {}
        for skill in specialists:
            extra: List[str] = []
            if skill == ENTITY_SKILL and offsite is not None:
                extra = ["--emit-requests", str(work / "offsite_requests.json")]
            futures[skill] = pool.submit(run_skill, skill, ledger_path, work, config_path,
                                         cap, extra)
        if offsite is not None:
            pass_one = futures[ENTITY_SKILL].result()
            log(f"phase 3 {ENTITY_SKILL} pass 1: exit {pass_one['exit']}")
            offsite_state = offsite(work / "offsite_requests.json")
            log(f"phase 3 off-site corroboration: {offsite_state}")
            futures[ENTITY_SKILL] = pool.submit(run_skill, ENTITY_SKILL, ledger_path, work,
                                                config_path, cap)
        for skill, future in futures.items():
            outcomes[skill] = future.result()
            log(f"phase 3 {skill}: exit {outcomes[skill]['exit']} in "
                f"{outcomes[skill]['seconds']}s")
    return {"outcomes": outcomes, "offsite": offsite_state}


# ========================================================================================
# main
# ========================================================================================

def honoured_crawl_delays(gate: Any) -> Dict[str, float]:
    """Hosts whose robots.txt Crawl-delay slowed this audit below its own pace.

    Reported because a long delay, correctly honoured, is often the reason a site's audit
    covers fewer pages -- and the reader should see that cause rather than guess at it.
    """
    limits = gate.stats().get("rate_limit", {})
    default = float(limits.get("default_min_interval", 0.0))
    return {host: float(info["min_interval"])
            for host, info in sorted(limits.get("hosts", {}).items())
            if float(info.get("min_interval", 0.0)) > default}


def coverage_block(ledger: Ledger, merged: Dict[str, Any], profile: Dict[str, Any],
                   render_info: Dict[str, Any], offsite_state: str,
                   run_budget: Optional[Any], live: bool,
                   crawl_delays: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    pages = ledger.entries(purpose="page")
    skipped_paths = []
    for entry in pages + ledger.entries(purpose="asset"):
        if entry.outcome in (ledger_mod.OUTCOME_BLOCKED_ROBOTS, ledger_mod.OUTCOME_BLOCKED_POLICY,
                             ledger_mod.OUTCOME_BUDGET, ledger_mod.OUTCOME_CIRCUIT_OPEN):
            normal = urlnorm.try_normalise(entry.url.split("#")[0])
            skipped_paths.append({"path": normal.path if normal else entry.url,
                                  "reason": entry.reason or entry.outcome})
    budget_info: Dict[str, Any] = {"requests_made": len([e for e in ledger.entries()
                                                          if e.status is not None])}
    if run_budget is not None:
        info = run_budget.to_dict()
        for degradation in info.get("degradations", []):
            degradation.pop("elapsed_seconds", None)
        budget_info.update(info)
    return {
        "pages_attempted": len(pages),
        "pages_fetched": len(ledger.pages()),
        "pages_rendered": sum(1 for e in pages if e.rendered_hash),
        "render_mode": render_info["mode"],
        "render_note": render_info.get("note", ""),
        "template_clusters": len(profile.get("template_clusters", [])),
        "robots_respected": True,
        "crawl_delay_seconds": dict(crawl_delays or {}),
        "paths_skipped": sorted(skipped_paths, key=lambda p: (p["path"], p["reason"])),
        "checks_skipped": merged["checks_skipped"],
        "skills_failed": merged["skills_failed"],
        "findings_rejected": merged["findings_rejected"],
        "budget": budget_info,
        "offsite_corroboration": offsite_state,
        "mode": "live" if live else "offline (existing evidence ledger)",
    }


def missing_dependencies(live: bool) -> List[str]:
    """Distribution names of required packages that cannot be imported."""
    needed = REQUIRED_PACKAGES + (LIVE_REQUIRED_PACKAGES if live else ())
    return [dist for module, dist in needed if importlib.util.find_spec(module) is None]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    missing = missing_dependencies(live=args.url is not None)
    if missing:
        print(f"missing required package(s): {', '.join(missing)}. From the marketplace root "
              f"run: python -m pip install -r requirements.txt", file=sys.stderr)
        return EXIT_DEPENDENCY
    log = RunLog(args.quiet)
    config = bootstrap.load_config(args.config)
    manifest = bootstrap.load_manifest()
    specialists = [s["id"] for s in manifest["skills"] if not s.get("entrypoint")]
    out = args.out.resolve()
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)

    live = args.url is not None
    state: Dict[str, Any] = {}
    if live:
        provisional_now = args.now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            state = live_crawl(args, config, out, provisional_now, log)
        except SystemExit as exc:
            log.write(out / "run.log")
            return int(exc.code)
        ledger_path = state["ledger_path"]
    else:
        ledger_path = args.ledger.resolve()
        if not (ledger_path / "index.json").exists():
            log(f"not an evidence ledger: {ledger_path}")
            return EXIT_UNREACHABLE

    reader = Ledger.open(ledger_path)
    now = audited_at(args, reader.meta)
    site = str(reader.meta.get("site_host") or "")

    if not reader.pages():
        page_attempts = reader.entries(purpose="page")
        blocked = [e for e in page_attempts if e.outcome == ledger_mod.OUTCOME_BLOCKED_ROBOTS]
        refused = [e for e in page_attempts if e.status in lexicons.CONTENT_REFUSAL_STATUSES]
        if blocked:
            log("every page is disallowed for this audit by robots.txt; reporting access "
                "findings only")
        elif refused:
            # A refusal is the site's answer, not a gap in the audit: an edge layer that
            # turns this honest client away turns crawlers away too, and that is the
            # finding. Content checks have nothing to read and report nothing.
            log(f"every page request was refused by the site (HTTP "
                f"{', '.join(sorted({str(e.status) for e in refused}))}); reporting access "
                f"findings only")
        else:
            log("no pages were fetched and none was refused by robots.txt or the site")
            log.write(out / "run.log")
            return EXIT_BUDGET if live else EXIT_UNREACHABLE
    elif live and state["budget"].pages_fetched < state["budget"].min_pages_for_coverage \
            and state["budget"].has_degraded(budget_mod.DEGRADE_EXHAUSTED):
        log("the budget closed before minimum coverage was reached")
        log.write(out / "run.log")
        return EXIT_BUDGET

    profile = build_profile(ledger_path, args.config, work)
    log(f"phase 2.5 profile: {profile['detected_type']} ({profile['confidence']}), "
        f"{len(profile['template_clusters'])} template cluster(s)")

    render_info = {"mode": "static", "note": "offline mode reuses renders already in the "
                                             "ledger", "rendered": 0}
    if live:
        run_budget = state["budget"]
        run_budget.apply_ladder()
        representatives = [c["example_urls"][0] for c in profile["template_clusters"]
                           if c.get("example_urls")]
        if args.render != "off" and not run_budget.has_degraded(budget_mod.DEGRADE_RENDER):
            render_info = crawl.render_tier(state["ledger"], representatives, run_budget,
                                            config, state["root"].host,
                                            state["gate"].user_agent, args.render)
        else:
            render_info = {"mode": "static", "rendered": 0,
                           "note": "the render tier was disabled" if args.render == "off"
                           else "the render tier was dropped by the time budget"}
        if args.render == "on" and render_info["mode"] != "playwright":
            log(f"--render on requested but unavailable: {render_info['note']}")
        if profile["detected_type"] == "publisher":
            crawl.probe_feeds(state["gate"], state["root"].origin, lexicons.FEED_PATHS)
        log(f"phase 2.5 render tier: {render_info['mode']} {render_info.get('note', '')}")
    elif any(e.rendered_hash for e in reader.entries(purpose="page")):
        render_info = {"mode": "playwright", "note": "renders captured in the ledger were "
                                                     "reused", "rendered": 0}

    offsite = None
    if live and args.offsite == "on" and state["budget"].may_go_offsite():
        gate, cfg = state["gate"], config
        offsite = lambda path: crawl.fetch_offsite(gate, path, cfg)  # noqa: E731
    fan = fan_out(ledger_path, work, config, args.config, specialists, offsite, log)
    offsite_state = fan["offsite"]
    if not live:
        offsite_state = "enabled" if reader.entries(purpose="offsite") else "disabled"
    elif args.offsite == "off":
        offsite_state = "disabled"

    rejected: List[Dict[str, str]] = []
    files = merge_findings.load_findings_files(
        {s: work / f"findings.{s}.json" for s in specialists}, rejected)
    site_profile = SiteProfile.from_dict(profile)
    merged = merge_findings.merge(files, site_profile, specialists, rejected)
    (work / "merged.json").write_text(json.dumps(merged, indent=2, sort_keys=True,
                                                 ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"phase 4 merge: {len(merged['findings'])} findings, "
        f"{len(merged['masked_findings'])} masked, {len(merged['findings_rejected'])} rejected")

    reader = Ledger.open(ledger_path)
    recommendations = recommend.generate(merged, site_profile, reader, config)
    log(f"phase 5 recommendations: {len(recommendations)}")

    profile_public = {k: v for k, v in profile.items() if k not in ("template_clusters",
                                                                     "pages")}
    report = render_report.build_report(
        site=site, audited_at=now, merged=merged, recommendations=recommendations,
        profile=profile_public,
        coverage=coverage_block(reader, merged, profile, render_info, offsite_state,
                                state.get("budget"), live,
                                honoured_crawl_delays(state["gate"]) if live else None),
        manifest=manifest,
        plan_size=int(bootstrap.threshold(config, "recommendations.action_plan_size", 5)),
    )

    result = verify_report.verify(report, ledger_path)
    (work / "verification.json").write_text(json.dumps(result, indent=2) + "\n",
                                            encoding="utf-8")
    request_log: List[str] = []
    if live:
        state["gate"].write_request_log(out / "requests.log")
        request_log = [f"requests: {json.dumps(state['gate'].stats(), sort_keys=True)}",
                       f"budget: {json.dumps(state['budget'].to_dict(), sort_keys=True)}"]

    if not result["verified"]:
        render_report.write(report, out, stem="audit.unverified")
        log(f"phase 6 VERIFICATION FAILED ({len(result['problems'])} problem(s)); the "
            f"report was not published. See work/verification.json")
        for problem in result["problems"][:10]:
            log(f"  - {problem}")
        log.write(out / "run.log", request_log)
        return EXIT_VERIFY

    for stale in (out / "audit.unverified.json", out / "audit.unverified.md"):
        if stale.exists():
            stale.unlink()
    (out / "audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    if not args.json_only:
        (out / "audit.md").write_text(render_report.render_markdown(report), encoding="utf-8")
    log(f"phase 6 verified: {result['evidence_refs_resolved']} evidence refs re-resolved; "
        f"wrote {out / 'audit.json'}")

    if live and not args.keep_evidence:
        shutil.rmtree(ledger_path, ignore_errors=True)
        log("phase 7 teardown: evidence ledger removed (--no-evidence)")
    log.write(out / "run.log", request_log)

    summary = report["summary"]
    print(f"{site}: {summary['total_findings']} findings "
          f"({summary['critical']} critical, {summary['high']} high, "
          f"{summary['medium']} medium, {summary['low']} low), "
          f"{summary['masked']} masked, {summary['proactive_recommendations']} "
          f"recommendations -> {out / 'audit.json'}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
