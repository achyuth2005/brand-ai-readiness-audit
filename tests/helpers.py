"""Shared test helpers: fixture ledgers, in-process skill runs, and a network blocker.

Fixtures are real evidence ledgers built from inline HTML, so the analyzers under test run
exactly the code path they run in production. There is no separate test mode that could
drift from the real one.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "lib", ROOT / "skills" / "ai-readiness-audit" / "scripts", ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from auditkit.ledger import Ledger, LedgerEntry  # noqa: E402

SITE = "https://site.test"
HOST = "site.test"
AUDITED_AT = "2026-09-13T10:00:00Z"
SPECIALISTS: Tuple[str, ...] = (
    "access-policy-audit", "render-extraction-audit", "structured-data-audit",
    "entity-trust-audit", "answerability-audit", "engagement-audit",
)


def build_ledger(directory: Path, specs: Iterable[Dict[str, Any]],
                 meta: Optional[Dict[str, Any]] = None) -> Path:
    """Create an evidence ledger from page specs.

    Spec keys: ``path`` (required), ``body``, ``status`` (default 200, ``None`` for no
    response), ``purpose`` (default ``page``), ``ctype``, ``headers``, ``depth``,
    ``referrer``, ``probe_agent``, ``key_suffix``, ``reason``, ``outcome``, ``url``.
    """
    directory = Path(directory)
    ledger = Ledger.create(directory, meta={
        "site_origin": SITE, "site_host": HOST, "audited_at": AUDITED_AT, **(meta or {}),
    })
    for spec in specs:
        url = spec.get("url") or (SITE + spec["path"])
        status = spec.get("status", 200)
        headers = {"content-type": spec.get("ctype", "text/html; charset=utf-8")}
        headers.update({k.lower(): v for k, v in (spec.get("headers") or {}).items()})
        if status is None:
            outcome = spec.get("outcome", "network_error")
        elif status >= 400:
            outcome = "http_error"
        else:
            outcome = "ok"
        body = spec.get("body")
        entry = LedgerEntry(
            url=url + spec.get("key_suffix", ""),
            final_url=url,
            status=status,
            outcome=outcome,
            reason=spec.get("reason", "" if outcome == "ok" else f"HTTP {status}"),
            headers=headers,
            encoding="utf-8",
            declared_encoding="utf-8",
            ttfb_ms=spec.get("ttfb_ms", 100.0),
            elapsed_ms=150.0,
            purpose=spec.get("purpose", "page"),
            depth=spec.get("depth"),
            referrer=spec.get("referrer", ""),
            probe_agent=spec.get("probe_agent", ""),
            truncated=spec.get("truncated", False),
        )
        ledger.put(entry, body.encode("utf-8") if isinstance(body, str) else body)
    return directory


@contextlib.contextmanager
def no_network():
    """Make any socket creation or DNS lookup fail loudly for the duration."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("network access attempted during analysis")

    saved = (socket.socket, socket.create_connection, socket.getaddrinfo)
    socket.socket = refuse  # type: ignore[assignment]
    socket.create_connection = refuse  # type: ignore[assignment]
    socket.getaddrinfo = refuse  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket, socket.create_connection, socket.getaddrinfo = saved


_MODULES: Dict[str, Any] = {}


def skill_module(skill: str) -> Any:
    """Import a specialist's analyzer script as a module (their file names differ)."""
    if skill not in _MODULES:
        script = next((ROOT / "skills" / skill / "scripts").glob("analyze_*.py"))
        name = "skill_" + skill.replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MODULES[skill] = module
    return _MODULES[skill]


def run_skill(skill: str, ledger_dir: Path, workdir: Path,
              extra: Sequence[str] = ()) -> Dict[str, Any]:
    """Run one specialist in-process with the network blocked; return its findings file."""
    out = Path(workdir) / f"findings.{skill}.json"
    module = skill_module(skill)
    with no_network(), contextlib.redirect_stderr(io.StringIO()):
        code = module.main(["--ledger", str(ledger_dir), "--out", str(out), "--quiet",
                            *extra])
    data = json.loads(out.read_text(encoding="utf-8"))
    data["_exit"] = code
    return data


def run_all_skills(ledger_dir: Path, workdir: Path) -> Dict[str, Dict[str, Any]]:
    return {skill: run_skill(skill, ledger_dir, workdir) for skill in SPECIALISTS}


def check_ids(data: Dict[str, Any], kind: str = "findings") -> set:
    return {item["check_id"] for item in data.get(kind, [])}


def run_offline_audit(ledger_dir: Path, out_dir: Path) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Run the entrypoint over an existing ledger; return (exit code, report or None)."""
    import run_audit  # imported lazily: it pulls in the whole entrypoint
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = run_audit.main(["--ledger", str(ledger_dir), "--out", str(out_dir),
                               "--now", AUDITED_AT, "--quiet"])
    report_path = Path(out_dir) / "audit.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    return code, report


def all_ids(results: Dict[str, Dict[str, Any]], kind: str = "findings") -> set:
    return {cid for data in results.values() for cid in check_ids(data, kind)}


def analyzer_errors(results: Dict[str, Dict[str, Any]]) -> List[str]:
    return [f"{skill}: {data['metrics']['analyzer_error']}"
            for skill, data in results.items()
            if data.get("metrics", {}).get("analyzer_error")]
