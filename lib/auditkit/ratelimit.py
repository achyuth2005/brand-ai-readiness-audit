"""Per-host politeness: token bucket, concurrency cap, backoff, circuit breaker.

Politeness in this package is *structural* before it is configured. The Evidence Ledger
guarantees one fetch per URL for the whole audit, and the Pure-Analyzer Invariant means
the six specialist skills cannot issue requests at all. This module governs what is left:
the pace of the single crawl phase.

Controls, and the failure each one prevents:

* **Minimum interval per host** -- prevents a burst against a small origin. Raised to
  ``Crawl-delay`` when robots.txt asks for more, never lowered below our own default.
* **Concurrency semaphore per host** -- bounds simultaneous connections regardless of
  how many worker threads exist.
* **Retry-After backoff** -- one retry on 429/503, honouring the header, then stop.
  Retry storms are how a polite crawler becomes an accidental load test.
* **Circuit breaker** -- after a few consecutive refusals a host is abandoned for the
  rest of the run. The audit then reports partial coverage honestly instead of
  hammering a host that is asking to be left alone.

Thread-safe: the crawl phase uses a small thread pool, and every counter here is taken
under a lock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class HostState:
    """Pacing and health state for one host."""

    min_interval: float
    max_concurrency: int
    next_allowed: float = 0.0
    consecutive_refusals: int = 0
    tripped: bool = False
    tripped_reason: str = ""
    requests: int = 0
    total_wait: float = 0.0
    semaphore: threading.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.semaphore = threading.Semaphore(self.max_concurrency)


class CircuitOpen(RuntimeError):
    """Raised when a host has been abandoned. Never retried within the same run."""


class RateLimiter:
    """Per-host pacing shared by every fetch in a run.

    One instance lives in the orchestrator and is handed to ``netgate``; nothing else
    constructs one, so there is exactly one view of how hard we are pushing each host.
    """

    def __init__(self, min_interval: float = 0.5, max_concurrency: int = 2,
                 hard_concurrency_cap: int = 4, breaker_threshold: int = 3,
                 max_backoff: float = 30.0, clock=time.monotonic, sleeper=time.sleep):
        self.default_min_interval = max(0.0, float(min_interval))
        self.max_concurrency = max(1, min(int(max_concurrency), int(hard_concurrency_cap)))
        self.breaker_threshold = max(1, int(breaker_threshold))
        self.max_backoff = float(max_backoff)
        # Injectable clock and sleeper keep the tests fast and deterministic: the safety
        # suite asserts pacing behaviour without spending real seconds.
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        self._hosts: Dict[str, HostState] = {}
        self.total_sleep = 0.0

    # -- host registration -------------------------------------------------------------

    def configure_host(self, host: str, crawl_delay: Optional[float] = None) -> None:
        """Register a host, applying ``Crawl-delay`` as a floor on our interval.

        A site asking for 10 seconds gets 10 seconds. A site asking for 0.1 still gets
        our own default, because robots.txt grants permission to be slower, not licence
        to be faster than we chose to be.
        """
        interval = self.default_min_interval
        if crawl_delay is not None and crawl_delay > interval:
            interval = min(float(crawl_delay), 60.0)
        with self._lock:
            state = self._hosts.get(host)
            if state is None:
                self._hosts[host] = HostState(
                    min_interval=interval, max_concurrency=self.max_concurrency
                )
            else:
                state.min_interval = max(state.min_interval, interval)

    def _state(self, host: str) -> HostState:
        with self._lock:
            state = self._hosts.get(host)
            if state is None:
                state = HostState(min_interval=self.default_min_interval,
                                  max_concurrency=self.max_concurrency)
                self._hosts[host] = state
            return state

    # -- acquire / release -------------------------------------------------------------

    def acquire(self, host: str) -> float:
        """Block until a request to ``host`` is permitted. Returns seconds waited.

        Raises :class:`CircuitOpen` if the host has been abandoned.
        """
        state = self._state(host)
        if state.tripped:
            raise CircuitOpen(
                f"host {host} was abandoned after {state.consecutive_refusals} "
                f"consecutive refusals: {state.tripped_reason}"
            )

        state.semaphore.acquire()
        try:
            waited = 0.0
            while True:
                with self._lock:
                    now = self._clock()
                    if now >= state.next_allowed:
                        # Schedule the next slot from now, so concurrent callers queue
                        # rather than all firing at once.
                        state.next_allowed = now + state.min_interval
                        state.requests += 1
                        state.total_wait += waited
                        self.total_sleep += waited
                        return waited
                    delay = state.next_allowed - now
                self._sleep(delay)
                waited += delay
        except BaseException:
            state.semaphore.release()
            raise

    def release(self, host: str) -> None:
        """Release the concurrency slot. Always call this, including on failure."""
        state = self._state(host)
        try:
            state.semaphore.release()
        except ValueError:  # pragma: no cover - released more than acquired
            pass

    # -- health ------------------------------------------------------------------------

    def note_success(self, host: str) -> None:
        state = self._state(host)
        with self._lock:
            state.consecutive_refusals = 0

    def note_refusal(self, host: str, status: Optional[int],
                     retry_after: Optional[float] = None) -> float:
        """Record a 429/503 and return how long to wait before the single retry.

        Trips the breaker once ``breaker_threshold`` consecutive refusals have been seen.
        """
        state = self._state(host)
        with self._lock:
            state.consecutive_refusals += 1
            count = state.consecutive_refusals
            if count >= self.breaker_threshold:
                state.tripped = True
                state.tripped_reason = (
                    f"{count} consecutive responses with status {status}"
                )
        # Honour Retry-After when the server states it; otherwise exponential with a cap.
        if retry_after is not None and retry_after >= 0:
            return min(float(retry_after), self.max_backoff)
        return min(self.default_min_interval * (2 ** count), self.max_backoff)

    def backoff(self, seconds: float) -> None:
        seconds = max(0.0, min(float(seconds), self.max_backoff))
        if seconds:
            self._sleep(seconds)
            self.total_sleep += seconds

    def is_open(self, host: str) -> bool:
        """True when ``host`` has been abandoned for the rest of this run."""
        return self._state(host).tripped

    def tripped_hosts(self) -> List[Dict[str, object]]:
        with self._lock:
            return sorted(
                (
                    {"host": host, "reason": s.tripped_reason, "requests": s.requests}
                    for host, s in self._hosts.items()
                    if s.tripped
                ),
                key=lambda d: str(d["host"]),
            )

    def stats(self) -> Dict[str, object]:
        with self._lock:
            hosts = {
                host: {
                    "requests": s.requests,
                    "min_interval": round(s.min_interval, 3),
                    "total_wait_seconds": round(s.total_wait, 3),
                    "circuit_open": s.tripped,
                }
                for host, s in sorted(self._hosts.items())
            }
        return {
            "default_min_interval": self.default_min_interval,
            "max_concurrency_per_host": self.max_concurrency,
            "breaker_threshold": self.breaker_threshold,
            "hosts": hosts,
        }


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """Parse a ``Retry-After`` header, which may be seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    import datetime as _dt
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    reference = _dt.datetime.now(_dt.timezone.utc) if now is None \
        else _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    return max(0.0, (when - reference).total_seconds())
