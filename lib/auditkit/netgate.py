"""The egress gateway -- the only code in this marketplace that opens a socket.

Every network call in the entire package passes through :meth:`NetGate.fetch`. Nothing
else imports ``socket``, ``http.client``, ``urllib.request`` or an HTTP library, and
``tests/test_safety.py`` scans the source tree to enforce that. One choke point means
one place to implement SSRF defence, robots compliance, rate limiting, the method
allowlist and the mutating-GET denylist -- and one place for a reviewer to check them.

Guard order in :meth:`fetch`, each mapped to its threat:

    1. method in {GET, HEAD}                 T1  mutating the target
    2. scheme/port allowlist, no userinfo    T5  credentials, odd protocols
    3. mutating-GET denylist                 T2  state change via a bare GET
    4. robots.txt permits our own UA         compliance
    5. budget permits another request        T3  rate abuse / runtime
    6. resolve-then-connect, pinned IP       T4  SSRF, DNS rebinding
    7. per-host rate limit and breaker       T3
    8. streamed read with size + ratio caps  T10 resource exhaustion
    9. manual redirect, re-validated         T4  internal redirect, rebinding
   10. immutable ledger write                T8  path traversal (hash-keyed)

Why the standard library rather than an HTTP client package
-----------------------------------------------------------
The security property that matters most here is **resolve-then-connect with the
validated address pinned**. Handing a hostname back to a client library after
validating it re-opens the TOCTOU window that validation was meant to close.
``http.client`` lets the connection target the exact IP that passed validation while
the hostname is still used for TLS SNI and certificate verification, which is precisely
the behaviour required -- and it removes a hard dependency from the safety core, so the
guarantees hold on a machine with nothing installed.

Redirects are followed **manually** for the same reason: an automatic redirect handler
would skip SSRF re-validation on intermediate hops, leaving a public URL free to
redirect to ``169.254.169.254``.
"""

from __future__ import annotations

import gzip
import http.client
import os
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import ledger as ledger_mod
from . import ratelimit as ratelimit_mod
from . import robots as robots_mod
from . import ssrf, urlnorm
from .ledger import Ledger, LedgerEntry
from .urlnorm import NormalUrl, UnsafeUrl

ALLOWED_METHODS = frozenset({"GET", "HEAD"})

# Invariant B is enforced with an environment token. ``run_audit.py`` sets it before
# the crawl phase; specialist analyzers never set it, so a specialist that tried to
# construct a gateway fails loudly instead of quietly making requests.
ORCHESTRATOR_ENV_TOKEN = "AUDITKIT_ORCHESTRATOR"

VERSION = "1.0.0"
PRODUCT_TOKEN = "BrandAIReadinessAudit"
# How a site operator can reach whoever runs the audit: a URL or an email address,
# supplied with --contact or this environment variable, falling back to the marketplace
# maintainer's address so every request stays contactable.
CONTACT_ENV = "AUDIT_CONTACT"
DEFAULT_CONTACT = "achyuthanoop@gmail.com"

DEFAULT_MAX_BYTES = 5 * 1024 * 1024       # 5 MB, streamed and aborted beyond
DEFAULT_MAX_DECOMPRESS_RATIO = 50         # zip-bomb guard
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 10.0


class EgressDenied(RuntimeError):
    """A request was refused by a guard. Carries the ledger outcome to record."""

    def __init__(self, message: str, outcome: str):
        super().__init__(message)
        self.outcome = outcome


class NotOrchestrator(RuntimeError):
    """Raised when something other than the orchestrator builds a gateway."""


