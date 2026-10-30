"""Safety controls: every row of the threat model has a test here."""

from __future__ import annotations

import ast
import gzip
import os
import tempfile
import time
import unittest
from pathlib import Path

import helpers
from helpers import ROOT, SITE, SPECIALISTS

from auditkit import budget as budget_mod
from auditkit import netgate, ratelimit, redact, robots, ssrf, urlnorm
from auditkit.ledger import Ledger, LedgerEntry, LedgerImmutable, LedgerReadOnly


def _gate(tmp: str, rules: robots.RobotsFile = None) -> netgate.NetGate:
    ledger = Ledger.create(Path(tmp) / "ledger")
    os.environ[netgate.ORCHESTRATOR_ENV_TOKEN] = "1"
    try:
        return netgate.NetGate(
            ledger, budget_mod.Budget(), ratelimit.RateLimiter(min_interval=0.0),
            lambda origin: rules or robots.allow_all(), contact="tests@site.test")
    finally:
        os.environ.pop(netgate.ORCHESTRATOR_ENV_TOKEN, None)


class MethodAndPolicyTests(unittest.TestCase):

    def test_method_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                with self.assertRaises(netgate.EgressDenied):
                    gate.fetch(f"{SITE}/", method=method)

    def test_gateway_refuses_outside_orchestrator(self):
        os.environ.pop(netgate.ORCHESTRATOR_ENV_TOKEN, None)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger.create(Path(tmp) / "ledger")
            with self.assertRaises(netgate.NotOrchestrator):
                netgate.NetGate(ledger, budget_mod.Budget(), ratelimit.RateLimiter(),
                                lambda origin: robots.allow_all())

    def test_mutating_get_denylist(self):
        refused = ["/logout", "/cart", "/account/unsubscribe/abc", "/a?action=delete",
                   "/confirm?token=aZ9kQ2mN7pL4xR8vT1yU", "/wp-admin/",
                   "/i/550e8400-e29b-41d4-a716-446655440000"]
        allowed = ["/help/cart-abandonment-guide", "/blog/2024-03-15-annual-report",
                   "/guides/the-best-cameras-for-2024", "/search?q=long+phrase",
                   "/p/nice-blue-widget"]
        for path in refused:
            self.assertTrue(urlnorm.is_mutating_get(urlnorm.normalise(SITE + path))[0], path)
        for path in allowed:
            self.assertFalse(urlnorm.is_mutating_get(urlnorm.normalise(SITE + path))[0], path)

    def test_mutating_urls_are_refused_without_a_request(self):
        with tempfile.TemporaryDirectory() as tmp, helpers.no_network():
            entry = _gate(tmp).fetch(f"{SITE}/logout")
            self.assertEqual(entry.outcome, "blocked_policy")

    def test_schemes_ports_and_credentials_refused(self):
        with tempfile.TemporaryDirectory() as tmp, helpers.no_network():
            gate = _gate(tmp)
            for url in ("file:///etc/passwd", "gopher://site.test/", "https://u:p@site.test/",
                        "https://site.test:22/"):
                self.assertEqual(gate.fetch(url).outcome, "blocked_policy", url)

    def test_ssrf_ranges(self):
        for address in ("127.0.0.1", "10.0.0.5", "172.16.1.1", "192.168.0.10", "100.64.0.1",
                        "169.254.169.254", "0.0.0.0", "::1", "fd00::1", "fe80::1",
                        "::ffff:127.0.0.1", "2002:7f00:1::"):
            self.assertFalse(ssrf.classify(address)[0], address)
        for address in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertTrue(ssrf.classify(address)[0], address)

    def test_private_targets_and_redirect_hops_are_revalidated(self):
        with self.assertRaises(ssrf.SSRFDenied):
            ssrf.validate_redirect_target("169.254.169.254")
        with tempfile.TemporaryDirectory() as tmp:
            entry = _gate(tmp).fetch("http://127.0.0.1/")
            self.assertEqual(entry.outcome, "blocked_policy")
            self.assertIn("127.0.0.0/8", entry.reason)


