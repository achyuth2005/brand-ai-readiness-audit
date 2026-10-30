"""The Finding contract -- the only way a problem enters the report.

Anti-hallucination, layer 1
---------------------------
    Scripts author findings. The model never does.

``Finding`` is the sole constructor, and it refuses to build anything that is not
grounded:

* ``check_id`` must exist in the closed registry (:mod:`auditkit.checks`).
* ``evidence`` must be a non-empty, script-generated string.
* at least one ``evidence_ref`` must be attached, and every ref must be one of five
  typed kinds that ``verify_report.py`` can *re-resolve against the stored bytes*.

There is deliberately no code path by which free text becomes a finding. A specialist
that wants to report something it cannot evidence has exactly two honest options: emit a
lower-confidence observation, or record a skipped check with a reason. Both are
first-class here.

Evidence reference kinds
------------------------
Each kind names something re-checkable, and the verification gate re-resolves all of
them before the report is allowed to exist::

    dom        a CSS selector that must still match, plus an excerpt
    header     an HTTP header that must still be present with that value
    file_line  a line number in a stored text artifact that must still contain that text
    jsonpath   a path into a parsed JSON payload that must still yield that value
    metric     a named measurement, recomputable within tolerance, with its threshold

The ``metric`` kind is what makes threshold-based findings auditable: the report carries
the measured value *and* the threshold it was compared against, so a reader can disagree
with the threshold rather than having to trust the verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import checks, severity as severity_mod

EVIDENCE_KINDS = ("dom", "header", "file_line", "jsonpath", "metric")
CONFIDENCES = ("high", "medium", "low")
SCOPE_LEVELS = ("site", "template", "section", "page")
EFFORTS = ("trivial", "low", "medium", "high")


class FindingError(ValueError):
    """Raised when a finding fails its own contract. Never caught to paper over."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FindingError(message)