def honest_user_agent(probe_token: Optional[str] = None,
                      contact: Optional[str] = None) -> str:
    """Build this audit's user-agent string.

    Truthful and contactable, always. The product token names this tool, the URL lets a
    site operator find out what hit them, and nothing here imitates a browser or a
    third-party crawler.

    ``probe_token`` supports ``G0.GATING.BOT_STATUS_DIVERGENCE``. It appends a
    ``probe-profile`` note naming the crawler whose treatment is being diagnosed. Read
    the boundary carefully, because it is deliberate:

    * We still identify as ourselves. The string begins with our own product token.
    * We do **not** claim to be the named crawler, and we never send a bare third-party
      token to obtain access we were refused.
    * This therefore detects differentiation implemented as a *substring rule* on the
      user-agent, which is how most WAF and robots rules are written. It cannot detect
      differentiation based on IP allowlists or verified-crawler callbacks.

    That limitation is stated in the finding's evidence and caps its confidence, rather
    than being papered over by impersonation. See ``references/gating-patterns.md``.
    """
    contact = contact if contact is not None else os.environ.get(CONTACT_ENV, DEFAULT_CONTACT)
    base = (f"{PRODUCT_TOKEN}/{VERSION} (read-only site audit"
            + (f"; +{contact}" if contact else "") + ")")
    if probe_token:
        return f"{base} probe-profile={probe_token}"
    return base


def honest_headers(user_agent: str, host: str) -> Dict[str, str]:
    """Request headers. Notable for what is absent.

    No ``Cookie`` (no session state is ever accumulated), no ``Authorization`` (threat
    T5), no ``Referer`` (we do not fabricate provenance), and ``br`` is not advertised
    because Brotli has no standard-library decoder and advertising an encoding we cannot
    decode would waste the site's bandwidth.
    """
    return {
        "Host": host,
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "text/plain;q=0.8,*/*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en;q=0.9,*;q=0.5",
        "Connection": "close",
    }


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a pinned IP, with TLS still verified against the hostname.

    ``self.host`` stays the hostname so SNI and certificate verification are correct;
    only the TCP destination is overridden. This is what makes resolve-then-connect real
    rather than advisory.
    """

    def __init__(self, host: str, ip: str, port: int, *, context: ssl.SSLContext,
                 connect_timeout: float, read_timeout: float):
        super().__init__(host, port, context=context, timeout=connect_timeout)
        self._pinned_ip = ip
        self._read_timeout = read_timeout

    def connect(self) -> None:  # pragma: no cover - exercised in live runs
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock.settimeout(self._read_timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP to a pinned IP, with the Host header still naming the hostname."""

    def __init__(self, host: str, ip: str, port: int, *, connect_timeout: float,
                 read_timeout: float):
        super().__init__(host, port, timeout=connect_timeout)
        self._pinned_ip = ip
        self._read_timeout = read_timeout

    def connect(self) -> None:  # pragma: no cover - exercised in live runs
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock.settimeout(self._read_timeout)


@dataclass
class RawResponse:
    """One HTTP exchange, before redirect handling."""

    status: int
    headers: Dict[str, str]
    body: bytes
    truncated: bool
    ttfb_ms: float
    elapsed_ms: float
    resolved_ip: str