class RobotsTests(unittest.TestCase):
    TEXT = ("User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n"
            "Disallow: /private/\n\nUser-agent: Bingbot\nDisallow: /*.pdf$\n"
            "Allow: /docs/\nDisallow: /docs/internal/\n")

    def test_rfc9309_precedence(self):
        rules = robots.RobotsFile.parse(self.TEXT)
        cases = [("Googlebot/2.1", "/products/x", True), ("Googlebot/2.1", "/private/x", False),
                 ("OtherBot", "/products/x", False), ("Bingbot", "/a/file.pdf", False),
                 ("Bingbot", "/a/file.pdfx", True), ("Bingbot", "/docs/guide", True),
                 ("Bingbot", "/docs/internal/x", False)]
        for agent, path, expected in cases:
            self.assertEqual(rules.allows(agent, path), expected, (agent, path))

    def test_status_semantics(self):
        self.assertTrue(robots.RobotsFile.from_response(404, None).allows("A", "/x"))
        self.assertFalse(robots.RobotsFile.from_response(503, None).allows("A", "/x"))
        self.assertFalse(robots.RobotsFile.from_response(None, None, timed_out=True)
                         .allows("A", "/x"))

    def test_disallowed_path_is_never_fetched(self):
        rules = robots.RobotsFile.parse("User-agent: *\nDisallow: /private\n")
        with tempfile.TemporaryDirectory() as tmp, helpers.no_network():
            entry = _gate(tmp, rules).fetch(f"{SITE}/private/page")
            self.assertEqual(entry.outcome, "blocked_robots")

    def test_probe_obeys_the_group_its_token_matches(self):
        rules = robots.RobotsFile.parse("User-agent: OAI-SearchBot\nDisallow: /\n\n"
                                        "User-agent: *\nAllow: /\n")
        with tempfile.TemporaryDirectory() as tmp, helpers.no_network():
            entry = _gate(tmp, rules).fetch(f"{SITE}/page", purpose="probe",
                                            probe_agent="OAI-SearchBot")
            self.assertEqual(entry.outcome, "blocked_robots")


