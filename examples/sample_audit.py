#!/usr/bin/env python3
"""Run the full audit offline on a bundled sample site. No network access is used.

Builds an evidence ledger for the test suite's integration site (``site.test``, a reserved
test domain with deliberate defects in every gate), then runs the real entrypoint on it,
exactly as ``run_audit.py --ledger`` re-analyses any stored crawl:

    python examples/sample_audit.py [--out sample-output]

Writes ``<out>/ledger/`` (the input) and ``<out>/report/`` (audit.json, audit.md, logs).
The audit time is fixed, so the report is byte-identical on every run; the SHA-256 printed
at the end lets you check that.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: E402  (fixture-ledger builder shared with the test suite)
from fixtures import sites  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("sample-output"))
    args = parser.parse_args()

    shutil.rmtree(args.out, ignore_errors=True)
    ledger = helpers.build_ledger(args.out / "ledger", sites.integration_site())
    report_dir = args.out / "report"
    code = subprocess.call([
        sys.executable, str(ROOT / "skills/ai-readiness-audit/scripts/run_audit.py"),
        "--ledger", str(ledger), "--out", str(report_dir), "--now", helpers.AUDITED_AT,
    ])
    report = report_dir / "audit.json"
    if code != 0 or not report.exists():
        print(f"audit failed with exit code {code}", file=sys.stderr)
        return code or 1
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    print(f"\nreport: {report_dir / 'audit.md'}\naudit.json sha256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