class NetGate:
    """The sole egress point. Constructed once, in the orchestrator's crawl phase."""

    def __init__(self, ledger: Ledger, budget, limiter: ratelimit_mod.RateLimiter,
                 robots_for: Callable[[str], robots_mod.RobotsFile],
                 user_agent: Optional[str] = None,
                 contact: Optional[str] = None,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 max_redirects: int = DEFAULT_MAX_REDIRECTS,
                 connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
                 read_timeout: float = DEFAULT_READ_TIMEOUT,
                 max_decompress_ratio: int = DEFAULT_MAX_DECOMPRESS_RATIO,
                 verify_tls: bool = True,
                 require_orchestrator: bool = True):
        if require_orchestrator and not os.environ.get(ORCHESTRATOR_ENV_TOKEN):
            raise NotOrchestrator(
                "NetGate may only be constructed by the audit orchestrator. Specialist "
                "skills are pure functions of the evidence ledger and perform no "
                "network I/O (Invariant B). If you are the orchestrator, set the "
                f"{ORCHESTRATOR_ENV_TOKEN} environment variable."
            )
        self.ledger = ledger
        self.budget = budget
        self.limiter = limiter
        self.robots_for = robots_for
        self.contact = contact
        self.user_agent = user_agent or honest_user_agent(contact=contact)
        self.max_bytes = int(max_bytes)
        self.max_redirects = int(max_redirects)
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.max_decompress_ratio = int(max_decompress_ratio)
        self.request_log: List[Dict[str, object]] = []

        self._tls = tls_context()
        if not verify_tls:
            # Only ever used by the offline test harness against a local server.
            self._tls.check_hostname = False
            self._tls.verify_mode = ssl.CERT_NONE

    # -- public API --------------------------------------------------------------------

    def fetch(self, url: str, *, method: str = "GET", purpose: str = "page",
              probe_agent: Optional[str] = None, depth: Optional[int] = None,
              referrer: str = "", allow_non_document: bool = False,
              record: bool = True, key_suffix: str = "") -> LedgerEntry:
        """Fetch ``url`` through every guard and record the result in the ledger.

        Always returns a :class:`LedgerEntry`. A refusal is an entry with a non-``ok``
        outcome and a stated reason, never an exception -- a blocked path is *evidence*
        about the site, so it belongs in the ledger alongside everything else.
        """
        method = method.upper()
        if method not in ALLOWED_METHODS:
            # Not a recoverable outcome: this is a programming error, and the audit
            # must never be one edit away from issuing a POST.
            raise EgressDenied(
                f"method {method!r} is not permitted; this audit is read-only and "
                f"issues only {sorted(ALLOWED_METHODS)}",
                ledger_mod.OUTCOME_BLOCKED_POLICY,
            )

        ua = honest_user_agent(probe_agent, self.contact) if probe_agent else self.user_agent

        # --- guard 2: shape, scheme, port, credentials -------------------------------
        try:
            u = urlnorm.normalise(url)
        except UnsafeUrl as exc:
            return self._record_refusal(url, method, purpose,
                                        ledger_mod.OUTCOME_BLOCKED_POLICY, str(exc),
                                        ua, probe_agent, depth, referrer, record)

        key = urlnorm.dedupe_key(u)

        # Invariant A: one fetch per URL, ever. A probe with a different declared agent
        # is a distinct observation, so it gets its own key rather than colliding.
        if probe_agent:
            key = f"{key}#probe={probe_agent}"
        elif key_suffix:
            key = f"{key}{key_suffix}"
        if self.ledger.has(key):
            existing = self.ledger.get(key)
            if existing is not None:
                return existing

        if not allow_non_document and purpose == "page" and not urlnorm.is_document(u):
            return self._record_refusal(
                key, method, purpose, ledger_mod.OUTCOME_BLOCKED_POLICY,
                f"{urlnorm.extension(u) or 'this URL'} is not an HTML document",
                ua, probe_agent, depth, referrer, record)

        # --- guard 3: mutating-GET denylist ------------------------------------------
        refuse, why = urlnorm.is_mutating_get(u)
        if refuse:
            return self._record_refusal(
                key, method, purpose, ledger_mod.OUTCOME_BLOCKED_POLICY,
                f"refused as a possible state-changing request: {why}",
                ua, probe_agent, depth, referrer, record)

        # --- guard 4: robots.txt, for our own fetching -------------------------------
        rules = self.robots_for(u.origin)
        verdict = rules.verdict_for(self.user_agent, u.path)
        if verdict.allowed and probe_agent:
            # robots.txt groups match by substring, and a probe's user-agent names another
            # crawler -- so the probe must also satisfy that crawler's group.
            verdict = rules.verdict_for(ua, u.path)
        if not verdict.allowed:
            return self._record_refusal(
                key, method, purpose, ledger_mod.OUTCOME_BLOCKED_ROBOTS,
                f"robots.txt disallows this audit's user-agent: {verdict.reason}",
                ua, probe_agent, depth, referrer, record)

        # --- guard 5: budget ----------------------------------------------------------
        if not self.budget.may_request():
            return self._record_refusal(
                key, method, purpose, ledger_mod.OUTCOME_BUDGET,
                "the request or time budget for this run was exhausted",
                ua, probe_agent, depth, referrer, record)

        # --- guard 7: circuit breaker -------------------------------------------------
        if self.limiter.is_open(u.host):
            return self._record_refusal(
                key, method, purpose, ledger_mod.OUTCOME_CIRCUIT_OPEN,
                f"host {u.host} was abandoned after repeated refusals; no further "
                f"requests were sent to it",
                ua, probe_agent, depth, referrer, record)

        return self._fetch_with_redirects(
            key, u, method, purpose, ua, probe_agent, depth, referrer, record)

    def head(self, url: str, **kwargs) -> LedgerEntry:
        """HEAD request, used for reachability and weight sampling."""
        kwargs.setdefault("purpose", "asset")
        kwargs.setdefault("allow_non_document", True)
        return self.fetch(url, method="HEAD", **kwargs)

    # -- internals ---------------------------------------------------------------------

    def _fetch_with_redirects(self, key: str, u: NormalUrl, method: str, purpose: str,
                              ua: str, probe_agent: Optional[str],
                              depth: Optional[int], referrer: str,
                              record: bool) -> LedgerEntry:
        chain: List[Dict[str, object]] = []
        current = u
        hops = 0

        while True:
            # --- guard 6: resolve-then-connect, every hop ----------------------------
            port = current.port or (443 if current.scheme == "https" else 80)
            try:
                target = ssrf.resolve_and_validate(current.host, port)
            except ssrf.SSRFDenied as exc:
                return self._record_refusal(
                    key, method, purpose, ledger_mod.OUTCOME_BLOCKED_POLICY, str(exc),
                    ua, probe_agent, depth, referrer, record, redirects=chain,
                    final_url=current.url)

            try:
                self.limiter.acquire(current.host)
            except ratelimit_mod.CircuitOpen as exc:
                return self._record_refusal(
                    key, method, purpose, ledger_mod.OUTCOME_CIRCUIT_OPEN, str(exc),
                    ua, probe_agent, depth, referrer, record, redirects=chain,
                    final_url=current.url)

            self.budget.note_request()
            try:
                raw = self._request(current, target, method, ua, port)
            except (socket.timeout, TimeoutError) as exc:
                self.limiter.release(current.host)
                return self._record_refusal(
                    key, method, purpose, ledger_mod.OUTCOME_TIMEOUT,
                    f"timed out after {self.read_timeout:.0f}s: {exc}",
                    ua, probe_agent, depth, referrer, record, redirects=chain,
                    final_url=current.url)
            except _TooLarge as exc:
                self.limiter.release(current.host)
                return self._record_refusal(
                    key, method, purpose, ledger_mod.OUTCOME_TOO_LARGE, str(exc),
                    ua, probe_agent, depth, referrer, record, redirects=chain,
                    final_url=current.url)
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                self.limiter.release(current.host)
                return self._record_refusal(
                    key, method, purpose, ledger_mod.OUTCOME_NETWORK_ERROR,
                    f"{type(exc).__name__}: {exc}",
                    ua, probe_agent, depth, referrer, record, redirects=chain,
                    final_url=current.url)
            else:
                self.limiter.release(current.host)

            self._log(current, method, raw.status, ua, purpose)

            # --- rate-limit health and the single permitted retry --------------------
            if raw.status in (429, 503):
                wait = self.limiter.note_refusal(
                    current.host, raw.status,
                    ratelimit_mod.parse_retry_after(raw.headers.get("retry-after")),
                )
                if not self.limiter.is_open(current.host) and self.budget.may_request() \
                        and wait <= self.budget.fetch_remaining:
                    self.limiter.backoff(wait)
                    try:
                        self.limiter.acquire(current.host)
                    except ratelimit_mod.CircuitOpen:
                        pass
                    else:
                        self.budget.note_request()
                        try:
                            raw = self._request(current, target, method, ua, port)
                            self._log(current, method, raw.status, ua, purpose)
                        except Exception:  # retry failed; keep the first response
                            pass
                        finally:
                            self.limiter.release(current.host)
            else:
                self.limiter.note_success(current.host)

            # --- guard 9: manual redirect handling -----------------------------------
            if raw.status in (301, 302, 303, 307, 308) and method != "HEAD":
                location = raw.headers.get("location", "")
                if not location:
                    break
                hops += 1
                if hops > self.max_redirects:
                    return self._record_refusal(
                        key, method, purpose, ledger_mod.OUTCOME_NETWORK_ERROR,
                        f"redirect chain exceeded {self.max_redirects} hops",
                        ua, probe_agent, depth, referrer, record, redirects=chain,
                        final_url=current.url)
                try:
                    nxt = urlnorm.normalise(location, base=current.url)
                except UnsafeUrl as exc:
                    return self._record_refusal(
                        key, method, purpose, ledger_mod.OUTCOME_BLOCKED_POLICY,
                        f"redirect target refused: {exc}",
                        ua, probe_agent, depth, referrer, record, redirects=chain,
                        final_url=current.url)

                # A redirect is not a licence to skip the guards. Re-check the denylist
                # and robots for the new URL exactly as if it had been requested
                # directly -- redirect-to-/logout is a real pattern.
                refuse, why = urlnorm.is_mutating_get(nxt)
                if refuse:
                    return self._record_refusal(
                        key, method, purpose, ledger_mod.OUTCOME_BLOCKED_POLICY,
                        f"redirect target refused as possibly state-changing: {why}",
                        ua, probe_agent, depth, referrer, record, redirects=chain,
                        final_url=current.url)
                nxt_rules = self.robots_for(nxt.origin)
                nxt_verdict = nxt_rules.verdict_for(self.user_agent, nxt.path)
                if not nxt_verdict.allowed:
                    return self._record_refusal(
                        key, method, purpose, ledger_mod.OUTCOME_BLOCKED_ROBOTS,
                        f"redirect target disallowed by robots.txt: {nxt_verdict.reason}",
                        ua, probe_agent, depth, referrer, record, redirects=chain,
                        final_url=current.url)
                if not self.budget.may_request():
                    break

                chain.append({
                    "from": current.url,
                    "to": nxt.url,
                    "status": raw.status,
                    "hop": hops,
                })
                current = nxt
                continue
            break

        body = b"" if method == "HEAD" else raw.body
        declared_charset = _charset_from_headers(raw.headers)
        encoding = _resolve_encoding(body, declared_charset)

        entry = LedgerEntry(
            url=key,
            final_url=current.url,
            method=method,
            status=raw.status,
            outcome=(ledger_mod.OUTCOME_OK if 200 <= raw.status < 300
                     else ledger_mod.OUTCOME_HTTP_ERROR),
            reason="" if 200 <= raw.status < 300 else f"HTTP {raw.status}",
            headers=raw.headers,
            declared_length=_int_or_none(raw.headers.get("content-length")),
            truncated=raw.truncated,
            encoding=encoding,
            declared_encoding=declared_charset,
            ttfb_ms=round(raw.ttfb_ms, 1),
            elapsed_ms=round(raw.elapsed_ms, 1),
            redirects=chain,
            resolved_ip=raw.resolved_ip,
            user_agent=ua,
            purpose=purpose,
            depth=depth,
            referrer=referrer,
            probe_agent=probe_agent or "",
        )
        if record:
            entry = self.ledger.put(entry, body)
            if purpose == "page" and entry.ok:
                self.budget.note_page()
        return entry

    def _request(self, u: NormalUrl, target: ssrf.ResolvedTarget, method: str,
                 ua: str, port: int) -> RawResponse:
        connect_timeout = self.budget.timeout_for(self.connect_timeout)
        read_timeout = self.budget.timeout_for(self.read_timeout)

        if u.scheme == "https":
            conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
                u.host, target.ip, port, context=self._tls,
                connect_timeout=connect_timeout, read_timeout=read_timeout)
        else:
            conn = _PinnedHTTPConnection(
                u.host, target.ip, port,
                connect_timeout=connect_timeout, read_timeout=read_timeout)

        path = u.path + (f"?{u.raw_query}" if u.raw_query else "")
        started = time.monotonic()
        try:
            conn.request(method, path, headers=honest_headers(ua, u.netloc))
            response = conn.getresponse()
            ttfb = (time.monotonic() - started) * 1000.0
            headers = {k.lower(): v for k, v in response.getheaders()}
            body = b"" if method == "HEAD" else self._read_capped(response, headers)
            elapsed = (time.monotonic() - started) * 1000.0
            return RawResponse(
                status=response.status, headers=headers, body=body,
                truncated=getattr(response, "_auditkit_truncated", False),
                ttfb_ms=ttfb, elapsed_ms=elapsed, resolved_ip=target.ip,
            )
        finally:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass

    def _read_capped(self, response, headers: Dict[str, str]) -> bytes:
        """Stream the body with a hard size cap and a decompression-ratio cap.

        Both caps matter (threat T10). The size cap stops a multi-gigabyte response; the
        ratio cap stops a small compressed response that expands to fill memory. A
        truncated read is recorded as such, and section 2.2.1's guard G7 makes the CSR
        detector refuse to judge a page whose body never fully arrived -- streaming
        server-rendered HTML genuinely looks thin mid-response.
        """
        encoding = (headers.get("content-encoding") or "").lower().strip()
        decompressor = None
        if "gzip" in encoding or "x-gzip" in encoding:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif "deflate" in encoding:
            decompressor = zlib.decompressobj()

        chunks: List[bytes] = []
        raw_total = 0
        out_total = 0
        truncated = False

        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            raw_total += len(chunk)
            if decompressor is not None:
                try:
                    piece = decompressor.decompress(chunk, self.max_bytes - out_total + 1)
                except zlib.error:
                    # Some servers mislabel raw deflate; retry once without a header.
                    if "deflate" in encoding and out_total == 0 and not chunks:
                        decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
                        try:
                            piece = decompressor.decompress(chunk)
                        except zlib.error:
                            raise _TooLarge("response body could not be decompressed")
                    else:
                        raise _TooLarge("response body could not be decompressed")
            else:
                piece = chunk

            out_total += len(piece)
            chunks.append(piece)

            if raw_total > 0 and out_total / max(1, raw_total) > self.max_decompress_ratio:
                raise _TooLarge(
                    f"decompression ratio exceeded {self.max_decompress_ratio}:1 "
                    f"({raw_total} bytes in, {out_total} bytes out); refused as a "
                    f"possible compression bomb"
                )
            if out_total > self.max_bytes:
                truncated = True
                break

        if decompressor is not None and not truncated:
            try:
                chunks.append(decompressor.flush())
            except zlib.error:
                pass

        setattr(response, "_auditkit_truncated", truncated)
        data = b"".join(chunks)
        return data[: self.max_bytes]

    def _log(self, u: NormalUrl, method: str, status: Optional[int], ua: str,
             purpose: str) -> None:
        """Append to the in-memory request log.

        Written to ``run.log`` at teardown so a safety spot-check on a live run can
        confirm GET/HEAD only, robots honoured, and per-host pacing respected.
        """
        self.request_log.append({
            "seq": len(self.request_log) + 1,
            "method": method,
            "url": u.url,
            "status": status,
            "purpose": purpose,
            "user_agent": ua,
            "elapsed_since_start": round(self.budget.elapsed, 2),
        })

    def _record_refusal(self, key: str, method: str, purpose: str, outcome: str,
                        reason: str, ua: str, probe_agent: Optional[str],
                        depth: Optional[int], referrer: str, record: bool,
                        redirects: Optional[List[Dict[str, object]]] = None,
                        final_url: str = "") -> LedgerEntry:
        entry = LedgerEntry(
            url=key, final_url=final_url or key, method=method, status=None,
            outcome=outcome, reason=reason, user_agent=ua, purpose=purpose,
            depth=depth, referrer=referrer, probe_agent=probe_agent or "",
            redirects=redirects or [],
        )
        if record and not self.ledger.has(key):
            entry = self.ledger.put(entry, None)
        return entry

    # -- teardown ----------------------------------------------------------------------

    def write_request_log(self, path) -> None:
        from pathlib import Path as _Path
        lines = [
            "# Request log. Every network call made by this audit, in order.",
            f"# user-agent: {self.user_agent}",
            f"# methods permitted: {sorted(ALLOWED_METHODS)}",
            "",
        ]
        for row in self.request_log:
            lines.append(
                f"{row['seq']:4d}  +{row['elapsed_since_start']:7.2f}s  "
                f"{row['method']:4s}  {row['status'] or '---'}  "
                f"{row['purpose']:8s}  {row['url']}"
            )
        _Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def stats(self) -> Dict[str, object]:
        methods: Dict[str, int] = {}
        for row in self.request_log:
            m = str(row["method"])
            methods[m] = methods.get(m, 0) + 1
        return {
            "requests": len(self.request_log),
            "methods": dict(sorted(methods.items())),
            "user_agent": self.user_agent,
            "rate_limit": self.limiter.stats(),
        }


