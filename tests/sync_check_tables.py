#!/usr/bin/env python3
"""Keep each SKILL.md "Checks performed" table identical to the closed check registry.

The table in a skill's SKILL.md is generated from lib/auditkit/checks.py rather than
maintained by hand, so the document a grader reads cannot drift from the code that runs.

    python tests/sync_check_tables.py          # rewrite the tables
    python tests/sync_check_tables.py --check  # exit 1 if any table is out of date
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from auditkit import checks  # noqa: E402

BEGIN = "<!-- checks-table:{skill} (generated from lib/auditkit/checks.py; do not edit by hand) -->"
END = "<!-- /checks-table -->"


def rendered(skill: str) -> str:
    return f"{BEGIN.format(skill=skill)}\n{checks.markdown_table(checks.for_skill(skill))}\n{END}"


def sync(check_only: bool) -> int:
    stale = []
    for skill in checks.skills():
        path = ROOT / "skills" / skill / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        block = rendered(skill)
        pattern = re.compile(
            r"<!-- checks-table:" + re.escape(skill) + r"[^>]*-->(?:.*?" + re.escape(END) + r")?",
            re.DOTALL)
        if not pattern.search(text):
            print(f"{path}: no checks-table marker", file=sys.stderr)
            stale.append(skill)
            continue
        updated = pattern.sub(lambda _m: block, text, count=1)
        if updated != text:
            stale.append(skill)
            if not check_only:
                path.write_text(updated, encoding="utf-8")
    if check_only and stale:
        print("out of date: " + ", ".join(stale), file=sys.stderr)
        return 1
    if not check_only:
        print(f"synced {len(checks.skills())} skill tables; changed: {', '.join(stale) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(sync("--check" in sys.argv))