class ResourceTests(unittest.TestCase):

    class _Response:
        def __init__(self, data: bytes):
            self._data = data

        def read(self, size: int) -> bytes:
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk

    def test_decompression_bomb_refused(self):
        bomb = gzip.compress(b"\0" * (40 * 1024 * 1024))
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            with self.assertRaises(netgate._TooLarge):
                gate._read_capped(self._Response(bomb), {"content-encoding": "gzip"})

    def test_size_cap_truncates(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            response = self._Response(b"a" * (gate.max_bytes + 100000))
            body = gate._read_capped(response, {})
            self.assertLessEqual(len(body), gate.max_bytes)
            self.assertTrue(response._auditkit_truncated)

    def test_rate_limit_interval_and_crawl_delay_floor(self):
        clock = [0.0]
        slept = []
        limiter = ratelimit.RateLimiter(min_interval=0.5, clock=lambda: clock[0],
                                        sleeper=lambda s: (slept.append(s),
                                                           clock.__setitem__(0, clock[0] + s)))
        limiter.configure_host("site.test", crawl_delay=2.0)
        limiter.acquire("site.test"); limiter.release("site.test")
        limiter.acquire("site.test"); limiter.release("site.test")
        self.assertAlmostEqual(sum(slept), 2.0, places=3)
        limiter.configure_host("fast.test", crawl_delay=0.1)
        self.assertEqual(limiter._state("fast.test").min_interval, 0.5)

    def test_circuit_breaker(self):
        limiter = ratelimit.RateLimiter(breaker_threshold=3, sleeper=lambda s: None)
        for _ in range(3):
            limiter.note_refusal("slow.test", 429)
        self.assertTrue(limiter.is_open("slow.test"))
        with self.assertRaises(ratelimit.CircuitOpen):
            limiter.acquire("slow.test")

    def test_budget_ladder(self):
        run_budget = budget_mod.Budget(total_seconds=100, reserve_seconds=20)
        run_budget.started = time.monotonic() - 70
        taken = run_budget.apply_ladder()
        self.assertEqual(taken[:3], [budget_mod.DEGRADE_RENDER, budget_mod.DEGRADE_OFFSITE,
                                     budget_mod.DEGRADE_PAGE_CAP])


class LedgerTests(unittest.TestCase):

    def test_read_only_and_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            writable = Ledger.create(Path(tmp) / "l")
            writable.put(LedgerEntry(url=f"{SITE}/", status=200), b"<html></html>")
            with self.assertRaises(LedgerImmutable):
                writable.put(LedgerEntry(url=f"{SITE}/", status=200), b"again")
            reader = Ledger.open(Path(tmp) / "l")
            with self.assertRaises(LedgerReadOnly):
                reader.put(LedgerEntry(url=f"{SITE}/x", status=200), b"x")

    def test_paths_are_hashes_and_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger.create(Path(tmp) / "l")
            ledger.put(LedgerEntry(url=f"{SITE}/../../etc/passwd", status=200), b"body")
            for sub in ("entries", "bodies"):
                for file in (Path(tmp) / "l" / sub).iterdir():
                    self.assertRegex(file.stem, r"^[0-9a-f]{64}$")
            body = next((Path(tmp) / "l" / "bodies").iterdir())
            body.write_bytes(b"tampered")
            self.assertTrue(Ledger.open(Path(tmp) / "l").verify_integrity())


class CodeHygieneTests(unittest.TestCase):
    NETWORK_MODULES = {"socket", "http.client", "urllib.request", "ssl", "httpx",
                       "requests", "urllib3", "aiohttp", "playwright"}
    ALLOWED = {"lib/auditkit/netgate.py", "lib/auditkit/ssrf.py", "lib/auditkit/render.py"}

    def _python_files(self):
        for base in ("lib", "skills"):
            yield from sorted((ROOT / base).rglob("*.py"))

    def test_only_the_gateway_modules_import_networking(self):
        offenders = []
        for path in self._python_files():
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if any(name == m or name.startswith(m + ".") for m in self.NETWORK_MODULES) \
                            and rel not in self.ALLOWED:
                        offenders.append(f"{rel}: {name}")
        self.assertEqual(offenders, [])

    def test_no_eval_or_exec(self):
        offenders = []
        for path in self._python_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id in ("eval", "exec", "__import__"):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_every_analyzer_runs_with_network_blocked(self):
        from fixtures import sites
        with tempfile.TemporaryDirectory() as tmp:
            ledger = helpers.build_ledger(Path(tmp) / "ledger", sites.well_built_site())
            results = helpers.run_all_skills(ledger, Path(tmp))
            self.assertEqual(helpers.analyzer_errors(results), [])
            self.assertTrue(all(r["_exit"] == 0 for r in results.values()))

    def test_user_agent_is_honest(self):
        for agent in (netgate.honest_user_agent(contact="ops@site.test"),
                      netgate.honest_user_agent("OAI-SearchBot", "ops@site.test")):
            self.assertTrue(agent.startswith(netgate.PRODUCT_TOKEN + "/"))
            for disguise in ("Mozilla", "Chrome", "Safari", "Googlebot"):
                self.assertNotIn(disguise, agent)


class RedactionTests(unittest.TestCase):

    def test_personal_data_masked_unless_it_is_the_finding(self):
        text = "Contact jane.doe@site.test or +44 20 7946 0958 about order 4111 1111 1111 1111"
        masked = redact.redact(text)
        self.assertNotIn("jane.doe@site.test", masked)
        self.assertNotIn("7946 0958", masked)
        self.assertNotIn("4111 1111 1111 1111", masked)
        kept = redact.redact(text, keep_business_contact=True)
        self.assertIn("jane.doe@site.test", kept)

    def test_prices_and_years_are_not_mistaken_for_phones(self):
        self.assertIn("2019 - 2024", redact.redact("Operating 2019 - 2024 at 49.99"))

    def test_secret_scan(self):
        self.assertTrue(redact.scan_for_secrets("key AKIAIOSFODNN7EXAMPLE here"))
        self.assertTrue(redact.scan_for_secrets("ghp_" + "a" * 36))
        self.assertFalse(redact.scan_for_secrets("an ordinary sentence"))


if __name__ == "__main__":
    unittest.main()
