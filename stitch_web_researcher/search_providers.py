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
from functools import wraps
from typing import Dict, List, Optional, Union

import httpx

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# 1. Abstract Search Provider Interface
# ────────────────────────────────────────────────────────────────

@dataclass
class RateLimit:
    """Per-provider rate limits.

    Attributes:
        search_interval: Minimum seconds between search-API calls to this
            provider (queries to the engine itself).
        fetch_interval: Minimum seconds between content downloads suggested
            for sessions using this provider. Consumed by
            ``WebResearcherToolbox`` as the default politeness delay when
            fetching pages found through this provider.
    """

    search_interval: float = 1.0
    fetch_interval: float = 0.5


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


class SearchProvider(ABC):
    """Abstract base class for all search providers."""

    #: Canonical selection name (M2). Subclasses must override; the
    #: toolbox matches ``provider=`` arguments against this attribute
    #: instead of deriving names from ``__class__.__name__``.
    name: str = ""

    @retry(max_attempts=3, delay=1.0, backoff=2.0)
    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Execute a web search and return a list of results.

        Public entry point: retries the provider's ``_search_impl``
        with exponential backoff on any exception (M3: the old
        ``@retry`` on ``WebResearcherToolbox.search_web`` was dead
        code because that method catches every provider exception
        and returns an error dict, so it never raised).
        """
        return self._search_impl(query, max_results)

    @abstractmethod
    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Provider-specific search implementation.

        Each result dict should contain at least:
          - "title": str
          - "url": str
          - "snippet": str
        """
        ...

    def _init_rate_limit(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ) -> None:
        """Normalize constructor arguments into a RateLimit instance.

        Accepts the legacy ``delay: float`` (search interval only) or a full
        ``RateLimit``, plus an optional ``fetch_delay`` override.
        """
        if isinstance(delay, RateLimit):
            # M13: keep a private copy — the fetch_delay override below
            # (and any future mutation) must not leak into a RateLimit
            # shared with other providers.
            self.rate_limit = replace(delay)
        elif delay is not None:
            self.rate_limit = RateLimit(search_interval=float(delay))
        else:
            self.rate_limit = RateLimit()
        if fetch_delay is not None:
            self.rate_limit.fetch_interval = float(fetch_delay)
        # Back-compat attribute consumed by _enforce_delay()
        self._delay = self.rate_limit.search_interval

    def _enforce_delay(self) -> None:
        """Per-provider rate limiting for search-API calls.

        The minimum gap between calls is ``self._delay`` plus a random
        0–1 s jitter (only when the delay is non-zero), which
        desynchronizes access patterns.
        """
        gap = self._delay
        if gap > 0:
            gap += random.uniform(0.0, 1.0)
        elapsed = time.time() - self._last_search
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_search = time.time()

    # Subclasses must set these in __init__
    _delay: float = 0.0
    _last_search: float = 0.0


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
    ):
        self._last_search = 0.0
        self._init_rate_limit(delay, fetch_delay)

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
        self._init_rate_limit(delay, fetch_delay)

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
        self._init_rate_limit(delay, fetch_delay)

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

_exa_available = False
try:
    from exa_py import Exa  # noqa: F401
    _exa_available = True
except ImportError:
    pass


class ExaProvider(SearchProvider):
    """Search via Exa.ai (semantic, LLM-native search).

    Exa returns query-relevant highlights that cut token usage by ~10x
    compared to full-page retrieval.

    Requires:
        - An API key (set EXA_API_KEY env var or pass explicitly)

    Free tier: 1,000 requests/month.
    """

    name = "exa"

    def __init__(
        self,
        api_key: Optional[str] = None,
        delay: Optional[Union[float, RateLimit]] = None,
        search_type: str = "auto",
        fetch_delay: Optional[float] = None,
    ):
        import os
        if not _exa_available:
            raise ImportError(
                "exa-py is not installed. Install it with: pip install exa-py"
            )
        from exa_py import Exa

        self.api_key = api_key or os.environ.get("EXA_API_KEY", "")
        self.client = Exa(api_key=self.api_key)
        self._last_search = 0.0
        self._init_rate_limit(delay, fetch_delay)
        self.search_type = search_type

    def _search_impl(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        self._enforce_delay()

        result = self.client.search(
            query,
            type=self.search_type,
            num_results=max_results,
            contents={"highlights": True},
        )

        return [
            {
                "title": getattr(r, "title", ""),
                "url": getattr(r, "url", ""),
                "snippet": getattr(r, "highlights", ""),
            }
            for r in result.results
        ]


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

    if _exa_available and os.environ.get("EXA_API_KEY"):
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
        self._init_rate_limit(delay, fetch_delay)
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
