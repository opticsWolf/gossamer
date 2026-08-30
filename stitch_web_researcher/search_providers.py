"""
Search provider interface and implementations.

Abstracts web search behind a common `SearchProvider` protocol so the
`WebResearcherToolbox` can plug in DuckDuckGo, Google, Bing, Exa, or any
custom provider at runtime.
"""

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

import httpx

logger = logging.getLogger(__name__)


class QuotaExhaustedError(RuntimeError):
    """Raised when an adapter's hard per-window quota has been reached.

    The retry decorator treats this as fatal (never retried — the window
    will not reset during backoff) and the harness treats it as a skip
    signal so it can fail over to another adapter instead of hammering an
    exhausted key or hitting a 429.
    """


@dataclass
class RateState:
    """Snapshot of an adapter's live politeness budget after a response.

    Populated by :meth:`ResourceAdapter.parse_headers` from server rate-limit
    headers (``X-RateLimit-*`` / ``Retry-After``) so the tool surface can
    report a per-provider ``budget`` to the harness.
    """

    rps: Optional[float] = None
    remaining: Optional[int] = None
    reset_seconds: Optional[int] = None
    retry_after: Optional[float] = None
    exhausted: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "rps_limit": self.rps,
            "remaining": self.remaining,
            "reset_seconds": self.reset_seconds,
            "retry_after": self.retry_after,
            "exhausted": self.exhausted,
        }


# ────────────────────────────────────────────────────────────────
# 1. Unified Adapter Interface
# ────────────────────────────────────────────────────────────────

@dataclass
class RateLimit:
    """Per-adapter politeness + quota policy.

    Attributes:
        search_interval: Minimum seconds between calls to this adapter
            (queries to the engine itself).
        fetch_interval: Minimum seconds between content downloads suggested
            for sessions using this adapter. Consumed by
            ``WebResearcherToolbox`` as the default politeness delay when
            fetching pages found through this adapter.
        jitter: Maximum random seconds added to ``search_interval`` to
            desynchronize access. ``0`` disables jitter.
        quota: Hard cap on calls per ``quota_window``. ``None`` means
            unlimited; once reached the next call raises
            :class:`QuotaExhaustedError` instead of a 429 / billing overage.
        quota_window: Granularity of the quota window — ``"day"`` (UTC
            calendar day) or ``"month"`` (UTC calendar month).
    """

    search_interval: float = 1.0
    fetch_interval: float = 0.5
    jitter: float = 0.0
    quota: Optional[int] = None
    quota_window: Literal["day", "month"] = "day"


# Per-provider politeness + quota defaults. Each legacy search engine falls
# back to its own constant when constructed without an explicit ``delay`` /
# ``RateLimit`` (the callers below pass the constant in when ``delay is None``).
# They encode the engines' real limits and keep access desynchronized with
# jitter:
#   - DuckDuckGo: no official API; ``ddgs`` scrapes the HTML endpoint, which
#     answers with HTTP 202 "Ratelimit". Politer than the 1.0 s base default,
#     with jitter so shared-IP clients don't collide.
#   - Google: 100 free queries/day, then paid. A hard quota turns an overage
#     into a clean skip instead of a billing surprise.
#   - Bing: S0 allows 5 req/s; 0.2 s keeps us compliant with margin.
#   - Exa: ``/search`` allows 10 QPS and a 1,000 req/month free tier.
_DUCKDUCKGO_RATE_LIMIT = RateLimit(search_interval=1.0, jitter=1.0)
_GOOGLE_RATE_LIMIT = RateLimit(
    search_interval=0.2, jitter=0.1, quota=100, quota_window="day"
)
_BING_RATE_LIMIT = RateLimit(search_interval=0.2, jitter=0.1)
_EXA_RATE_LIMIT = RateLimit(
    search_interval=0.1, jitter=0.05, quota=1000, quota_window="month"
)

