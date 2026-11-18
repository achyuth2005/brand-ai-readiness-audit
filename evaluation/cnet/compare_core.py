"""Compare an audit.json against a site's core findings (default: phase3/core_findings_cnet.json).

Usage: python compare_core.py <audit.json or audit.unverified.json> [core_findings.json]
Default core file: core_findings_cnet.json (the demo site).
Exit 0 = every core finding present (PASS); exit 1 = at least one core finding missing.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    core_path = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "core_findings_cnet.json"
    core = json.loads(core_path.read_text())
    report = json.loads(Path(sys.argv[1]).read_text())
    got = {(f["check_id"], f["severity"]) for f in report["findings"]}
    masked = {(f["check_id"], f["severity"]) for f in report.get("masked_findings", [])}
    want = {(c["check_id"], c["severity"]) for c in core["core"]}
    allowed = {(c["check_id"], c["severity"]) for c in core["variable"]}
    missing = sorted(want - got)
    extra = sorted(got - want - allowed)
    s = report["summary"]
    print(f"site {report['site']}  audited_at {report['audited_at']}")
    print(f"summary: {s['total_findings']} findings ({s['critical']} critical / {s['high']} high / "
          f"{s['medium']} medium / {s['low']} low), {s['masked']} masked")
    print(f"core present: {len(want) - len(missing)}/{len(want)}")
    for m in missing:
        note = " (present but masked)" if m in masked else ""
        print(f"  MISSING core: {m[1]} {m[0]}{note}")
    for e in extra:
        print(f"  NEW (not in the reference runs): {e[1]} {e[0]}")
    skills_failed = report.get("coverage", {}).get("skills_failed") or []
    if skills_failed:
        print(f"  WARNING skills_failed: {[x.get('skill') for x in skills_failed]} "
              "(run one audit at a time, on mains power)")
    print("PASS" if not missing else "FAIL")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
