"""Every threshold the code reads is declared in config/thresholds.json with a rationale."""

from __future__ import annotations

import json
import re
import unittest

from helpers import ROOT

KEY_RE = re.compile(r'threshold\(\s*(?:[\w.]+\s*,\s*)?"([a-z_]+\.[a-z_]+)"')


class ConfigTests(unittest.TestCase):

    def setUp(self):
        self.config = json.loads((ROOT / "config" / "thresholds.json").read_text(encoding="utf-8"))

    def _referenced_keys(self):
        keys = set()
        for base in ("lib", "skills"):
            for path in (ROOT / base).rglob("*.py"):
                keys.update(KEY_RE.findall(path.read_text(encoding="utf-8")))
        return keys

    def test_every_referenced_threshold_is_declared(self):
        keys = self._referenced_keys()
        self.assertGreater(len(keys), 50, "the scan should find the thresholds in use")
        missing = []
        for key in sorted(keys):
            section, name = key.split(".", 1)
            entry = self.config.get(section, {}).get(name)
            if not isinstance(entry, dict) or "value" not in entry:
                missing.append(key)
        self.assertEqual(missing, [])

    def test_every_declared_threshold_has_a_rationale(self):
        thin = []
        for section, entries in self.config.items():
            if section.startswith("_"):
                continue
            for name, entry in entries.items():
                if not isinstance(entry, dict) or len(str(entry.get("rationale", ""))) < 30:
                    thin.append(f"{section}.{name}")
        self.assertEqual(thin, [])


if __name__ == "__main__":
    unittest.main()
