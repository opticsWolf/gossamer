"""
robots.txt compliance for the web researcher (S4).

An unattended researcher that fetches whatever the model names should
behave like a polite crawler: fetch and cache ``/robots.txt`` per host,
honor ``Disallow``/``Allow`` for its user agent, and apply the site's
``Crawl-delay`` in the per-domain rate limiter.

Design notes
------------
- This module is a *leaf*: it talks to the network with a plain ``httpx``
  client and never routes through the toolbox fetch pipeline, so a robots
  check can never trigger another robots check (no recursion).
- Callers are expected to have validated the URL already (the S1 SSRF
  guard); the robots fetch targets the same host as the requested URL.
- Failure semantics follow common crawler practice: a 404 robots.txt or
  any fetch/parse error means *allowed* (cached with a short TTL so a
  broken host is not re-probed on every request). A 401/403 response
  means *disallow all* (the site refuses to publish its rules).
- Thread-safe (S5): one lock guards the per-host cache and the in-flight
  set, so concurrent requests for the same host probe robots.txt once.
  A host that is currently being probed but has no cached state yet is
  treated as allowed for that one call (the next call sees the result).
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 3600
FAILURE_TTL_SECONDS = 5 * 60
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_ROBOTS_BYTES = 512 * 1024


@dataclass
class _Rule:
    """One ``Allow``/``Disallow`` line from the applicable UA group."""

    path: str  # raw path as written in robots.txt
    regex: "re.Pattern[str]"
    allow: bool  # True = Allow, False = Disallow


@dataclass
class _HostState:
    """Cached robots.txt state for one host (scheme+host+port)."""

    rules: list = field(default_factory=list)
    crawl_delay: Optional[float] = None
    disallow_all: bool = False
    fetched_at: float = 0.0
    ttl: float = DEFAULT_TTL_SECONDS


# A host with no cached state that is currently being probed: allow.
_UNKNOWN_ALLOWED = _HostState(fetched_at=0.0, ttl=0.0)


def _url_path(url: str) -> str:
    """Path (+ query string) that robots.txt rules are matched against."""
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return path


def _path_regex(path: str) -> "re.Pattern[str]":
    """Compile a robots.txt path rule.

    Supports the two spec extensions: ``*`` matches any run of characters
    and a trailing ``$`` anchors the end of the path.
    """
    anchored = path.endswith("$")
    if anchored:
        path = path[:-1]
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in path)
    if anchored:
        return re.compile(f"^{pattern}$")
    return re.compile(f"^{pattern}")


def _parse_robots(
    text: str, user_agent: str
) -> tuple[list, Optional[float]]:
    """Parse robots.txt into ``(rules, crawl_delay)`` for one UA.

    Group selection follows the common convention: an exact user-agent
    match beats a substring match, which beats the ``*`` group. Within
    the chosen group, the *longest* matching path wins and a tie goes to
    ``Allow`` (per the Google robots.txt specification). ``Crawl-delay``
    falls back to the ``*`` group when the chosen group has none.
    """
    groups: list[dict] = []
    current: Optional[dict] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()
        if directive == "user-agent":
            current = {"agents": [value], "rules": [], "crawl_delay": None}
            groups.append(current)
            continue
        if current is None:
            # Rules before any User-agent line: implicit * group.
            current = {"agents": ["*"], "rules": [], "crawl_delay": None}
            groups.append(current)
        if directive in ("disallow", "allow"):
            if value:  # an empty "Disallow:" is a no-op
                current["rules"].append((directive == "allow", value))
        elif directive in ("crawl-delay", "crawl delay", "crawler-delay"):
            try:
                current["crawl_delay"] = max(0.0, float(value))
            except ValueError:
                pass

    if not groups:
        return [], None

    ua = user_agent.lower()

    def _score(group: dict) -> int:
        for agent in group["agents"]:
            a = agent.strip().lower()
            if a == ua:
                return 3
            if a in ua:
                return 2
        return 0

    best_score = 0
    chosen: Optional[dict] = None
    for group in groups:
        score = _score(group)
        if score > best_score:
            best_score, chosen = score, group
    if chosen is None:
        for group in groups:
            if any(a.strip().lower() == "*" for a in group["agents"]):
                chosen = group
                break
    if chosen is None:
        return [], None

    rules = [
        _Rule(path=path, regex=_path_regex(path), allow=allow)
        for allow, path in chosen["rules"]
    ]
    delay = chosen["crawl_delay"]
    if delay is None:
        for group in groups:
            if (
                group["crawl_delay"] is not None
                and any(a.strip().lower() == "*" for a in group["agents"])
            ):
                delay = group["crawl_delay"]
                break
    return rules, delay


class RobotsChecker:
    """Per-host robots.txt fetch + cache with Disallow/Allow/Crawl-delay.

    Parameters
    ----------
    enabled:
        When False, ``is_allowed`` is always True and ``crawl_delay``
        always None (explicit opt-out, S4).
    user_agent:
        The UA string matched against ``User-agent`` groups.
    client:
        Injectable httpx-compatible client (tests). When None, a short-
        lived client is created per probe.
    """

    def __init__(
        self,
        enabled: bool = True,
        user_agent: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        ttl: float = DEFAULT_TTL_SECONDS,
        failure_ttl: float = FAILURE_TTL_SECONDS,
        max_bytes: int = MAX_ROBOTS_BYTES,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.enabled = enabled
        self.user_agent = user_agent or "stitch-web-researcher/1.0"
        self.timeout = timeout
        self.ttl = ttl
        self.failure_ttl = failure_ttl
        self.max_bytes = max_bytes
        self._client = client
        self._lock = threading.Lock()
        self._hosts: dict[str, _HostState] = {}
        self._in_flight: set[str] = set()

    # ── Public API ─────────────────────────────────────────

    def is_allowed(self, url: str) -> bool:
        """True if the host's robots.txt permits fetching ``url``."""
        if not self.enabled:
            return True
        state = self._state_for(url)
        if state.disallow_all:
            return False
        path = _url_path(url)
        best: Optional[_Rule] = None
        for rule in state.rules:
            if rule.regex.match(path):
                if best is None:
                    best = rule
                elif len(rule.path) > len(best.path):
                    best = rule
                elif len(rule.path) == len(best.path) and rule.allow and not best.allow:
                    best = rule  # tie -> Allow wins
        return best is None or best.allow

    def crawl_delay(self, url: str) -> Optional[float]:
        """The host's requested Crawl-delay (seconds), or None."""
        if not self.enabled:
            return None
        return self._state_for(url).crawl_delay

    def clear(self) -> None:
        """Drop all cached host states (next use re-probes)."""
        with self._lock:
            self._hosts.clear()

    # ── Internals ──────────────────────────────────────────

    def _state_for(self, url: str) -> _HostState:
        parts = urlsplit(url)
        host = (parts.netloc or "").lower()
        now = time.monotonic()
        with self._lock:
            state = self._hosts.get(host)
            if state is not None and (now - state.fetched_at) < state.ttl:
                return state
            if host in self._in_flight:
                # Another thread is probing; don't serialize on it.
                return state if state is not None else _UNKNOWN_ALLOWED
            self._in_flight.add(host)
        try:
            fresh = self._fetch_state(parts)
        finally:
            with self._lock:
                self._in_flight.discard(host)
        with self._lock:
            self._hosts[host] = fresh
        return fresh

    def _fetch_state(self, parts) -> _HostState:
        robots_url = f"{parts.scheme or 'http'}://{parts.netloc}/robots.txt"
        # Failure default: allowed, short TTL.
        state = _HostState(fetched_at=time.monotonic(), ttl=self.failure_ttl)
        try:
            own_client = self._client is None
            client = self._client if self._client is not None else httpx.Client(
                timeout=self.timeout
            )
            try:
                response = client.get(
                    robots_url, headers={"User-Agent": self.user_agent}
                )
            finally:
                if own_client:
                    client.close()
            if response.status_code in (401, 403):
                # Site refuses to publish rules: treat as disallow-all.
                state.disallow_all = True
                state.ttl = self.ttl
                return state
            if response.status_code >= 400:
                # 404 (no robots.txt) is confidently allow-all; other
                # server errors stay on the short failure TTL.
                if response.status_code == 404:
                    state.ttl = self.ttl
                return state
            body = response.content[: self.max_bytes].decode("utf-8", "replace")
            state.rules, state.crawl_delay = _parse_robots(body, self.user_agent)
            state.ttl = self.ttl
        except Exception:
            logger.debug(
                "robots.txt fetch failed for %s (treating as allowed)",
                robots_url,
                exc_info=True,
            )
        return state
