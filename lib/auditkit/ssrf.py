"""SSRF defence: resolve-then-connect with a pinned IP (threat T4).

The audit takes a URL from whoever runs it and fetches it. That makes it a
server-side-request-forgery primitive unless the destination is validated, and
validating the *hostname* is not enough:

* ``http://localhost/``, ``http://169.254.169.254/`` (cloud instance metadata) and
  internal hostnames resolve to addresses that must never be reached.
* A hostname can resolve to a private address while looking public
  (``127.0.0.1.nip.io``), so only the resolved address can be trusted.
* Between "check the address" and "open the connection" a DNS answer can change --
  the classic DNS-rebinding TOCTOU window. Closing it requires connecting to the
  address that was actually validated, not re-resolving the name.
* A public URL can *redirect* to a private one, so every hop needs the same treatment.

The contract this module provides:

    ip = ssrf.resolve_and_validate(host)   # every resolved address must pass
    ...connect to ip, passing host only as TLS SNI / Host header...

:func:`resolve_and_validate` fails closed. If *any* address a hostname resolves to is
disallowed, the whole host is refused -- a host with one public and one loopback
address is exactly what a rebinding attack looks like.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import List, Sequence, Tuple

# Networks that must never be contacted. Each entry names why, because a reviewer
# should be able to check the reasoning rather than trust the list.
_DENIED_V4: Tuple[Tuple[str, str], ...] = (
    ("0.0.0.0/8", "this host, unspecified"),
    ("10.0.0.0/8", "RFC 1918 private"),
    ("100.64.0.0/10", "RFC 6598 carrier-grade NAT"),
    ("127.0.0.0/8", "loopback"),
    ("169.254.0.0/16", "link-local, incl. cloud instance metadata at 169.254.169.254"),
    ("172.16.0.0/12", "RFC 1918 private"),
    ("192.0.0.0/24", "IETF protocol assignments"),
    ("192.0.2.0/24", "TEST-NET-1 documentation"),
    ("192.88.99.0/24", "6to4 relay anycast, deprecated"),
    ("192.168.0.0/16", "RFC 1918 private"),
    ("198.18.0.0/15", "benchmarking"),
    ("198.51.100.0/24", "TEST-NET-2 documentation"),
    ("203.0.113.0/24", "TEST-NET-3 documentation"),
    ("224.0.0.0/4", "multicast"),
    ("240.0.0.0/4", "reserved"),
    ("255.255.255.255/32", "broadcast"),
)

_DENIED_V6: Tuple[Tuple[str, str], ...] = (
    ("::/128", "unspecified"),
    ("::1/128", "loopback"),
    ("::ffff:0:0/96", "IPv4-mapped -- validated separately as IPv4"),
    ("64:ff9b::/96", "NAT64 well-known prefix"),
    ("100::/64", "discard-only"),
    ("2001:2::/48", "benchmarking"),
    ("2001:db8::/32", "documentation"),
    ("fc00::/7", "unique local addresses"),
    ("fe80::/10", "link-local"),
    ("ff00::/8", "multicast"),
)

DENIED_NETWORKS: List[Tuple[ipaddress._BaseNetwork, str]] = [
    (ipaddress.ip_network(cidr), why) for cidr, why in _DENIED_V4 + _DENIED_V6
]

# Cloud metadata endpoints worth naming explicitly in an error message, because seeing
# the name is how an operator realises what was just attempted.
KNOWN_METADATA_ADDRESSES = {
    "169.254.169.254": "cloud instance metadata (AWS/GCP/Azure/DO)",
    "100.100.100.200": "Alibaba Cloud instance metadata",
    "192.0.0.192": "Oracle Cloud instance metadata",
    "fd00:ec2::254": "AWS IMDSv6 instance metadata",
}


class SSRFDenied(ValueError):
    """Raised when a destination fails address validation. Never retried."""


@dataclass(frozen=True)
class ResolvedTarget:
    """A validated destination, pinned to the address that passed validation."""

    host: str                 # the original hostname, used for SNI and the Host header
    addresses: Tuple[str, ...]  # every address that resolved, all validated
    family: int               # socket.AF_INET or socket.AF_INET6 for the pinned address
    ip: str                   # the address to connect to

    @property
    def is_literal(self) -> bool:
        try:
            ipaddress.ip_address(self.host.strip("[]"))
            return True
        except ValueError:
            return False


def classify(addr: str) -> Tuple[bool, str]:
    """Return ``(allowed, reason)`` for a single IP address.

    ``reason`` names the denying network when the address is refused, and is empty when
    it is allowed. Pure function: no DNS, no sockets, trivially unit-testable.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False, f"{addr!r} is not a valid IP address"

    if addr in KNOWN_METADATA_ADDRESSES:
        return False, f"{addr} is {KNOWN_METADATA_ADDRESSES[addr]}"

    # Unwrap IPv4-mapped IPv6 (::ffff:127.0.0.1) so it cannot smuggle a private v4
    # address past the v4 rules.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        mapped = str(ip.ipv4_mapped)
        allowed, reason = classify(mapped)
        if not allowed:
            return False, f"IPv4-mapped address {addr} -> {reason}"
        return True, ""

    # Unwrap 6to4 (2002::/16) and Teredo (2001::/32), which also embed v4 addresses.
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.sixtofour is not None:
            allowed, reason = classify(str(ip.sixtofour))
            if not allowed:
                return False, f"6to4 address {addr} -> {reason}"
        if ip.teredo is not None:
            allowed, reason = classify(str(ip.teredo[1]))
            if not allowed:
                return False, f"Teredo address {addr} -> {reason}"

    for network, why in DENIED_NETWORKS:
        if ip.version == network.version and ip in network:
            # The IPv4-mapped range is handled above; reaching it here means a bare
            # ::ffff:0:0/96 address with no embedded v4, which is not routable anyway.
            return False, f"{addr} is in {network} ({why})"

    # Belt and braces: the stdlib's own classification catches anything the explicit
    # table misses in a future Python.
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast \
            or ip.is_reserved or ip.is_unspecified:
        return False, f"{addr} is classified non-global by the standard library"

    return True, ""