@dataclass
class EvidenceRef:
    """One re-resolvable pointer into the evidence ledger."""

    kind: str
    url_hash: str
    selector: str = ""
    name: str = ""
    value: Any = None
    path: str = ""
    line: Optional[int] = None
    text: str = ""
    excerpt: str = ""
    threshold: Any = None
    tolerance: Optional[float] = None

    def __post_init__(self) -> None:
        _require(self.kind in EVIDENCE_KINDS,
                 f"evidence kind {self.kind!r} must be one of {EVIDENCE_KINDS}")
        _require(bool(self.url_hash),
                 f"{self.kind} evidence must name the ledger entry it came from")
        if self.kind == "dom":
            _require(bool(self.selector), "dom evidence requires a selector")
        elif self.kind == "header":
            _require(bool(self.name), "header evidence requires a header name")
        elif self.kind == "file_line":
            _require(self.line is not None and self.line >= 1,
                     "file_line evidence requires a 1-based line number")
        elif self.kind == "jsonpath":
            _require(bool(self.path), "jsonpath evidence requires a path")
        elif self.kind == "metric":
            _require(bool(self.name), "metric evidence requires a metric name")
            _require(self.value is not None, "metric evidence requires a measured value")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind, "url_hash": self.url_hash}
        for key in ("selector", "name", "path", "text", "excerpt"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.value is not None:
            out["value"] = self.value
        if self.line is not None:
            out["line"] = self.line
        if self.threshold is not None:
            out["threshold"] = self.threshold
        if self.tolerance is not None:
            out["tolerance"] = self.tolerance
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceRef":
        return cls(
            kind=data["kind"], url_hash=data["url_hash"],
            selector=data.get("selector", ""), name=data.get("name", ""),
            value=data.get("value"), path=data.get("path", ""),
            line=data.get("line"), text=data.get("text", ""),
            excerpt=data.get("excerpt", ""), threshold=data.get("threshold"),
            tolerance=data.get("tolerance"),
        )


# Convenience constructors. Named so the call sites in the analyzers read as the
# observations they are.

def dom_ref(url_hash: str, selector: str, excerpt: str = "") -> EvidenceRef:
    return EvidenceRef(kind="dom", url_hash=url_hash, selector=selector, excerpt=excerpt)


def header_ref(url_hash: str, name: str, value: str) -> EvidenceRef:
    return EvidenceRef(kind="header", url_hash=url_hash, name=name.lower(), value=value)


def file_line_ref(url_hash: str, path: str, line: int, text: str) -> EvidenceRef:
    return EvidenceRef(kind="file_line", url_hash=url_hash, path=path, line=line,
                       text=text)


def jsonpath_ref(url_hash: str, path: str, value: Any) -> EvidenceRef:
    return EvidenceRef(kind="jsonpath", url_hash=url_hash, path=path, value=value)


def metric_ref(url_hash: str, name: str, value: Any, threshold: Any = None,
               tolerance: Optional[float] = None) -> EvidenceRef:
    return EvidenceRef(kind="metric", url_hash=url_hash, name=name, value=value,
                       threshold=threshold, tolerance=tolerance)


@dataclass
class Scope:
    """What the finding applies to, and how much of the site that is."""

    level: str = "page"
    template_id: str = ""
    pages_affected: int = 1
    pages_sampled: int = 1
    example_urls: List[str] = field(default_factory=list)
    path_class: str = ""

    def __post_init__(self) -> None:
        _require(self.level in SCOPE_LEVELS,
                 f"scope level {self.level!r} must be one of {SCOPE_LEVELS}")
        _require(self.pages_affected >= 0, "pages_affected cannot be negative")
        # Cap the example list: three URLs is enough to act on, and an unbounded list
        # makes the report unreadable on a large site.
        self.example_urls = sorted(set(self.example_urls))[:3]

    @property
    def key(self) -> str:
        """Deduplication key: two findings with the same check and scope are one."""
        if self.level == "site":
            return "site"
        if self.level == "template" and self.template_id:
            return f"template:{self.template_id}"
        if self.example_urls:
            return f"{self.level}:{self.example_urls[0]}"
        return f"{self.level}:{self.path_class or 'unknown'}"

    @property
    def evidence_fraction(self) -> str:
        """The ``k/n`` string that makes evidence quantitative rather than adjectival."""
        return f"{self.pages_affected}/{self.pages_sampled}"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "level": self.level,
            "pages_affected": self.pages_affected,
            "pages_sampled": self.pages_sampled,
        }
        if self.template_id:
            out["template_id"] = self.template_id
        if self.example_urls:
            out["example_urls"] = self.example_urls
        if self.path_class:
            out["path_class"] = self.path_class
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scope":
        return cls(
            level=data.get("level", "page"),
            template_id=data.get("template_id", ""),
            pages_affected=int(data.get("pages_affected", 1)),
            pages_sampled=int(data.get("pages_sampled", 1)),
            example_urls=list(data.get("example_urls", [])),
            path_class=data.get("path_class", ""),
        )


@dataclass
class SuggestedAction:
    """The fix. Ordered steps, an owner, and a way to verify it worked.

    ``verification`` is required, and that requirement is the point: it is what turns a
    recommendation into something a non-expert can close out, and it forces the fix to be
    concrete enough to be checkable. A "fix" nobody can confirm is advice, not a fix.
    """

    summary: str
    steps: List[str] = field(default_factory=list)
    effort: str = "medium"
    owner_role: str = ""
    verification: str = ""
    expected_effect: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.summary.strip()), "a suggested action needs a summary")
        _require(self.effort in EFFORTS,
                 f"effort {self.effort!r} must be one of {EFFORTS}")
        _require(bool(self.steps), "a suggested action needs at least one concrete step")
        _require(bool(self.verification.strip()),
                 "a suggested action must say how to verify the fix worked")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": list(self.steps),
            "effort": self.effort,
            "owner_role": self.owner_role,
            "verification": self.verification,
            "expected_effect": self.expected_effect,
        }