def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Exponential-backoff retry decorator for Python-layer methods.

    Retries the wrapped call on any exception, sleeping ``delay``
    seconds between attempts (multiplied by ``backoff`` after each
    failure). The final attempt's exception propagates to the caller.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except QuotaExhaustedError:
                    # A hard quota stop must never be retried: the window
                    # will not reset during backoff. Re-raise at once so the
                    # harness can fail over to another adapter.
                    raise
                except Exception as e:
                    if attempt == max_attempts - 1:
                        logger.error(
                            "Function %s failed after %d attempts: %s",
                            func.__name__, max_attempts, e
                        )
                        raise
                    logger.warning(
                        "Attempt %d/%d for %s failed: %s. Retrying in %.1fs",
                        attempt + 1, max_attempts, func.__name__, e, _delay
                    )
                    time.sleep(_delay)
                    _delay *= backoff
        return wrapper
    return decorator


class ResourceAdapter(ABC):
    """Unified interface for API access: web search + domain data sources.

    Every data source the harness can ask for — a web search engine, a
    scholarly index, a legal register, a financial feed, a geo/climate
    service — shares the same cross-cutting concerns. This base owns them
    so a concrete adapter only implements the request + parse contract
    (:meth:`_search_impl` / :meth:`fetch`). Responsibilities:

      * politeness   — per-call minimum gap + jitter (never hammer a host)
      * quota        — hard per-window cap; raises :class:`QuotaExhaustedError`
      * auth injection — provider-specific key injection (no secrets in code)
      * live retune  — read server rate-limit headers, adjust the bucket live
      * execution    — retry/backoff wrapper; callers never see a raw 429

    Concrete adapters set :attr:`name` / :attr:`domain` / :attr:`requires_key`
    and implement :meth:`_search_impl` (and :meth:`fetch` where the source is
    a lookup rather than a text search).
    """

    #: stable id, matched by the tool surface (e.g. "google", "openalex")
    name: str = ""
    #: category used for tool routing and docs
    domain: str = ""
    #: whether a key is required (surface can gate on this)
    requires_key: bool = False

    # politeness / quota state — set per instance in _init_rate_limit
    rate_limit: RateLimit
    _delay: float = 0.0
    _last_search: float = 0.0
    _quota_used: int = 0
    _quota_period: Optional[str] = None

    def _init_rate_limit(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        jitter: Optional[float] = None,
    ) -> None:
        """Normalize constructor arguments into a RateLimit instance.

        Accepts the legacy ``delay: float`` (search interval only) or a full
        ``RateLimit``, plus optional ``fetch_delay`` and ``jitter`` overrides.
        """
        if isinstance(delay, RateLimit):
            # M13: keep a private copy — the fetch_delay override below
            # (and any future mutation) must not leak into a RateLimit
            # shared with other providers.
            self.rate_limit = replace(delay)
        elif delay is not None:
            self.rate_limit = RateLimit(
                search_interval=float(delay),
                jitter=float(jitter) if jitter is not None else 0.0,
            )
        else:
            self.rate_limit = RateLimit()
        if fetch_delay is not None:
            self.rate_limit.fetch_interval = float(fetch_delay)
        # Back-compat attribute consumed by _enforce_delay()
        self._delay = self.rate_limit.search_interval

    def _reset_quota_if_rolled(self) -> None:
        """Reset the in-flight quota counter when its UTC window has rolled.

        A new calendar day/month starts the counter at zero automatically.
        """
        if self.rate_limit.quota is None:
            return
        now = datetime.now(timezone.utc)
        period = (
            now.date().isoformat()
            if self.rate_limit.quota_window == "day"
            else now.strftime("%Y-%m")
        )
        if self._quota_period != period:
            self._quota_period = period
            self._quota_used = 0

    def _enforce_delay(self) -> None:
        """Politeness + quota gate for one outgoing call.

        Enforced, in order:
          1. reset a quota window that has rolled over (UTC day/month);
          2. enforce the hard quota — raise :class:`QuotaExhaustedError`
             once the per-window limit is reached;
          3. wait out the minimum gap, ``self._delay`` + a random
             ``0..jitter`` s (only when the delay is non-zero).

        The quota counter is bumped only after the gate passes, so it
        counts calls actually admitted to the provider.
        """
        self._reset_quota_if_rolled()
        quota = self.rate_limit.quota
        if quota is not None and self._quota_used >= quota:
            raise QuotaExhaustedError(
                f"{self.name!r} quota exhausted "
                f"({self._quota_used}/{quota} per {self.rate_limit.quota_window}); "
                f"retry after the window resets"
            )

        gap = self._delay
        if gap > 0:
            gap += random.uniform(0.0, self.rate_limit.jitter)
        elapsed = time.time() - self._last_search
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_search = time.time()

        if quota is not None:
            self._quota_used += 1

    # ── auth + response contract (overridable) ──
    def inject_auth(
        self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None
    ) -> tuple:
        """Return ``(url, params, headers)`` with this adapter's auth applied.

        Base is a no-op for keyless adapters; subclasses with keys override
        it. Missing keys are not raised here — the tool surface gates on
        :attr:`requires_key` before construction.
        """
        return url, dict(params or {}), dict(headers or {})

    def parse_headers(self, status: int, headers: Mapping[str, str]) -> RateState:
        """Read server rate-limit headers into a :class:`RateState`.

        Base returns an empty state (adapters without rate-limit headers);
        subclasses parse ``X-RateLimit-*`` / ``Retry-After`` to retune live.
        """
        return RateState()

    # ── execution contract ──
    @retry(max_attempts=3, delay=1.0, backoff=2.0)
    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Execute a search and return a list of result dicts.

        Public entry point: retries :meth:`_search_impl` with exponential
        backoff on any exception (M3). A :class:`QuotaExhaustedError` is
        never retried — the window will not reset during backoff — so the
        harness can fail over to another adapter.
        """
        return self._search_impl(query, max_results)

    @abstractmethod
    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Adapter-specific search implementation.

        Each result dict should contain at least:
          - "title": str
          - "url": str
          - "snippet": str
        """
        ...

    def fetch(self, record_id: str, params: Optional[dict] = None) -> List[Dict[str, str]]:
        """Fetch one record by id (lookup-style sources).

        Default raises :class:`NotImplementedError`; search-only adapters
        inherit it. Domain adapters override it.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support fetch")


class SearchProvider(ResourceAdapter):
    """Abstract base class for web-search adapters (``domain='search'``).

    Keeps the historical search contract (:meth:`search` / :meth:`_search_impl`)
    as the narrowing of the unified :class:`ResourceAdapter`.
    """

    #: Canonical selection name (M2). Subclasses must override; the
    #: toolbox matches ``provider=`` arguments against this attribute
    #: instead of deriving names from ``__class__.__name__``.
    name: str = ""
    domain: str = "search"


# ────────────────────────────────────────────────────────────────
# 2. DuckDuckGo Provider (default, no API key)
# ────────────────────────────────────────────────────────────────

class DuckDuckGoProvider(SearchProvider):
    """Search via DuckDuckGo using the ddgs library."""

    name = "duckduckgo"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        jitter: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._init_rate_limit(
            delay if delay is not None else _DUCKDUCKGO_RATE_LIMIT,
            fetch_delay,
            jitter,
        )

    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        from ddgs import DDGS

        self._enforce_delay()
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)
            raise

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", r.get("url", "")),
                "snippet": r.get("body", r.get("snippet", "")),
            }
            for r in results
        ]


# ────────────────────────────────────────────────────────────────
# 3. Google Custom Search JSON API Provider
# ────────────────────────────────────────────────────────────────

class GoogleProvider(SearchProvider):
    """Search via Google Custom Search JSON API.

    Requires:
        - An API key (set GOOGLE_API_KEY env var or pass explicitly)
        - A Search Engine ID / CX (set GOOGLE_CX env var or pass explicitly)

    Free tier: 100 queries/day.
    """

    name = "google"

    def __init__(
        self,
        api_key: Optional[str] = None,
        cx: Optional[str] = None,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        import os
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.cx = cx or os.environ.get("GOOGLE_CX", "")
        self._last_search = 0.0
        self._init_rate_limit(
            delay if delay is not None else _GOOGLE_RATE_LIMIT, fetch_delay
        )

    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        if not self.api_key or not self.cx:
            raise RuntimeError(
                "GoogleProvider requires GOOGLE_API_KEY and GOOGLE_CX environment "
                "variables (or explicit api_key/cx parameters)."
            )

        self._enforce_delay()

        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.api_key,
            "cx": self.cx,
            "q": query,
            "num": min(max_results, 10),
        }
        resp = httpx.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])

        return [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items[:max_results]
        ]


# ────────────────────────────────────────────────────────────────
# 4. Bing Web Search API Provider
# ────────────────────────────────────────────────────────────────

class BingProvider(SearchProvider):
    """Search via Bing Web Search API (Azure Cognitive Services).

    Requires:
        - An API key (set BING_API_KEY env var or pass explicitly)
    """

    name = "bing"

    def __init__(
        self,
        api_key: Optional[str] = None,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        import os
        self.api_key = api_key or os.environ.get("BING_API_KEY", "")
        self._last_search = 0.0
        self._init_rate_limit(
            delay if delay is not None else _BING_RATE_LIMIT, fetch_delay
        )

    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        if not self.api_key:
            raise RuntimeError(
                "BingProvider requires BING_API_KEY environment variable "
                "(or explicit api_key parameter)."
            )

        self._enforce_delay()

        url = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": self.api_key}
        params = {"q": query, "count": max_results, "responseFilter": "WebPages"}
        resp = httpx.get(url, headers=headers, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("webPages", {}).get("value", [])

        return [
            {
                "title": item.get("name", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in results
        ]


# ────────────────────────────────────────────────────────────────
# 5. Exa.ai Provider (semantic, LLM-native search)
# ────────────────────────────────────────────────────────────────

class ExaProvider(SearchProvider):
    """Search via Exa.ai (semantic, LLM-native search).

    Implemented directly against Exa's REST API
    (``POST https://api.exa.ai/v1/search``) with ``httpx`` -- no third-party
    SDK required, so Exa works out of the box whenever ``EXA_API_KEY`` is set.

    A subset of the ``exa-py`` SDK search surface is exposed. The base
    interface :meth:`_search_impl` (used by the toolbox) runs a balanced
    ``auto`` search with ``contents={'highlights': True}`` and returns the
    projectable ``title`` / ``url`` / ``snippet`` shape. The public
    :meth:`search` method is SDK-like and accepts per-call keyword arguments
    forwarded to the REST API:

    - ``type`` -- one of :attr:`SEARCH_TYPES` (default ``auto``); also
      ``instant`` / ``fast`` for low latency and ``deep`` / ``deep-lite`` /
      ``deep-reasoning`` for multi-step synthesis.
    - ``num_results`` (per-call, else the ``max_results`` argument),
      ``include_domains`` / ``exclude_domains``,
      ``start_published_date`` / ``end_published_date``,
      ``category`` (e.g. ``company`` / ``publication`` / ``people``),
      ``moderation``, ``system_prompt``, ``output_schema``,
      ``additional_queries`` (deep-search variants),
      ``contents`` (a dict such as ``{'highlights': True}``,
      ``{'text': True}`` or ``{'summary': {'query': '...'}}``),
      ``text_filters`` and ``result_filters``.

    Results always carry ``title`` / ``url`` / ``snippet``; richer fields
    (``text``, ``summary``, ``publishedDate``, ``author``,
    ``linkingDomains``, structured ``content``) are included when the API
    returns them.

    Requires:
        - An API key (set EXA_API_KEY env var or pass explicitly)

    Free tier: 1,000 requests/month.
    """

    name = "exa"

    #: Search modes accepted by the ``type`` field, slowest / richest last.
    SEARCH_TYPES = (
        "auto", "instant", "fast", "deep-lite", "deep", "deep-reasoning",
    )

    #: Data categories that focus the search (the ``category`` field).
    CATEGORIES = (
        "company", "publication", "news", "personal site",
        "financial report", "people",
    )

    #: SDK-style (snake_case) -> REST API (camelCase) parameter aliases.
    _PARAM_ALIASES = {
        "num_results": "numResults",
        "include_domains": "includeDomains",
        "exclude_domains": "excludeDomains",
        "start_published_date": "startPublishedDate",
        "end_published_date": "endPublishedDate",
        "system_prompt": "systemPrompt",
        "output_schema": "outputSchema",
        "additional_queries": "additionalQueries",
        "text_filters": "textFilters",
        "result_filters": "resultFilters",
    }

    #: Extra result fields surfaced verbatim alongside title/url/snippet.
    _EXTRA_FIELDS = (
        "text", "summary", "publishedDate", "author",
        "linkingDomains", "fauna", "content",
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        delay: Optional[Union[float, RateLimit]] = None,
        search_type: str = "auto",
        fetch_delay: Optional[float] = None,
        **search_params,
    ):
        import os
        self.api_key = api_key or os.environ.get("EXA_API_KEY", "")
        self._last_search = 0.0
        self._init_rate_limit(
            delay if delay is not None else _EXA_RATE_LIMIT, fetch_delay
        )
        self.search_type = search_type
        #: configured default per-call search params (SDK snake_case names)
        self.search_params: dict = dict(search_params or {})

    # ── request building ─────────────────────────────────────────────
    def _effective_params(self, overrides: Mapping[str, Any]) -> dict:
        """Merge constructor defaults, per-call overrides, and the default
        ``contents={'highlights': True}`` (so ``snippet`` is populated)."""
        merged: dict = {
            "type": self.search_type,
            "contents": {"highlights": True},
            **self.search_params,
        }
        merged.update(overrides)
        return merged

    def _build_body(
        self, query: str, max_results: int, params: Mapping[str, Any]
    ) -> dict:
        # ``type`` / ``numResults`` are passed through verbatim; the Exa API
        # validates them and returns a clear 400 for bad values (mirroring
        # the SDK, which does no client-side type validation). An explicit
        # per-call ``num_results`` (SDK alias) wins over ``max_results``.
        body: dict = {"query": query}
        body["numResults"] = params.get("num_results", max_results)
        body["type"] = params.get("type", self.search_type)

        for key, value in params.items():
            if value is None or key in ("type", "num_results"):
                continue
            body[self._PARAM_ALIASES.get(key, key)] = value
        return body

    def _map_results(self, results: Any) -> List[Dict[str, Any]]:
        mapped: List[Dict[str, Any]] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            item: Dict[str, Any] = {
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "snippet": r.get("highlights") or "",
            }
            for key in self._EXTRA_FIELDS:
                if r.get(key) is not None:
                    item[key] = r[key]
            mapped.append(item)
        return mapped

    def _http_search(
        self,
        query: str,
        max_results: int,
        params: Mapping[str, Any],
    ) -> tuple:
        """Perform the REST call. Returns ``(mapped_results, raw_data)``."""
        self._enforce_delay()

        if not self.api_key:
            raise RuntimeError(
                "ExaProvider requires EXA_API_KEY environment variable "
                "(or explicit api_key parameter)."
            )

        body = self._build_body(query, max_results, params)
        response = httpx.post(
            "https://api.exa.ai/v1/search",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        return self._map_results(data.get("results", [])), data

    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        results, _data = self._http_search(
            query, max_results, self._effective_params({})
        )
        return results

    @retry(max_attempts=3, delay=1.0, backoff=2.0)
    def search(
        self, query: str, max_results: int = 5, **params
    ) -> List[Dict[str, Any]]:
        """SDK-like search: ``search(query, type='deep', contents={...}, ...)``.

        Per-call keyword arguments override the constructor defaults and are
        forwarded to Exa's REST API. Results always include ``title`` /
        ``url`` / ``snippet``; richer fields appear when the API returns them.
        """
        results, _data = self._http_search(
            query, max_results, self._effective_params(params)
        )
        return results


# ────────────────────────────────────────────────────────────────
# 6. Provider Registry Helpers
# ────────────────────────────────────────────────────────────────

def get_default_providers() -> List[SearchProvider]:
    """Return a list of providers that are actually available (keys configured)."""
    providers: List[SearchProvider] = [DuckDuckGoProvider()]

    import os

    if os.environ.get("GOOGLE_API_KEY") and os.environ.get("GOOGLE_CX"):
        providers.append(GoogleProvider())

    if os.environ.get("BING_API_KEY"):
        providers.append(BingProvider())

    if os.environ.get("EXA_API_KEY"):
        providers.append(ExaProvider())

    return providers


# ────────────────────────────────────────────────────────────────
# 5. Browser-Oxide Stealth Search Provider
# ────────────────────────────────────────────────────────────────

class BrowserOxideSearchProvider(SearchProvider):
    """Search via browser_oxide's stealth headless engine.

    Renders the DuckDuckGo HTML endpoint (no-JS friendly, parse-stable)
    inside browser_oxide — a from-scratch Rust browser with real BoringSSL
    TLS/JA4 fingerprinting and real JS execution — then parses result links
    from the rendered DOM.

    Useful when plain-HTTP scraping gets blocked but you want search
    without any API keys. Requires ``browser_oxide`` to be installed.

    Note: keep ONE instance per application; it persists its browser
    engine across searches. Call :meth:`close` when done.
    """

    name = "browser"

    SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._init_rate_limit(
            delay if delay is not None else _DUCKDUCKGO_RATE_LIMIT, fetch_delay
        )
        self._browser = None

    def _get_browser(self):
        if self._browser is None:
            import browser_oxide
            # Profile is mandatory in v0.1.x — bare Browser() hits a fatal
            # V8 HandleScope error at construction.
            self._browser = browser_oxide.Browser(
                profile=browser_oxide.Profile.chrome()
            )
        return self._browser

    def close(self) -> None:
        """Shut down the persistent browser engine thread."""
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

    def __del__(self):
        self.close()

    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        self._enforce_delay()
        serp_html = self._render_results(query)

        import html as html_mod
        from urllib.parse import parse_qs, unquote, urlparse

        out = []
        for title, url, snippet in self._parse_results(serp_html):
            # Unwrap DDG's /l/?uddg=<encoded-target> redirect links
            if "uddg=" in url:
                try:
                    target = parse_qs(urlparse(url).query)["uddg"][0]
                    url = unquote(target)
                except Exception:
                    pass
            out.append({
                "title": html_mod.unescape(title),
                "url": url,
                "snippet": html_mod.unescape(snippet),
            })
            if len(out) >= max_results:
                break
        return out

    def _render_results(self, query: str) -> str:
        browser = self._get_browser()
        from urllib.parse import quote
        page = browser.navigate(
            self.SEARCH_URL.format(query=quote(query)),
            max_iterations=8,
        )
        if page.is_challenge:
            raise RuntimeError(
                f"Anti-bot challenge detected ({page.verdict}) during search"
            )
        return page.html

    @staticmethod
    def _parse_results(html: str):
        """Parse a DDG HTML-endpoint SERP into (title, url, snippet) triples."""
        import re

        results = []
        for m in re.finditer(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.S,
        ):
            url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            tail = html[m.end():m.end() + 4000]
            snip = re.search(
                r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', tail, re.S
            )
            snippet = re.sub(r"<[^>]+>", "", snip.group(1)) if snip else ""
            results.append((title.strip(), url.strip(), snippet.strip()))
        return results


# Accepted spellings → canonical provider ``name`` (M2: aliases used to
# resolve to themselves, so e.g. ``provider="ddg"`` never matched the
# registered DuckDuckGoProvider, and BrowserOxideSearchProvider was
# unselectable by name at all).
PROVIDER_NAME_MAP: Dict[str, str] = {
    "duckduckgo": "duckduckgo",
    "ddg": "duckduckgo",
    "google": "google",
    "bing": "bing",
    "exa": "exa",
    "browser": "browser",
    "browser_oxide": "browser",
    "browseroxide": "browser",
}


def resolve_provider_name(name: str) -> Optional[str]:
    """Case-insensitive provider name/alias lookup.

    Maps every accepted spelling (including aliases such as ``ddg`` and
    ``browser_oxide``) to the provider's canonical ``name``; returns
    ``None`` when unknown (M2).
    """
    return PROVIDER_NAME_MAP.get(name.lower().strip())
