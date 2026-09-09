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

Escape hatch: set ``GOSSAMER_WEB_RESEARCHER_ALLOW_PRIVATE`` to a truthy
value to skip validation entirely. The environment is under operator
control, not the LLM's — intended for developers and tests that need to
hit local servers.
"""

from __future__ import annotations


from gossamer import _core as _rust

# Operator-controlled bypass (see module docstring). Renamed with the package;
# the legacy STITCH_* spelling still works.
_ENV_ALLOW_PRIVATE = "GOSSAMER_ALLOW_PRIVATE"
_ENV_ALLOW_PRIVATE_LEGACY = "STITCH_WEB_RESEARCHER_ALLOW_PRIVATE"

# Hostname suffixes that are never public — documented here because the
# policy lives in Rust now (src/ssrf.rs keeps its own copy).
_INTERNAL_SUFFIXES = (".local", ".internal", ".localhost")


class SsrfBlockedError(ValueError):
    """Raised when a URL targets a non-public (SSRF) destination."""


def _allow_private() -> bool:
    from gossamer.env import getenv_bool

    return getenv_bool(
        _ENV_ALLOW_PRIVATE, False, legacy=_ENV_ALLOW_PRIVATE_LEGACY
    )


def validate_public_url(url: str) -> None:
    """Raise ``SsrfBlockedError`` if ``url`` would reach a non-public host.

    Only ``http``/``https`` URLs are accepted. IP literals are checked
    directly; domain names are resolved and *every* returned address is
    checked (a CNAME chain that lands in a private range is blocked).

    This is a policy check, not a full validation — callers keep their
    own scheme/format checks where they have stricter requirements.

    The check itself runs in Rust (``src/ssrf.rs`` — IANA tables probed
    from CPython's ``ipaddress``; parity pinned by
    ``tests/test_rust_parity_ssrf.py``); this wrapper only maps the
    error into :class:`SsrfBlockedError`.
    """
    if _allow_private():
        return
    try:
        _rust.ssrf_check_url(url, False)
    except ValueError as e:
        raise SsrfBlockedError(str(e)) from None
