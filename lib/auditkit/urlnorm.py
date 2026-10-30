"""URL normalisation, dedupe keys, and the mutating-GET denylist.

Two jobs, both load-bearing:

1. **Canonical form** -- so the Evidence Ledger fetches each URL exactly once. Without a
   stable dedupe key, ``/a``, ``/a/``, ``/a?utm_source=x`` and ``/A`` become four
   requests to one page, which is both impolite and a source of phantom duplicate
   findings.

2. **The mutating-GET denylist** (threat T2). A method allowlist of ``{GET, HEAD}`` is
   necessary but *not sufficient*: plenty of sites mutate state on GET. A naive crawler
   following every link will unsubscribe a mailing list, empty a cart, consume a
   single-use magic link, or log itself out. This is the control most auditors omit, and
   the one that would do real damage to a live site.

Nothing here opens a socket.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (None, 80, 443, 8080, 8443)

# Query parameters that never change what a page says. Dropped from the dedupe key so
# tracking variants collapse to one fetch, and kept in a separate field so
# G2.CANONICAL.PARAM_DUPLICATES can still reason about them.
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "utm_name", "utm_cid", "utm_reader", "utm_referrer", "utm_social",
    "gclid", "gbraid", "wbraid", "dclid", "fbclid", "msclkid", "twclid", "ttclid",
    "igshid", "mc_cid", "mc_eid", "yclid", "_ga", "_gl", "ref", "ref_src",
    "s_kwcid", "vero_id", "vero_conv", "pk_campaign", "pk_kwd", "piwik_campaign",
    "hsa_acc", "hsa_cam", "hsa_grp", "hsa_ad", "hsa_src", "hsa_tgt", "hsa_kw",
    "hsa_mt", "hsa_net", "hsa_ver", "_hsenc", "_hsmi", "__hstc", "__hssc", "__hsfp",
    "mkt_tok", "trk", "trkCampaign", "sc_cid", "cmpid", "campaign_id",
})

# ---------------------------------------------------------------------------------------
# Mutating-GET denylist (threat T2).
#
# Each entry below corresponds to a real-world state change triggered by a bare GET.
# When in doubt we refuse to fetch: a skipped URL costs a little coverage (recorded
# honestly in ``coverage.paths_skipped``), while a fetched one can cost the site data.
# ---------------------------------------------------------------------------------------

# Path segments. Matched against individual, case-folded, percent-decoded segments so
# that ``/help/cart-abandonment-guide`` (an article) is not confused with ``/cart``.
MUTATING_PATH_SEGMENTS = frozenset({
    # session teardown
    "logout", "log-out", "signout", "sign-out", "logoff", "log-off",
    # subscription state
    "unsubscribe", "optout", "opt-out", "unfollow", "resubscribe",
    # one-shot confirmations, usually token-bearing
    "confirm", "confirmation", "verify", "verification", "activate", "activation",
    "reset", "reset-password", "approve", "accept-invite", "redeem",
    # destructive
    "delete", "remove", "destroy", "cancel", "revoke", "purge", "clear",
    # commerce state
    "cart", "basket", "checkout", "add-to-cart", "addtocart", "order-complete",
    "payment", "pay",
    # administrative surfaces
    "admin", "administrator", "wp-admin", "wp-login.php", "xmlrpc.php",
    "install", "installer", "setup", "phpmyadmin", "adminer.php",
    # bulk egress
    "export", "download-all", "backup", "dump",
})

# Query keys whose presence alone means "this link performs an action".
MUTATING_QUERY_KEYS = frozenset({
    "add_to_cart", "add-to-cart", "remove_item", "remove-item", "removed_item",
    "token", "auth", "authkey", "auth_token", "nonce", "_wpnonce", "otp", "magic",
    "magic_link", "confirm", "confirmation_token", "activation_key", "reset_key",
    "unsubscribe_token", "invite", "invitation_token", "session", "sessionid",
    "sid", "phpsessid", "jsessionid", "access_token", "id_token", "code",
    "api_key", "apikey", "signature", "sig",
})

# ``key=value`` pairs where the *value* is the verb.
MUTATING_QUERY_VALUES: Dict[str, frozenset] = {
    "action": frozenset({
        "delete", "remove", "destroy", "logout", "signout", "unsubscribe", "confirm",
        "reset", "clear", "purge", "cancel", "revoke", "approve", "activate", "empty",
        "add", "update", "edit", "save", "submit",
    }),
    "do": frozenset({"delete", "remove", "logout", "confirm", "reset", "cancel"}),
    "op": frozenset({"delete", "remove", "logout", "confirm", "reset", "cancel"}),
    "cmd": frozenset({"delete", "remove", "logout", "confirm", "reset", "cancel"}),
    "mode": frozenset({"delete", "remove", "logout", "confirm"}),
}

# Non-document extensions. Skipped for content analysis; a HEAD may still be issued for
# weight/reachability checks, which is why this list is separate from the denylist.
NON_DOCUMENT_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp", ".tiff",
    ".css", ".js", ".mjs", ".map", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".gz", ".tar", ".bz2", ".7z", ".rar", ".dmg", ".exe", ".msi", ".pkg", ".deb",
    ".mp3", ".mp4", ".m4a", ".m4v", ".webm", ".ogg", ".ogv", ".wav", ".mov", ".avi",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".rtf",
})

# Path classes. Drives false-positive rule R4: a ``noindex`` on a search results page is
# correct engineering, and content checks have no business running on a login form.
UTILITY_PATH_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("search", re.compile(r"(^|/)(search|suche|recherche|find)(/|$)")),
    ("auth", re.compile(r"(^|/)(login|signin|sign-in|register|signup|sign-up|"
                        r"password|account|my-account|dashboard|profile|portal)(/|$)")),
    ("commerce_state", re.compile(r"(^|/)(cart|basket|checkout|order|orders|payment|"
                                  r"wishlist|compare)(/|$)")),
    ("legal", re.compile(r"(^|/)(privacy|privacy-policy|terms|terms-of-service|tos|"
                         r"legal|cookie-policy|cookies|imprint|impressum|gdpr|"
                         r"accessibility|disclaimer|dsar)(/|$)")),
    ("taxonomy", re.compile(r"(^|/)(tag|tags|category|categories|topic|topics|author|"
                            r"archive|archives|page)(/|$)")),
    ("feed", re.compile(r"(^|/)(feed|rss|atom|feeds)(/|$)|\.(rss|atom)$")),
    ("print", re.compile(r"(^|/)(print|printable|amp)(/|$)|[?&]print=")),
    ("quickview", re.compile(r"(^|/)(quick-?view|quick-?look|quick-?shop)(\.\w+)?(/|$)")),
    ("thankyou", re.compile(r"(^|/)(thank-you|thanks|thankyou|success|confirmed|"
                            r"submitted)(/|$)")),
    ("error", re.compile(r"(^|/)(404|403|500|error|not-found|oops)(/|$)")),
    # "api" only as the first segment: /api/v1/... is an endpoint, while /docs/Web/API/...
    # is documentation about one.
    ("admin", re.compile(r"(^|/)(admin|wp-admin|wp-json|cms|backend)(/|$)|^/api(/|$)")),
    ("utility_doc", re.compile(r"(^|/)(sitemap[^/]*\.xml|robots\.txt|llms\.txt|"
                               r"ads\.txt|security\.txt|humans\.txt)$")),
)

# Content path classes that *do* get the full content check suite.
CONTENT_PATH_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("home", re.compile(r"^/?$")),
    ("about", re.compile(r"(^|/)(about|about-us|company|who-we-are|our-story|team|"
                         r"leadership|mission)(/|$)")),
    ("contact", re.compile(r"(^|/)(contact|contact-us|get-in-touch|locations|"
                           r"stores|find-us)(/|$)")),
    ("pricing", re.compile(r"(^|/)(pricing|price|prices|plans|packages|tarife|"
                           r"subscribe|editions)(/|$)")),
    # "p", "item" and "items" also end ordinary documentation and API paths
    # (/CSSRuleList/item), so they mark a product only with a child segment.
    ("product", re.compile(r"(^|/)(product|products|shop|store|collections?|catalog(ue)?|"
                           r"sku)(/|$)|(^|/)(p|item|items)/[^/]+")),
    ("article", re.compile(r"(^|/)(blog|news|article|articles|post|posts|press|"
                           r"newsroom|insights|stories|resources|updates|"
                           r"changelog|release-notes)(/|$)")),
    ("docs", re.compile(r"(^|/)(docs|doc|documentation|guide|guides|kb|"
                        r"knowledge-base|help|support|manual|reference|api-docs|"
                        r"tutorial|tutorials|learn)(/|$)")),
    ("faq", re.compile(r"(^|/)(faq|faqs|questions|q-and-a|qa)(/|$)")),
    ("service", re.compile(r"(^|/)(service|services|solutions|features|platform|"
                           r"capabilities|use-cases|industries)(/|$)")),
    ("careers", re.compile(r"(^|/)(careers|jobs|join-us|hiring|vacancies)(/|$)")),
    ("event", re.compile(r"(^|/)(event|events|webinar|webinars|conference)(/|$)")),
)

# Dated article URL shapes: /2024/03/slug, /2024-03-15-slug.
DATED_PATH_RE = re.compile(r"/(19|20)\d{2}[/-](0[1-9]|1[0-2])([/-]\d{1,2})?[/-]?")

# "/p/12" is deliberately NOT pagination: it is one of the most common product URL
# shapes, and misclassifying it would mislabel a whole commerce template as paginated.
PAGINATION_RE = re.compile(r"(^|/)(page|seite|pagina)[/-]?\d+(/|$)|[?&](page|p|start|"
                           r"offset)=\d+")

_HIGH_ENTROPY_RE = re.compile(r"^[A-Za-z0-9_\-=+/.]{20,}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{20,}$")


@dataclass(frozen=True)
class NormalUrl:
    """A normalised absolute URL plus the fields the safety checks need."""

    scheme: str
    host: str            # lowercased, IDNA-encoded, no trailing dot
    port: Optional[int]  # None when the scheme default
    path: str            # always begins with "/"
    query: str           # sorted, tracking params removed
    raw_query: str       # original query, preserved for evidence
    userinfo: str        # non-empty means embedded credentials -> rejected upstream
    fragment: str = ""

    @property
    def netloc(self) -> str:
        if self.port is None:
            return self.host
        return f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.netloc}"

    @property
    def url(self) -> str:
        """Canonical absolute URL, without fragment. This is the ledger's dedupe key."""
        return urlunsplit((self.scheme, self.netloc, self.path, self.query, ""))

    @property
    def fetch_url(self) -> str:
        """URL to actually request: canonical path with the *original* query preserved.

        Tracking parameters are dropped from the dedupe key but kept on the wire, because
        stripping them can change what a server returns and we are auditing what a
        fetcher would really see.
        """
        return urlunsplit((self.scheme, self.netloc, self.path, self.raw_query, ""))

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.url


