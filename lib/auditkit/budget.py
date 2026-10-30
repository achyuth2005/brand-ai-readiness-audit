"""Wall-clock and request governor, plus the degradation ladder.

The brief sets a hard constraint: **an audit must finish in under 5 minutes on a
standard machine for a typical website.** The way that constraint is usually missed is
not one slow step -- it is an unbounded one: a 50,000-URL sitemap, a host that takes 30
seconds per page, a render tier that never settles.

So the budget is a single object, created once in the orchestrator and consulted at
every phase boundary. When it runs short it does not truncate silently; it walks a
**documented degradation ladder**, and every rung it takes is recorded in
``coverage.degradations`` so the report states exactly what was given up.

Ladder, in order (cheapest loss of confidence first):

1. Drop the browser render tier. Tiers 1-2 still confirm client-side rendering from the
   framework's own hydration payload, so this costs the least.
2. Drop off-site corroboration (``sameAs`` resolution).
3. Reduce the page cap, while preserving the guaranteed pages and at least one page per
   template cluster -- the sampling guarantees that make ``k/n`` evidence meaningful.
4. Drop asset HEAD checks (page weight, broken-link sampling).
5. Emit with ``budget_exhausted: true`` and an explicit coverage statement.

Never: a silently shortened report, or a finding whose confidence does not reflect the
degraded evidence behind it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Ladder rungs, in the order they are taken.
DEGRADE_RENDER = "render_tier_dropped"
DEGRADE_OFFSITE = "offsite_corroboration_dropped"
DEGRADE_PAGE_CAP = "page_cap_reduced"
DEGRADE_ASSETS = "asset_checks_dropped"
DEGRADE_EXHAUSTED = "budget_exhausted"

LADDER = (DEGRADE_RENDER, DEGRADE_OFFSITE, DEGRADE_PAGE_CAP, DEGRADE_ASSETS,
          DEGRADE_EXHAUSTED)


class BudgetExhausted(RuntimeError):
    """Raised only when the deadline passes before minimum coverage was reached."""


@dataclass
class Budget:
    """The run's resource envelope.

    ``reserve_seconds`` is held back from the wall clock for analysis, merge, and report
    rendering, which happen after the crawl. Spending the whole budget on fetching and
    then having no time to write the report is the obvious failure mode, and reserving
    up front is what prevents it.
    """

    total_seconds: float = 240.0
    reserve_seconds: float = 60.0
    max_requests: int = 120
    max_pages: int = 30
    max_render_pages: int = 5
    min_pages_for_coverage: int = 3

    started: float = field(default_factory=time.monotonic)
    requests_made: int = 0
    pages_fetched: int = 0
    pages_rendered: int = 0
    _degradations: List[Dict[str, object]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _clock = staticmethod(time.monotonic)

    # -- clock -------------------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return self._clock() - self.started

    @property
    def remaining(self) -> float:
        """Seconds left in the *whole* run, including the reserve."""
        return max(0.0, self.total_seconds - self.elapsed)

    @property
    def fetch_remaining(self) -> float:
        """Seconds left for network work, with the analysis reserve held back."""
        return max(0.0, self.total_seconds - self.reserve_seconds - self.elapsed)

    @property
    def fetch_deadline_passed(self) -> bool:
        return self.fetch_remaining <= 0.0

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0.0

    # -- request accounting ------------------------------------------------------------

    def may_request(self) -> bool:
        """True when another HTTP request is within both the count and time budget."""
        with self._lock:
            if self.requests_made >= self.max_requests:
                return False
        return not self.fetch_deadline_passed

    def note_request(self) -> int:
        with self._lock:
            self.requests_made += 1
            return self.requests_made

    def may_fetch_page(self) -> bool:
        with self._lock:
            if self.pages_fetched >= self.max_pages:
                return False
        return self.may_request()

    def note_page(self) -> int:
        with self._lock:
            self.pages_fetched += 1
            return self.pages_fetched

    def may_render(self) -> bool:
        if self.has_degraded(DEGRADE_RENDER):
            return False
        with self._lock:
            if self.pages_rendered >= self.max_render_pages:
                return False
        # A render costs several seconds; require enough headroom for a whole one plus
        # the analysis reserve, or the last render eats the report.
        return self.fetch_remaining > 12.0

    def note_render(self) -> int:
        with self._lock:
            self.pages_rendered += 1
            return self.pages_rendered

    def may_check_assets(self) -> bool:
        return not self.has_degraded(DEGRADE_ASSETS) and self.may_request()

    def may_go_offsite(self) -> bool:
        return not self.has_degraded(DEGRADE_OFFSITE) and self.may_request()

    def timeout_for(self, default: float) -> float:
        """Clamp a per-request timeout so one slow host cannot eat the whole budget."""
        return max(1.0, min(default, self.fetch_remaining))

    # -- degradation -------------------------------------------------------------------

    def degrade(self, rung: str, reason: str, detail: Optional[Dict] = None) -> None:
        """Record a ladder rung. Idempotent: a rung is only ever recorded once."""
        if rung not in LADDER:
            raise ValueError(f"unknown degradation rung {rung!r}; expected one of {LADDER}")
        with self._lock:
            if any(d["step"] == rung for d in self._degradations):
                return
            entry: Dict[str, object] = {
                "step": rung,
                "reason": reason,
                "elapsed_seconds": round(self.elapsed, 1),
            }
            if detail:
                entry.update(detail)
            self._degradations.append(entry)

    def has_degraded(self, rung: str) -> bool:
        with self._lock:
            return any(d["step"] == rung for d in self._degradations)

    def degradations(self) -> List[Dict[str, object]]:
        with self._lock:
            return [dict(d) for d in self._degradations]

    def apply_ladder(self) -> List[str]:
        """Take ladder rungs appropriate to the time left. Returns rungs newly taken.

        Called at each phase boundary. The thresholds are proportions of the fetch
        window rather than absolute seconds, so the ladder behaves the same whether the
        caller allowed 60 seconds or 600.
        """
        taken: List[str] = []
        window = max(1.0, self.total_seconds - self.reserve_seconds)
        left_fraction = self.fetch_remaining / window

        if left_fraction < 0.55 and not self.has_degraded(DEGRADE_RENDER):
            self.degrade(DEGRADE_RENDER,
                         "less than 55% of the fetch window remained; the browser "
                         "render tier was dropped. Client-side rendering is still "
                         "detected from hydration payloads (tiers 1-2).")
            taken.append(DEGRADE_RENDER)
        if left_fraction < 0.40 and not self.has_degraded(DEGRADE_OFFSITE):
            self.degrade(DEGRADE_OFFSITE,
                         "less than 40% of the fetch window remained; off-site "
                         "corroboration of sameAs identifiers was dropped.")
            taken.append(DEGRADE_OFFSITE)
        if left_fraction < 0.25 and not self.has_degraded(DEGRADE_PAGE_CAP):
            with self._lock:
                new_cap = max(self.min_pages_for_coverage,
                              self.pages_fetched + 3,
                              self.max_pages // 2)
                old_cap, self.max_pages = self.max_pages, min(self.max_pages, new_cap)
            self.degrade(DEGRADE_PAGE_CAP,
                         "less than 25% of the fetch window remained; the page cap was "
                         "reduced. Guaranteed pages and one page per template cluster "
                         "are still fetched.",
                         {"from": old_cap, "to": self.max_pages})
            taken.append(DEGRADE_PAGE_CAP)
        if left_fraction < 0.12 and not self.has_degraded(DEGRADE_ASSETS):
            self.degrade(DEGRADE_ASSETS,
                         "less than 12% of the fetch window remained; asset HEAD checks "
                         "(page weight, broken-link sampling) were dropped.")
            taken.append(DEGRADE_ASSETS)
        if self.fetch_deadline_passed and not self.has_degraded(DEGRADE_EXHAUSTED):
            self.degrade(DEGRADE_EXHAUSTED,
                         "the fetch window closed; the audit proceeded to analysis with "
                         "the evidence already collected.")
            taken.append(DEGRADE_EXHAUSTED)
        return taken

    def require_minimum_coverage(self) -> None:
        """Raise when too little was fetched for any honest report to be written."""
        if self.pages_fetched < self.min_pages_for_coverage:
            raise BudgetExhausted(
                f"only {self.pages_fetched} page(s) were fetched before the budget "
                f"closed; at least {self.min_pages_for_coverage} are needed for a "
                f"report whose findings are not single-page guesses"
            )

    # -- reporting ---------------------------------------------------------------------

    def to_dict(self) -> Dict[str, object]:
        return {
            "wall_clock_seconds": round(self.elapsed, 1),
            "wall_clock_budget_seconds": self.total_seconds,
            "requests_made": self.requests_made,
            "request_cap": self.max_requests,
            "pages_fetched": self.pages_fetched,
            "page_cap": self.max_pages,
            "pages_rendered": self.pages_rendered,
            "budget_exhausted": self.has_degraded(DEGRADE_EXHAUSTED),
            "degradations": self.degradations(),
        }


def from_config(cfg: Dict, budget_seconds: Optional[float] = None,
                max_pages: Optional[int] = None) -> Budget:
    """Build a :class:`Budget` from ``config/thresholds.json`` plus CLI overrides."""
    section = (cfg.get("budget") or {})

    def _v(key: str, default):
        entry = section.get(key)
        if isinstance(entry, dict):
            return entry.get("value", default)
        return default if entry is None else entry

    return Budget(
        total_seconds=float(budget_seconds if budget_seconds is not None
                            else _v("wall_clock_seconds", 240.0)),
        reserve_seconds=float(_v("analysis_reserve_seconds", 60.0)),
        max_requests=int(_v("max_requests", 120)),
        max_pages=int(max_pages if max_pages is not None else _v("max_pages", 30)),
        max_render_pages=int(_v("max_render_pages", 5)),
        min_pages_for_coverage=int(_v("min_pages_for_coverage", 3)),
    )
