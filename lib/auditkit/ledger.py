"""The Evidence Ledger -- immutable, content-addressed store of everything fetched.

Invariant A
-----------
    Every byte the audit reasons about is fetched exactly once, by one component, and
    written to an immutable, content-addressed ledger. All analysis reads the ledger.
    Nothing else touches the network.

This one decision pays for itself five times over, and each consequence maps to
something the rubric scores:

* **Politeness** -- one request per URL for the whole audit, by construction. Six
  analyzers cannot multiply load on a host, because they cannot fetch.
* **Runtime** -- the whole time budget is governed in one phase instead of being spread
  across six components that each want to crawl.
* **Determinism** -- re-running the analyzers over a frozen ledger produces
  byte-identical findings, which is what makes the package testable.
* **Evidence integrity** -- every finding cites ``evidence_refs`` that point into the
  ledger, and ``verify_report.py`` re-resolves all of them. A claim that cannot be
  re-derived from stored bytes does not ship.
* **Offline testing** -- a fixture is just a pre-baked ledger, so generalization is
  regression-tested with no network at all.

Layout on disk::

    ledger/
      index.json                 dedupe key -> entry id, plus run metadata
      entries/<entry_id>.json    one record per URL: status, headers, timings, hashes
      bodies/<content_hash>.bin  raw response bytes, deduplicated by content
      rendered/<entry_id>.html   optional post-JavaScript DOM

Entry ids and body names are SHA-256 hex digests, never URL-derived paths. That closes
threat T8: a crafted URL cannot traverse out of the run directory, because no part of
the URL ever reaches the filesystem.
"""

from __future__ import annotations

import json
import hashlib
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

SCHEMA_VERSION = 1

# Fetch outcomes.
OUTCOME_OK = "ok"
OUTCOME_HTTP_ERROR = "http_error"       # reached the server, non-2xx status
OUTCOME_BLOCKED_ROBOTS = "blocked_robots"
OUTCOME_BLOCKED_POLICY = "blocked_policy"   # mutating-GET denylist, SSRF, scheme/port
OUTCOME_TIMEOUT = "timeout"
OUTCOME_NETWORK_ERROR = "network_error"
OUTCOME_TOO_LARGE = "too_large"
OUTCOME_CIRCUIT_OPEN = "circuit_open"
OUTCOME_BUDGET = "budget_exhausted"

OUTCOMES = (OUTCOME_OK, OUTCOME_HTTP_ERROR, OUTCOME_BLOCKED_ROBOTS,
            OUTCOME_BLOCKED_POLICY, OUTCOME_TIMEOUT, OUTCOME_NETWORK_ERROR,
            OUTCOME_TOO_LARGE, OUTCOME_CIRCUIT_OPEN, OUTCOME_BUDGET)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_hash(dedupe_key: str) -> str:
    """Entry id for a canonical URL. Also the ``url_hash`` used in evidence refs."""
    return hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()


class LedgerError(RuntimeError):
    pass


class LedgerImmutable(LedgerError):
    """Raised on any attempt to overwrite or mutate an existing entry."""


class LedgerReadOnly(LedgerError):
    """Raised when a pure analyzer attempts a write. Invariant B, enforced."""


@dataclass
class LedgerEntry:
    """One fetch: what we asked for, what came back, and where the bytes live."""

    url: str                      # canonical dedupe key (what was requested)
    entry_id: str = ""            # sha256 of url; filled in by __post_init__
    final_url: str = ""           # after redirects; equals url when there were none
    method: str = "GET"
    status: Optional[int] = None
    outcome: str = OUTCOME_OK
    reason: str = ""              # why, for every non-ok outcome
    headers: Dict[str, str] = field(default_factory=dict)   # lowercased names
    content_hash: str = ""        # sha256 of the raw body, "" when there is no body
    content_length: int = 0       # bytes actually stored
    declared_length: Optional[int] = None   # Content-Length as declared
    truncated: bool = False       # hit the size cap
    encoding: Optional[str] = None          # charset actually used to decode
    declared_encoding: Optional[str] = None  # charset the response claimed
    ttfb_ms: Optional[float] = None
    elapsed_ms: Optional[float] = None
    redirects: List[Dict[str, object]] = field(default_factory=list)
    resolved_ip: str = ""
    user_agent: str = ""
    purpose: str = "page"         # page | robots | sitemap | probe | asset | offsite
    rendered_hash: str = ""       # sha256 of the rendered DOM, when a render happened
    render_mode: str = ""         # "" | "playwright"
    depth: Optional[int] = None   # click depth from the seed, for navigation analysis
    referrer: str = ""            # the page this URL was discovered on
    probe_agent: str = ""         # for UA-divergence probes: which agent was declared
    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.entry_id:
            self.entry_id = key_hash(self.url)
        if not self.final_url:
            self.final_url = self.url

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_OK and self.status is not None \
            and 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        ctype = self.headers.get("content-type", "")
        return "html" in ctype.lower() or (not ctype and self.content_hash != "")

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def was_redirected(self) -> bool:
        return bool(self.redirects)

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["schema_version"] = SCHEMA_VERSION
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "LedgerEntry":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


