"""Identical input produces byte-identical output, apart from audited_at (fixed here)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import helpers
from fixtures import sites


class DeterminismTests(unittest.TestCase):

    def test_two_runs_over_one_frozen_ledger_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = helpers.build_ledger(Path(tmp) / "ledger", sites.integration_site())
            first_code, _ = helpers.run_offline_audit(ledger, Path(tmp) / "run1")
            second_code, _ = helpers.run_offline_audit(ledger, Path(tmp) / "run2")
            self.assertEqual((first_code, second_code), (0, 0))
            for name in ("audit.json", "audit.md"):
                first = (Path(tmp) / "run1" / name).read_bytes()
                second = (Path(tmp) / "run2" / name).read_bytes()
                self.assertEqual(first, second, f"{name} differs between runs")


if __name__ == "__main__":
    unittest.main()