@dataclass
class Finding:
    """One evidence-backed problem. The only way a defect reaches the report."""

    check_id: str
    title: str
    evidence: str
    suggested_action: SuggestedAction
    mechanism: str
    scope: Scope = field(default_factory=Scope)
    confidence: str = "medium"
    fact_class: str = ""
    evidence_refs: List[EvidenceRef] = field(default_factory=list)
    detected_by: str = ""
    corroborated_by: List[str] = field(default_factory=list)
    signal_count: int = 1
    degraded: bool = False
    notes: List[str] = field(default_factory=list)

    # Assigned by the merge step, never by a specialist.
    id: str = ""
    severity: str = ""
    score: float = 0.0
    masked_by: str = ""
    masked_reason: str = ""
    masks: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require(checks.is_known(self.check_id),
                 f"unknown check_id {self.check_id!r}: findings may only cite ids in "
                 f"the closed registry (auditkit/checks.py)")
        _require(bool(self.title.strip()), f"{self.check_id}: a finding needs a title")
        _require(len(self.evidence.strip()) >= 20,
                 f"{self.check_id}: evidence must be a substantive, script-generated "
                 f"statement of what was observed, not a label")
        _require(bool(self.evidence_refs),
                 f"{self.check_id}: a finding needs at least one evidence_ref that the "
                 f"verification gate can re-resolve against the stored bytes")
        _require(self.confidence in CONFIDENCES,
                 f"{self.check_id}: confidence must be one of {CONFIDENCES}")
        _require(bool(self.mechanism.strip()),
                 f"{self.check_id}: a finding must state the mechanism by which it hurts "
                 f"discoverability or engagement")

        meta = checks.meta(self.check_id)
        if not self.fact_class:
            self.fact_class = meta["fact"]
        if not self.detected_by:
            self.detected_by = meta["skill"]
        if not self.severity:
            self.severity, self.score = severity_mod.derive(
                self.gate, self.scope.level, self.fact_class, self.confidence,
                self.check_id,
            )

    # -- derived ------------------------------------------------------------------------

    @property
    def gate(self) -> str:
        return checks.gate_of(self.check_id)

    @property
    def category(self) -> str:
        return checks.meta(self.check_id)["category"]

    @property
    def reference(self) -> str:
        return checks.meta(self.check_id)["reference"]

    @property
    def dedupe_key(self) -> str:
        return f"{self.check_id}|{self.scope.key}"

    @property
    def is_masked(self) -> bool:
        return bool(self.masked_by)

    @property
    def priority_value(self) -> float:
        return severity_mod.priority(self.score, self.confidence,
                                     self.suggested_action.effort)

    @property
    def priority_label(self) -> str:
        return severity_mod.priority_label(self.priority_value)

    def severity_explanation(self) -> str:
        return severity_mod.explain(self.gate, self.scope.level, self.fact_class,
                                    self.confidence, self.check_id)

    # -- serialisation ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to the report's finding shape.

        The brief's five required keys come first and are always present; everything
        after them is the additive superset that makes the finding auditable.
        """
        out: Dict[str, Any] = {
            # ---- required by the brief ----
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "evidence": self.evidence,
            "suggested_action": {
                "summary": self.suggested_action.summary,
                "priority": self.priority_label,
            },
            # ---- additive ----
            "check_id": self.check_id,
            "gate": self.gate,
            "category": self.category,
            "confidence": self.confidence,
            "detected_by": self.detected_by,
            "mechanism": self.mechanism,
            "scope": self.scope.to_dict(),
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
            "suggested_action_detail": self.suggested_action.to_dict(),
            "severity_derivation": self.severity_explanation(),
            "fact_class": self.fact_class,
            "signal_count": self.signal_count,
            "references": [self.reference],
        }
        if self.corroborated_by:
            out["corroborated_by"] = sorted(set(self.corroborated_by))
        if self.masks:
            out["masks"] = sorted(set(self.masks))
        if self.masked_by:
            out["masked_by"] = self.masked_by
            out["masked_reason"] = self.masked_reason
        if self.degraded:
            out["evidence_degraded"] = True
        if self.notes:
            out["notes"] = list(self.notes)
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        """Rebuild from a specialist's findings file. Used by the merge step."""
        detail = data.get("suggested_action_detail") or {}
        action = SuggestedAction(
            summary=(data.get("suggested_action") or {}).get("summary", ""),
            steps=list(detail.get("steps", [])) or ["(no steps recorded)"],
            effort=detail.get("effort", "medium"),
            owner_role=detail.get("owner_role", ""),
            verification=detail.get("verification", "") or "(not recorded)",
            expected_effect=detail.get("expected_effect", ""),
        )
        finding = cls(
            check_id=data["check_id"],
            title=data["title"],
            evidence=data["evidence"],
            suggested_action=action,
            mechanism=data.get("mechanism", ""),
            scope=Scope.from_dict(data.get("scope") or {}),
            confidence=data.get("confidence", "medium"),
            fact_class=data.get("fact_class", ""),
            evidence_refs=[EvidenceRef.from_dict(r)
                           for r in data.get("evidence_refs", [])],
            detected_by=data.get("detected_by", ""),
            corroborated_by=list(data.get("corroborated_by", [])),
            signal_count=int(data.get("signal_count", 1)),
            degraded=bool(data.get("evidence_degraded", False)),
            notes=list(data.get("notes", [])),
        )
        finding.id = data.get("id", "")
        if data.get("severity"):
            finding.severity = data["severity"]
        return finding


