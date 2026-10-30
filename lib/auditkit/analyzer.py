"""Shared harness for the six specialist analyzers.

Gives every specialist the same CLI, the same read-only view of the evidence ledger, and
the same page-level derived data, so the skills differ only in what they *check*.

Invariant B is visible here: :func:`build_context` opens the ledger read-only, and this
module imports nothing from :mod:`auditkit.netgate`. A specialist has no code path to the
network, and ``tests/test_safety.py`` asserts that every analyzer runs to completion with
egress blocked.

Standalone use is a design requirement, not a side effect. Each specialist passes test
T2 -- "could a user invoke it alone and get a coherent answer?" -- so when ``--profile``
is omitted the harness recomputes template clusters and site profile from the ledger
itself. That makes each skill genuinely useful on its own, which is what justifies the
decomposition.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import bootstrap, dom, profile as profile_mod, template as template_mod
from . import sdextract, textnorm, urlnorm
from .finding import Finding, FindingsFile, SuggestedAction
from .ledger import Ledger, LedgerEntry

EXIT_OK = 0
EXIT_MALFORMED_LEDGER = 2


@dataclass
class PageView:
    """One crawled page plus everything the detectors commonly need.

    Parsing and structured-data extraction are lazy and cached: six analyzers over 30
    pages would otherwise reparse the same HTML 180 times, which matters inside a
    240-second budget.
    """

    entry: LedgerEntry
    ledger: Ledger = field(repr=False)
    cluster_id: str = ""
    path_classes: Tuple[str, ...] = ()
    _doc: Optional[dom.Document] = field(default=None, repr=False)
    _rendered: Optional[dom.Document] = field(default=None, repr=False)
    _sd: Optional[sdextract.SDExtraction] = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return self.entry.url

    @property
    def url_hash(self) -> str:
        return self.entry.entry_id

    @property
    def normal(self) -> Optional[urlnorm.NormalUrl]:
        return urlnorm.try_normalise(self.entry.final_url or self.entry.url)

    @property
    def path(self) -> str:
        n = self.normal
        return n.path if n else "/"

    @property
    def depth(self) -> int:
        n = self.normal
        return urlnorm.depth(n) if n else 0

    @property
    def is_utility(self) -> bool:
        """Rule R4: content checks stay silent on utility surfaces by default."""
        n = self.normal
        return urlnorm.is_utility(n) if n else False

    @property
    def doc(self) -> dom.Document:
        if self._doc is None:
            self._doc = dom.Document(self.ledger.text(self.entry), url=self.entry.url,
                                     entry=self.entry)
        return self._doc

    @property
    def rendered_doc(self) -> Optional[dom.Document]:
        """The post-JavaScript DOM, when this page was rendered. ``None`` otherwise."""
        if self._rendered is None:
            html = self.ledger.rendered_text(self.entry)
            if html is None:
                return None
            self._rendered = dom.Document(html, url=self.entry.url, entry=self.entry)
        return self._rendered

    @property
    def sd(self) -> sdextract.SDExtraction:
        if self._sd is None:
            self._sd = sdextract.extract(self.doc, base_url=self.entry.final_url)
        return self._sd

    @property
    def truncated(self) -> bool:
        """Guard G7: a body that never fully arrived must not be judged as thin."""
        return bool(self.entry.truncated)

    def has_path_class(self, *names: str) -> bool:
        return bool(set(names) & set(self.path_classes))


@dataclass
class Context:
    """Everything a specialist reads. Read-only by construction."""

    ledger: Ledger
    config: Dict[str, Any]
    site_profile: profile_mod.SiteProfile
    pages: List[PageView]
    clusters: template_mod.ClusterSet
    root: Path
    args: argparse.Namespace
    computed_profile: bool = False

    # -- threshold access --------------------------------------------------------------

    def threshold(self, dotted: str, default: Any = None) -> Any:
        value = bootstrap.threshold(self.config, dotted, None)
        if value is None:
            if default is None:
                raise KeyError(
                    f"threshold {dotted!r} is not defined in config/thresholds.json. "
                    f"Every tunable must be declared there with a rationale; detectors "
                    f"may not carry bare numeric literals."
                )
            return default
        return value

    def rationale(self, dotted: str) -> str:
        return bootstrap.rationale(self.config, dotted)

    # -- page selection ----------------------------------------------------------------

    def content_pages(self, include_utility: bool = False) -> List[PageView]:
        """Pages the content checks apply to (rule R4)."""
        out = [p for p in self.pages if p.entry.ok and p.entry.is_html]
        if not include_utility:
            out = [p for p in out if not p.is_utility]
        return out

    def pages_of_class(self, *names: str) -> List[PageView]:
        return [p for p in self.content_pages() if p.has_path_class(*names)]

    def homepage(self) -> Optional[PageView]:
        for page in self.pages:
            if page.path in ("/", "") and page.entry.ok:
                return page
        return self.pages[0] if self.pages else None

    def cluster_of(self, page: PageView) -> Optional[template_mod.Cluster]:
        return self.clusters.by_id(page.cluster_id) if page.cluster_id else None

    def entry_for_purpose(self, purpose: str) -> Optional[LedgerEntry]:
        entries = self.ledger.entries(purpose=purpose)
        return entries[0] if entries else None

    def entries_for_purpose(self, purpose: str) -> List[LedgerEntry]:
        return self.ledger.entries(purpose=purpose)

    def allows(self, check_id: str) -> bool:
        """False when the detected profile scopes this check out."""
        return self.site_profile.allows(check_id)

    def near_miss_margin(self) -> float:
        return float(self.threshold("false_positive_control.near_miss_margin", 0.2))

    def min_sample(self) -> int:
        return int(self.threshold("false_positive_control.min_sample_pages", 3))

    def site_url(self) -> str:
        page = self.homepage()
        if page is not None:
            n = page.normal
            if n:
                return n.origin
        meta = self.ledger.meta
        return str(meta.get("site_origin", ""))

    def site_host(self) -> str:
        origin = self.site_url()
        n = urlnorm.try_normalise(origin) if origin else None
        return n.host if n else str(self.ledger.meta.get("site_host", ""))


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def build_parser(skill_id: str, description: str) -> argparse.ArgumentParser:
    """The CLI contract shared by every specialist.

    Exit codes are disciplined: **findings are never a non-zero exit.** Only a broken
    audit is. A specialist that skipped every check because a dependency was missing
    still exits 0, having said so in ``checks_skipped``.
    """
    parser = argparse.ArgumentParser(
        prog=f"analyze ({skill_id})",
        description=description,
        epilog="Read-only. This skill performs no network I/O; it reads the evidence "
               "ledger only. Exit 0 = ran (findings are not failures); 2 = malformed "
               "ledger.",
    )
    parser.add_argument("--ledger", required=True, type=Path,
                        help="path to the evidence ledger directory")
    parser.add_argument("--out", required=True, type=Path,
                        help="path to write the findings JSON file")
    parser.add_argument("--profile", type=Path, default=None,
                        help="optional site-profile JSON from the orchestrator. When "
                             "omitted, the profile and template clusters are recomputed "
                             "from the ledger so the skill works standalone.")
    parser.add_argument("--config", type=Path, default=None,
                        help="path to config/thresholds.json (default: the "
                             "marketplace's own)")
    parser.add_argument("--emit-requests", type=Path, default=None,
                        help="two-pass off-site protocol, pass 1: write the off-site "
                             "requests this skill wants to a JSON file and stop. The "
                             "orchestrator validates and fetches them through the single "
                             "egress gateway, then re-runs the skill over the extended "
                             "ledger. Only entity-trust-audit declares requests.")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the human-readable summary on stderr")
    return parser


def build_context(args: argparse.Namespace) -> Context:
    """Open the ledger read-only and assemble the shared derived views."""
    root = bootstrap.setup()
    config = bootstrap.load_config(args.config)

    try:
        ledger = Ledger.open(args.ledger)
    except Exception as exc:
        raise MalformedLedger(f"cannot open ledger at {args.ledger}: {exc}") from exc

    entries = [e for e in ledger.pages(ok_only=True)]
    computed = False

    clusters = template_mod.ClusterSet(
        jaccard_threshold=float(bootstrap.threshold(config, "template.jaccard_threshold", 0.85)),
        shingle_k=int(bootstrap.threshold(config, "template.shingle_k", 5)),
        depth=int(bootstrap.threshold(config, "template.skeleton_depth", 6)),
    )

    profile_data: Dict[str, Any] = {}
    if args.profile and Path(args.profile).exists():
        with Path(args.profile).open("r", encoding="utf-8") as fh:
            profile_data = json.load(fh)

    pages: List[PageView] = []
    cluster_map: Dict[str, str] = {}
    for page_info in profile_data.get("pages") or []:
        if isinstance(page_info, dict) and page_info.get("url"):
            cluster_map[page_info["url"]] = page_info.get("template_id", "")

    # Rebuild the cluster objects from the orchestrator's assignment when available, so
    # every skill reports the same template ids as the merged report.
    if profile_data.get("template_clusters"):
        for entry_data in profile_data["template_clusters"]:
            cluster = template_mod.Cluster(
                id=entry_data.get("id", ""),
                fingerprint=entry_data.get("fingerprint", ""),
                urls=list(entry_data.get("urls", [])),
                path_classes=dict(entry_data.get("path_classes", {})),
            )
            clusters.clusters.append(cluster)
            for url in cluster.urls:
                clusters._by_url[url] = cluster.id

    for entry in entries:
        normal = urlnorm.try_normalise(entry.final_url or entry.url)
        path_classes = urlnorm.path_classes(normal) if normal else ()
        view = PageView(entry=entry, ledger=ledger, path_classes=tuple(path_classes))
        if entry.url in cluster_map:
            view.cluster_id = cluster_map[entry.url]
        elif clusters.cluster_id_for(entry.url):
            view.cluster_id = clusters.cluster_id_for(entry.url)
        else:
            computed = True
            cluster = clusters.add(entry.url, view.doc, path_classes)
            view.cluster_id = cluster.id
        pages.append(view)

    # Per-cluster medians are the baseline that "thin", "truncated" and
    # "boilerplate-dominant" judgements are measured against. A page is thin relative to
    # its own template, which is the only comparison that generalizes across sites.
    for view in pages:
        cluster = clusters.by_id(view.cluster_id)
        if cluster is None:
            continue
        try:
            cluster.record_metric("main_words", textnorm.word_count(view.doc.main_text()))
            cluster.record_metric("body_words", textnorm.word_count(view.doc.body_text()))
            cluster.record_metric("html_bytes", view.doc.html_bytes)
        except Exception:
            continue

    if profile_data.get("detected_type"):
        site_profile = profile_mod.SiteProfile.from_dict(profile_data)
    else:
        computed = True
        site_profile = _recompute_profile(pages, config)

    return Context(
        ledger=ledger, config=config, site_profile=site_profile, pages=pages,
        clusters=clusters, root=root, args=args, computed_profile=computed,
    )


def _recompute_profile(pages: Sequence[PageView],
                       config: Dict[str, Any]) -> profile_mod.SiteProfile:
    """Classify from the ledger, for standalone runs with no orchestrator profile."""
    evidence = []
    for page in pages:
        try:
            evidence.append(profile_mod.page_evidence(
                page.doc, page.url, page.path_classes,
                page.sd.types_present(), page.cluster_id,
            ))
        except Exception:
            continue
    return profile_mod.classify(
        evidence,
        ambiguity_margin=float(bootstrap.threshold(config, "profile.ambiguity_margin",
                                                   0.15)),
    )


class MalformedLedger(RuntimeError):
    """Exit code 2: the ledger could not be read as a ledger."""


def run(skill_id: str, description: str,
        detector: Callable[[Context, FindingsFile], None],
        argv: Optional[Sequence[str]] = None) -> int:
    """Standard ``main()`` for a specialist.

    Wraps the detector so that a crash in one skill cannot take down the audit: the
    orchestrator records the failure in ``coverage.skills_failed`` and the report says
    plainly which checks did not run. Partial-but-honest beats complete-but-fabricated,
    and it protects the time budget.
    """
    parser = build_parser(skill_id, description)
    args = parser.parse_args(argv)

    findings = FindingsFile(skill_id)
    try:
        context = build_context(args)
    except MalformedLedger as exc:
        print(f"{skill_id}: {exc}", file=sys.stderr)
        return EXIT_MALFORMED_LEDGER
    except Exception as exc:
        print(f"{skill_id}: could not build analysis context: {exc}", file=sys.stderr)
        return EXIT_MALFORMED_LEDGER

    if not dom.HAVE_BS4:
        # A missing optional dependency is not an audit failure. Every check this skill
        # owns is recorded as skipped, with the remedy, and the exit code stays 0.
        from . import checks as checks_mod
        for check_id in checks_mod.for_skill(skill_id):
            findings.skip(check_id, reason="beautifulsoup4 is not installed, so no DOM "
                                           "analysis could run",
                          remedy="pip install -r requirements.txt")
        findings.write(args.out)
        print(f"{skill_id}: skipped all checks (beautifulsoup4 missing)", file=sys.stderr)
        return EXIT_OK

    try:
        detector(context, findings)
    except Exception:
        # Record the failure inside the findings file so the orchestrator can surface it
        # in coverage rather than silently losing a whole gate.
        detail = traceback.format_exc(limit=4)
        findings.note_metric("analyzer_error", detail.strip().splitlines()[-1])
        from . import checks as checks_mod
        emitted = {f.check_id for f in findings.findings}
        for check_id in checks_mod.for_skill(skill_id):
            if check_id not in emitted:
                findings.skip(check_id,
                              reason=f"the analyzer stopped early: "
                                     f"{detail.strip().splitlines()[-1]}",
                              remedy="re-run this skill standalone against the ledger "
                                     "to reproduce")
        findings.write(args.out)
        print(f"{skill_id}: analyzer error, partial findings written\n{detail}",
              file=sys.stderr)
        return EXIT_OK

    findings.write(args.out)
    if not args.quiet:
        print(findings.summary_line(), file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# Shared detector helpers
# --------------------------------------------------------------------------------------

def group_by_template(context: Context, hits: Sequence[Tuple[str, Any]],
                      min_sample: Optional[int] = None,
                      total: Optional[int] = None) -> List[Dict[str, Any]]:
    """Collapse per-page hits to template scope (rule R3), then to site scope.

    When one defect spans several template clusters and together covers the site-wide
    share of sampled pages, it is one problem in a shared layout -- a missing viewport
    tag, say -- and is reported once, not once per template.
    """
    groups = template_mod.collapse(
        hits, context.clusters,
        min_sample=min_sample if min_sample is not None else context.min_sample(),
    )
    pool = total if total is not None else len(context.content_pages())
    share = float(context.threshold("false_positive_control.site_wide_share", 0.6))
    urls = sorted({url for url, _ in hits})
    if len(groups) > 1 and pool and len(urls) >= context.min_sample() \
            and len(urls) / pool >= share:
        ordered = sorted(hits, key=lambda h: h[0])
        return [{
            "level": "site", "template_id": "", "pages_affected": len(urls),
            "pages_sampled": pool, "example_urls": urls[:3], "path_class": "",
            "payloads": [payload for _url, payload in ordered], "urls": urls,
            "template_bound": True,
        }]
    # Below the site-wide share, one defect across several groups is still one finding: a
    # reader given two entries with identical titles cannot tell whether they are two
    # problems or one. The finding keeps every affected URL and the honest denominator,
    # and is template-scoped when any template carried the defect on most of its pages.
    if len(groups) > 1:
        ordered = sorted(hits, key=lambda h: h[0])
        classes = {g["path_class"] for g in groups}
        bound = any(g["template_bound"] for g in groups)
        return [{
            "level": "template" if bound else "section", "template_id": "",
            "pages_affected": len(urls), "pages_sampled": max(pool, len(urls)),
            "example_urls": urls[:3], "path_class": classes.pop() if len(classes) == 1 else "",
            "payloads": [payload for _url, payload in ordered], "urls": urls,
            "template_bound": bound,
        }]
    return groups


def scope_from_group(group: Dict[str, Any], all_pages: int,
                     is_policy_file: bool = False):
    """Build a :class:`~auditkit.finding.Scope` from a collapsed group."""
    from .finding import Scope
    from .severity import scope_level_for

    level = scope_level_for(
        pages_affected=group["pages_affected"],
        pages_sampled=all_pages if all_pages else group["pages_sampled"],
        is_policy_file=is_policy_file,
        template_bound=group.get("template_bound", False),
    )
    return Scope(
        level=level,
        template_id=group.get("template_id", ""),
        pages_affected=group["pages_affected"],
        pages_sampled=group["pages_sampled"],
        example_urls=list(group.get("example_urls", [])),
        path_class=group.get("path_class", ""),
    )


def kn(affected: int, sampled: int) -> str:
    return f"{affected}/{sampled}"


def summarise_urls(urls: Sequence[str], limit: int = 3) -> str:
    shown = sorted(urls)[:limit]
    text = ", ".join(shown)
    if len(urls) > limit:
        text += f" (and {len(urls) - limit} more)"
    return text


def text_contains_any(haystack: str, needles: Iterable[str]) -> List[str]:
    lowered = haystack.lower()
    return [n for n in needles if n in lowered]


def first_n_paragraph_text(page: PageView, count: int) -> str:
    paragraphs = page.doc.paragraphs(min_words=4)
    return textnorm.normalise_text(" ".join(paragraphs[:count]))


def page_for(context: Context, url: str) -> Optional[PageView]:
    return next((p for p in context.pages if p.url == url), None)


def prefix(group: Dict[str, Any]) -> str:
    """``3/12 sampled pages in template cluster T2`` -- the k/n lead of an evidence string."""
    return (f"{kn(group['pages_affected'], group['pages_sampled'])} sampled pages"
            + (f" in template cluster {group['template_id']}"
               if group.get("template_id") else ""))


def emit_group(context: Context, out: FindingsFile, check_id: str,
               group: Dict[str, Any], *, title: str, evidence: str, mechanism: str,
               action: SuggestedAction, refs: List[Any], confidence: str = "high",
               signal_count: int = 2, fact_class: str = "",
               total: Optional[int] = None, notes: Optional[List[str]] = None) -> None:
    """Emit one template-collapsed finding."""
    total_pages = total if total is not None else len(context.content_pages())
    out.add(Finding(
        check_id=check_id, title=title, evidence=evidence, mechanism=mechanism,
        suggested_action=action, confidence=confidence, signal_count=signal_count,
        fact_class=fact_class, scope=scope_from_group(group, total_pages),
        evidence_refs=refs, notes=notes or [],
    ))


@dataclass
class PageCheck:
    """A declarative per-page check: a detector plus the finding's fixed wording.

    ``detect(page)`` returns ``None`` when the page is fine, or a payload dict::

        {"observed": "<clause describing what was measured on this page>",
         "refs": [EvidenceRef, ...],          # built with page.url_hash
         "confidence": "medium",              # optional override
         "fact_class": "primary"}             # optional override

    The runner applies profile scoping, collapses hits to template scope (rule R3), and
    builds the finding. Keeping wording, mechanism and fix beside the detector is what
    lets a reader audit one check in one place.
    """

    check_id: str
    title: str
    mechanism: str
    fix: str
    steps: Sequence[str]
    verification: str
    detect: Callable[[PageView], Optional[Dict[str, Any]]]
    effort: str = "low"
    owner: str = ""
    expected_effect: str = ""
    confidence: str = "high"
    signal_count: int = 2
    fact_class: str = ""
    applies: Callable[[PageView], bool] = field(default=lambda page: True)
    template_only: bool = False
    min_pages: int = 1


def run_page_checks(context: Context, out: FindingsFile, checks: Sequence[PageCheck],
                    pages: Optional[Sequence[PageView]] = None) -> None:
    """Run declarative per-page checks and emit template-collapsed findings."""
    pool = list(pages) if pages is not None else context.content_pages()
    for check in checks:
        if not context.allows(check.check_id):
            out.skip(check.check_id,
                     reason=f"not applicable to the detected "
                            f"{context.site_profile.detected_type} site profile")
            continue
        applicable = [p for p in pool if check.applies(p)]
        hits: List[Tuple[str, Any]] = []
        failure = ""
        for page in applicable:
            try:
                payload = check.detect(page)
            except Exception as exc:  # one bad page must not sink the check
                failure = f"{type(exc).__name__}: {exc}"
                continue
            if payload:
                hits.append((page.url, payload))
        if failure and not hits:
            out.skip(check.check_id, reason=f"detector error on at least one page: "
                                            f"{failure}")
            continue
        if len({u for u, _ in hits}) < check.min_pages:
            continue
        for group in group_by_template(context, hits, total=len(applicable) or len(pool)):
            if check.template_only and not group["template_bound"] \
                    and group["pages_affected"] < context.min_sample():
                continue
            payload = group["payloads"][0]
            emit_group(
                context, out, check.check_id, group,
                title=check.title,
                evidence=(f"{prefix(group)}: {payload['observed']}. Affected: "
                          f"{summarise_urls(group['urls'])}."),
                mechanism=check.mechanism,
                action=SuggestedAction(
                    summary=check.fix, steps=list(check.steps), effort=check.effort,
                    owner_role=check.owner, verification=check.verification,
                    expected_effect=check.expected_effect,
                ),
                refs=list(payload["refs"]),
                confidence=payload.get("confidence", check.confidence),
                signal_count=payload.get("signal_count", check.signal_count),
                fact_class=payload.get("fact_class", check.fact_class),
                total=len(applicable) or len(pool),
            )
