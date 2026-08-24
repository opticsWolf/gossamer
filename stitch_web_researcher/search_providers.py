"""
Search provider interface and implementations.

Abstracts web search behind a common `SearchProvider` protocol so the
`WebResearcherToolbox` can plug in DuckDuckGo, Google, Bing, Exa, or any
custom provider at runtime.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# 1. Abstract Search Provider Interface
# ────────────────────────────────────────────────────────────────

class SearchProvider(ABC):
    """Abstract base class for all search providers."""

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Execute a web search and return a list of results.

        Each result dict should contain at least:
          - "title": str
          - "url": str
          - "snippet": str
        """
        ...

    def _enforce_delay(self) -> None:
        """Simple per-provider rate limiting."""
        elapsed = time.time() - self._last_search
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        self._last_search = time.time()

    # Subclasses must set these in __init__
    _delay: float = 0.0
    _last_search: float = 0.0


# ────────────────────────────────────────────────────────────────
# 2. DuckDuckGo Provider (default, no API key)
# ────────────────────────────────────────────────────────────────

class DuckDuckGoProvider(SearchProvider):
    """Search via DuckDuckGo using the ddgs library."""

    def __init__(self, delay: float = 1.0):
        self._delay = delay
        self._last_search = 0.0

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
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

    def __init__(
        self,
        api_key: Optional[str] = None,
        cx: Optional[str] = None,
        delay: float = 1.0,
    ):
        import os
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.cx = cx or os.environ.get("GOOGLE_CX", "")
        self._delay = delay
        self._last_search = 0.0

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
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

    def __init__(self, api_key: Optional[str] = None, delay: float = 1.0):
        import os
        self.api_key = api_key or os.environ.get("BING_API_KEY", "")
        self._delay = delay
        self._last_search = 0.0

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
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

    def __init__(
        self,
        api_key: Optional[str] = None,
        delay: float = 0.5,
        search_type: str = "auto",
    ):
        import os
        if not _exa_available:
            raise ImportError(
                "exa-py is not installed. Install it with: pip install exa-py"
            )
        from exa_py import Exa

        self.api_key = api_key or os.environ.get("EXA_API_KEY", "")
        self.client = Exa(api_key=self.api_key)
        self._delay = delay
        self._last_search = 0.0
        self.search_type = search_type

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
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


PROVIDER_NAME_MAP: Dict[str, type] = {
    "duckduckgo": DuckDuckGoProvider,
    "ddg": DuckDuckGoProvider,
    "google": GoogleProvider,
    "bing": BingProvider,
    "exa": ExaProvider,
}


def resolve_provider_name(name: str) -> Optional[str]:
    """Case-insensitive provider name lookup. Returns canonical name or None."""
    lower = name.lower().strip()
    for key, _cls in PROVIDER_NAME_MAP.items():
        if key == lower:
            return key
    return None