@dataclass
class Observation:
    """A low-confidence or informational item. Deliberately *not* a finding.

    Observations exist so that "we noticed something but cannot stand behind it as a
    defect" has an honest home. They are excluded from the summary counts, which is what
    keeps the finding count meaningful (rule R7).
    """

    check_id: str
    title: str
    detail: str
    evidence_refs: List[EvidenceRef] = field(default_factory=list)
    confidence: str = "low"
    detected_by: str = ""

    def __post_init__(self) -> None:
        _require(checks.is_known(self.check_id),
                 f"unknown check_id {self.check_id!r} in an observation")
        if not self.detected_by:
            self.detected_by = checks.meta(self.check_id)["skill"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "gate": checks.gate_of(self.check_id),
            "title": self.title,
            "detail": self.detail,
            "confidence": self.confidence,
            "detected_by": self.detected_by,
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
        }


@dataclass
class SkippedCheck:
    """A check that could not run.

    Rule R6: absence of evidence is not evidence. A check that could not run produces a
    skip entry with a reason and a remedy -- never a finding, and never a silent gap.
    """

    check_id: str
    reason: str
    remedy: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {"check_id": self.check_id, "reason": self.reason}
        if self.remedy:
            out["remedy"] = self.remedy
        return out


@dataclass
class NearMiss:
    """A check that measured something but stayed below its firing threshold.

    These feed the proactive recommendation engine's most credible generator: advice
    grounded in a real measurement of *this* site, stated with the number and the
    threshold, and honest about not being a defect.
    """

    check_id: str
    metric: str
    value: float
    threshold: float
    scope_key: str = ""
    detail: str = ""
    examples: List[str] = field(default_factory=list)

    @property
    def proximity(self) -> float:
        """How close to firing, as a fraction of the threshold."""
        if self.threshold == 0:
            return 0.0
        return abs(self.value - self.threshold) / abs(self.threshold)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "proximity": round(self.proximity, 4),
            "scope_key": self.scope_key,
            "detail": self.detail,
            "examples": list(self.examples)[:3],
        }


