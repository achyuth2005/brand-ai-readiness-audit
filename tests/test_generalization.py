"""Generalization by construction: no site-specific logic, and no guessing a site's type."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest

from helpers import ROOT

from auditkit import profile

# Hosts that may appear in detection code: vocabulary namespaces and reserved examples.
# localhost appears only in SSRF documentation describing what is refused.
ALLOWED_HOSTS = {"schema.org", "www.sitemaps.org", "sitemaps.org", "json-schema.org",
                 "example.com", "localhost"}
URL_HOST_RE = re.compile(r"https?://([a-z0-9][a-z0-9.\-]*[a-z0-9])", re.IGNORECASE)
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class GeneralizationTests(unittest.TestCase):

    def test_no_site_specific_hosts_in_detection_code(self):
        offenders = []
        paths = list((ROOT / "lib").rglob("*.py")) + list((ROOT / "skills").glob("*/scripts/*.py"))
        for path in sorted(paths):
            for host in URL_HOST_RE.findall(path.read_text(encoding="utf-8")):
                host = host.lower()
                if host in ALLOWED_HOSTS or IP_RE.match(host):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {host}")
        self.assertEqual(offenders, [])

    def test_balanced_evidence_yields_ambiguous_not_a_guess(self):
        pages = [
            {"url": "https://site.test/a", "schema_types": ("Product",), "text_lower": "",
             "has_add_to_cart": True},
            {"url": "https://site.test/b", "schema_types": ("Article",), "text_lower": "",
             "has_byline": True},
        ]
        result = profile.classify(pages)
        self.assertEqual(result.detected_type, profile.AMBIGUOUS)
        # Declining to guess scopes out every type-conditional expectation.
        self.assertIn("G2.SD.TYPE_MISMATCH", result.to_dict()["checks_not_applicable"])

    def test_the_domain_name_is_never_evidence(self):
        def evidence(host):
            return [{"url": f"https://{host}/about", "schema_types": (), "text_lower": "about"},
                    {"url": f"https://{host}/contact", "schema_types": (), "text_lower": "hi"}]
        shop = profile.classify(evidence("shop.test"))
        neutral = profile.classify(evidence("neutral.test"))
        self.assertEqual(shop.scores, neutral.scores)
        self.assertEqual(shop.detected_type, neutral.detected_type)

    def test_skill_documentation_tables_match_the_registry(self):
        result = subprocess.run([sys.executable, str(ROOT / "tests" / "sync_check_tables.py"),
                                 "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
