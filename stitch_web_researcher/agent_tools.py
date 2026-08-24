import asyncio
import copy
import json
import logging
import time
from collections import defaultdict
from functools import wraps
from pathlib import Path
import tempfile
from typing import Optional
from urllib.parse import urlparse

import httpx
from pdf_oxide import PdfDocument
from office_oxide import Document as OfficeDoc

from stitch_web_researcher._core import fetch_and_extract, batch_research
from stitch_web_researcher.token_budget import truncate_to_tokens, count_tokens
from stitch_web_researcher.structured_parser import StructuredOxideParser, ParsedDocumentPayload
from stitch_web_researcher.search_providers import (
    DuckDuckGoProvider,
    RateLimit,
    resolve_provider_name,
)
from stitch_web_researcher import meta_extractor
from stitch_web_researcher.cache import Cache

logger = logging.getLogger(__name__)

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

    Returns (markdown, links, metadata) tuple.
    """
    from urllib.parse import urlparse

    import html2md
    from scraper import Html, Selector
    from url import Url

    # Profile.chrome() is mandatory in v0.1.x — bare Browser() hits a fatal
    # V8 HandleScope error at construction.
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

    # Process HTML through the same pipeline as fetch_and_extract
    document = Html.parse_document(html)
    base = Url.parse(url)

    # Extract links
    link_sel = Selector.parse("a[href]").unwrap()
    links = []
    seen = set()
    for el in document.select(link_sel):
        href = el.value().attr("href")
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            abs_url = str(base.join(href))
        except Exception:
            continue
        if abs_url not in seen and urlparse(abs_url).scheme in ("http", "https"):
            seen.add(abs_url)
            links.append(abs_url)
            if len(links) >= 20:
                break

    # Extract main content
    main_content = _extract_main_content(document)
    markdown = html2md.parse_html(main_content)
    return markdown, links, metadata


def _extract_main_content(document) -> str:
    """Extract main textual content from HTML using heuristics."""
    from scraper import Selector

    for sel_str in ["article", "main", "[role='main']", ".content", "#content"]:
        sel = Selector.parse(sel_str).unwrap()
        for el in document.select(sel):
            return el.html()

    body_sel = Selector.parse("body").unwrap()
    for body in document.select(body_sel):
        return body.html()

    return document.html()


def fetch_smart_page(url: str) -> tuple[str, list[str], dict]:
    """Fetch a page with headless JS rendering via browser_oxide.

    Falls back to static fetch_and_extract if browser_oxide is unavailable
    or fails.

    Returns (markdown, links, metadata) tuple.
    """
    if _browser_oxide_available:
        try:
            return _fetch_with_browser_oxide(url)
        except Exception as e:
            logger.warning(
                "browser_oxide smart fetch failed for %s: %s -- falling back to static",
                url, e,
            )

    # Fallback to static Rust fetch
    md, links = fetch_and_extract(url)
    # Rust core doesn't return HTML, so metadata is empty for static fallback
    return md, links, {}


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
]



class WebResearcherToolbox:
    """LLM tool routing layer with caching, rate limiting, and token budgeting."""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    def __init__(
        self,
        cache_dir: str = ".web_research_cache",
        cache_ttl_seconds: int = 3600,
        ddgs_delay: float = 1.0,
        domain_delay: float = 0.5,
        max_markdown_chars: int = 8000,
        max_tokens: int = 0,
        model_name: str = "gpt-4o",
        max_links: int = 20,
        search_providers: Optional[list] = None,
        default_provider_index: int = 0,
        fetch_delay: Optional[float] = None,
    ):
        # Two-tier cache (memory LRU + file TTL)
        self.cache = Cache(
            cache_dir=cache_dir,
            ttl_seconds=cache_ttl_seconds,
        )
        self.ddgs_delay = ddgs_delay
        self.domain_delay = domain_delay
        self.max_markdown_chars = max_markdown_chars
        self.max_tokens = max_tokens
        self.model_name = model_name
        self.max_links = max_links

        # Search providers: default to DuckDuckGo if none specified
        if search_providers:
            self.providers = search_providers
        else:
            self.providers = [DuckDuckGoProvider(delay=ddgs_delay)]
        self.default_provider = self.providers[default_provider_index]

        # Effective content-fetch interval (per-domain politeness delay).
        # Resolution order: explicit fetch_delay arg > active provider's
        # RateLimit.fetch_interval > legacy domain_delay.
        if fetch_delay is not None:
            self._fetch_interval = float(fetch_delay)
        else:
            rl = getattr(self.default_provider, "rate_limit", None)
            if isinstance(rl, RateLimit):
                self._fetch_interval = rl.fetch_interval
            else:
                self._fetch_interval = self.domain_delay

        self.visited_urls: set[str] = set()
        self._domain_last_seen: dict[str, float] = defaultdict(float)
        self._ua_index = 0

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
        """Validate URL scheme and format."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        if not parsed.netloc:
            raise ValueError(f"Invalid URL (no host): {url}")

    def _rate_limit_domain(self, url: str) -> None:
        """Enforce per-domain rate limiting for content fetching."""
        parsed = urlparse(url)
        domain = parsed.netloc
        last_seen = self._domain_last_seen[domain]
        elapsed = time.time() - last_seen
        if elapsed < self._fetch_interval:
            time.sleep(self._fetch_interval - elapsed)
        self._domain_last_seen[domain] = time.time()

    def _cache_key(self, url: str) -> str:
        """Generate cache key for a URL (use URL directly)."""
        return url

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

    def inspect_html_page(self, url: str, use_smart: bool = False) -> str:
        """
        Fetch and extract markdown + follow-up links + HTML metadata from a web page.

        Parameters
        ----------
        url : str
            The URL to inspect.
        use_smart : bool
            If True, attempt headless JS rendering via browser_oxide first,
            then fall back to static reqwest fetch.
        """
        if url in self.visited_urls:
            logger.warning("URL already visited: %s", url)
            return json.dumps({"warning": "URL already visited", "url": url}, indent=2)

        self._validate_url(url)
        self.visited_urls.add(url)
        self._rate_limit_domain(url)

        try:
            if use_smart:
                markdown, links, html_metadata = fetch_smart_page(url)
                fetch_method = "smart"
            else:
                markdown, links = fetch_and_extract(url)
                html_metadata = {}
                fetch_method = "static"
            logger.info("Fetched %s via %s (%d chars, %d links)", url, fetch_method, len(markdown), len(links))
            truncated_md = self._truncate(
                markdown, self.max_markdown_chars, self.max_tokens
            )

            # Build compact metadata summary for LLM output
            meta_summary = self._compact_metadata(html_metadata)

            return json.dumps(
                {
                    "url": url,
                    "markdown": truncated_md,
                    "markdown_tokens": count_tokens(truncated_md, self.model_name),
                    "follow_up_links": links[: self.max_links],
                    "total_links": len(links),
                    "fetch_method": fetch_method,
                    "metadata": meta_summary,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            logger.error("HTML inspection failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"HTML inspection failed: {str(e)}"}, indent=2
            )

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

        return compact

    async def inspect_html_page_async(self, url: str, use_smart: bool = False) -> str:
        """Async version of inspect_html_page."""
        if url in self.visited_urls:
            logger.warning("URL already visited: %s", url)
            return json.dumps({"warning": "URL already visited", "url": url}, indent=2)

        self._validate_url(url)
        self.visited_urls.add(url)
        self._rate_limit_domain(url)

        try:
            if use_smart:
                markdown, links, html_metadata = fetch_smart_page(url)
                fetch_method = "smart"
            else:
                markdown, links = fetch_and_extract(url)
                html_metadata = {}
                fetch_method = "static"
            truncated_md = self._truncate(
                markdown, self.max_markdown_chars, self.max_tokens
            )

            meta_summary = self._compact_metadata(html_metadata)

            return json.dumps(
                {
                    "url": url,
                    "markdown": truncated_md,
                    "markdown_tokens": count_tokens(truncated_md, self.model_name),
                    "follow_up_links": links[: self.max_links],
                    "total_links": len(links),
                    "fetch_method": fetch_method,
                    "metadata": meta_summary,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            logger.error("Async HTML inspection failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"HTML inspection failed: {str(e)}"}, indent=2
            )

    # ───────────────────────────────
    # Batch Inspection
    # ───────────────────────────────

    def batch_inspect_pages(self, urls: list) -> str:
        """
        Fetch multiple pages concurrently using the Rust batch engine.
        """
        for url in urls:
            if url in self.visited_urls:
                logger.warning("Skipping already-visited URL in batch: %s", url)
                continue
            self._validate_url(url)
            self.visited_urls.add(url)

        try:
            results = batch_research(urls)
            output = []
            for url, md_opt, links_opt in results:
                if md_opt is not None and links_opt is not None:
                    truncated_md = self._truncate(
                        md_opt, self.max_markdown_chars, self.max_tokens
                    )
                    output.append(
                        {
                            "url": url,
                            "markdown": truncated_md,
                            "markdown_tokens": count_tokens(
                                truncated_md, self.model_name
                            ),
                            "follow_up_links": links_opt[: self.max_links],
                            "total_links": len(links_opt),
                        }
                    )
                else:
                    output.append({"url": url, "error": md_opt or "Unknown error"})
            return json.dumps(output, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Batch inspection failed: %s", e)
            return json.dumps({"error": f"Batch inspection failed: {str(e)}"}, indent=2)

    # ───────────────────────────────
    # Document Extraction
    # ───────────────────────────────

    def extract_document(self, source: str) -> str:
        """Extract text content from PDF, DOCX, or XLSX documents."""
        parsed = urlparse(source)
        is_url = parsed.scheme in ("http", "https")

        if is_url:
            self._validate_url(source)
            self._rate_limit_domain(source)

        cached = self.cache.get(source)
        if cached is not None:
            return json.dumps({"cache_hit": True, "content": cached}, indent=2)

        try:
            if is_url:
                content = self._download_and_extract(source)
            else:
                content = self._extract_local(source)

            self.cache.put(source, content)
            truncated = self._truncate(content, self.max_markdown_chars, self.max_tokens)
            return json.dumps(
                {
                    "source": source,
                    "content": truncated,
                    "content_tokens": count_tokens(truncated, self.model_name),
                },
                indent=2,
                ensure_ascii=False,
            )
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

    def inspect_html_structured(self, url: str, use_smart: bool = False) -> str:
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
        if url in self.visited_urls:
            logger.warning("URL already visited: %s", url)
            return json.dumps({"warning": "URL already visited", "url": url}, indent=2)

        self._validate_url(url)
        self.visited_urls.add(url)
        self._rate_limit_domain(url)

        try:
            if use_smart:
                markdown, links, html_metadata = fetch_smart_page(url)
                fetch_method = "smart"
            else:
                markdown, links = fetch_and_extract(url)
                html_metadata = {}
                fetch_method = "static"

            # Build structured payload via unified parser
            parser = StructuredOxideParser()
            payload = parser.parse_html(
                markdown=markdown,
                links=links,
                html_metadata=html_metadata,
                url=url,
                max_links=self.max_links,
            )

            truncated_json = self._truncate(
                payload.to_json(), self.max_markdown_chars, self.max_tokens
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
        """Clear both memory and disk caches."""
        self.cache.clear()
        return json.dumps({"cache_cleared": True, "stats": self.cache.stats()}, indent=2)

    def get_cache_stats(self) -> str:
        """Return detailed cache statistics."""
        return json.dumps(self.cache.stats(), indent=2)