class FindingsFile:
    """The inter-skill contract: what a specialist writes and the merge step reads.

    Carries findings, observations, skipped checks, near misses and posture facts. The
    last two are why the entrypoint can generate evidence-linked proactive advice
    without any specialist having to know that the recommendation engine exists.
    """

    def __init__(self, skill: str):
        self.skill = skill
        self.findings: List[Finding] = []
        self.observations: List[Observation] = []
        self.skipped: List[SkippedCheck] = []
        self.near_misses: List[NearMiss] = []
        self.posture: Dict[str, Any] = {}
        self.metrics: Dict[str, Any] = {}

    # -- emit -------------------------------------------------------------------------

    def add(self, finding: Finding) -> Finding:
        """Add a finding, routing low confidence to observations (rule R7).

        The routing lives here rather than in each detector so that no detector can
        forget it: anything the audit is not confident about cannot become a counted
        problem, however high it scored.
        """
        if not finding.detected_by:
            finding.detected_by = self.skill
        if finding.severity == "info":
            # Below the lowest severity band: real, but too small to count as a problem.
            self.observations.append(Observation(
                check_id=finding.check_id,
                title=finding.title,
                detail=(f"{finding.evidence} Reported as an observation because its "
                        f"computed severity ({finding.severity_explanation()}) is below "
                        f"the threshold for a finding."),
                evidence_refs=finding.evidence_refs,
                confidence=finding.confidence,
                detected_by=finding.detected_by,
            ))
            return finding
        if finding.confidence == "low":
            self.observations.append(Observation(
                check_id=finding.check_id,
                title=finding.title,
                detail=(f"{finding.evidence} Reported as an observation rather than a "
                        f"finding because only {finding.signal_count} independent "
                        f"signal(s) supported it."),
                evidence_refs=finding.evidence_refs,
                confidence="low",
                detected_by=finding.detected_by,
            ))
            return finding
        self.findings.append(finding)
        return finding

    def observe(self, check_id: str, title: str, detail: str,
                evidence_refs: Optional[Sequence[EvidenceRef]] = None,
                confidence: str = "low") -> None:
        self.observations.append(Observation(
            check_id=check_id, title=title, detail=detail,
            evidence_refs=list(evidence_refs or []), confidence=confidence,
            detected_by=self.skill,
        ))

    def skip(self, check_id: str, reason: str, remedy: str = "") -> None:
        self.skipped.append(SkippedCheck(check_id=check_id, reason=reason, remedy=remedy))

    def near_miss(self, check_id: str, metric: str, value: float, threshold: float,
                  scope_key: str = "", detail: str = "",
                  examples: Optional[Sequence[str]] = None) -> None:
        self.near_misses.append(NearMiss(
            check_id=check_id, metric=metric, value=value, threshold=threshold,
            scope_key=scope_key, detail=detail, examples=list(examples or []),
        ))

    def note_posture(self, key: str, value: Any) -> None:
        """Record a present-but-shallow signal, for the posture-gap generator."""
        self.posture[key] = value

    def note_metric(self, key: str, value: Any) -> None:
        self.metrics[key] = value

    # -- serialise --------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Sorted on every axis, because determinism is a stated guarantee."""
        return {
            "skill": self.skill,
            "findings": [f.to_dict() for f in sorted(
                self.findings, key=lambda f: (f.check_id, f.scope.key))],
            "observations": [o.to_dict() for o in sorted(
                self.observations, key=lambda o: (o.check_id, o.title))],
            "checks_skipped": [s.to_dict() for s in sorted(
                self.skipped, key=lambda s: s.check_id)],
            "near_misses": [n.to_dict() for n in sorted(
                self.near_misses, key=lambda n: (n.check_id, n.scope_key))],
            "posture": dict(sorted(self.posture.items())),
            "metrics": dict(sorted(self.metrics.items())),
        }

    def write(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")

    @staticmethod
    def load(path: Path) -> Dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def summary_line(self) -> str:
        return (f"{self.skill}: {len(self.findings)} finding(s), "
                f"{len(self.observations)} observation(s), "
                f"{len(self.skipped)} skipped, {len(self.near_misses)} near miss(es)")
