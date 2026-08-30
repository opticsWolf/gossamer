"""Search concern: provider search, failover/merge, result caching.

Extracted from the WebResearcherToolbox during the agent_tools composition
split (phase 2). SearchService owns the pure-search path (``web_search`` /
``search_web``); the search-then-fetch ``research`` step is delegated back
to the toolbox (Crawler) via ``self._tb.research``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import List, Optional
from urllib.parse import urlparse, urlunparse

from stitch_web_researcher.search_providers import resolve_provider_name
from stitch_web_researcher.guard import evaluate

logger = logging.getLogger(__name__)


class SearchService:
    """Pure search path: provider dispatch, failover/merge, result cache."""

    def __init__(self, tb):
        self._tb = tb



    def _finish_search(self, results: list, fallback: Optional[dict] = None) -> str:
        """§7: optionally guard search results (search_results scope).

        *fallback* carries a ``provider_fallback`` note when the requested
        provider was recognized but not registered, so the model can see
        that the engine it asked for is not the one that answered.

        When the guard is active and the ``search_results`` scope is enabled,
        a guard block is attached by wrapping the list in
        ``{"results": [...], "guard": {...}}``; in block mode the results are
        withheld. When the guard is off (the default), the bare list is
        returned unchanged, so the common output shape never changes.
        """
        text = " ".join(
            f"{r.get('title', '')} {r.get('snippet', '')}"
            for r in results
            if isinstance(r, dict)
        )
        block, _redacted, withheld = evaluate(
            self._tb._guard, [("search_results", text)], main_scope="search_results"
        )
        if block is None:
            if fallback is not None:
                return json.dumps(
                    {"results": results, "provider_fallback": fallback},
                    indent=2,
                    ensure_ascii=False,
                )
            return json.dumps(results, indent=2, ensure_ascii=False)
        if withheld:
            return json.dumps(
                {
                    "error": "results withheld by prompt-injection guard",
                    "guard": block,
                },
                indent=2,
            )
        envelope = {"results": results, "guard": block}
        if fallback is not None:
            envelope["provider_fallback"] = fallback
        return json.dumps(envelope, indent=2, ensure_ascii=False)

    def web_search(
        self,
        query: str,
        search_only: bool = False,
        max_results: int = 5,
        depth: int = 5,
        max_tokens: int = 0,
        provider: Optional[str] = None,
    ) -> str:
        """Unified web_search + research entry point (P8 tool ``web_search``).

        ``search_only=True``  -> pure provider search (the old
            ``search_web``): returns a deduped list of results.
        ``search_only=False`` -> search, dedupe, then fetch up to
            ``depth`` candidate pages through the normal pipeline and
            return one record per source with status/markdown/provenance
            (the old ``research``).

        Parameters
        ----------
        query : str
            The search query / research topic.
        search_only : bool
            Default False. True returns only provider results.
        max_results : int
            Maximum search results to consider (default 5).
        depth : int
            Candidate pages to fetch when search_only is False (default 5).
        max_tokens : int
            Global token budget for the response (0 = toolbox default).
        provider : str, optional
            Search engine to prefer (registry default: "duckduckgo").
        """
        if search_only:
            return self.search_web(
                query, max_results=max_results, provider=provider
            )
        return self._tb.research(topic=query, depth=depth, max_tokens=max_tokens)

    def search_web(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> str:
        """
        Search the web using a specific provider or the default.

        Results are cached under a result-level key (Tier 2.8, review item 8)
        so repeat queries within the cache TTL do not re-query the provider.
        Within a provider's results, duplicates are removed by URL. When
        ``ToolboxConfig.search_merge`` is enabled, every provider is queried
        and the results are merged + deduped (provider priority order kept);
        otherwise providers are strict failover (first success wins).

        Parameters
        ----------
        query : str
            The search query.
        max_results : int
            Maximum number of results to return.
        provider : str or None
            Provider name (e.g., "duckduckgo", "google", "bing", "exa",
            "browser"). Aliases such as "ddg" and "browser_oxide" work.
            Falls back through all providers if the chosen one fails.

        Returns
        -------
        str
            JSON-serialised list of results or error dict.
        """
        cache_key = self._search_cache_key(query, max_results, provider)
        cached = self._search_cache_get(cache_key)
        if cached is not None:
            logger.debug("search cache hit for %r", query)
            # The guard is a live policy, so it is re-evaluated on every
            # call even when the underlying results came from the cache.
            return self._finish_search(
                cached, self._provider_fallback_note(provider)
            )

        if provider and resolve_provider_name(provider) is None:
            # Silently substituting another engine would let the model draw
            # conclusions about coverage it never actually got.
            known = sorted(
                {getattr(p, "name", "?") for p in self._tb.providers if hasattr(p, "name")}
            )
            return json.dumps(
                {
                    "error": f"Unknown search provider: {provider!r}",
                    "available_providers": known,
                },
                indent=2,
            )

        providers_to_try = self._resolve_providers(provider)

        if self._tb._search_merge:
            results, any_ok = self._search_merged(
                providers_to_try, query, max_results
            )
        else:
            results, any_ok = self._search_failover(
                providers_to_try, query, max_results
            )

        if not any_ok:
            return json.dumps({"error": f"All search providers failed for: {query}"}, indent=2)

        self._search_cache_put(cache_key, results)
        return self._finish_search(
            results, self._provider_fallback_note(provider)
        )

    def _search_failover(self, providers_to_try, query, max_results):
        """Tier 2.8: default strict-failover search (first success wins).

        Returns ``(results, any_ok)``; results are deduped by URL.
        """
        for prov in providers_to_try:
            try:
                results = prov.search(query, max_results=max_results)
            except Exception as e:
                logger.warning(
                    "Provider %s failed for '%s': %s — trying next",
                    prov.__class__.__name__, query, e,
                )
                continue
            return self._dedup_results(results), True
        return [], False

    def _search_merged(self, providers_to_try, query, max_results):
        """Tier 2.8: cross-provider merge (every provider is queried).

        Results from each provider are appended in provider priority order
        and deduped by URL; collection stops once *max_results* distinct
        results have accumulated. Returns ``(results, any_ok)``.
        """
        merged: list = []
        seen: set = set()
        any_ok = False
        for prov in providers_to_try:
            try:
                results = prov.search(query, max_results=max_results)
            except Exception as e:
                logger.warning(
                    "Provider %s failed for '%s': %s — skipping",
                    prov.__class__.__name__, query, e,
                )
                continue
            any_ok = True
            for r in results:
                if not isinstance(r, dict):
                    continue
                key = self._result_url_key(r)
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
                merged.append(r)
                if len(merged) >= max_results:
                    break
            if len(merged) >= max_results:
                break
        return merged[:max_results], any_ok

    @staticmethod
    def _result_url_key(result: dict) -> str:
        """Normalised URL key used to dedup search results.

        Scheme/host lowercased, default port dropped, trailing slash removed,
        fragment dropped. Returns '' when the result has no usable URL (such
        results are never deduped).
        """
        url = result.get("url")
        if not url or not isinstance(url, str):
            return ""
        parts = urlparse(url.strip())
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()
        if scheme in ("http", "https") and parts.port in (None, 80, 443):
            netloc = host
        elif parts.port:
            netloc = f"{host}:{parts.port}"
        else:
            netloc = host
        path = parts.path or ""
        if path.endswith("/"):
            path = path[:-1]
        return urlunparse((scheme, netloc, path, "", parts.query, ""))

    def _dedup_results(self, results: list) -> list:
        """Remove duplicate results by normalised URL, preserving order.

        Results without a usable URL are all kept (they cannot be deduped).
        """
        seen: set = set()
        out: list = []
        for r in results:
            if isinstance(r, dict):
                key = self._result_url_key(r)
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
            out.append(r)
        return out

    def _search_cache_key(
        self, query: str, max_results: int, provider: Optional[str]
    ) -> str:
        """Result-level cache key (Tier 2.8, review item 8).

        Normalised so equivalent spellings of the same query share one entry:
        query lowercased + whitespace collapsed, plus the result count, the
        canonical provider selection, and the merge mode.
        """
        normalized_query = " ".join((query or "").lower().split())
        provider_part = resolve_provider_name(provider) if provider else "default"
        provider_part = provider_part or "default"
        material = f"{normalized_query}|{max_results}|{provider_part}|merge={self._tb._search_merge}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _search_cache_get(self, key: str) -> Optional[list]:
        """Return cached search results for *key*, or None on miss/expired.

        Tier 2.8: in-memory, session-scoped (see ``__init__``).
        """
        with self._tb._search_mem_lock:
            item = self._tb._search_mem.get(key)
            if item is None:
                return None
            timestamp, value = item
            if time.time() - timestamp > self._tb._search_mem_ttl:
                del self._tb._search_mem[key]
                return None
            self._tb._search_mem.move_to_end(key)
            return value

    def _search_cache_put(self, key: str, results: list) -> None:
        """Cache a successful search-result list under its result-level key."""
        with self._tb._search_mem_lock:
            self._tb._search_mem[key] = (time.time(), results)
            self._tb._search_mem.move_to_end(key)
            while len(self._tb._search_mem) > 256:
                self._tb._search_mem.popitem(last=False)

    def _search_cache_clear(self) -> None:
        """Drop all cached search results (invoked by ``clear_cache``)."""
        with self._tb._search_mem_lock:
            self._tb._search_mem.clear()

    def _provider_fallback_note(self, provider_name: Optional[str]) -> Optional[dict]:
        """Describe a recognized-but-unregistered provider substitution.

        Returns ``None`` when the requested provider actually answers (or
        none was requested). Otherwise the note names what was asked for
        and what is available, so the model does not read another engine's
        coverage as the one it selected.
        """
        if not provider_name:
            return None
        canonical = resolve_provider_name(provider_name)
        if canonical is None:
            return None  # rejected up front by search_web
        registered = [getattr(p, "name", "?") for p in self._tb.providers]
        if canonical in registered:
            return None
        return {
            "requested": canonical,
            "reason": "provider not registered",
            "used": registered,
        }

    def _resolve_providers(self, provider_name: Optional[str]) -> list:
        """
        Build an ordered list of providers to try.

        If *provider_name* is given, put that provider first, then fall
        back through the rest.  If None, use the full provider list in
        registration order.
        """
        if not provider_name:
            return list(self._tb.providers)

        canonical = resolve_provider_name(provider_name)
        if canonical:
            # Match on the provider's explicit canonical name (M2: the old
            # __class__.__name__ derivation made aliases like "ddg" and
            # BrowserOxideSearchProvider unselectable).
            matched = [
                p for p in self._tb.providers
                if getattr(p, "name", None) == canonical
            ]
            if matched:
                others = [p for p in self._tb.providers if p not in matched]
                return matched + others

        # A recognized name whose provider is not registered is ordinary
        # failover: log it so an operator can see the substitution, and
        # search on. An *unrecognized* name never reaches here -- search_web
        # rejects it up front so the model can correct its own call.
        logger.warning(
            "Search provider %r is not registered; falling back to %s",
            provider_name,
            ", ".join(getattr(p, "name", "?") for p in self._tb.providers) or "none",
        )
        return list(self._tb.providers)

    async def search_web_async(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> str:
        """Async version of search_web.

        "Async" here means *thread pool*: the blocking provider call is
        offloaded to the default executor via run_in_executor so the event
        loop stays responsive. The underlying search SDK call remains
        synchronous (there is no native async I/O) -- see the README
        "Async" note.
        """
        # Search is I/O-bound but ddgs/google/bing SDKs are sync;
        # run in executor for non-blocking behaviour.
        # M6/F6: get_running_loop() replaces the deprecated event-loop
        # lookup; the module-level asyncio import suffices.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.search_web, query, max_results, provider
        )

