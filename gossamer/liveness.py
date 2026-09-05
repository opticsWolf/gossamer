"""Source liveness probing (Workstream 2).

``check_liveness`` reports whether a URL is reachable WITHOUT pulling the
full page -- a lightweight status probe for the ``check_sources`` tool and
for surfacing ``dropped_dupes`` / source health in research results.

Design:

* The SSRF policy is enforced up front via ``validate_public_url`` (S1),
  reusing the same guard every other fetch path uses.
* Politeness is an *injectable* throttle (default: none) so the toolbox can
  pass ``_rate_limit_domain`` to avoid hammering a host, while tests stay
  offline.
* The network call is an *injectable* ``request_fn`` (default: a real
  ``httpx`` HEAD/GET). ``httpx`` is the library the rest of the package
  already uses -- no new HTTP dependency -- and injecting it keeps the
  function fully testable offline.

The function never raises for a probe outcome: every result -- reachable,
unreachable, blocked, error -- comes back as a status dict so callers can
render it instead of branching on exceptions.
"""

from __future__ import annotations

from typing import Callable, Optional

import httpx

from gossamer.ssrf import SsrfBlockedError, validate_public_url

__all__ = ["check_liveness", "LIVENESS_TIMEOUT"]

# Default per-URL probe timeout (seconds). A liveness check must be quick;
# a slow host is reported as a probe error, not left hanging a whole batch.
LIVENESS_TIMEOUT: float = 10.0

# A permissive UA so status probes are not rejected by bot filters.
_UA = {
    "User-Agent": (
        "gossamer-liveness/0.1 "
        "(+https://github.com/opticsWolf/gossamer)"
    )
}

# A request_fn maps (url, timeout) -> (http_status_or_None, error_or_None).
RequestFn = Callable[[str, float], "tuple[Optional[int], Optional[str]]"]


def _http_probe(
    url: str, timeout: float, method: str = "head"
) -> "tuple[Optional[int], Optional[str]]":
    """Probe *url* over httpx: HEAD (default) with a GET fallback, or GET
    outright when *method* is ``"get"`` (``check_sources(mode="content")``
    — same status contract, for hosts that reject HEAD)."""
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            if method == "get":
                resp = client.get(url, headers=_UA)
            else:
                try:
                    resp = client.head(url, headers=_UA)
                except httpx.NotSupportedError:
                    resp = client.get(url, headers=_UA)
            return resp.status_code, None
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def check_liveness(
    url: str,
    timeout: float = LIVENESS_TIMEOUT,
    *,
    validator: Optional[Callable[[str], None]] = None,
    throttle: Optional[Callable[[str], None]] = None,
    method: str = "head",
    request_fn: Optional[RequestFn] = None,
) -> dict:
    """Probe *url* for reachability. Returns a status dict, never raises.

    Parameters
    ----------
    url:
        The URL to probe.
    timeout:
        Per-probe timeout in seconds.
    validator:
        Optional ``validator(url) -> None`` that raises on a non-public
        destination. Defaults to :func:`gossamer.ssrf.
        validate_public_url`.
    throttle:
        Optional ``throttle(url) -> None`` politeness hook (e.g. the
        toolbox's per-domain rate limiter). Called before the probe; a
        failure here is swallowed so politeness never aborts a probe.
    method:
        ``"head"`` (default) or ``"get"``. Only affects the default
        ``request_fn``; an injected probe decides for itself.
    request_fn:
        Optional ``(url, timeout) -> (http_status|None, error|None)``.
        Defaults to an ``httpx`` HEAD/GET probe; inject a fake in tests.

    Returns
    -------
    dict
        ``{"url", "status", "alive", "http_status"}`` plus ``error`` when
        the probe could not determine reachability. ``status`` is one of
        ``"ok"`` (2xx/3xx), ``"unreachable"`` (4xx/5xx), ``"blocked"``
        (SSRF / validation), or ``"error"`` (network / exception).
    """
    # 1) SSRF guard -- reject non-public destinations before any socket.
    try:
        (validator or validate_public_url)(url)
    except Exception as exc:  # SSRF block or a custom validator's rejection
        return {
            "url": url,
            "status": "blocked",
            "alive": False,
            "http_status": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    # 2) Politeness -- never let a throttle error abort the probe.
    if throttle is not None:
        try:
            throttle(url)
        except Exception:  # noqa: BLE001 -- politeness must not break liveness
            pass

    # 3) Probe.
    if request_fn is not None:
        fn: RequestFn = request_fn
    else:
        probe_method = method if method == "get" else "head"

        def fn(u: str, t: float):
            return _http_probe(u, t, method=probe_method)

    http_status, error = fn(url, timeout)
    if error is not None:
        return {
            "url": url,
            "status": "error",
            "alive": False,
            "http_status": http_status,
            "error": error,
        }
    alive = http_status is not None and 200 <= http_status < 400
    return {
        "url": url,
        "status": "ok" if alive else "unreachable",
        "alive": alive,
        "http_status": http_status,
    }
