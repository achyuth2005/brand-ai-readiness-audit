"""Positive fixtures: each skill's detectors fire on pages that contain their defects."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import helpers
from fixtures import sites


class DetectorTests(unittest.TestCase):

    def _run(self, skill, specs):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = helpers.build_ledger(Path(tmp) / "ledger", specs)
            data = helpers.run_skill(skill, ledger, Path(tmp))
        self.assertEqual(data["_exit"], 0)
        self.assertNotIn("analyzer_error", data.get("metrics", {}))
        return data

    def assertFires(self, data, expected, kind="findings"):
        found = helpers.check_ids(data, kind)
        missing = sorted(set(expected) - found)
        self.assertEqual(missing, [], f"expected {kind} not produced; got {sorted(found)}")

    def test_access_policy(self):
        data = self._run("access-policy-audit", sites.access_positive())
        self.assertFires(data, {"G0.ROBOTS.AI_AGENT_BLOCKED", "G0.DIRECTIVE.NOSNIPPET",
                                "G0.DIRECTIVE.NOINDEX_HEADER", "G0.SITEMAP.MISSING"})
        self.assertFires(data, {"G0.ROBOTS.TRAINING_AGENT_BLOCKED"}, kind="observations")
        blocked = next(f for f in data["findings"] if f["check_id"] == "G0.ROBOTS.AI_AGENT_BLOCKED")
        self.assertIn("OAI-SearchBot", blocked["title"])
        self.assertTrue(any(r["kind"] == "file_line" for r in blocked["evidence_refs"]))

    def test_render_extraction(self):
        data = self._run("render-extraction-audit", sites.render_positive())
        self.assertFires(data, {"G1.RENDER.CSR_CONTENT_GAP", "G1.NONTEXT.FACT_LOCKED_IN_MEDIA",
                                "G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES",
                                "G1.TEXT.MAIN_LANDMARK_MISSING"})
        csr = next(f for f in data["findings"] if f["check_id"] == "G1.RENDER.CSR_CONTENT_GAP")
        self.assertEqual(csr["confidence"], "high", "tier 2 should confirm from the payload")
        self.assertTrue(any(r["kind"] == "jsonpath" for r in csr["evidence_refs"]))

    def test_structured_data(self):
        data = self._run("structured-data-audit", sites.structured_positive())
        self.assertFires(data, {"G2.SD.MARKUP_TEXT_CONTRADICTION", "G2.SD.JSONLD_SYNTAX_ERROR",
                                "G2.SD.UNKNOWN_TYPE", "G2.SD.PROP_TYPE_INVALID",
                                "G2.CANONICAL.NON_SELF_REFERENTIAL"})
        syntax = next(f for f in data["findings"] if f["check_id"] == "G2.SD.JSONLD_SYNTAX_ERROR")
        self.assertRegex(syntax["evidence"], r"line \d+ column \d+")

    def test_entity_trust(self):
        data = self._run("entity-trust-audit", sites.entity_positive())
        self.assertFires(data, {"G3.ENTITY.AMBIGUITY_RISK", "G3.ENTITY.NAME_INCONSISTENCY",
                                "G3.FRESH.SITEMAP_LASTMOD_UNIFORM",
                                "G3.FRESH.STALE_CONTENT_MARKERS",
                                "G3.CORROB.NO_AUTHOR_IDENTITY",
                                "G3.CORROB.NO_CITABLE_PRIMARY_SOURCE"})
        found = helpers.check_ids(data) | helpers.check_ids(data, "observations")
        self.assertIn("G3.FRESH.DATE_CONTRADICTION", found)

    def test_entity_two_pass_protocol_declares_but_never_fetches(self):
        org = {"@context": "https://schema.org", "@type": "Organization", "name": "Widgets",
               "sameAs": ["https://www.wikidata.org/wiki/Q100000002"]}
        specs = [sites.page("/", sites.html("Widgets", "<h1>Widgets</h1><p>Home.</p>",
                                            head=sites.ld(org)))]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = helpers.build_ledger(Path(tmp) / "ledger", specs)
            requests = Path(tmp) / "requests.json"
            data = helpers.run_skill("entity-trust-audit", ledger, Path(tmp),
                                     extra=["--emit-requests", str(requests)])
            import json
            declared = json.loads(requests.read_text())["requests"]
        self.assertEqual(data["findings"], [])
        self.assertEqual([r["method"] for r in declared], ["GET"])
        self.assertEqual(declared[0]["purpose"], "offsite")

    def test_answerability(self):
        data = self._run("answerability-audit", sites.answerability_positive())
        self.assertFires(data, {"G3.ANSW.LOW_SELF_CONTAINMENT", "G3.ANSW.NO_DEFINITIONAL_SENTENCE",
                                "G3.ANSW.NO_ANCHOR_ADDRESSABILITY", "G3.ANSW.ANSWER_BURIED",
                                "G3.ANSW.PRONOUN_ONLY_ENTITY_REFERENCE"})

    def test_engagement(self):
        data = self._run("engagement-audit", sites.engagement_positive())
        self.assertFires(data, {"GE.FRICTION.CONTENT_GATING_OVERLAY", "GE.FRICTION.NO_VIEWPORT_META",
                                "GE.NAV.DEAD_END_PAGE", "GE.ORIENT.DEEP_PAGE_NO_CONTEXT",
                                "GE.ORIENT.GENERIC_H1"})
        viewport = [f for f in data["findings"] if f["check_id"] == "GE.FRICTION.NO_VIEWPORT_META"]
        self.assertEqual(len(viewport), 1, "a shared-layout defect is one site-wide finding")
        self.assertEqual(viewport[0]["scope"]["level"], "site")


if __name__ == "__main__":
    unittest.main()