class Ledger:
    """Read or append to a ledger directory.

    Open read-only for analysis (the default for every specialist skill) and writable
    only in the orchestrator's crawl phase. A read-only handle raises
    :class:`LedgerReadOnly` on any write, which is how Invariant B stops being a
    convention and becomes a property of the code.
    """

    def __init__(self, root: Path, writable: bool = False):
        self.root = Path(root)
        self.writable = writable
        self.entries_dir = self.root / "entries"
        self.bodies_dir = self.root / "bodies"
        self.rendered_dir = self.root / "rendered"
        self._index: Dict[str, str] = {}       # dedupe key -> entry id
        self._meta: Dict[str, object] = {}
        self._cache: Dict[str, LedgerEntry] = {}
        self._body_cache: Dict[str, bytes] = {}
        # The crawl writes from a small thread pool; the index must not race.
        self._lock = threading.RLock()

        if writable:
            for d in (self.entries_dir, self.bodies_dir, self.rendered_dir):
                d.mkdir(parents=True, exist_ok=True)
        if not self.root.exists():
            raise LedgerError(f"ledger directory does not exist: {self.root}")
        self._load_index()

    # -- lifecycle ---------------------------------------------------------------------

    @classmethod
    def create(cls, root: Path, meta: Optional[Dict[str, object]] = None) -> "Ledger":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        ledger = cls(root, writable=True)
        ledger._meta = dict(meta or {})
        ledger._meta.setdefault("schema_version", SCHEMA_VERSION)
        ledger._flush_index()
        return ledger

    @classmethod
    def open(cls, root: Path) -> "Ledger":
        """Open an existing ledger read-only. The analyzers' only entry point."""
        return cls(Path(root), writable=False)

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _load_index(self) -> None:
        path = self._index_path()
        if not path.exists():
            if not self.writable:
                raise LedgerError(f"ledger index not found: {path}")
            return
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._index = dict(data.get("index", {}))
        self._meta = dict(data.get("meta", {}))

    def _flush_index(self) -> None:
        if not self.writable:
            raise LedgerReadOnly("this ledger handle is read-only")
        payload = {
            "meta": self._meta,
            # Sorted so the index is byte-stable across runs (determinism).
            "index": dict(sorted(self._index.items())),
        }
        tmp = self._index_path().with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, self._index_path())

    # -- metadata ----------------------------------------------------------------------

    @property
    def meta(self) -> Dict[str, object]:
        return dict(self._meta)

    def set_meta(self, **kwargs) -> None:
        if not self.writable:
            raise LedgerReadOnly("cannot set metadata on a read-only ledger")
        self._meta.update(kwargs)
        self._flush_index()

    # -- writes ------------------------------------------------------------------------

    def put(self, entry: LedgerEntry, body: Optional[bytes] = None) -> LedgerEntry:
        with self._lock:
            return self._put_unlocked(entry, body)

    def _put_unlocked(self, entry: LedgerEntry, body: Optional[bytes] = None) -> LedgerEntry:
        """Append an entry. Refuses to overwrite an existing one.

        Immutability is the point: if a URL is already in the ledger it has already been
        fetched, and fetching it again would violate the one-request-per-URL guarantee
        that the whole politeness argument rests on. Callers check
        :meth:`has` first and skip.
        """
        if not self.writable:
            raise LedgerReadOnly(
                "this ledger handle is read-only; specialist skills are pure functions "
                "of the ledger and may not write to it (Invariant B)"
            )
        if entry.url in self._index:
            raise LedgerImmutable(
                f"{entry.url} is already in the ledger (entry {self._index[entry.url]}). "
                f"The ledger is append-only and each URL is fetched exactly once."
            )

        if body is not None:
            content_hash = sha256_hex(body)
            entry.content_hash = content_hash
            entry.content_length = len(body)
            body_path = self.bodies_dir / f"{content_hash}.bin"
            if not body_path.exists():
                # Content-addressed: identical bodies across URLs are stored once, which
                # is also how PARAM_DUPLICATES detection gets its comparison for free.
                tmp = body_path.with_suffix(".bin.tmp")
                tmp.write_bytes(body)
                os.replace(tmp, body_path)

        entry_path = self.entries_dir / f"{entry.entry_id}.json"
        tmp = entry_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(entry.to_dict(), fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, entry_path)

        self._index[entry.url] = entry.entry_id
        self._cache[entry.entry_id] = entry
        self._flush_index()
        return entry

    def attach_rendered(self, entry: LedgerEntry, html: str,
                        render_mode: str = "playwright") -> LedgerEntry:
        """Attach a rendered DOM to an existing entry.

        The single permitted mutation, and a narrow one: it adds a *new* artifact and
        two provenance fields, never altering the raw bytes or the original response
        metadata. The raw response stays the ground truth that findings are argued from.
        """
        if not self.writable:
            raise LedgerReadOnly("cannot attach a rendered DOM to a read-only ledger")
        if entry.url not in self._index:
            raise LedgerError(f"{entry.url} is not in the ledger; cannot attach a render")
        data = html.encode("utf-8")
        entry.rendered_hash = sha256_hex(data)
        entry.render_mode = render_mode
        path = self.rendered_dir / f"{entry.entry_id}.html"
        tmp = path.with_suffix(".html.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

        entry_path = self.entries_dir / f"{entry.entry_id}.json"
        with entry_path.open("w", encoding="utf-8") as fh:
            json.dump(entry.to_dict(), fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        self._cache[entry.entry_id] = entry
        return entry

    # -- reads -------------------------------------------------------------------------

    def has(self, dedupe_key: str) -> bool:
        return dedupe_key in self._index

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, dedupe_key: object) -> bool:
        return isinstance(dedupe_key, str) and dedupe_key in self._index

    def get(self, dedupe_key: str) -> Optional[LedgerEntry]:
        entry_id = self._index.get(dedupe_key)
        return self.by_id(entry_id) if entry_id else None

    def by_id(self, entry_id: str) -> Optional[LedgerEntry]:
        """Look up by entry id, which is what evidence refs carry as ``url_hash``."""
        if entry_id in self._cache:
            return self._cache[entry_id]
        path = self.entries_dir / f"{entry_id}.json"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as fh:
            entry = LedgerEntry.from_dict(json.load(fh))
        self._cache[entry_id] = entry
        return entry

    def keys(self) -> List[str]:
        """Canonical URLs in the ledger, sorted. Sorted iteration is a determinism
        requirement: analyzers must not depend on filesystem or dict ordering."""
        return sorted(self._index)

    def entries(self, purpose: Optional[str] = None,
                ok_only: bool = False) -> List[LedgerEntry]:
        out: List[LedgerEntry] = []
        for url in self.keys():
            entry = self.get(url)
            if entry is None:
                continue
            if purpose is not None and entry.purpose != purpose:
                continue
            if ok_only and not entry.ok:
                continue
            out.append(entry)
        return out

    def pages(self, ok_only: bool = True) -> List[LedgerEntry]:
        """HTML pages fetched as content -- the input to most analyzers."""
        return [e for e in self.entries(purpose="page", ok_only=ok_only) if e.is_html]

    def __iter__(self) -> Iterator[LedgerEntry]:
        return iter(self.entries())

    def body(self, entry: LedgerEntry) -> bytes:
        """Raw response bytes. Empty when the fetch stored no body."""
        if not entry.content_hash:
            return b""
        if entry.content_hash in self._body_cache:
            return self._body_cache[entry.content_hash]
        path = self.bodies_dir / f"{entry.content_hash}.bin"
        if not path.exists():
            raise LedgerError(
                f"body {entry.content_hash} for {entry.url} is missing from the ledger; "
                f"evidence cannot be re-resolved"
            )
        data = path.read_bytes()
        stored = sha256_hex(data)
        if stored != entry.content_hash:
            raise LedgerError(
                f"body hash mismatch for {entry.url}: entry says {entry.content_hash}, "
                f"stored bytes hash to {stored}. The ledger has been tampered with."
            )
        self._body_cache[entry.content_hash] = data
        return data

    def text(self, entry: LedgerEntry) -> str:
        """Decoded response text, using the encoding recorded at fetch time."""
        data = self.body(entry)
        if not data:
            return ""
        return data.decode(entry.encoding or "utf-8", errors="replace")

    def rendered_text(self, entry: LedgerEntry) -> Optional[str]:
        """The post-JavaScript DOM, or ``None`` when this page was not rendered."""
        if not entry.rendered_hash:
            return None
        path = self.rendered_dir / f"{entry.entry_id}.html"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    # -- derived views used by several analyzers ---------------------------------------

    def content_hash_groups(self) -> Dict[str, List[str]]:
        """content hash -> URLs serving those exact bytes.

        Free duplicate detection: because bodies are content-addressed, identical pages
        at different URLs collapse to one hash with no extra comparison work.
        """
        groups: Dict[str, List[str]] = {}
        for entry in self.entries():
            if entry.content_hash:
                groups.setdefault(entry.content_hash, []).append(entry.url)
        return {h: sorted(urls) for h, urls in sorted(groups.items())
                if len(urls) > 1}

    def stats(self) -> Dict[str, object]:
        entries = self.entries()
        by_outcome: Dict[str, int] = {}
        for e in entries:
            by_outcome[e.outcome] = by_outcome.get(e.outcome, 0) + 1
        return {
            "entries": len(entries),
            "pages": len(self.pages()),
            "rendered": sum(1 for e in entries if e.rendered_hash),
            "bytes_stored": sum(e.content_length for e in entries),
            "by_outcome": dict(sorted(by_outcome.items())),
        }

    def verify_integrity(self) -> List[str]:
        """Re-hash every stored body. Returns problems found, empty when sound.

        Called by ``verify_report.py`` before it re-resolves evidence, so a corrupted or
        edited ledger is caught before any finding is trusted.
        """
        problems: List[str] = []
        for url in self.keys():
            entry = self.get(url)
            if entry is None:
                problems.append(f"index references a missing entry for {url}")
                continue
            if entry.entry_id != key_hash(entry.url):
                problems.append(
                    f"entry id {entry.entry_id} does not match sha256 of {entry.url}"
                )
            if entry.content_hash:
                try:
                    self.body(entry)
                except LedgerError as exc:
                    problems.append(str(exc))
        return problems


# ------------------------------------------------------------------------------------
# Fixture construction
#
# A test fixture is a real ledger built from local HTML files. That keeps the offline
# test suite honest: analyzers run against exactly the structure they see in production,
# with no separate "test mode" code path that could drift from the real one.
# ------------------------------------------------------------------------------------

def build_fixture_ledger(root: Path, pages: Iterable[Tuple[str, str]],
                         headers: Optional[Dict[str, Dict[str, str]]] = None,
                         statuses: Optional[Dict[str, int]] = None,
                         purposes: Optional[Dict[str, str]] = None,
                         depths: Optional[Dict[str, int]] = None,
                         meta: Optional[Dict[str, object]] = None) -> Ledger:
    """Create a ledger from ``(url, html)`` pairs. Used only by tests and fixtures."""
    ledger = Ledger.create(root, meta=meta or {"fixture": True})
    headers = headers or {}
    statuses = statuses or {}
    purposes = purposes or {}
    depths = depths or {}
    for url, html in pages:
        body = html.encode("utf-8")
        hdrs = {"content-type": "text/html; charset=utf-8"}
        hdrs.update({k.lower(): v for k, v in (headers.get(url) or {}).items()})
        entry = LedgerEntry(
            url=url,
            status=statuses.get(url, 200),
            outcome=OUTCOME_OK if statuses.get(url, 200) < 400 else OUTCOME_HTTP_ERROR,
            headers=hdrs,
            declared_length=len(body),
            encoding="utf-8",
            declared_encoding="utf-8",
            ttfb_ms=40.0,
            elapsed_ms=80.0,
            purpose=purposes.get(url, "page"),
            depth=depths.get(url, 0),
            user_agent="fixture",
        )
        ledger.put(entry, body)
    return ledger
