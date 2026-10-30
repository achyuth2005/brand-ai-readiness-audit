"""Dominance masking: consequences are retained but never reported as peers of their cause."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import helpers
from fixtures import sites

import merge_findings
from auditkit.finding import Finding, Scope, SuggestedAction, metric_ref


def make(check_id, *, level="template", template="T1", url="https://site.test/a",
         confidence="high", title=""):
    return Finding(
        check_id=check_id,
        title=title or f"{check_id} fixture finding",
        evidence="A script-generated evidence statement for the masking tests.",
        mechanism="A stated mechanism, long enough to satisfy the contract.",
        suggested_action=SuggestedAction(summary="Apply the fix", steps=["Apply the fix."],
                                         verification="Re-run and confirm."),
        confidence=confidence,
        scope=Scope(level=level, template_id=template, pages_affected=3, pages_sampled=3,
                    example_urls=[url]),
        evidence_refs=[metric_ref("0" * 64, "fixture_metric", 1)],
    )


class MaskingUnitTests(unittest.TestCase):

    def test_render_gap_masks_its_consequences_but_never_engagement(self):
        csr = make("G1.RENDER.CSR_CONTENT_GAP")
        absent = make("G2.SD.ABSENT")
        no_h1 = make("GE.ORIENT.NO_H1")
        active, masked = merge_findings.mask([csr, absent, no_h1])
        self.assertIn(csr, active)
        self.assertIn(no_h1, active)
        self.assertEqual([(m.check_id, by.check_id) for m, by, _ in masked],
                         [("G2.SD.ABSENT", "G1.RENDER.CSR_CONTENT_GAP")])

    def test_a_masked_finding_never_masks_anything(self):
        wall = make("G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT")
        csr = make("G1.RENDER.CSR_CONTENT_GAP")
        absent = make("G2.SD.ABSENT")
        _active, masked = merge_findings.mask([wall, csr, absent])
        maskers = {m.check_id: by.check_id for m, by, _ in masked}
        self.assertEqual(maskers, {"G1.RENDER.CSR_CONTENT_GAP": "G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT",
                                   "G2.SD.ABSENT": "G0.GATING.AUTH_WALL_ON_PUBLIC_CONTENT"})

    def test_downstream_never_masks_upstream(self):
        syntax = make("G2.SD.JSONLD_SYNTAX_ERROR")
        csr = make("G1.RENDER.CSR_CONTENT_GAP")
        active, masked = merge_findings.mask([syntax, csr])
        self.assertIn(csr, active)
        self.assertNotIn("G1.RENDER.CSR_CONTENT_GAP", [m.check_id for m, _, _ in masked])

    def test_masking_requires_overlapping_scope(self):
        csr = make("G1.RENDER.CSR_CONTENT_GAP", template="T1", url="https://site.test/a")
        absent = make("G2.SD.ABSENT", level="page", template="T2", url="https://site.test/b")
        _active, masked = merge_findings.mask([csr, absent])
        self.assertEqual(masked, [])

    def test_blocking_both_core_indexes_masks_interpretation_and_citation(self):
        google = make("G0.ROBOTS.AI_AGENT_BLOCKED", level="site",
                      title="robots.txt blocks Googlebot")
        bing = make("G0.ROBOTS.AI_AGENT_BLOCKED", level="site", template="T9",
                    url="https://site.test/z", title="robots.txt blocks Bingbot")
        answer = make("G3.ANSW.NO_DEFINITIONAL_SENTENCE", level="site")
        _active, masked = merge_findings.mask([google, bing, answer])
        self.assertEqual([m.check_id for m, _, _ in masked], ["G3.ANSW.NO_DEFINITIONAL_SENTENCE"])


class MaskingIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        ledger = helpers.build_ledger(Path(cls.tmp) / "ledger", sites.integration_site())
        cls.code, cls.report = helpers.run_offline_audit(ledger, Path(cls.tmp) / "out")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_audit_verified(self):
        self.assertEqual(self.code, 0)
        self.assertIsNotNone(self.report)

    def test_render_gap_masks_structured_data_absence_on_its_template(self):
        csr = next(f for f in self.report["findings"]
                   if f["check_id"] == "G1.RENDER.CSR_CONTENT_GAP")
        masked = {f["id"]: f for f in self.report["masked_findings"]}
        masked_checks = {masked[i]["check_id"] for i in csr.get("masks", [])}
        self.assertIn("G2.SD.ABSENT", masked_checks)

    def test_masking_graph_is_consistent_and_excludes_engagement(self):
        active = {f["id"] for f in self.report["findings"]}
        for item in self.report["masked_findings"]:
            self.assertIn(item["masked_by"], active)
            self.assertNotEqual(item["gate"], "GE")
        self.assertEqual(self.report["summary"]["masked"], len(self.report["masked_findings"]))

    def test_action_plan_never_schedules_a_masked_finding(self):
        masked = {f["id"] for f in self.report["masked_findings"]}
        self.assertFalse(set(self.report["action_plan"]["first_five"]) & masked)

    def test_masked_severity_is_below_its_masker(self):
        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        by_id = {f["id"]: f for f in self.report["findings"]}
        for item in self.report["masked_findings"]:
            self.assertGreater(rank[item["severity"]], rank[by_id[item["masked_by"]]["severity"]])


if __name__ == "__main__":
    unittest.main()
