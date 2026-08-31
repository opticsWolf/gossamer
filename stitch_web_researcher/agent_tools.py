import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import random
import re
import threading
import time
import warnings
from enum import Enum
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse, urljoin, urlunparse

import httpx
from pydantic import BaseModel, Field

from stitch_web_researcher._core import (
    batch_research,
    fetch_html_full,
    fetch_html_conditional,
    extract_links_from_html as _extract_links_from_html,
    process_rendered_html as _process_rendered_html,
    extract_main_content_markdown,
    extract_tables_from_html,
    init_rust_logging as _init_rust_logging,
    configure_http as _configure_http,
)
from stitch_web_researcher.token_budget import truncate_to_tokens, count_tokens
from stitch_web_researcher.structured_parser import (
    StructuredOxideParser,
    DOCUMENT_EXTENSIONS,
    FollowUpCandidate,
    ParsedDocumentPayload,
    build_follow_up_candidates,
    require_office_oxide,
    require_pdf_oxide,
)
from stitch_web_researcher.search_providers import (
    DuckDuckGoProvider,
    RateLimit,
    resolve_provider_name,
)
from stitch_web_researcher.text_links import extract_links
from stitch_web_researcher import meta_extractor
from stitch_web_researcher.cache import Cache
from stitch_web_researcher.robots import RobotsChecker
from stitch_web_researcher.ssrf import SsrfBlockedError, validate_public_url
from stitch_web_researcher.sections import select_relevant_sections
from stitch_web_researcher.guard import (
    GuardConfig,
    JailGuardGuard,
    build_guard,
    evaluate,
    wrap_untrusted,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────
# Re-exports for backwards compatibility.
#
# The names below moved to dedicated submodules during the composition
# split (config.py / models.py / fetch.py / crawl.py). They are re-imported
# here so existing ``from stitch_web_researcher.agent_tools import <name>``
# usages keep working, and so the WebResearcherToolbox body below can keep
# referencing them unchanged. New code should import from the submodules.
# ────────────────────────────────────────────────────────────────────────
from stitch_web_researcher.config import (  # noqa: F401
    _MISSING,
    _coerce_fetch_mode,
    _resolve_fetch_strategy,
    _LLM_TOOL_DEFINITIONS,
    FetchMode,
    TOOL_REGISTRY,
    ToolParam,
    ToolSpec,
    ToolboxConfig,
    normalize_url,
)
from stitch_web_researcher.models import (  # noqa: F401
    _JSON_FIT_FLOOR,
    _MD_INLINE_LINK_RE,
    _MD_NON_LINK_PREFIXES,
    _absolutize_markdown_links,
    _browser_provenance,
    _domain_of,
    _normalize_batch_results,
    _provenance_from_fetch_meta,
    _sha256_hex,
    _utc_now_iso,
    BatchEntry,
    ExtractionResult,
    FetchStats,
    InspectionResult,
)
from stitch_web_researcher.fetch import (  # noqa: F401
    _browser_oxide_available,
    _fetch_with_browser_oxide,
    _maybe_init_rust_logging,
    fetch_smart_page,
)
from stitch_web_researcher.crawl import (  # noqa: F401
    _CrawlCorpus,
    _load_thesaurus,
    Crawler,
)
from stitch_web_researcher.search import SearchService  # noqa: F401
from stitch_web_researcher.dedup import dedupe  # noqa: F401  # Workstream 2
from stitch_web_researcher.liveness import check_liveness, LIVENESS_TIMEOUT  # noqa: F401  # Workstream 2
from stitch_web_researcher.fetch import FetchService  # noqa: F401
from stitch_web_researcher.document import DocumentExtractor  # noqa: F401
from stitch_web_researcher.budget import ContentBudget  # noqa: F401
from stitch_web_researcher.discovery import ResourceDiscovery  # noqa: F401
from stitch_web_researcher.research_categories import CATEGORIES, search_category  # noqa: F401
from stitch_web_researcher.citations import format_citations  # noqa: F401


class WebResearcherToolbox:
    """LLM tool routing layer with caching, rate limiting, and token budgeting."""

    # M7: bounds for per-process state so a long-lived MCP server does
    # not grow without limit (FIFO eviction of the oldest entries).
    VISITED_URL_CAP = 20000
    DOMAIN_TS_CAP = 4096

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    def __init__(self, config: Optional[ToolboxConfig] = None, **legacy_kwargs):
        """
        Parameters
        ----------
        config : ToolboxConfig, optional
            Preferred construction style::

                WebResearcherToolbox(ToolboxConfig(max_tokens=4000))

        **legacy_kwargs :
            Deprecated passthrough of the former keyword arguments
            (``cache_dir=…``, ``max_tokens=…``, …). Will be removed in a
            future release; passing them emits a DeprecationWarning.
        """
        if config is None:
            if legacy_kwargs:
                warnings.warn(
                    "Passing keyword arguments to WebResearcherToolbox is "
                    "deprecated; construct with ToolboxConfig(...) instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            config = ToolboxConfig(**legacy_kwargs)
        elif legacy_kwargs:
            raise TypeError(
                "Pass either a ToolboxConfig or legacy keyword arguments, not both."
            )

        # Two-tier cache (memory LRU + file TTL)
        self.cache = Cache(
            cache_dir=config.cache_dir,
            ttl_seconds=config.cache_ttl_seconds,
            max_disk_bytes=config.cache_max_bytes,
        )
        self.fetch_mode = config.fetch_mode
        self.ddgs_delay = config.ddgs_delay
        self.ddgs_jitter = config.ddgs_jitter
        self.domain_delay = config.domain_delay
        self.max_markdown_chars = config.max_markdown_chars
        self.max_tokens = config.max_tokens
        self.model_name = config.model_name
        self.max_links = config.max_links
        self.link_budget_ratio = config.link_budget_ratio

        # Search providers: default to DuckDuckGo if none specified
        if config.search_providers:
            self.providers = config.search_providers
        else:
            self.providers = [
                DuckDuckGoProvider(delay=config.ddgs_delay, jitter=config.ddgs_jitter)
            ]
        idx = config.default_provider_index
        try:
            self.default_provider = self.providers[idx]
        except IndexError as e:
            raise IndexError(
                f"default_provider_index {idx} out of range for "
                f"{len(self.providers)} provider(s)"
            ) from e

        self._fetch_interval = self._resolve_fetch_interval(config)
        self._fetch_jitter = float(config.fetch_jitter)

        # M7: bounded OrderedDicts (FIFO) instead of an unbounded set
        # and a defaultdict (whose reads insert unseen keys).
        self.visited_urls: OrderedDict[str, None] = OrderedDict()
        self._domain_last_seen: OrderedDict[str, float] = OrderedDict()
        self._ua_index = 0
        # S5: the MCP SDK dispatches synchronous tools on worker threads,
        # so tool calls run concurrently against this one instance. These
        # locks protect the read-modify-write state below.
        self._throttle_lock = threading.Lock()
        self._visit_lock = threading.Lock()
        # URLs currently being fetched (in-flight guard, see
        # _claim_in_flight). Distinct from visited_urls on purpose: C3
        # semantics mark a URL visited only after a *successful* fetch,
        # while in-flight covers the fetch window itself.
        self._in_flight: set[str] = set()
        # Candidate pool size: how many links we collect per page before
        # handing ALL of them to the LLM for topic-based selection.
        self.link_cap = max(1, int(config.candidate_cap))
        # Upper bound on simultaneous connections opened by batch fetching.
        self.max_concurrency = max(1, int(config.max_concurrency))
        # Workstream 2: per-URL timeout for the check_sources liveness probe.
        try:
            self._liveness_timeout = float(config.liveness_timeout)
        except (TypeError, ValueError):
            self._liveness_timeout = LIVENESS_TIMEOUT
        # S3: response-body size cap, passed through to the Rust core.
        self.max_response_bytes = max(1, int(config.max_response_bytes))
        # S4: robots.txt compliance (per-host fetch + cache, Disallow/
        # Allow/Crawl-delay). The matched UA is the first rotation entry;
        # all rotation entries are equivalent desktop Chrome UAs.
        self._robots = RobotsChecker(
            enabled=config.respect_robots,
            user_agent=self.USER_AGENTS[0],
        )
        # Tier 1.4: conditional revalidation of expired static page entries.
        self.conditional_revalidation = config.conditional_revalidation
        # §7: prompt-injection annotation layer (off by default; the guard
        # is a NoopGuard with zero cost unless config.guard is enabled).
        self._guard = build_guard(config.guard)
        if isinstance(self._guard, JailGuardGuard):
            # Download the model at construction time so the 90 MB fetch
            # and cold start do not land inside the first tool call.
            self._guard.preload()
        # Tier 2.6: fetch observability (latency/bytes/domain/errors) and the
        # opt-in Rust tracing -> Python logging bridge.
        self._fetch_stats = FetchStats(latency_window=config.fetch_stats_window)
        _maybe_init_rust_logging()
        # Tier 2.7: push HTTP transport overrides (proxy / User-Agent /
        # headers / cookies) into the lazily-built shared Rust client. Only
        # invoked when at least one override is set, so the default fetch
        # path stays untouched (zero cost otherwise).
        if config.http_proxy or config.user_agent or config.custom_headers or config.cookies:
            _configure_http(
                config.http_proxy,
                config.user_agent,
                list(config.custom_headers.items()),
                list(config.cookies.items()),
            )
        # Tier 2.8: cross-provider merge flag for search_web (item 8).
        self._search_merge = config.search_merge
        # Tier 2.8: in-memory, per-toolbox search-result cache (bounded +
        # TTL). Kept separate from the page disk cache so session-scoped
        # search results never leak across toolbox instances or processes.
        self._search_mem: "OrderedDict[str, tuple[float, list]]" = OrderedDict()
        self._search_mem_ttl = config.cache_ttl_seconds
        self._search_mem_lock = threading.Lock()

        self._search = SearchService(self)

        self._fetch = FetchService(self)

        self._doc = DocumentExtractor(self)

        self._crawler = Crawler(self)

        self._budget = ContentBudget(self)

        self._discovery = ResourceDiscovery(self)

    def _resolve_fetch_interval(self, config: ToolboxConfig) -> float:
        """Effective content-fetch interval (per-domain politeness delay).

        Resolution order: explicit fetch_delay > active provider's
        RateLimit.fetch_interval > legacy domain_delay.
        """
        if config.fetch_delay is not None:
            return float(config.fetch_delay)
        rl = getattr(self.default_provider, "rate_limit", None)
        if isinstance(rl, RateLimit):
            return rl.fetch_interval
        return config.domain_delay

    def _next_headers(self) -> dict:
        """Rotate User-Agent and return full browser headers.

        S5: the counter is shared across concurrent tool calls, so the
        read-bump happens under a lock to keep rotations distinct.
        """
        with self._throttle_lock:
            ua = self.USER_AGENTS[self._ua_index % len(self.USER_AGENTS)]
            self._ua_index += 1
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
        }

    def _validate_url(self, url: str) -> None:
        """Validate URL scheme and format, and apply the SSRF policy (S1).

        ``url`` is LLM-supplied content in every tool that calls this,
        so non-public destinations (cloud metadata, loopback, RFC1918,
        internal names) are rejected before any network I/O happens.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        if not parsed.netloc:
            raise ValueError(f"Invalid URL (no host): {url}")
        try:
            validate_public_url(url)
        except SsrfBlockedError as e:
            logger.warning("Blocked non-public URL %s: %s", url, e)
            raise

    @staticmethod
    def _extract_html_metadata(html: Optional[str], url: str) -> dict:
        """Run meta-oxide over *html*, failing soft to an empty dict.

        Metadata is a bonus on top of the content, never a reason to lose a
        page, so an extractor error degrades to ``{}`` with a log line.
        """
        if not html:
            return {}
        try:
            return meta_extractor.extract_all(html, url)
        except Exception:
            logger.debug("metadata extraction failed for %s", url, exc_info=True)
            return {}

    @staticmethod
    def _url_error(raw: str, exc: Exception) -> dict:
        """Standard error record for a URL rejected before any network I/O.

        URL rejection is a *recoverable* outcome for an LLM caller — the model
        picked a bad link and should pick another — so it travels through the
        same ``{"error": ...}`` contract as a fetch failure instead of escaping
        as an exception. The reason is kept verbatim so the model can
        self-correct rather than retry the same URL.
        """
        return {
            "url": raw,
            "error": f"URL rejected: {exc}",
            "error_type": type(exc).__name__,
        }

    def _prepare_url(self, raw: str) -> tuple[Optional[str], Optional[dict]]:
        """Normalize and validate *raw*, turning rejection into a record.

        Returns ``(url, None)`` when the URL is usable, or ``(None, error)``
        when it is not. Single-URL tools return that error as their payload;
        ``batch_inspect_pages`` attaches it to the one offending entry so a
        single bad URL cannot abort the whole batch.
        """
        try:
            url = normalize_url(raw)
            self._validate_url(url)
        except (ValueError, SsrfBlockedError) as e:
            return None, self._url_error(raw, e)
        return url, None

    def _robots_disallows(self, url: str) -> bool:
        """S4: True if the host's robots.txt forbids fetching ``url``.

        The checker fails open by design (fetch/parse errors are treated
        as *allowed*), so a checker bug can at worst over-fetch, never
        break fetching.
        """
        try:
            return not self._robots.is_allowed(url)
        except Exception:
            logger.debug("robots.txt check failed for %s", url, exc_info=True)
            return False

    @staticmethod
    def _crawl_host_key(url: str) -> str:
        """Host with a leading ``www.`` stripped, for same-host checks."""
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def _rate_limit_domain(
        self, url: str, politeness_root: Optional[str] = None
    ) -> None:
        """Enforce per-domain rate limiting for content fetching.

        The minimum gap between same-domain fetches is the resolved
        ``_fetch_interval`` plus a random ``0.._fetch_jitter`` s jitter
        (only when the interval is non-zero), which desynchronizes access
        patterns. A Crawl-delay requested in the site's robots.txt (S4)
        raises the gap floor.

        *politeness_root* makes the throttle crawl-aware: when set, only
        fetches on that host key are throttled -- cross-domain links (each
        visited once) skip politeness entirely, so a crawl never slows
        down on external hosts. When ``None`` (single/batch fetch), every
        domain is throttled per-domain; a first visit to an unseen domain
        still takes no gap because ``elapsed`` is large.
        """
        parsed = urlparse(url)
        domain = parsed.netloc
        # Crawl politeness: only throttle the crawl's own host. External
        # hosts are visited once, so skipping keeps the crawl fast without
        # hammering them. (Static method, so call it unbound-style.)
        if politeness_root is not None and self._crawl_host_key(
            url
        ) != politeness_root:
            return
        # S4: resolve the Crawl-delay *before* taking the throttle lock --
        # the first probe of a host performs a robots.txt network fetch,
        # which must not hold the lock.
        crawl_delay: Optional[float] = None
        try:
            crawl_delay = self._robots.crawl_delay(url)
        except Exception:
            logger.debug("robots Crawl-delay lookup failed", exc_info=True)
        # S5: the read-sleep-write on the per-domain timestamp happens
        # under a lock so concurrent tool calls share one politeness gap
        # instead of each sleeping independently.
        with self._throttle_lock:
            # M7: .get instead of __getitem__ — the old defaultdict
            # inserted an entry on every read of an unseen domain.
            last_seen = self._domain_last_seen.get(domain, 0.0)
            elapsed = time.time() - last_seen
            gap = self._fetch_interval
            if gap > 0:
                gap += random.uniform(0.0, self._fetch_jitter)
            if crawl_delay is not None and crawl_delay > gap:
                gap = crawl_delay
            if elapsed < gap:
                time.sleep(gap - elapsed)
            self._domain_last_seen[domain] = time.time()
            self._domain_last_seen.move_to_end(domain)
            # M7: keep the map bounded (evict least-recently-seen).
            while len(self._domain_last_seen) > self.DOMAIN_TS_CAP:
                self._domain_last_seen.popitem(last=False)

    def _claim_in_flight(self, url: str) -> bool:
        """Atomically claim a URL for fetching (S5).

        Returns False if the URL is already visited or already being
        fetched by another thread, so concurrent tool calls never
        double-fetch the same new page. C3 semantics are preserved:
        ``visited_urls`` still only gains a URL after a successful fetch;
        the in-flight set covers the fetch window itself.
        """
        with self._visit_lock:
            if url in self.visited_urls or url in self._in_flight:
                return False
            self._in_flight.add(url)
            return True

    def _release_in_flight(self, url: str) -> None:
        """Release an in-flight claim (idempotent, always safe)."""
        with self._visit_lock:
            self._in_flight.discard(url)

    def _mark_visited(self, url: str) -> None:
        """Record a successful fetch (C3: success only, S5: locked).

        M7: visited_urls is a bounded FIFO — once it exceeds the cap the
        oldest entries are evicted down to half the cap (amortized
        O(1) per insert). Eviction only affects future re-fetch dedup:
        evicted URLs can still be served from the page cache."""
        with self._visit_lock:
            self.visited_urls[url] = None
            if len(self.visited_urls) > self.VISITED_URL_CAP:
                while len(self.visited_urls) > self.VISITED_URL_CAP // 2:
                    self.visited_urls.popitem(last=False)

    # Query parameters that never change page content -- stripped so that
    # campaign-tagged variants of one document share a single cache entry.
    _TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref_")

    def _cache_key(self, url: str) -> str:
        """Canonical cache key for a URL, so variant spellings of the same
        resource cannot produce 'double-tracked' cache entries:

          https://WWW.Example.com:443/docs/report.pdf?x=1&utm_source=x#top
          http-->https kept | host lowercased | default port dropped |
          path de-slashes   | fragment dropped | utm_* etc. dropped

        Two URLs mapping to the same key share one cache entry; different
        resources still always map to different keys.
        """
        from urllib.parse import parse_qsl, urlencode, urlunparse

        normalized = normalize_url(url)
        parts = urlparse(normalized)

        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[len("www."):]
        port = parts.port
        default_port = {"http": 80, "https": 443}.get(parts.scheme)
        netloc = host if (port is None or port == default_port) else f"{host}:{port}"

        path = parts.path.rstrip("/") or "/"

        query_items = sorted(
            (k.lower(), v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith(self._TRACKING_PARAM_PREFIXES)
        )
        # Key names are lowercased: servers treat them case-sensitively,
        # but for caching purposes ?ID=7 and ?id=7 are the same document.
        query = urlencode(query_items)

        # scheme preserved (http/https may genuinely serve different content),
        # fragment dropped (never part of the fetched resource).
        return urlunparse((parts.scheme, netloc, path, "", query, ""))

    # ───────────────────────────────
    # LLM Tool Definitions
    # ───────────────────────────────

    def get_llm_definitions(self) -> list[dict]:
        """Return tool definitions for LLM function calling."""
        return copy.deepcopy(list(_LLM_TOOL_DEFINITIONS))

    def execute_tool(self, name: str, arguments: Optional[dict] = None) -> str:
        """Dispatch a tool call by name (P8).

        Single dispatcher shared by every surface: LLM function-calling
        consumers and scripted automation can drive the toolbox with a
        ``(name, arguments)`` pair and get the tool's JSON string reply.

        Args:
            name: Registered tool name (see ``TOOL_REGISTRY``).
            arguments: Keyword arguments for the tool; omitted optional
                parameters fall back to the registry defaults.

        Raises:
            ValueError: if *name* is not a registered tool.
        """
        for spec in TOOL_REGISTRY:
            if spec.name == name:
                return getattr(self, spec.method)(**spec.kwargs(arguments))
        raise ValueError(
            f"Unknown tool: {name!r}. Valid tools: "
            + ", ".join(spec.name for spec in TOOL_REGISTRY)
        )

    # ───────────────────────────────

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

        Thin delegation to SearchService (search.py). ``search_only=True``
        returns a pure provider search; ``search_only=False`` also fetches
        candidate pages via the toolbox research pipeline (``research``).
        """
        return self._search.web_search(
            query,
            search_only=search_only,
            max_results=max_results,
            depth=depth,
            max_tokens=max_tokens,
            provider=provider,
        )

    def research_by_category(
        self, query: str, max_results: int = 5, provider: Optional[str] = None
    ) -> str:
        """Category-aware, provider-specific search (P8 tool ``research_by_category``).

        Thin wrapper over :func:`research_categories.search_category`. Classifies
        *query* into a domain category (scholarly / legal / financial / geo /
        general) and triggers **one** provider. Pass ``provider=<id>`` to call a
        specific provider separately (e.g. ``provider=crossref`` for a
        scholarly query); otherwise the category's default (first) provider is
        used. There is no automatic fallback between providers -- the caller
        chooses which source to query. Returns a JSON payload naming the chosen
        category, the provider actually called, and results.
        """
        return json.dumps(
            search_category(self, query, provider=provider, max_results=max_results),
            indent=2,
            default=str,
        )

    def research_categories(self) -> str:
        """Introspection: the category -> provider map (NOT an MCP tool).

        Returns the research taxonomy as JSON so callers can enumerate the
        available categories, their purposes and the provider each routes to.
        Deliberately *not* registered in ``TOOL_REGISTRY``, so it is not part
        of the MCP surface; call it directly on the toolbox.
        """
        payload = [
            {
                "category": c.name,
                "description": c.description,
                "providers": list(c.providers),
                "default_provider": c.default_provider,
                "provider_kind": c.kind,
            }
            for c in CATEGORIES
        ]
        return json.dumps(payload, indent=2)

    def export_citations(
        self,
        results,
        style: str = "bibtex",
        enrich: bool = False,
        dedupe: bool = True,
    ) -> str:
        """Reconstruct and export citations from results (Plan workstream 1).

        ``results`` is a list of entries; each entry is a bare DOI
        (``10.xxxx/...``), a URL, or a JSON-serialized adapter result dict
        (any of the dicts returned by ``web_search`` / ``research`` /
        ``research_by_category``). DOIs and URLs are extracted from the
        scholarly adapters' ``doi`` / ``url`` fields.

        APA (7th) renders through citeproc-py (official CSL engine) with the
        bundled ``apa.csl`` style when citeproc-py is installed, falling back
        to a best-effort approximation otherwise. MLA (9th) uses the
        approximation -- no MLA style ships with citeproc-py.

        Parameters
        ----------
        style:
            One of ``bibtex`` / ``csl-json`` / ``apa`` / ``mla``.
        enrich:
            When true, make one canonical DOI lookup per unique DOI to fill
            in a missing venue / abstract (best-effort; never raises).
        dedupe:
            Collapse records sharing a DOI or URL before formatting.

        Returns the formatted citations as text (empty-result case returns a
        JSON error dict so callers never branch on an empty string). Never
        raises: a bad *style* or *results* yields a JSON error dict.
        """
        parsed = []
        for item in (results or []):
            if isinstance(item, str):
                try:
                    loaded = json.loads(item)
                except (json.JSONDecodeError, TypeError):
                    loaded = None
                if isinstance(loaded, dict):
                    parsed.append(loaded)
                    continue
            parsed.append(item)
        try:
            text = format_citations(
                parsed, style=style, enrich=enrich, dedupe=dedupe
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, indent=2)
        if not text:
            return json.dumps(
                {
                    "error": "no citable records: pass DOIs, URLs, or result dicts",
                    "style": style,
                    "count": 0,
                },
                indent=2,
            )
        return text

    def check_sources(self, urls: list, mode: str = "status") -> str:
        """Probe source URLs for reachability without downloading pages.

        (Workstream 2) Mirrors research()'s fan-out: validate each URL
        through the SSRF guard, then probe it politely (per-domain
        throttle). Each probe is a lightweight status check (HEAD/
        minimal GET) -- never a full page load -- so a batch of hundreds
        of URLs stays cheap. Returns a JSON envelope with per-URL status
        plus a summary. ``mode=\"status\"`` (default) is the current probe;
        ``\"content\"`` is reserved for a future full-fetch variant.
        """
        try:
            if mode not in ("status", "content"):
                mode = "status"
            # Normalise to a clean list of URLs (accept plain strings or
            # {url}/{href}/{link} dicts).
            raw = []
            for u in urls or []:
                if isinstance(u, dict):
                    url = u.get("url") or u.get("href") or u.get("link")
                else:
                    url = u
                if not isinstance(url, str):
                    continue
                url = url.strip()
                if url:
                    raw.append(url)
            # De-dup by normalised URL (Workstream 2 shared helper) so the
            # same resource is not probed twice. dedupe works on dicts, so
            # wrap, dedupe, then unwrap back to plain URL strings.
            kept, _ = dedupe([{"url": u} for u in raw], by=("url",))
            urls_to_probe = [d["url"] for d in kept]

            results = []
            for url in urls_to_probe:
                results.append(
                    check_liveness(
                        url,
                        timeout=self._liveness_timeout,
                        throttle=self._rate_limit_domain,
                    )
                )

            summary = {"ok": 0, "unreachable": 0, "blocked": 0, "error": 0}
            for r in results:
                status = r.get("status", "error")
                summary[status] = summary.get(status, 0) + 1

            return json.dumps(
                {
                    "tool": "check_sources",
                    "count": len(results),
                    "mode": mode,
                    "summary": summary,
                    "results": results,
                },
                indent=2,
            )
        except Exception as exc:  # noqa: BLE001 -- never let the tool crash
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}", "results": []},
                indent=2,
            )

    def search_web(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> str:
        """Pure provider search (delegates to SearchService).

        Dedupes + optionally cross-merges results, caches them under a
        result-level key, and never raises (returns a JSON error dict).
        """
        return self._search.search_web(query, max_results=max_results, provider=provider)

    async def search_web_async(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> str:
        """Async version of search_web.

        "Async" here means *thread pool*: the blocking provider call is
        offloaded to the default executor via run_in_executor so the event
        loop stays responsive. Delegates to SearchService (search.py)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.search_web, query, max_results, provider
        )

    # ───────────────────────────────
    # ───────────────────────
    # HTML Page Inspection
    # ───────────────────────

    def inspect_html_page(
        self,
        url: str,
        use_smart: str = FetchMode.AUTO.value,
        query: Optional[str] = None,
        offset: int = 0,
        max_chunks: int = 1,
        structured: bool = False,
        store_dir: Optional[str] = None,
    ) -> str:
        """
        Fetch and extract markdown + follow-up links + HTML metadata from a web page.

        When ``structured=True`` the page is returned as a structured
        ``ParsedDocumentPayload`` (metadata, tables, links) instead of
        raw markdown + a compact metadata summary -- the same payload
        ``extract_document(structured=True)`` produces. With the default
        ``structured=False`` the text/markdown shape is returned (and
        ``query`` / ``offset`` / ``max_chunks`` paging apply).

        Results are served from the two-tier cache when a fresh entry exists.

        Parameters
        ----------
        url : str
            The URL to inspect.
        use_smart : {"auto", "browser", "static"}
            Per-call render strategy. "auto" (default) follows fetch_mode
            (static first, stealth browser on failure/non-text); "browser"
            renders with the headless browser first (static on failure);
            "static" is static-only.
        query : str, optional
            The research query. When the page does not fit the output
            budget, only the sections most relevant to this query are
            returned; the payload then reports sections_available /
            sections_selected / section_anchors so the caller knows what
            it is (and is not) seeing.
        offset : int, optional
            Character offset into the full page markdown to start reading
            from (default 0). Pass the previous call's ``next_offset`` to
            resume where it stopped; paging reads the full cached markdown
            at read time, so no re-fetch happens.
        max_chunks : int, optional
            Number of consecutive budget-sized chunks to return (default 1).
            Each chunk individually respects the output budget; the total
            payload may exceed it. Explicit paging takes precedence over
            query-based section selection.
        store_dir : str, optional
            When set, persist the *full* page markdown plus its images to a
            ``<stem>.md`` / ``<stem>.files/`` pair under this directory and
            return a JSON manifest (paths + rewritten body) instead of the
            markdown slice. Image refs are downloaded and rewritten to local
            relative paths, so the store is self-contained. Mutually with
            ``structured`` (structured output ignores ``store_dir``).
        """
        if structured:
            return self._doc._inspect_html_structured_impl(url, use_smart)
        return self._fetch._inspect_html_page_impl(
            url, use_smart, query, offset, max_chunks,
            store_dir=store_dir,
        )

    async def inspect_html_page_async(
        self,
        url: str,
        use_smart: str = FetchMode.AUTO.value,
        query: Optional[str] = None,
        offset: int = 0,
        max_chunks: int = 1,
        store_dir: Optional[str] = None,
    ) -> str:
        """Async version of inspect_html_page.

        "Async" here means *thread pool*: the shared blocking implementation
        is offloaded to the default executor via run_in_executor so the event
        loop stays responsive. The underlying fetch remains synchronous --
        see the README "Async" note. ``store_dir`` (when set) persists the
        full page markdown + images to a ``<stem>.md`` / ``<stem>.files/``
        pair and returns a manifest, mirroring the sync method.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._fetch._inspect_html_page_impl(
                url, use_smart, query, offset, max_chunks, store_dir=store_dir,
            ),
        )

    # ───────────────────────
    # Batch Inspection
    # ───────────────────────

    def batch_inspect_pages(self, urls: list) -> str:
        """
        Fetch multiple pages concurrently using the Rust batch engine.

        Pages already in the page cache are served straight from it (C6);
        only genuinely new URLs reach the fetch engine, and fetched pages
        are stored back into the cache so later single-page inspections
        (and repeated batches) are nearly free. Output is merged back in
        the caller's input order, and every entry has the same shape as an
        ``inspect_html_page`` result (metadata, cache_hit, fetch_method).

        With ``fetch_mode="browser"`` every page is fetched sequentially
        through the stealth browser instead (per-domain rate limits apply).
        """
        return self._fetch.batch_inspect_pages_impl(urls)

    async def batch_inspect_pages_async(self, urls: list) -> str:
        """Async version of batch_inspect_pages.

        "Async" here means *thread pool*: the shared blocking batch
        implementation is offloaded to the default executor via
        run_in_executor so the event loop stays responsive. The underlying
        batch fetch remains synchronous -- see the README "Async" note.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.batch_inspect_pages, urls
        )

    def extract_document(
        self,
        source: str,
        pages: Optional[str] = None,
        structured: bool = False,
        *,
        store: bool = False,
        store_dir: Optional[str] = None,
    ) -> str:
        """Extract text content from documents (PDF/DOCX/XLSX/PPTX, JSON,
        XML/RSS feeds, and text-like bodies). Thin delegation to
        ``DocumentExtractor``; see that method for the full contract. With
        ``store=true`` the original bytes and extracted markdown are written
        to disk and the result's ``stored`` field reports the paths."""
        return self._doc.extract_document(
            source, pages, structured, store=store, store_dir=store_dir
        )

    def extract_document_structured(self, source: str) -> str:
        """Extract a document into a structured ParsedDocumentPayload
        (metadata, pages, tables). Thin delegation to
        ``DocumentExtractor``.
        """
        return self._doc.extract_document_structured(source)

    def inspect_html_structured(
        self, url: str, use_smart: str = FetchMode.AUTO.value
    ) -> str:
        """Fetch a web page and return it as a structured
        ParsedDocumentPayload (metadata, markdown, tables, links). Thin
        delegation to ``DocumentExtractor``.
        """
        return self._doc.inspect_html_structured(url, use_smart)

    def focused_discovery(
        self,
        root_url: str,
        query: Optional[str] = None,
        max_depth: int = 3,
        max_pages: int = 15,
        same_host: bool = False,
        min_score: float = 0.05,
        excerpts: bool = False,
        search_prior: bool = False,
        seed_urls: Optional[list] = None,
    ) -> str:
        """Focused, relevance-ranked traversal of a URL's link graph to
        find the pages most relevant to a query (thin delegation to
        ``Crawler`` in crawl.py; see that method for the full algorithm).
        Follows cross-domain links unless same_host=True.
        """
        return self._crawler.crawl(
            root_url,
            query=query,
            max_depth=max_depth,
            max_pages=max_pages,
            same_host=same_host,
            min_score=min_score,
            excerpts=excerpts,
            search_prior=search_prior,
            seed_urls=seed_urls,
        )

    def discover_resources(self, url: str) -> str:
        """Discover a site's structured resources (Tier 3.12). Thin
        delegation to ``ResourceDiscovery`` (discovery.py); see that
        method for the full algorithm.
        """
        return self._discovery.discover_resources(url)

    # ------------------------------------------------------------------
    # Research orchestration (Tier 3.13)
    # ------------------------------------------------------------------

    #: Hard cap on pages fetched per research run.
    _RESEARCH_MAX_PAGES = 10

    def research(
        self, topic: str, depth: int = 5, max_tokens: int = 0
    ) -> str:
        """Run a small orchestrated research pass (Tier 3.13).

        Plan -> fan out -> dedupe, on top of the existing pipeline:

        1. Search *topic* through the configured providers.
        2. Dedupe and validate the result URLs, keeping the top
           *depth* (hard cap ``_RESEARCH_MAX_PAGES``).
        3. Fetch each through the normal page pipeline
           (``_inspect_html_page_impl``) with cache, robots, rate
           limits, and provenance.

        The toolbox has no LLM: the returned JSON carries per-source
        status, markdown content, and provenance (plus the search
        title/snippet) - exactly what the calling agent needs to write
        a cited synthesis itself.

        Returns
        -------
        str
            JSON: ``topic``, ``depth``, ``sources`` (list of
            ``{url, title, snippet, status, result?|error?}``), and
            ``count`` (successful fetches).
        """
        topic = (topic or "").strip()
        if not topic:
            return json.dumps(
                {"error": "research: topic is required"}, indent=2
            )
        try:
            depth = int(depth)
        except (TypeError, ValueError):
            depth = 5
        depth = max(1, min(depth, self._RESEARCH_MAX_PAGES))

        # 1) Plan: search the topic (search_web degrades to an error
        # dict instead of raising when every provider fails).
        try:
            results = json.loads(
                self.search_web(topic, max_results=min(depth * 2, 20))
            )
        except (json.JSONDecodeError, TypeError):
            results = []
        if not isinstance(results, list):
            results = []

        # 2) Validate + normalise candidate URLs (SSRF). search_web() has
        # already deduped by URL (Workstream 2) and surfaced the drop count;
        # this pass only enforces the SSRF guard and takes the top *depth*.
        candidates = []
        for item in results:
            if not isinstance(item, dict):
                continue
            try:
                url = normalize_url(str(item.get("url") or ""))
            except ValueError:
                continue  # non-http scheme or malformed: skip
            if not url:
                continue
            try:
                self._validate_url(url)
            except Exception:
                continue  # SSRF guard: skip quietly
            candidates.append(
                (
                    url,
                    str(item.get("title") or ""),
                    str(item.get("snippet") or ""),
                )
            )
        candidates = candidates[:depth]
        dropped_dupes = getattr(self._search, "_last_search_dropped", 0)

        # 3) Fan out through the normal page pipeline. Each source gets
        # the toolbox default budget; the final global budget is applied
        # to the whole response below, so later sources are the first to
        # give ground under budget pressure.
        sources = []
        for url, title, snippet in candidates:
            record = {"url": url, "title": title, "snippet": snippet}
            try:
                raw_page = self._fetch._inspect_html_page_impl(
                    url, None, "", 0, 1
                )
                try:
                    page = json.loads(raw_page)
                except json.JSONDecodeError:
                    page = raw_page
            except Exception as e:
                logger.warning("research fetch failed for %s: %s", url, e)
                record["status"] = "error"
                record["error"] = str(e)
                sources.append(record)
                continue
            # _inspect_html_page_impl reports failures (fetch errors,
            # robots disallow, already visited) as {"error"|"warning": ...}
            # dicts rather than raising.
            if isinstance(page, dict) and (
                "error" in page or "warning" in page
            ):
                record["status"] = "error"
                record["error"] = str(
                    page.get("error") or page.get("warning")
                )
            else:
                record["status"] = "ok"
                record["result"] = page
            sources.append(record)

        budget = max_tokens if max_tokens and max_tokens > 0 else self.max_tokens
        result = {
            "topic": topic,
            "depth": depth,
            "sources": sources,
            "count": sum(1 for s in sources if s["status"] == "ok"),
            # Workstream 2: how many candidate URLs were collapsed as
            # duplicates before the fan-out (informational for the caller).
            "dropped_dupes": dropped_dupes,
        }
        return self._budget._fit_json(
            lambda b: self._budget._shrink_research(result, b),
            self.max_markdown_chars,
            budget,
            {
                "topic": topic,
                "depth": depth,
                "error": "research result too large for the output budget",
                "hint": "lower depth or raise max_tokens",
            },
        )

    # Stats & Management
    # ───────────────────────────────

    def get_stats(self) -> str:
        """Return toolbox statistics."""
        return json.dumps(
            {
                "visited_urls_count": len(self.visited_urls),
                "cache": self.cache.stats(),
                # Tier 2.6: fetch observability (latency percentiles, bytes,
                # per-domain request counts, error counts by class).
                "fetches": self._fetch_stats.to_dict(),
                "max_tokens": self.max_tokens,
                "model_name": self.model_name,
                # §7: guard measurement section (always present; zeroed when
                # the guard is disabled) for the cost/flag-rate A/B.
                "guard": self._guard.stats.to_dict(),
            },
            indent=2,
        )

    def manage_cache(self, action: str = "prune") -> str:
        """Unified cache maintenance (P8 tool ``manage_cache``).

        action : {"clear", "prune", "reset"}
            "clear"  -> wipe memory + disk caches and the visited set.
            "prune"  -> remove expired entries and evict to the size cap.
            "reset"  -> forget visited URLs (caches are NOT cleared).
        """
        if action == "clear":
            return self.clear_cache()
        if action == "reset":
            return self.reset_visited()
        return self.prune_cache()

    def reset_visited(self) -> str:
        """Clear the visited URL set (and in-flight claims, S5).

        P8: returns a confirmation JSON (was ``None``) so every surface —
        MCP tool, LLM tool, and execute_tool — gets the same reply."""
        with self._visit_lock:
            self.visited_urls.clear()
            self._in_flight.clear()
        return json.dumps(
            {"visited_cleared": True, "visited_urls_count": 0}, indent=2
        )

    def clear_cache(self) -> str:
        """Clear both memory and disk caches and the visited-URL set (C3:
        after a cache reset, previously visited URLs can be re-fetched).

        S6: the disk cache is cleared by removing only cache-owned
        files (``*.cache``/``*.meta``/``*.tmp``) -- the configured cache
        directory and any unrelated files in it are left intact."""
        self.cache.clear()
        self._search._search_cache_clear()
        self.reset_visited()
        return json.dumps({"cache_cleared": True, "stats": self.cache.stats()}, indent=2)

    def prune_cache(self) -> str:
        """Remove expired cache entries and evict to the size cap (Tier 2.5).

        Unlike ``clear_cache`` this keeps valid, in-TTL entries and does not
        touch the visited-URL set -- it only sweeps entries that expired while
        never being requested again and trims the disk cache to
        ``cache_max_bytes``. Returns a summary of what was removed."""
        return json.dumps({"prune": self.cache.prune()}, indent=2)

