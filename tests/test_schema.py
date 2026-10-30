"""Format compliance: manifest, Agent Skills frontmatter, report schema, verification gate."""

from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers
from helpers import ROOT
from fixtures import sites

import verify_report
from auditkit import checks

ALLOWED_FRONTMATTER = {"name", "description", "license", "compatibility", "allowed-tools",
                       "metadata"}


def frontmatter(path: Path) -> dict:
    """Minimal parser for the frontmatter shape the spec defines (scalars plus one map)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} does not start with YAML frontmatter"
    block = text.split("---\n", 2)[1]
    data: dict = {}
    current_map = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current_map is not None:
            key, _, value = line.strip().partition(":")
            data[current_map][key.strip()] = value.strip().strip('"')
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            data[key] = {}
            current_map = key
        else:
            data[key] = value
            current_map = None
    return data


class ManifestTests(unittest.TestCase):

    def setUp(self):
        self.manifest = json.loads((ROOT / "marketplace.json").read_text(encoding="utf-8"))

    def test_exactly_one_entrypoint_matching_the_top_level_key(self):
        entrypoints = [s["id"] for s in self.manifest["skills"] if s.get("entrypoint")]
        self.assertEqual(entrypoints, [self.manifest["entrypoint"]])

    def test_manifest_skills_match_the_registry_owners(self):
        specialists = {s["id"] for s in self.manifest["skills"] if not s.get("entrypoint")}
        self.assertEqual(specialists, set(checks.skills()))

    def test_every_skill_folder_is_spec_compliant(self):
        for skill in self.manifest["skills"]:
            folder = ROOT / skill["path"]
            skill_md = folder / "SKILL.md"
            self.assertTrue(skill_md.exists(), skill_md)
            meta = frontmatter(skill_md)
            self.assertLessEqual(set(meta), ALLOWED_FRONTMATTER, skill["id"])
            self.assertEqual(meta["name"], folder.name)
            self.assertEqual(meta["name"], skill["id"])
            self.assertRegex(meta["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertLessEqual(len(meta["name"]), 64)
            self.assertTrue(20 <= len(meta["description"]) <= 1024, skill["id"])
            self.assertLessEqual(len(meta.get("compatibility", "")), 500)
            self.assertIsInstance(meta["allowed-tools"], str)
            if not skill.get("entrypoint"):
                self.assertNotIn("Write", meta["allowed-tools"].split(),
                                 "specialists declare least privilege")
            self.assertLess(len(skill_md.read_text(encoding="utf-8").splitlines()), 500)

    def test_every_referenced_reference_file_exists(self):
        for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
            for ref in re.findall(r"`(references/[\w\-]+\.md)`", skill_md.read_text()):
                self.assertTrue((skill_md.parent / ref).exists(), f"{skill_md.parent.name}: {ref}")

    def test_every_check_has_a_reference_document(self):
        for check_id in checks.all_ids():
            self.assertTrue((ROOT / checks.meta(check_id)["reference"]).exists(), check_id)


class DependencyPreflightTests(unittest.TestCase):
    """A clean machine gets an actionable exit code, never a traceback mid-audit."""

    def test_missing_required_package_is_reported_before_any_work(self):
        from unittest import mock
        import run_audit
        self.assertEqual(run_audit.missing_dependencies(live=True), [])
        real = run_audit.importlib.util.find_spec
        with mock.patch.object(run_audit.importlib.util, "find_spec",
                               side_effect=lambda name: None if name == "bs4" else real(name)):
            self.assertEqual(run_audit.missing_dependencies(live=False), ["beautifulsoup4"])
            with tempfile.TemporaryDirectory() as tmp:
                code = run_audit.main(["--ledger", tmp, "--out", tmp, "--quiet"])
            self.assertEqual(code, run_audit.EXIT_DEPENDENCY)


class ReportTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ledger = helpers.build_ledger(Path(cls.tmp) / "ledger", sites.integration_site())
        cls.out = Path(cls.tmp) / "out"
        cls.code, cls.report = helpers.run_offline_audit(cls.ledger, cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_report_validates_and_verifies(self):
        self.assertEqual(self.code, 0)
        result = verify_report.verify(self.report, self.ledger)
        self.assertTrue(result["verified"], result["problems"])
        self.assertGreater(result["evidence_refs_resolved"], 0)

    def test_brief_required_fields_are_always_present(self):
        for key in ("site", "audited_at", "summary", "findings"):
            self.assertIn(key, self.report)
        for key in ("total_findings", "critical", "high", "medium"):
            self.assertIn(key, self.report["summary"])
        for finding in self.report["findings"]:
            for key in ("id", "title", "severity", "evidence", "suggested_action"):
                self.assertIn(key, finding)
            self.assertEqual(set(finding["suggested_action"]), {"summary", "priority"})

    def test_findings_files_validate_against_their_schema(self):
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        report_schema = json.loads((ROOT / "schema/audit-report.schema.json").read_text())
        findings_schema = json.loads((ROOT / "schema/finding.schema.json").read_text())
        registry = Registry().with_resources([
            ("audit-report.schema.json", Resource.from_contents(report_schema)),
            (report_schema["$id"], Resource.from_contents(report_schema))])
        validator = Draft202012Validator(findings_schema, registry=registry)
        for path in sorted((self.out / "work").glob("findings.*.json")):
            errors = [e.message for e in validator.iter_errors(json.loads(path.read_text()))]
            self.assertEqual(errors, [], path.name)

    def test_evidence_pointing_at_a_nested_entity_verifies_by_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = helpers.build_ledger(Path(tmp) / "ledger", sites.nested_entity_evidence())
            code, report = helpers.run_offline_audit(ledger, Path(tmp) / "out")
            self.assertEqual(code, 0)
            nested = [ref for finding in report["findings"] + report["masked_findings"]
                      for ref in finding["evidence_refs"]
                      if ref["kind"] == "jsonpath" and ref["path"] not in ("$", "")]
            self.assertTrue(nested, "the fixture should yield evidence at a nested JSON path")
            result = verify_report.verify(report, ledger)
            self.assertTrue(result["verified"], result["problems"])

    def _verify(self, mutated):
        return verify_report.verify(mutated, self.ledger)

    def test_verification_rejects_a_misstated_count(self):
        mutated = copy.deepcopy(self.report)
        mutated["summary"]["critical"] += 1
        self.assertFalse(self._verify(mutated)["verified"])

    def test_verification_rejects_unresolvable_evidence(self):
        mutated = copy.deepcopy(self.report)
        for finding in mutated["findings"]:
            for ref in finding["evidence_refs"]:
                if ref["kind"] == "dom":
                    ref["selector"] = "#no-such-node-anywhere"
                    result = self._verify(mutated)
                    self.assertFalse(result["verified"])
                    self.assertTrue(any("matches nothing" in p for p in result["problems"]))
                    return
        self.skipTest("no dom evidence ref in this report")

    def test_verification_rejects_an_invented_check(self):
        mutated = copy.deepcopy(self.report)
        mutated["findings"][0]["check_id"] = "G1.MADE.UP"
        self.assertFalse(self._verify(mutated)["verified"])

    def test_verification_rejects_a_masked_finding_in_the_plan(self):
        if not self.report["masked_findings"]:
            self.skipTest("no masked findings in this report")
        mutated = copy.deepcopy(self.report)
        mutated["action_plan"]["first_five"][0] = mutated["masked_findings"][0]["id"]
        self.assertFalse(self._verify(mutated)["verified"])


if __name__ == "__main__":
    unittest.main()
