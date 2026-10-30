#!/usr/bin/env python3
"""Phase 6b -- VERIFY. The anti-hallucination gate.

    A report that cannot be re-derived from stored bytes is not a report.

This script re-opens the evidence ledger and checks the rendered report against it. Any
failure is a non-zero exit, and the orchestrator does not publish ``audit.json`` or
``audit.md`` when verification fails -- the candidate files are left under a
``.unverified`` name with the problem list beside them.

What is checked
---------------
1. **Schema.** The report validates against ``schema/audit-report.schema.json``.
2. **Counts.** ``summary.total_findings`` equals the number of findings and every
   per-severity count matches exactly. Masked findings and observations are not counted.
3. **Identity.** Every id is unique and well-formed; every ``check_id`` exists in the
   closed registry.
4. **Dependencies.** ``masks`` and ``masked_by`` agree in both directions; no masked
   finding appears in the action plan; every plan id exists.
5. **Advice is not defect.** Every proactive recommendation carries ``is_defect: false``.
6. **Ledger integrity.** Every stored body re-hashes to its recorded hash.
7. **Evidence re-resolution.** Every evidence ref on every finding and masked finding:

   ``dom``        the selector still matches a node in the stored response
   ``header``     the header is present, with that value when one is recorded
   ``file_line``  the stored file's line still contains that text
   ``jsonpath``   the path resolves in the page's JSON-LD or hydration payload to that value
   ``metric``     the ledger entry it names exists and the value is well-formed. Metric
                  *values* are recomputed by re-running the analyzers over the frozen
                  ledger, which is what the determinism test does; this gate does not
                  re-implement every detector a second time.

8. **Secrets.** The report contains no credential-shaped strings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_here = Path(__file__).resolve()
while _here.parent != _here and not (_here / "marketplace.json").exists():
    _here = _here.parent
sys.path.insert(0, str(_here / "lib"))

from auditkit import bootstrap, checks, redact, sdextract, textnorm  # noqa: E402
from auditkit.dom import Document  # noqa: E402
from auditkit.ledger import Ledger, LedgerError  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 2


class Verifier:
    def __init__(self, report: Dict[str, Any], ledger: Ledger):
        self.report = report
        self.ledger = ledger
        self.problems: List[str] = []
        self.resolved = 0
        self._docs: Dict[str, Document] = {}

    def fail(self, message: str) -> None:
        self.problems.append(message)

    # -- 1 schema ------------------------------------------------------------------------
    def check_schema(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.fail("jsonschema is not installed, so the report could not be validated "
                      "against its schema (pip install -r requirements.txt)")
            return
        schema = bootstrap.load_schema("audit-report.schema.json")
        for error in sorted(Draft202012Validator(schema).iter_errors(self.report),
                            key=lambda e: list(e.path))[:20]:
            location = "/".join(str(p) for p in error.path) or "(root)"
            self.fail(f"schema: {location}: {error.message[:200]}")

    # -- 2 counts ------------------------------------------------------------------------
    def check_counts(self) -> None:
        findings = self.report.get("findings", [])
        summary = self.report.get("summary", {})
        if summary.get("total_findings") != len(findings):
            self.fail(f"summary.total_findings is {summary.get('total_findings')} but "
                      f"{len(findings)} findings are listed")
        for level in ("critical", "high", "medium", "low"):
            actual = sum(1 for f in findings if f.get("severity") == level)
            if summary.get(level, 0) != actual:
                self.fail(f"summary.{level} is {summary.get(level)} but {actual} "
                          f"{level} finding(s) are listed")
        if "masked" in summary and summary["masked"] != len(self.report.get("masked_findings", [])):
            self.fail("summary.masked does not match masked_findings")

    # -- 3, 4, 5 identity and dependencies --------------------------------------------------
    def check_identity(self) -> None:
        findings = self.report.get("findings", [])
        masked = self.report.get("masked_findings", [])
        recs = self.report.get("proactive_recommendations", [])
        ids = [f.get("id") for f in findings + masked] + [r.get("id") for r in recs]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            self.fail(f"duplicate ids: {', '.join(duplicates)}")
        for item in findings + masked:
            if not checks.is_known(item.get("check_id", "")):
                self.fail(f"{item.get('id')}: check_id {item.get('check_id')!r} is not in "
                          f"the closed registry")
            elif checks.gate_of(item["check_id"]) != item.get("gate"):
                self.fail(f"{item.get('id')}: gate {item.get('gate')} disagrees with the "
                          f"registry")

        active_ids = {f["id"] for f in findings}
        masked_ids = {f["id"] for f in masked}
        by_id = {f["id"]: f for f in findings + masked}
        for item in masked:
            masker = by_id.get(item.get("masked_by", ""))
            if masker is None:
                self.fail(f"{item['id']}: masked_by {item.get('masked_by')!r} does not exist")
            elif item["id"] not in masker.get("masks", []):
                self.fail(f"{item['id']}: masked by {masker['id']} but not listed in its masks")
            elif masker["id"] in masked_ids:
                self.fail(f"{item['id']}: masked by {masker['id']}, which is itself masked")
        for item in findings:
            for masked_id in item.get("masks", []):
                if masked_id not in masked_ids:
                    self.fail(f"{item['id']}: masks {masked_id}, which is not a masked finding")

        plan = self.report.get("action_plan", {}).get("first_five", [])
        rec_ids = {r["id"] for r in recs}
        for plan_id in plan:
            if plan_id in masked_ids:
                self.fail(f"action plan lists {plan_id}, which is masked by a root cause")
            elif plan_id not in active_ids and plan_id not in rec_ids:
                self.fail(f"action plan lists {plan_id}, which does not exist")
        for rec in recs:
            if rec.get("is_defect") is not False:
                self.fail(f"{rec.get('id')}: a proactive recommendation must carry "
                          f"is_defect: false")

    # -- 6 ledger ------------------------------------------------------------------------
    def check_ledger(self) -> None:
        for problem in self.ledger.verify_integrity()[:10]:
            self.fail(f"ledger integrity: {problem}")

    # -- 7 evidence ----------------------------------------------------------------------
    def _doc(self, entry) -> Optional[Document]:
        if entry.entry_id not in self._docs:
            try:
                self._docs[entry.entry_id] = Document(self.ledger.text(entry), url=entry.url)
            except (LedgerError, RuntimeError):
                return None
        return self._docs[entry.entry_id]

    def check_evidence(self) -> None:
        for item in self.report.get("findings", []) + self.report.get("masked_findings", []):
            refs = item.get("evidence_refs", [])
            if not refs:
                self.fail(f"{item.get('id')}: has no evidence_refs")
            for index, ref in enumerate(refs):
                problem = self.resolve(ref)
                if problem:
                    self.fail(f"{item.get('id')} ({item.get('check_id')}) evidence_refs"
                              f"[{index}] {ref.get('kind')}: {problem}")
                else:
                    self.resolved += 1

    def resolve(self, ref: Dict[str, Any]) -> str:
        """Return "" when the ref resolves, or a description of why it does not."""
        entry = self.ledger.by_id(ref.get("url_hash", ""))
        if entry is None:
            return f"ledger entry {ref.get('url_hash', '')[:12]} does not exist"
        kind = ref.get("kind")

        if kind == "metric":
            value = ref.get("value")
            if not isinstance(value, (int, float, str)) or isinstance(value, bool):
                return f"metric {ref.get('name')!r} has a malformed value {value!r}"
            return ""

        if kind == "header":
            actual = entry.header(ref.get("name", ""))
            if not actual:
                return f"header {ref.get('name')!r} is not present on the stored response"
            expected = ref.get("value")
            if expected not in (None, "") and str(expected).strip().lower() != actual.strip().lower():
                return f"header {ref.get('name')!r} is {actual!r}, not {expected!r}"
            return ""

        if kind == "file_line":
            lines = self.ledger.text(entry).splitlines()
            line = int(ref.get("line", 0))
            if not 1 <= line <= len(lines):
                return f"line {line} is outside the stored file ({len(lines)} lines)"
            if textnorm.collapse_space(lines[line - 1]) != textnorm.collapse_space(ref.get("text", "")):
                return f"line {line} reads {lines[line - 1].strip()!r}, not {ref.get('text')!r}"
            return ""

        if kind == "dom":
            doc = self._doc(entry)
            if doc is None:
                return "the stored response could not be parsed"
            if doc.select_one(ref.get("selector", "")) is None:
                return f"selector {ref.get('selector')!r} matches nothing in the stored response"
            return ""

        if kind == "jsonpath":
            doc = self._doc(entry)
            if doc is None:
                return "the stored response could not be parsed"
            return "" if self._jsonpath_resolves(doc, ref.get("path", ""), ref.get("value")) \
                else f"path {ref.get('path')!r} does not yield {str(ref.get('value'))[:60]!r}"
        return f"unknown evidence kind {kind!r}"

    def _jsonpath_resolves(self, doc: Document, path: str, expected: Any) -> bool:
        candidates: List[Tuple[Any, str]] = []
        extraction = sdextract.extract(doc)
        for node in extraction.nodes:
            prefix = node.json_path
            if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "["):
                candidates.append((node, path[len(prefix):]))
        for blob in doc.state_blobs():
            if blob.get("data") is not None and path.startswith("$"):
                candidates.append((blob["data"], path[1:]))
        for root, remainder in sorted(candidates, key=lambda c: len(c[1])):
            value = self._walk(root, remainder)
            if value is _MISSING:
                continue
            if self._matches(value, expected):
                return True
        return False

    @staticmethod
    def _walk(root: Any, remainder: str) -> Any:
        import re
        current = root
        for token in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", remainder):
            key, index = token
            if index:
                if isinstance(current, list) and int(index) < len(current):
                    current = current[int(index)]
                else:
                    return _MISSING
                continue
            if isinstance(current, sdextract.SDNode):
                if key == "@type":
                    current = list(current.types)
                    continue
                value = current.get(key)
            elif isinstance(current, dict):
                value = current.get(key)
            else:
                return _MISSING
            if value is None:
                return _MISSING
            current = value
        return current

    @staticmethod
    def _matches(value: Any, expected: Any) -> bool:
        if expected is None:
            return True
        # Strip the truncation mark before normalising: normalisation expands "…" to "...",
        # which would otherwise survive as a suffix no stored value starts with.
        raw_want = str(expected)
        if raw_want.endswith("…"):
            raw_want = raw_want[:-1]
        want = textnorm.comparison_key(raw_want).strip()
        leaves = value if isinstance(value, list) else [value]
        for leaf in leaves:
            if isinstance(leaf, (str, int, float)):
                texts = [leaf]
            else:
                # A path that ends on an entity (``$.publisher``, ``$.hasVariant[3]``) is
                # evidence about that entity's type; its first scalar is only a fallback.
                texts = list(sdextract.node_types(leaf)) + [sdextract.scalar(leaf)]
            for text in texts:
                got = textnorm.comparison_key(str(text))
                if got == want or (want and got.startswith(want)):
                    return True
                try:
                    if abs(float(got) - float(want)) < 1e-9:
                        return True
                except ValueError:
                    pass
        return False

    # -- 8 secrets -----------------------------------------------------------------------
    def check_secrets(self) -> None:
        found = redact.scan_payload(self.report)
        if found:
            self.fail(f"the report contains credential-shaped strings: {', '.join(found)}")

    def run(self) -> Dict[str, Any]:
        self.check_schema()
        self.check_counts()
        self.check_identity()
        self.check_ledger()
        self.check_evidence()
        self.check_secrets()
        return {
            "verified": not self.problems,
            "evidence_refs_resolved": self.resolved,
            "problems": self.problems,
        }


class _Missing:
    pass


_MISSING = _Missing()


def verify(report: Dict[str, Any], ledger_path: Path) -> Dict[str, Any]:
    try:
        ledger = Ledger.open(ledger_path)
    except Exception as exc:
        return {"verified": False, "evidence_refs_resolved": 0,
                "problems": [f"the evidence ledger could not be opened: {exc}"]}
    return Verifier(report, ledger).run()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an audit report against its "
                                                 "evidence ledger.")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = verify(report, args.ledger)
    print(json.dumps(result, indent=2))
    return EXIT_OK if result["verified"] else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
