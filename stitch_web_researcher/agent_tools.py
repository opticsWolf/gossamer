import asyncio
import copy
import json
import logging
import random
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

import httpx
from pydantic import BaseModel, Field
from pdf_oxide import PdfDocument
from office_oxide import Document as OfficeDoc

from stitch_web_researcher._core import (
    batch_research,
    fetch_html_full,
    extract_links_from_html as _extract_links_from_html,
    process_rendered_html as _process_rendered_html,
    extract_main_content_markdown,
)
from stitch_web_researcher.token_budget import truncate_to_tokens, count_tokens
from stitch_web_researcher.structured_parser import (
    StructuredOxideParser,
    FollowUpCandidate,
    build_follow_up_candidates,
)
from stitch_web_researcher.search_providers import (
    DuckDuckGoProvider,
    RateLimit,
    resolve_provider_name,
)
from stitch_web_researcher import meta_extractor
from stitch_web_researcher.cache import Cache
from stitch_web_researcher.ssrf import SsrfBlockedError, validate_public_url

logger = logging.getLogger(__name__)


# ───────────────────────────────
# Inspection output models (guarantee clean, schema-valid JSON)
# ───────────────────────────────

class ExtractionResult(BaseModel):
    """Unified result of a document extraction (cache hit or fresh).

    Both paths serialize to the same schema so consumers never need to
    branch on how the content was obtained.
    """
    source: str
    content: str = ""
    content_tokens: int = 0
    cache_hit: bool = False


class InspectionResult(BaseModel):
    """Structured result of an HTML page inspection.

    ``truncated`` is True whenever the delivered link list is not the
    complete set found on the page — either the collection cap was hit
    (link_cap) or the output budget forced candidates to be dropped.
    """
    url: str
    markdown: str = ""
    markdown_tokens: int = 0
    follow_up_links: list[FollowUpCandidate] = Field(default_factory=list)
    delivered_links: int = 0
    total_links: int = 0
    truncated: bool = False
    fetch_method: Optional[str] = None
    cache_hit: bool = False
    metadata: dict = Field(default_factory=dict)

# ───────────────────────────────
# Smart fetch (browser_oxide with fallback)
# ───────────────────────────────

_browser_oxide_available = False
try:
    import browser_oxide
    _browser_oxide_available = True
except ImportError:
    pass


def _fetch_with_browser_oxide(url: str) -> tuple[str, list[str], dict]:
    """Fetch a page using browser_oxide (stealth headless browser).

    Navigation runs in browser_oxide; link extraction and markdown
    conversion run in the Rust core (``_core.process_rendered_html``),
    so only ``browser_oxide`` itself needs to be installed.

    Returns (markdown, links, metadata) tuple.
    """
    browser = browser_oxide.Browser(profile=browser_oxide.Profile.chrome())
    try:
        page = browser.navigate(url, max_iterations=5)
        if page.is_challenge:
            raise RuntimeError(
                f"Anti-bot challenge detected ({page.verdict}) for {url}"
            )
        html = page.html
    finally:
        browser.close()

    # Extract HTML metadata via meta-oxide
    metadata = meta_extractor.extract_all(html, url)

    # Debug visibility: record which main-content container the Rust core's
    # heuristics selected (article / main / [role='main'] / .content / …).
    selector_label, _md = extract_main_content_markdown(html)
    metadata["content_selector"] = selector_label

    # Anchored links + markdown via the Rust core
    links = _extract_links_from_html(html, url, 100)
    markdown, _links, removed = _process_rendered_html(html, url)
    # S2: report how many hidden nodes the Rust core stripped.
    if removed:
        metadata["hidden_blocks_removed"] = removed
    return markdown, links, metadata


# ───────────────────────────────
# Link classification & follow-up helpers
# (moved to structured_parser.py in 0.1.4 so the structured payload can
#  share the same FollowUpCandidate model; re-exported above for
#  backwards compatibility)
# ───────────────────────────────


def fetch_smart_page(url: str) -> tuple[str, list[str], dict]:
    """Fetch a page with headless JS rendering via browser_oxide.

    Falls back to a static Rust fetch if browser_oxide is unavailable or
    fails. The fallback now extracts metadata from the fetched HTML too
    (C2), so both paths return the same (markdown, links, metadata) shape.

    Returns (markdown, links, metadata) tuple.
    """
    # S1: this function is reachable with LLM-supplied URLs; the Rust
    # static path enforces the SSRF policy itself, but the browser path
    # needs the check here.
    validate_public_url(url)

    if _browser_oxide_available:
        try:
            return _fetch_with_browser_oxide(url)
        except Exception as e:
            logger.warning(
                "browser_oxide smart fetch failed for %s: %s -- falling back to static",
                url, e,
            )

    # Fallback to static Rust fetch — fetch_html_full keeps the raw HTML so
    # metadata extraction matches the browser path (C2).
    html, md, links, removed = fetch_html_full(url, 100)
    metadata = meta_extractor.extract_all(html, url)
    if removed:
        metadata["hidden_blocks_removed"] = removed
    return md, links, metadata


