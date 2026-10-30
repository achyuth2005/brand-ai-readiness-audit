"""auditkit -- the shared library behind the brand-ai-readiness-audit marketplace.

This is the only code in the package that may touch a socket, and only one module in it
actually does (:mod:`auditkit.netgate`). Everything else is a pure function of the
evidence ledger.

Two invariants hold the design together, and both are enforced structurally rather than
by convention:

**Invariant A -- the Evidence Ledger.** Every byte the audit reasons about is fetched
exactly once, by one component, and written to an immutable, content-addressed ledger.
All analysis reads the ledger. Nothing else touches the network. See
:mod:`auditkit.ledger`.

**Invariant B -- the Pure-Analyzer Invariant.** Specialist skills are pure functions of
the ledger: no network I/O, no writes outside their own findings file, no mutation of the
ledger. A read-only :class:`~auditkit.ledger.Ledger` handle raises on any write, and
:class:`~auditkit.netgate.NetGate` refuses to initialise outside the orchestrator.

Module map
----------
Safety core (the only path to the network)
    :mod:`~auditkit.netgate`    sole egress point: method allowlist, redirects, caps
    :mod:`~auditkit.ssrf`       resolve-then-connect, private-range denial
    :mod:`~auditkit.robots`     RFC 9309 matcher, per-agent groups, crawl-delay
    :mod:`~auditkit.ratelimit`  per-host token bucket, backoff, circuit breaker
    :mod:`~auditkit.urlnorm`    canonical URLs, dedupe keys, mutating-GET denylist
    :mod:`~auditkit.budget`     wall-clock governor and degradation ladder
    :mod:`~auditkit.ledger`     immutable content-addressed evidence store

Analysis core (pure functions)
    :mod:`~auditkit.dom`        parsing, visible text, evidence anchors
    :mod:`~auditkit.textnorm`   text, money, date and name normalisation
    :mod:`~auditkit.sdextract`  JSON-LD / Microdata / RDFa extraction and flattening
    :mod:`~auditkit.cssutil`    static CSS for overlay geometry and declared contrast
    :mod:`~auditkit.template`   DOM skeleton clustering, k/n collapse
    :mod:`~auditkit.profile`    evidence-based site-type classification
    :mod:`~auditkit.lexicons`   all bundled detection vocabulary
    :mod:`~auditkit.render`     optional Playwright tier, feature-detected

Contracts
    :mod:`~auditkit.checks`     the closed check registry
    :mod:`~auditkit.finding`    the Finding contract -- the only way to emit a problem
    :mod:`~auditkit.severity`   gate x scope x fact x confidence -> severity
    :mod:`~auditkit.redact`     PII and secret redaction for evidence
    :mod:`~auditkit.bootstrap`  marketplace-root discovery and config loading
"""

from __future__ import annotations

__version__ = "1.0.0"
__license__ = "MIT"

# Deliberately no eager imports of the submodules. Analyzers import exactly what they
# need, which keeps a specialist's import graph free of the networking modules -- so
# Invariant B is visible in the import list, not just asserted in a docstring.

__all__ = [
    "bootstrap", "budget", "checks", "cssutil", "dom", "finding", "ledger",
    "lexicons", "netgate", "profile", "ratelimit", "redact", "render", "robots",
    "sdextract", "severity", "template", "textnorm", "urlnorm",
]