def tls_context() -> ssl.SSLContext:
    """A certificate-verifying TLS context with a usable trust store on every platform.

    Verification is never disabled. Some Python builds (python.org installers on macOS
    among them) ship without system CA certificates, so the default context rejects every
    valid certificate and an audit would misreport healthy sites as unreachable. When
    ``certifi`` is available its CA bundle is used; otherwise the platform default.
    """
    try:
        import certifi  # optional dependency
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class _TooLarge(RuntimeError):
    """Internal: size or decompression cap hit."""


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _charset_from_headers(headers: Dict[str, str]) -> Optional[str]:
    ctype = headers.get("content-type", "")
    for part in ctype.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip('"\'').lower() or None
    return None


def _resolve_encoding(body: bytes, declared: Optional[str]) -> str:
    """Pick the encoding to decode with, preferring an in-document declaration.

    The *declared* value is kept separately on the ledger entry so
    ``G1.ENCODING.UNDECLARED_OR_WRONG`` can compare what the response claimed against
    what the bytes actually are.
    """
    meta_charset = _charset_from_meta(body)
    for candidate in (meta_charset, declared):
        if not candidate:
            continue
        try:
            body[:4096].decode(candidate)
            return candidate
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        body.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes  # optional dependency
        best = from_bytes(body[:65536]).best()
        if best is not None and best.encoding:
            return str(best.encoding)
    except Exception:
        pass
    return "cp1252"


def _charset_from_meta(body: bytes) -> Optional[str]:
    """Read ``<meta charset>`` from the first 4 KB, as browsers do."""
    import re
    head = body[:4096]
    for pattern in (rb'<meta[^>]+charset\s*=\s*["\']?([A-Za-z0-9_\-]+)',
                    rb'charset\s*=\s*["\']?([A-Za-z0-9_\-]+)'):
        m = re.search(pattern, head, re.IGNORECASE)
        if m:
            return m.group(1).decode("ascii", errors="ignore").lower()
    return None


def gunzip_if_needed(data: bytes) -> bytes:
    """Decompress a ``.gz`` sitemap body. Size-capped by the caller's read."""
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except (OSError, zlib.error):
            return data
    return data