# ───────────────────────────────
# Retry & Rate-Limit Utilities
# ───────────────────────────────

def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Exponential-backoff retry decorator for Python-layer methods."""
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


# ───────────────────────────────
# WebResearcherToolbox
# ───────────────────────────────

# Module-level LLM function-calling tool definitions (static data).
_LLM_TOOL_DEFINITIONS = [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the web using one or more search providers. Set provider to choose a specific engine; falls back through others on failure.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default: 5)",
                            "default": 5,
                        },
                        "provider": {
                            "type": "string",
                            "enum": ["duckduckgo", "google", "bing", "exa"],
                            "description": "Search engine to prefer. Falls back through other providers on failure.",
                            "default": "duckduckgo",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "inspect_html_page",
                "description": "Fetch and extract markdown content from a web page. Set use_smart=True for JS-rendered pages (SPA, anti-bot). Returns markdown text and follow-up links.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to inspect",
                        },
                        "use_smart": {
                            "type": "boolean",
                            "description": "If true, use headless browser rendering (browser_oxide) for JS-heavy pages",
                            "default": False,
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "batch_inspect_pages",
                "description": "Fetch multiple web pages concurrently. Returns markdown and links for each.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of URLs to inspect",
                        },
                    },
                    "required": ["urls"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "extract_document",
                "description": "Extract text content from PDF, DOCX, or XLSX documents via URL or local path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "URL or local file path to the document",
                        },
                    },
                    "required": ["source"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "extract_document_structured",
                "description": "Extract structured content (metadata, pages, tables) from PDF, DOCX, XLSX, or PPTX documents via URL or local path. Returns a validated ParsedDocumentPayload as JSON.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "URL or local file path to the document",
                        },
                    },
                    "required": ["source"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "inspect_html_structured",
                "description": "Fetch a web page and return it as a structured ParsedDocumentPayload with metadata (OG, Twitter, JSON-LD), markdown content, and links. Set use_smart=True for JS-rendered pages.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to inspect",
                        },
                        "use_smart": {
                            "type": "boolean",
                            "description": "If true, use headless browser rendering (browser_oxide) for JS-heavy pages",
                            "default": False,
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "clear_cache",
                "description": "Clear both the in-memory and disk research caches and the visited-URL set. Use when you want to force fresh fetches (e.g., starting a new research session or suspecting stale content). Returns confirmation with post-clear statistics.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "reset_visited",
                "description": "Forget all previously visited URLs so they can be fetched again (caches are NOT cleared). Use after a fetch failure you want to retry, or when starting a new research session on the same pages.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
]



def normalize_url(raw: str, base: Optional[str] = None) -> str:
    """Auto-convert URL-like strings into proper absolute URLs.

    Handles the messy inputs an LLM tends to produce:
      - surrounding whitespace / quotes / angle brackets
      - missing scheme ("example.com/doc.pdf", "www.example.com/a")
      - protocol-relative ("//cdn.example.com/x")
      - page-relative paths ("/files/report.pdf") when *base* is given

    Returns a clean absolute http(s) URL; raises ValueError for strings
    that cannot be interpreted as one.
    """
    s = (raw or '').strip().strip('"\'').strip("<>").strip()
    if not s:
        raise ValueError("Empty URL")

    # Page-relative or root-relative path: resolve against base first.
    if base and not urlparse(s).scheme and not s.startswith("//"):
        s = urljoin(base, s)

    # Protocol-relative //host/path
    if s.startswith("//"):
        s = "https:" + s

    parsed = urlparse(s)
    if " " in s:
        raise ValueError(f"Cannot interpret {raw!r} as a URL (contains spaces)")

    if not parsed.scheme:
        # Bare domain like "example.com/a" or "www.example.com".
        # Only auto-prefix when the host looks domain-like; a bare word
        # is more likely a mistake than an intranet hostname.
        candidate_host = parsed.path.split("/")[0]
        if "." not in candidate_host and candidate_host != "localhost":
            raise ValueError(f"{raw!r} does not look like a URL")
        s = "https://" + s
        parsed = urlparse(s)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme in {raw!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"Cannot parse {raw!r} as a URL (no host)")
    return s


@dataclass
class ToolboxConfig:
    """Construction options for :class:`WebResearcherToolbox`.

    Grouping the knobs in one object keeps the toolbox constructor stable as
    options grow. All fields have the same defaults the toolbox historically
    used.
    """

    cache_dir: str = ".web_research_cache"
    cache_ttl_seconds: int = 3600
    ddgs_delay: float = 1.0
    domain_delay: float = 0.5
    max_markdown_chars: int = 8000
    max_tokens: int = 0
    model_name: str = "gpt-4o"
    max_links: int = 20
    search_providers: Optional[list] = None
    default_provider_index: int = 0
    fetch_delay: Optional[float] = None
    fetch_mode: str = "auto"
    candidate_cap: int = 500
    max_concurrency: int = 8
    # Fraction of the output budget (chars/tokens) reserved for the
    # follow-up link list and JSON envelope, so budget enforcement never
    # starves link delivery on content-rich pages (C1).
    link_budget_ratio: float = 0.25

    def __post_init__(self):
        if self.fetch_mode not in ("auto", "browser", "static"):
            raise ValueError(
                f"Invalid fetch_mode {self.fetch_mode!r}; expected 'auto', 'browser', or 'static'"
            )
        if self.candidate_cap < 1:
            raise ValueError("candidate_cap must be >= 1")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if not 0.0 <= self.link_budget_ratio < 0.9:
            raise ValueError("link_budget_ratio must be in [0.0, 0.9)")


class WebResearcherToolbox:
    """LLM tool routing layer with caching, rate limiting, and token budgeting."""

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
        )
        self.fetch_mode = config.fetch_mode
        self.ddgs_delay = config.ddgs_delay
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
            self.providers = [DuckDuckGoProvider(delay=config.ddgs_delay)]
        idx = config.default_provider_index
        try:
            self.default_provider = self.providers[idx]
        except IndexError as e:
            raise IndexError(
                f"default_provider_index {idx} out of range for "
                f"{len(self.providers)} provider(s)"
            ) from e

        self._fetch_interval = self._resolve_fetch_interval(config)

        self.visited_urls: set[str] = set()
        self._domain_last_seen: dict[str, float] = defaultdict(float)
        self._ua_index = 0
        # Candidate pool size: how many links we collect per page before
        # handing ALL of them to the LLM for topic-based selection.
        self.link_cap = max(1, int(config.candidate_cap))
        # Upper bound on simultaneous connections opened by batch fetching.
        self.max_concurrency = max(1, int(config.max_concurrency))

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

    def _truncate(
        self, text: str, char_limit: int, token_limit: int = 0
    ) -> str:
        """
        Apply token-aware truncation.

        1. If *token_limit* > 0, truncate to that many tokens first.
        2. Then apply the character limit as a safety cap.

        This two-pass approach ensures we never exceed the token
        budget (primary constraint) while also staying below the
        character ceiling (fallback safety net).
        """
        if token_limit > 0:
            text = truncate_to_tokens(text, token_limit, self.model_name)
        if len(text) > char_limit:
            text = text[:char_limit] + "\n\n... [truncated]"
        return text

    def _content_budget(self) -> tuple[int, int]:
        """(markdown_chars, markdown_tokens) budget with a links reserve.

        A fraction of the output budget (``link_budget_ratio``) is held back
        for the follow-up link list and the JSON envelope so that budget
        enforcement in ``_build_inspection_result`` always has room to keep
        at least some links on content-rich pages (C1: previously the
        markdown was truncated to exactly the envelope budget, leaving zero
        room for links, which were then all dropped).
        """
        keep = 1.0 - self.link_budget_ratio
        chars = int(self.max_markdown_chars * keep)
        tokens = int(self.max_tokens * keep) if self.max_tokens > 0 else 0
        return chars, tokens

    def _next_headers(self) -> dict:
        """Rotate User-Agent and return full browser headers."""
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

    def _rate_limit_domain(self, url: str) -> None:
        """Enforce per-domain rate limiting for content fetching.

        The minimum gap between same-domain fetches is the resolved
        ``_fetch_interval`` plus a random 0–1 s jitter (only when the
        interval is non-zero), which desynchronizes access patterns.
        """
        parsed = urlparse(url)
        domain = parsed.netloc
        last_seen = self._domain_last_seen[domain]
        elapsed = time.time() - last_seen
        gap = self._fetch_interval
        if gap > 0:
            gap += random.uniform(0.0, 1.0)
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._domain_last_seen[domain] = time.time()

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
        return copy.deepcopy(_LLM_TOOL_DEFINITIONS)

    # ───────────────────────────────
    # Search
    # ───────────────────────────────

    @retry(max_attempts=3, delay=1.0, backoff=2.0)
    def search_web(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> str:
        """
        Search the web using a specific provider or the default.

        Parameters
        ----------
        query : str
            The search query.
        max_results : int
            Maximum number of results to return.
        provider : str or None
            Provider name (e.g., "duckduckgo", "google", "bing", "exa").
            Falls back through all providers if the chosen one fails.

        Returns
        -------
        str
            JSON-serialised list of results or error dict.
        """
        # Resolve provider
        providers_to_try = self._resolve_providers(provider)

        for prov in providers_to_try:
            try:
                results = prov.search(query, max_results=max_results)
                return json.dumps(results, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(
                    "Provider %s failed for '%s': %s — trying next",
                    prov.__class__.__name__, query, e,
                )

        # All providers failed
        return json.dumps({"error": f"All search providers failed for: {query}"}, indent=2)

    def _resolve_providers(self, provider_name: Optional[str]) -> list:
        """
        Build an ordered list of providers to try.

        If *provider_name* is given, put that provider first, then fall
        back through the rest.  If None, use the full provider list in
        registration order.
        """
        if not provider_name:
            return list(self.providers)

        canonical = resolve_provider_name(provider_name)
        if canonical:
            # Find matching providers and put them first
            matched = [
                p for p in self.providers
                if resolve_provider_name(p.__class__.__name__.replace("Provider", "").lower()) == canonical
            ]
            if matched:
                others = [p for p in self.providers if p not in matched]
                return matched + others

        # Unknown name — just use all providers
        return list(self.providers)

    async def search_web_async(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> str:
        """Async version of search_web."""
        # Search is I/O-bound but ddgs/google/bing SDKs are sync;
        # run in executor for non-blocking behaviour.
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.search_web, query, max_results, provider
        )

    # ───────────────────────────────
    # HTML Page Inspection
    # ───────────────────────────────

    def _format_follow_ups(self, anchored_links) -> list:
        """Turn (url, anchor_text) pairs into LLM-friendly candidates.

        Each candidate carries the anchor text (so the model can judge
        relevance by name) and a 'type' hint: 'document' links should be
        fetched via extract_document, 'page' links via inspect_html_page.

        No truncation here: the caller (LLM) performs relevance selection
        itself, based on the research topic; we deliver the complete
        titled/typed candidate list. (The structured payload path, in
        contrast, honors max_links.)
        """
        return build_follow_up_candidates(anchored_links)

    def _build_inspection_result(
        self,
        url: str,
        markdown: str,
        links_pairs,
        meta_summary: dict,
        fetch_method: Optional[str],
        markdown_truncated: bool = False,
    ) -> InspectionResult:
        """Assemble an InspectionResult and enforce the output budget.

        Budget enforcement drops candidates from the END (halving) until the
        serialized JSON fits within max_markdown_chars / max_tokens. The
        markdown is expected to have been pre-truncated with a links reserve
        (``_content_budget``) so that the envelope has room to keep some
        links on content-rich pages (C1). The ``truncated`` flag records any
        loss — collection-cap hits, markdown truncation, AND budget-driven
        drops — so the model always knows whether it saw every link.
        ``delivered_links`` makes the flag actionable. The returned JSON is
        schema-valid by construction (Pydantic serializes it; we never
        string-cut the output).
        """
        result = InspectionResult(
            url=url,
            markdown=markdown,
            markdown_tokens=count_tokens(markdown, self.model_name),
            follow_up_links=self._format_follow_ups(links_pairs),
            total_links=len(links_pairs),
            truncated=len(links_pairs) >= self.link_cap,
            fetch_method=fetch_method,
            metadata=meta_summary or {},
        )

        candidates = result.follow_up_links
        while True:
            payload = result.model_dump_json()
            over_chars = len(payload) > self.max_markdown_chars
            over_tokens = (
                self.max_tokens > 0
                and count_tokens(payload, self.model_name) > self.max_tokens
            )
            if not (over_chars or over_tokens):
                break
            if not result.follow_up_links:
                break  # nothing left to drop; markdown is already pre-truncated
            keep = len(result.follow_up_links) // 2
            result.follow_up_links = result.follow_up_links[:keep]
            result.truncated = True
        result.truncated = result.truncated or markdown_truncated
        result.delivered_links = len(result.follow_up_links)
        result.total_links = max(result.total_links, len(candidates))
        return result

    # ── Fetch strategies ─────────────────────────────
    # Each returns the full (markdown, anchored_links, metadata, method) tuple.

    def _static_fetch(self, url: str):
        """Plain HTTP fetch via the Rust core.

        C2: the Rust core returns the raw HTML alongside the markdown and
        links, so the static path runs the same meta-oxide metadata
        extraction as the browser path — no second network round-trip.
        """
        html, md, links, removed = fetch_html_full(url, self.link_cap)
        metadata = meta_extractor.extract_all(html, url)
        if removed:
            metadata["hidden_blocks_removed"] = removed
        return md, links, metadata, "static"

    def _browser_fetch(self, url: str):
        """Stealth-browser fetch; failures propagate (strict)."""
        md, links, meta = _fetch_with_browser_oxide(url)
        return md, links, meta, "browser"

    def _fetch_html(self, url: str, use_smart: Optional[bool] = None):
        """Fetch an HTML page honoring ``self.fetch_mode``.

        Modes:
            "browser": every fetch goes through the stealth browser;
                failures propagate (strict).
            "static": plain HTTP fetch via the Rust core only.
            "auto": static first; falls back to the stealth browser when
                the static fetch raises or returns non-text content.

        ``use_smart`` overrides per call: ``False`` forces static-only,
        ``True`` tries the stealth browser first (falling back to static).
        """
        if self.fetch_mode == "browser":
            if use_smart is False:
                return self._static_fetch(url)
            return self._browser_fetch(url)

        if use_smart is True:
            try:
                return self._browser_fetch(url)
            except Exception as e:
                logger.warning(
                    "Stealth fetch failed for %s: %s -- falling back to static", url, e
                )

        if self.fetch_mode == "static" or use_smart is False:
            return self._static_fetch(url)

        # auto: static first, stealth fallback on failure or non-text content
        try:
            result = self._static_fetch(url)
            if self._looks_like_text(result[0]):
                return result
            logger.info("Static fetch returned non-text content for %s", url)
        except Exception as e:
            logger.warning("Static fetch failed for %s: %s -- trying stealth browser", url, e)

        if not _browser_oxide_available:
            raise RuntimeError(
                f"Fetch failed for {url} and browser_oxide is not installed"
            )
        md, links, meta = _fetch_with_browser_oxide(url)
        return md, links, meta, "stealth-fallback"

    @staticmethod
    def _looks_like_text(md: str) -> bool:
        """Heuristic: reject empty or binary-garbage payloads (e.g. undecoded
        compressed responses), which would otherwise poison LLM context.

        Uses Unicode categories rather than printability: legitimate text in
        any language (incl. CJK) contains almost no control/format/unassigned
        codepoints, while binary bytes mis-decoded as text are full of them.
        """
        if not md or not md.strip():
            return False
        import unicodedata
        sample = md[:2000]
        bad = sum(
            unicodedata.category(c) in ("Cc", "Cn", "Co", "Cs")
            and c not in "\n\r\t"
            for c in sample
        )
        return bad / len(sample) < 0.02

    # ── Page-level two-tier cache helpers ───────────────

    def _page_cache_get(self, url: str):
        """Return a cached (markdown, links, meta, method) tuple, or None.

        Entries store the *untruncated* fetch result so budget changes
        between calls are honored on every read. Keys are namespaced
        ("page:") so they can never collide with structured-payload or
        document entries for the same URL.
        """
        raw = self.cache.get("page:" + self._cache_key(url))
        if raw is None:
            return None
        try:
            entry = json.loads(raw)
            return (
                entry.get("markdown", ""),
                [tuple(pair) for pair in entry.get("links", [])],
                entry.get("meta") or {},
                entry.get("method"),
            )
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.warning("Corrupt page-cache entry for %s -- refetching", url)
            return None

    def _page_cache_put(
        self,
        url: str,
        markdown: str,
        links: list,
        metadata: dict,
        method: Optional[str],
    ) -> None:
        """Cache an untruncated fetch result under the canonical URL key."""
        self.cache.put(
            "page:" + self._cache_key(url),
            json.dumps(
                {
                    "markdown": markdown,
                    "links": links,
                    "meta": metadata,
                    "method": method,
                },
                ensure_ascii=False,
            ),
        )

    def _inspect_html_page_impl(self, url: str, use_smart: Optional[bool] = None) -> str:
        """Shared implementation behind ``inspect_html_page`` (sync + async).

        Note on retries: the Rust core already retries transient HTTP
        failures 3× with exponential backoff; a Python-layer retry here
        would multiply attempts (3×3) and sleep redundantly, so none is
        applied at this layer.

        Visited-URL semantics (C3): a URL is marked visited only after a
        *successful* fetch, so a transient failure never blacklists it. A
        repeat visit to a URL whose result is still cached is served from
        the cache (``cache_hit: true``) instead of a content-free warning
        — data beats a warning.
        """
        url = normalize_url(url)
        self._validate_url(url)

        cached = self._page_cache_get(url)
        from_cache = cached is not None
        if cached is None:
            if url in self.visited_urls:
                logger.warning("URL already visited (not in cache): %s", url)
                return json.dumps(
                    {"warning": "URL already visited", "url": url}, indent=2
                )
            # Politeness delay applies only when we will actually fetch.
            self._rate_limit_domain(url)
            try:
                cached = self._fetch_html(url, use_smart)
            except Exception as e:
                logger.error("HTML inspection failed for %s: %s", url, e)
                return json.dumps(
                    {"error": f"HTML inspection failed: {str(e)}"}, indent=2
                )
            # Mark visited and cache only after a successful fetch (C3).
            self.visited_urls.add(url)
            self._page_cache_put(url, *cached)

        markdown, links, html_metadata, fetch_method = cached
        logger.info(
            "%s %s via %s (%d chars, %d links)",
            "Cached" if from_cache else "Fetched",
            url,
            fetch_method,
            len(markdown),
            len(links),
        )
        md_chars, md_tokens = self._content_budget()
        truncated_md = self._truncate(markdown, md_chars, md_tokens)

        # Build compact metadata summary for LLM output
        meta_summary = self._compact_metadata(html_metadata)

        result = self._build_inspection_result(
            url, truncated_md, links, meta_summary, fetch_method,
            markdown_truncated=truncated_md != markdown,
        )
        if from_cache:
            result.cache_hit = True
        return result.model_dump_json()

    def inspect_html_page(self, url: str, use_smart: Optional[bool] = None) -> str:
        """
        Fetch and extract markdown + follow-up links + HTML metadata from a web page.

        Results are served from the two-tier cache when a fresh entry exists.

        Parameters
        ----------
        url : str
            The URL to inspect.
        use_smart : bool
            If True, attempt headless JS rendering via browser_oxide first,
            then fall back to static reqwest fetch.
        """
        return self._inspect_html_page_impl(url, use_smart)

    def _compact_metadata(self, raw: dict) -> dict:
        """
        Compact raw meta-oxide output into a lean dict for LLM context.

        Keeps: title, description, canonical, og_title, og_type, og_image,
        twitter_card, twitter_title, jsonld (first 2 objects).
        """
        if not raw:
            return {}

        rel = raw.get("rel_links", {})
        compact: dict = {}

        # (section, source key, target key) triples — copied when truthy.
        field_copies = [
            ("meta", "title", "title"),
            ("meta", "description", "description"),
            ("meta", "canonical", "canonical"),
            ("meta", "language", "language"),
            ("opengraph", "title", "og_title"),
            ("opengraph", "type", "og_type"),
            ("opengraph", "image", "og_image"),
            ("twitter", "card", "twitter_card"),
            ("twitter", "title", "twitter_title"),
        ]
        for section, source_key, target_key in field_copies:
            value = raw.get(section, {}).get(source_key)
            if value:
                compact[target_key] = value

        jsonld = raw.get("jsonld") or []
        if jsonld:
            compact["jsonld"] = jsonld[:2]  # cap at 2 objects

        # A rel-link canonical overrides the plain meta canonical.
        if rel.get("canonical"):
            compact["canonical"] = rel["canonical"][0]
        if rel.get("alternate"):
            compact["alternates"] = rel["alternate"]

        # S2: surface how many hidden nodes were stripped from the
        # main-content fragment before markdown conversion.
        if raw.get("hidden_blocks_removed"):
            compact["hidden_blocks_removed"] = raw["hidden_blocks_removed"]

        return compact

    async def inspect_html_page_async(self, url: str, use_smart: Optional[bool] = None) -> str:
        """Async version of inspect_html_page (shared implementation,
        executed in the default executor to stay non-blocking)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._inspect_html_page_impl, url, use_smart
        )

    # ───────────────────────────────
    # Batch Inspection
    # ───────────────────────────────

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
        # Normalize (C6: single-page inspection normalizes too, so the same
        # page can never occupy two cache/visited entries), then partition
        # into cached vs. uncached. Visited URLs are marked only after
        # success (C3), so failed batch entries remain retryable.
        pending: list[str] = []
        cached_entries: dict[str, tuple] = {}
        seen = set()
        for raw in urls:
            url = normalize_url(raw)
            if url in seen:
                continue
            seen.add(url)
            self._validate_url(url)
            cached = self._page_cache_get(url)
            if cached is not None:
                cached_entries[url] = cached
                continue
            if url in self.visited_urls:
                logger.warning("Skipping already-visited URL in batch: %s", url)
                continue
            pending.append(url)

        try:
            fetched: dict[str, dict] = {}
            if self.fetch_mode == "browser":
                for url in pending:
                    try:
                        # Sequential stealth-browser fetches honor the same
                        # per-domain politeness gap as single fetches.
                        self._rate_limit_domain(url)
                        entry = self._fetch_html(url)
                        self.visited_urls.add(url)  # success only (C3)
                        self._page_cache_put(url, *entry)  # C6
                        fetched[url] = self._batch_result(
                            url, *entry, cache_hit=False
                        )
                    except Exception as e:
                        fetched[url] = {"url": url, "error": str(e)}
            else:
                results = []
                if pending:
                    results = batch_research(
                        pending,
                        max_links=self.link_cap,
                        max_concurrency=self.max_concurrency,
                        # Same-domain staggering inside the batch engine (0 disables).
                        domain_gap_ms=int(self._fetch_interval * 1000),
                    )
                for url, md_opt, links_opt in results:
                    if md_opt is not None and links_opt is not None:
                        self.visited_urls.add(url)  # success only (C3)
                        # C6: store back into the shared page cache. The
                        # batch engine has no metadata, so meta stays empty.
                        self._page_cache_put(url, md_opt, links_opt, {}, "static")
                        fetched[url] = self._batch_result(
                            url, md_opt, links_opt, {}, "static", cache_hit=False
                        )
                    else:
                        fetched[url] = {
                            "url": url, "error": md_opt or "Unknown error"
                        }

            # Merge cached + fetched entries back in input order (C6).
            output = []
            emitted = set()
            for raw in urls:
                url = normalize_url(raw)
                if url in emitted:
                    continue
                emitted.add(url)
                if url in cached_entries:
                    md, links, meta, method = cached_entries[url]
                    output.append(
                        self._batch_result(
                            url, md, links, meta, method, cache_hit=True
                        )
                    )
                elif url in fetched:
                    output.append(fetched[url])
            return json.dumps(output, ensure_ascii=False)
        except Exception as e:
            logger.error("Batch inspection failed: %s", e)
            return json.dumps({"error": f"Batch inspection failed: {str(e)}"}, indent=2)

    def _batch_result(
        self,
        url: str,
        md: str,
        links,
        meta: dict,
        method: str,
        cache_hit: bool = False,
    ) -> dict:
        """Build one batch output entry with the same shape as a
        single-page ``inspect_html_page`` result (C6)."""
        md_chars, md_tokens = self._content_budget()
        truncated_md = self._truncate(md, md_chars, md_tokens)
        result = self._build_inspection_result(
            url, truncated_md, links, self._compact_metadata(meta), method,
            markdown_truncated=truncated_md != md,
        )
        if cache_hit:
            result.cache_hit = True
        return json.loads(result.model_dump_json())

    # ───────────────────────────────
    # Document Extraction
    # ───────────────────────────────

    def extract_document(self, source: str) -> str:
        """Extract text content from PDF, DOCX, or XLSX documents."""
        try:
            source = normalize_url(source)  # may still be a local path
            is_url = True
        except ValueError:
            is_url = urlparse(source).scheme in ("http", "https")

        if is_url:
            self._validate_url(source)
            self._rate_limit_domain(source)

        cache_key = self._cache_key(source) if is_url else source
        cached = self.cache.get(cache_key)
        if cached is not None:
            # Re-apply the budget on every read (C4): the cache holds
            # untruncated content and the budget may have changed since the
            # entry was stored — same read-time truncation as the page cache.
            truncated = self._truncate(cached, self.max_markdown_chars, self.max_tokens)
            return ExtractionResult(
                source=source,
                content=truncated,
                content_tokens=count_tokens(truncated, self.model_name),
                cache_hit=True,
            ).model_dump_json()

        try:
            if is_url:
                content = self._download_and_extract(source)
            else:
                content = self._extract_local(source)

            self.cache.put(cache_key, content)
            truncated = self._truncate(content, self.max_markdown_chars, self.max_tokens)
            return ExtractionResult(
                source=source,
                content=truncated,
                content_tokens=count_tokens(truncated, self.model_name),
                cache_hit=False,
            ).model_dump_json()
        except Exception as e:
            logger.error("Document extraction failed for %s: %s", source, e)
            return json.dumps(
                {"error": f"Document extraction failed: {str(e)}"}, indent=2
            )

    def _download_and_extract(self, url: str) -> str:
        """Download a document from URL and extract its content."""
        with httpx.Client(timeout=30) as client:
            response = client.get(url, headers=self._next_headers())
            response.raise_for_status()
            return self._extract_from_bytes(response.content, url)

    def _extract_local(self, path: str) -> str:
        """Extract content from a local document file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        content = file_path.read_bytes()
        return self._extract_from_bytes(content, str(file_path))

    def _extract_from_bytes(self, data: bytes, source: str) -> str:
        """Extract text from document bytes based on file type."""
        suffix = Path(source).suffix.lower()

        if suffix == ".pdf":
            doc = PdfDocument.from_bytes(data)
            return doc.to_markdown_all()
        elif suffix in (".docx", ".xlsx", ".pptx"):
            doc = OfficeDoc.from_bytes(data)
            return doc.to_markdown()
        else:
            raise ValueError(f"Unsupported document format: {suffix}")

    # ───────────────────────────────
    # ───────────────────────────────
    # Structured Document Extraction
    # ───────────────────────────────

    def extract_document_structured(self, source: str) -> str:
        """
        Download (if URL) and parse a document into a structured
        ParsedDocumentPayload with metadata, pages, and tables.
        """
        import os
        import tempfile as tf

        parsed = urlparse(source)
        is_url = parsed.scheme in ("http", "https")

        if is_url:
            self._validate_url(source)
            self._rate_limit_domain(source)

        tmp_path = None
        try:
            if is_url:
                with httpx.Client(timeout=30) as client:
                    response = client.get(source, headers=self._next_headers())
                    response.raise_for_status()

                suffix = Path(source).suffix.lower() or ".pdf"
                with tf.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(response.content)
                    tmp_path = tmp.name
            else:
                tmp_path = source

            parser = StructuredOxideParser()
            payload = parser.parse_file(tmp_path)

            if is_url and tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            truncated_json = self._truncate(
                payload.to_json(), self.max_markdown_chars, self.max_tokens
            )
            return truncated_json

        except Exception as e:
            logger.error("Structured extraction failed for %s: %s", source, e)
            if is_url and tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            return json.dumps(
                {"error": f"Structured document extraction failed: {str(e)}"}, indent=2
            )

    # ───────────────────────────────
    # HTML Structured Inspection
    # ───────────────────────────────

    def inspect_html_structured(self, url: str, use_smart: Optional[bool] = None) -> str:
        """
        Fetch a web page and return it as a structured ParsedDocumentPayload
        with metadata (OG, Twitter, JSON-LD), markdown content, and links.

        Unifies the HTML fetching pipeline with the structured document
        pipeline so that web pages and file documents produce the same
        ParsedDocumentPayload output.

        Parameters
        ----------
        url : str
            The URL to inspect.
        use_smart : bool
            If True, attempt headless JS rendering via browser_oxide first.

        Returns
        -------
        str
            JSON-serialised ParsedDocumentPayload (token-truncated).
        """
        url = normalize_url(url)
        self._validate_url(url)

        # Cache stores the untruncated payload JSON; budgets are re-applied
        # on every read so changed limits are honored. A cached result is
        # served on repeat visits too (C3: data beats a warning).
        cached_json = self.cache.get("structured:" + self._cache_key(url))

        try:
            if cached_json is not None:
                logger.info("Cache hit (structured) for %s", url)
                return self._truncate(
                    cached_json, self.max_markdown_chars, self.max_tokens
                )
            if url in self.visited_urls:
                logger.warning("URL already visited (not in cache): %s", url)
                return json.dumps(
                    {"warning": "URL already visited", "url": url}, indent=2
                )
            self._rate_limit_domain(url)

            markdown, links, html_metadata, fetch_method = self._fetch_html(url, use_smart)

            # Build structured payload via unified parser
            parser = StructuredOxideParser()
            payload = parser.parse_html(
                markdown=markdown,
                links=links,
                html_metadata=html_metadata,
                url=url,
                max_links=self.max_links,
            )

            payload_json = payload.to_json()
            self.cache.put("structured:" + self._cache_key(url), payload_json)
            self.visited_urls.add(url)  # success only (C3)
            truncated_json = self._truncate(
                payload_json, self.max_markdown_chars, self.max_tokens
            )
            return truncated_json
        except Exception as e:
            logger.error("Structured HTML inspection failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"Structured HTML inspection failed: {str(e)}"}, indent=2
            )

    # Stats & Management
    # ───────────────────────────────

    def get_stats(self) -> str:
        """Return toolbox statistics."""
        return json.dumps(
            {
                "visited_urls_count": len(self.visited_urls),
                "cache": self.cache.stats(),
                "max_tokens": self.max_tokens,
                "model_name": self.model_name,
            },
            indent=2,
        )

    def reset_visited(self) -> None:
        """Clear the visited URL set."""
        self.visited_urls.clear()

    def clear_cache(self) -> str:
        """Clear both memory and disk caches and the visited-URL set (C3:
        after a cache reset, previously visited URLs can be re-fetched)."""
        self.cache.clear()
        self.visited_urls.clear()
        return json.dumps({"cache_cleared": True, "stats": self.cache.stats()}, indent=2)
