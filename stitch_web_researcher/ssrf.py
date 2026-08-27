"""
SSRF guard for LLM-supplied URLs (S1, CODE_REVIEW_2026-08-27).

The agent is pointed at arbitrary URLs by content it reads. Without a
network policy, a malicious page can instruct the agent to fetch:

- cloud metadata endpoints (``169.254.169.254``, ``fd00:ec2::254``)
  → credential theft,
- loopback services (``127.0.0.1``, admin panels),
- RFC1918 / ULA hosts (intranet scanners, internal APIs),
- internal DNS names (``*.local``, ``*.internal``).

Every fetch entry point that accepts an LLM-supplied URL must call
``validate_public_url`` first. The Rust core enforces the same policy on
every request and redirect hop (defense in depth, see ``src/lib.rs``).

Known limitation: DNS is resolved here and resolved again by the HTTP
client; a hostile DNS server could rebind between the two lookups. The
practical SSRF vectors (direct metadata URLs, CNAME chains, redirects)
are all caught.

Escape hatch: set ``STITCH_WEB_RESEARCHER_ALLOW_PRIVATE`` to a truthy
value to skip validation entirely. The environment is under operator
control, not the LLM's — intended for developers and tests that need to
hit local servers.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Operator-controlled bypass (see module docstring).
_ENV_ALLOW_PRIVATE = "STITCH_WEB_RESEARCHER_ALLOW_PRIVATE"

# Hostnames that are never public, regardless of what DNS says.
_INTERNAL_SUFFIXES = (".local", ".internal", ".localhost")


class SsrfBlockedError(ValueError):
    """Raised when a URL targets a non-public (SSRF) destination."""


def _allow_private() -> bool:
    return (
        os.environ.get(_ENV_ALLOW_PRIVATE, "").strip().lower()
        in ("1", "true", "yes", "on")
    )


def _ip_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that must never be fetched."""
    return (
        ip.is_unspecified      # 0.0.0.0 / ::
        or ip.is_loopback      # 127.0.0.0/8 / ::1
        or ip.is_link_local    # 169.254.0.0/16 (cloud metadata) / fe80::/10
        or ip.is_private       # RFC1918 / ULA fc00::/7 (incl. fd00:ec2::254)
        or ip.is_reserved      # 240.0.0.0/4 and friends
    )


def _check_ip(host: str, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if _ip_disallowed(ip):
        raise SsrfBlockedError(
            f"Host {host!r} ({ip}) is not a public address"
        )


def validate_public_url(url: str) -> None:
    """Raise ``SsrfBlockedError`` if ``url`` would reach a non-public host.

    Only ``http``/``https`` URLs are accepted. IP literals are checked
    directly; domain names are resolved and *every* returned address is
    checked (a CNAME chain that lands in a private range is blocked).

    This is a policy check, not a full validation — callers keep their
    own scheme/format checks where they have stricter requirements.
    """
    if _allow_private():
        return

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SsrfBlockedError(
            f"URL scheme {scheme!r} is not allowed for fetching"
        )

    host = parsed.hostname
    if not host:
        raise SsrfBlockedError(f"URL has no host: {url}")

    host_l = host.lower().rstrip(".")

    if host_l == "localhost" or host_l.endswith(_INTERNAL_SUFFIXES):
        raise SsrfBlockedError(f"Host {host!r} is an internal name")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as e:
        raise SsrfBlockedError(f"URL has an invalid port: {url}") from e

    # IP literal — check directly, no DNS involved.
    try:
        literal = ipaddress.ip_address(host_l)
    except ValueError:
        literal = None

    if literal is not None:
        _check_ip(host, literal)
        return

    # Domain — resolve and check every address the name points at.
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SsrfBlockedError(
            f"DNS resolution failed for {host!r}: {e}"
        ) from None

    if not infos:
        raise SsrfBlockedError(f"DNS returned no addresses for {host!r}")

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        _check_ip(host, ip)