class UnsafeUrl(ValueError):
    """Raised for URLs this audit refuses to fetch, with the reason in ``args[0]``."""


def _idna(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    if not host:
        return host
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        # Already-encoded, or an unusual label. Keep the lowercased form; ssrf.py does
        # the authoritative validation of whatever we hand it.
        return host


def _normalise_path(path: str) -> str:
    if not path:
        return "/"
    # Percent-decode only unreserved characters, then re-encode consistently, so that
    # /caf%C3%A9 and /café produce one key.
    decoded = unquote(path)
    encoded = quote(decoded, safe="/~:@!$&'()*+,;=-._")
    # Collapse duplicate slashes and resolve dot segments without touching the host.
    parts: List[str] = []
    for seg in encoded.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    out = "/" + "/".join(parts)
    if encoded.endswith("/") and out != "/":
        out += "/"
    return out


def _normalise_query(query: str) -> str:
    if not query:
        return ""
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    if not pairs:
        return ""
    return urlencode(sorted(pairs), doseq=True)


def normalise(url: str, base: Optional[str] = None) -> NormalUrl:
    """Parse and canonicalise ``url``, resolving against ``base`` when relative.

    Raises :class:`UnsafeUrl` for anything outside the scheme/port allowlist or carrying
    embedded credentials. Shape validation only -- no DNS, no sockets.
    """
    if base:
        url = _join(base, url)
    parts = urlsplit(url.strip())

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrl(f"scheme {scheme or '(none)'!r} is not in {ALLOWED_SCHEMES}")

    userinfo = ""
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, netloc = netloc.rsplit("@", 1)
    if userinfo:
        # Threat T5. Refused rather than stripped: a URL carrying credentials is a
        # signal that someone is pointing the audit at an authenticated surface.
        raise UnsafeUrl("URL carries embedded credentials (userinfo component)")

    host_part, _, port_part = netloc.rpartition(":")
    if not host_part or "]" in port_part:  # no colon, or IPv6 without a port
        host_raw, port = netloc, None
    else:
        host_raw = host_part
        try:
            port = int(port_part)
        except ValueError:
            raise UnsafeUrl(f"malformed port in netloc {netloc!r}") from None

    host_raw = host_raw.strip("[]")
    host = _idna(host_raw)
    if not host:
        raise UnsafeUrl("URL has no host")

    if port is not None:
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        elif port not in ALLOWED_PORTS:
            raise UnsafeUrl(f"port {port} is not in {ALLOWED_PORTS}")

    return NormalUrl(
        scheme=scheme,
        host=host,
        port=port,
        path=_normalise_path(parts.path),
        query=_normalise_query(parts.query),
        raw_query=parts.query,
        userinfo="",
        fragment=parts.fragment,
    )


def _join(base: str, ref: str) -> str:
    """Resolve ``ref`` against ``base`` without urljoin's scheme-relative surprises."""
    from urllib.parse import urljoin
    return urljoin(base, ref)


def try_normalise(url: str, base: Optional[str] = None) -> Optional[NormalUrl]:
    """:func:`normalise` that returns ``None`` instead of raising.

    Used when walking links found on a page, where unsupported schemes (``mailto:``,
    ``tel:``, ``javascript:``) are ordinary and not worth an exception each.
    """
    try:
        return normalise(url, base=base)
    except (UnsafeUrl, ValueError):
        return None


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_like_opaque_token(value: str) -> bool:
    """True for the shape of a single-use action token.

    A long, high-entropy, *structureless* string in a URL is the signature of a magic
    link, a confirmation token, or a session id. Fetching one can consume it.

    The hard part is not catching tokens, it is *not* catching slugs. Entropy alone
    does not separate them: ``cart-abandonment-guide`` has a higher character entropy
    than many real tokens, because English words over 20 characters use a wide
    alphabet. Refusing it would make the audit silently skip ordinary content pages and
    under-report coverage -- a false positive in the safety layer, which is the worst
    place to have one.

    So structure is tested before randomness. Two or more hyphen/underscore-separated
    alphabetic words means human-authored text, whatever its entropy; a dated slug like
    ``2024-03-15-annual-report`` qualifies on its two word parts. Only strings with no
    word structure left are then judged on the markers of machine-generated opacity:
    a hex digest, mixed case with digits, digit dominance, base64 padding, or one
    unbroken run of 20+ characters.
    """
    if len(value) < 20:
        return False
    if not _HIGH_ENTROPY_RE.match(value):
        return False
    if _HEX_RE.match(value):
        return True

    parts = [p for p in re.split(r"[-_.]+", value) if p]
    # Human-authored text: two or more real alphabetic words.
    wordlike = [p for p in parts if p.isalpha() and 2 <= len(p) <= 20]
    if len(wordlike) >= 2:
        return False
    # A single long lowercase word is a slug too (e.g. /internationalisation).
    if len(parts) == 1 and parts[0].isalpha() and parts[0].islower():
        return False

    if _shannon_entropy(value) < 3.2:
        return False

    digits = sum(1 for c in value if c.isdigit())
    digit_ratio = digits / len(value)
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    longest_run = max(len(p) for p in parts)

    if has_upper and has_lower and digits:
        return True          # aZ9kQ2mN7pL4xR8vT1yU
    if digit_ratio >= 0.25:
        return True          # UUIDs, numeric ids, hex-ish mixes
    if any(c in "=+/" for c in value):
        return True          # base64url payloads
    return longest_run >= 20


def is_mutating_get(u: NormalUrl) -> Tuple[bool, str]:
    """Threat T2 gate. Returns ``(refuse, reason)``.

    ``reason`` is empty when the URL is safe to fetch. When it is not, the caller must
    not fetch -- and should record the skip in ``coverage.paths_skipped`` so the gap is
    visible in the report rather than silent.
    """
    segments = [unquote(s).lower() for s in u.path.split("/") if s]
    for seg in segments:
        if seg in MUTATING_PATH_SEGMENTS:
            return True, f"path segment {seg!r} is on the mutating-GET denylist"

    if u.raw_query:
        for key, value in parse_qsl(u.raw_query, keep_blank_values=True):
            k = key.lower()
            v = value.strip().lower()
            if k in MUTATING_QUERY_KEYS:
                return True, f"query parameter {key!r} indicates a state-changing link"
            allowed = MUTATING_QUERY_VALUES.get(k)
            if allowed and v in allowed:
                return True, f"query parameter {key}={value!r} names a state-changing action"
            if looks_like_opaque_token(value):
                return True, (f"query parameter {key!r} carries a high-entropy opaque "
                              f"token, the signature of a single-use action link")

    for seg in segments:
        if looks_like_opaque_token(seg):
            return True, ("path contains a high-entropy opaque token, the signature of "
                          "a single-use action link")
    return False, ""


def path_classes(u: NormalUrl) -> Tuple[str, ...]:
    """All path classes matching ``u``, utility classes first.

    Content checks consult this for rule R4. A URL may match more than one class
    (``/blog/page/2`` is both ``article`` and paginated); callers decide precedence.
    """
    p = u.path.lower()
    found: List[str] = []
    for name, pattern in UTILITY_PATH_PATTERNS:
        if pattern.search(p):
            found.append(name)
    for name, pattern in CONTENT_PATH_PATTERNS:
        if pattern.search(p):
            found.append(name)
    if DATED_PATH_RE.search(p) and "article" not in found:
        found.append("article")
    if PAGINATION_RE.search(p) or PAGINATION_RE.search("?" + u.raw_query.lower()):
        found.append("paginated")
    return tuple(dict.fromkeys(found))


UTILITY_CLASSES = frozenset(name for name, _ in UTILITY_PATH_PATTERNS)


def is_utility(u: NormalUrl) -> bool:
    """True when the URL is a utility surface rather than brand content.

    Rule R4: content checks are silent here by default, because ``noindex`` on a search
    results page, a thin login form, and an undated legal page are all correct.
    """
    classes = set(path_classes(u))
    if not classes & UTILITY_CLASSES:
        return False
    # A page can look like both; content wins so that /about/privacy-by-design is not
    # dismissed as legal boilerplate.
    content_classes = {name for name, _ in CONTENT_PATH_PATTERNS} - {"home"}
    return not (classes & content_classes)


def is_document(u: NormalUrl) -> bool:
    """True when the URL plausibly serves an HTML document."""
    tail = u.path.rsplit("/", 1)[-1]
    if "." not in tail:
        return True
    ext = tail[tail.rfind("."):].lower()
    if ext in NON_DOCUMENT_EXTENSIONS or ext == ".pdf":
        return False
    return ext in ("", ".html", ".htm", ".xhtml", ".php", ".asp", ".aspx", ".jsp",
                   ".shtml", ".cfm", ".do", ".action")


def extension(u: NormalUrl) -> str:
    tail = u.path.rsplit("/", 1)[-1]
    return tail[tail.rfind("."):].lower() if "." in tail else ""


def depth(u: NormalUrl) -> int:
    """Path depth. ``/`` is 0, ``/a`` is 1, ``/a/b`` is 2."""
    return len([s for s in u.path.split("/") if s])


def registrable_hint(host: str) -> str:
    """Best-effort registrable-domain hint, with no Public Suffix List dependency.

    ``tldextract`` downloads the PSL on first use, which would make audit behaviour
    depend on a remote file and break the determinism guarantee (see README, Determinism).
    Same-site comparison uses :func:`same_site` instead; this function exists only for
    display and for brand-name comparison, where being one label off is harmless.
    """
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    # Two-level public suffixes we may meet: co.uk, com.au, co.jp, com.br...
    if len(labels[-1]) == 2 and labels[-2] in (
        "co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "gr", "sch", "mil",
    ):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_site(a: str, b: str) -> bool:
    """True when two hosts belong to the same site for link-graph purposes.

    Deliberately generous: ``www`` and other single-label subdomains of a shared parent
    count as the same site, which is what matters for internal-link and orphan analysis.
    """
    a, b = a.lower().rstrip("."), b.lower().rstrip(".")
    if a == b:
        return True
    return registrable_hint(a) == registrable_hint(b)


def host_variants(u: NormalUrl) -> List[str]:
    """Candidate duplicate-host URLs for G0.HTTP.MIXED_CANONICAL_HOST.

    Returns the http/https and www/non-www permutations of the site root, which is the
    smallest probe set that can demonstrate an unresolved duplicate-host condition.
    """
    bare = u.host[4:] if u.host.startswith("www.") else u.host
    hosts = [bare, f"www.{bare}"]
    out: List[str] = []
    for scheme in ("https", "http"):
        for host in hosts:
            candidate = f"{scheme}://{host}/"
            if candidate not in out:
                out.append(candidate)
    return out


def dedupe_key(u: NormalUrl) -> str:
    """The ledger key. Case-folded host, canonical path, tracking-free sorted query."""
    return u.url


def strip_index(u: NormalUrl) -> str:
    """Dedupe key with a trailing ``index.*`` and trailing slash removed.

    ``/about/``, ``/about``, and ``/about/index.html`` are one page on the great
    majority of servers; treating them as three inflates duplicate findings.
    """
    path = u.path
    tail = path.rsplit("/", 1)[-1]
    if tail.startswith("index.") or tail in ("index", "default", "default.html",
                                             "default.htm"):
        path = path[: -len(tail)]
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((u.scheme, u.netloc, path or "/", u.query, ""))


def summarise_denylist() -> Dict[str, Iterable[str]]:
    """Machine-readable denylist summary, embedded in the report's methodology block."""
    return {
        "path_segments": sorted(MUTATING_PATH_SEGMENTS),
        "query_keys": sorted(MUTATING_QUERY_KEYS),
        "query_actions": sorted(
            f"{k}={v}" for k, vs in MUTATING_QUERY_VALUES.items() for v in sorted(vs)
        ),
        "opaque_token_rule": "length >= 20 and Shannon entropy >= 3.5 bits/char",
    }