def resolve(host: str, port: int = 443) -> List[Tuple[int, str]]:
    """Resolve ``host`` to ``(family, address)`` pairs.

    Raises :class:`SSRFDenied` when the name does not resolve, so callers have a single
    exception type to handle for "cannot be reached safely".
    """
    bare = host.strip("[]")
    try:
        ipaddress.ip_address(bare)
    except ValueError:
        pass
    else:
        family = socket.AF_INET6 if ":" in bare else socket.AF_INET
        return [(family, bare)]

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFDenied(f"host {host!r} does not resolve: {exc.strerror or exc}") from None

    seen: List[Tuple[int, str]] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        addr = sockaddr[0]
        if (family, addr) not in seen:
            seen.append((family, addr))
    if not seen:
        raise SSRFDenied(f"host {host!r} resolved to no usable IPv4/IPv6 address")
    return seen


def resolve_and_validate(host: str, port: int = 443,
                         prefer_v4: bool = True) -> ResolvedTarget:
    """Resolve ``host`` and refuse it unless **every** resolved address is allowed.

    Fails closed on purpose. A hostname answering with both a public and a private
    address is the signature of a rebinding attempt, and there is no legitimate reason
    to audit such a host.

    The returned :class:`ResolvedTarget` pins one address. Connecting to that pinned
    address -- rather than passing the hostname to the socket layer again -- is what
    closes the TOCTOU window between validation and connection.
    """
    resolved = resolve(host, port)

    for family, addr in resolved:
        allowed, reason = classify(addr)
        if not allowed:
            raise SSRFDenied(
                f"refusing to connect to {host!r}: it resolves to {reason}. "
                f"This audit only fetches publicly routable addresses."
            )

    # Prefer IPv4 by default: dual-stack hosts where only v6 is broken are common, and
    # a failed connection costs budget we would rather spend on pages.
    ordered = sorted(
        resolved,
        key=lambda fa: (0 if (fa[0] == socket.AF_INET) == prefer_v4 else 1, fa[1]),
    )
    family, ip = ordered[0]
    return ResolvedTarget(
        host=host,
        addresses=tuple(addr for _f, addr in resolved),
        family=family,
        ip=ip,
    )


def validate_redirect_target(host: str, port: int = 443) -> ResolvedTarget:
    """Re-validate a redirect hop.

    A separate name for the same operation, so the call site in ``netgate`` reads as the
    deliberate re-validation it is. Following redirects automatically would skip this
    and leave an internal-redirect hole: a public URL answering
    ``302 Location: http://169.254.169.254/`` would be followed without a second check.
    """
    return resolve_and_validate(host, port)


def describe_policy() -> Sequence[str]:
    """Human-readable policy lines, embedded in the report's methodology block."""
    return tuple(
        f"deny {network} ({why})" for network, why in DENIED_NETWORKS
    ) + (
        "resolve-then-connect: the validated address is pinned for the connection",
        "fail closed: a host is refused if any of its addresses is disallowed",
        "every redirect hop is re-validated before it is followed",
    )
