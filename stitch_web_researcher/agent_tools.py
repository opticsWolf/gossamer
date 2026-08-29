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
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


# ───────────────────────────────
# Tier 2.6: fetch observability
# ───────────────────────────────
class FetchStats:
    """Lightweight, thread-safe fetch observability (Tier 2.6).

    Tracks per-fetch latency (bounded sliding window), total bytes
    downloaded, per-domain request counts, and error counts by exception
    class. Exposed via ``get_stats()["fetches"]``.
    """

    def __init__(self, latency_window: int = 1024) -> None:
        self._lock = threading.Lock()
        self._latencies: deque = deque(maxlen=max(1, int(latency_window)))
        self._bytes = 0
        self._fetches = 0
        self._errors = 0
        self._by_domain: dict = {}
        self._by_error: dict = {}

    def record_success(self, domain: str, latency_s: float, nbytes: int) -> None:
        with self._lock:
            self._latencies.append(latency_s)
            self._bytes += max(0, int(nbytes))
            self._fetches += 1
            self._by_domain[domain] = self._by_domain.get(domain, 0) + 1

    def record_error(self, domain: str, latency_s: float, exc: BaseException) -> None:
        with self._lock:
            self._latencies.append(latency_s)
            self._fetches += 1
            self._errors += 1
            self._by_domain[domain] = self._by_domain.get(domain, 0) + 1
            cls = type(exc).__name__
            self._by_error[cls] = self._by_error.get(cls, 0) + 1

    @staticmethod
    def _percentile(sorted_vals, p: float) -> float:
        if not sorted_vals:
            return 0.0
        if len(sorted_vals) == 1:
            return float(sorted_vals[0])
        k = (len(sorted_vals) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(sorted_vals[int(k)])
        return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))

    def to_dict(self) -> dict:
        with self._lock:
            lats = sorted(self._latencies)
            return {
                "fetches": self._fetches,
                "errors": self._errors,
                "bytes_downloaded": self._bytes,
                "latency_ms": {
                    "p50": round(self._percentile(lats, 0.50) * 1000, 1),
                    "p95": round(self._percentile(lats, 0.95) * 1000, 1),
                    "p99": round(self._percentile(lats, 0.99) * 1000, 1),
                    "max": round(lats[-1] * 1000, 1) if lats else 0.0,
                },
                "requests_by_domain": dict(
                    sorted(self._by_domain.items(), key=lambda kv: (-kv[1], kv[0]))
                ),
                "errors_by_class": dict(self._by_error),
            }


def _domain_of(url: str) -> str:
    """Best-effort host (netloc) for per-domain stats; falls back to url."""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


# Tier 2.6: Rust `tracing` -> Python `logging` bridge (opt-in via
# STITCH_RUST_LOG). Initialised once per process; no-op when unset.
_rust_log_initialized = False


def _maybe_init_rust_logging() -> None:
    global _rust_log_initialized
    if _rust_log_initialized:
        return
    level = os.environ.get("STITCH_RUST_LOG", "").strip()
    if not level:
        _rust_log_initialized = True
        return
    try:
        _init_rust_logging(level)
    except Exception:
        logger.debug("Rust logging bridge init failed", exc_info=True)
    _rust_log_initialized = True


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
    # Tier 1.2: set only for page-range reads (extract_document pages=...).
    # page_range echoes the request; page_start/page_end are the 1-based
    # inclusive bounds actually delivered (clamped to the document);
    # total_pages is the document's full page count. On a cache hit of a
    # range read the bounds are re-derivable only from the stored content,
    # so they stay 0/None there.
    page_range: Optional[str] = None
    page_start: Optional[int] = 0
    page_end: Optional[int] = 0
    total_pages: int = 0
    # Tier 1.3: provenance. fetched_at is the download/parse time (None
    # on cache hits — the stored content carries no timestamp);
    # content_hash is the SHA-256 of the full untruncated content, tying
    # range reads and cache hits back to the stored bytes. http_status /
    # final_url / content_type are set for URL sources.
    fetched_at: Optional[str] = None
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    content_type: Optional[str] = None
    content_hash: Optional[str] = None
    # Deep-research support: URLs written into the document text (detected
    # from the full, untruncated content). Documents lose the <a href>
    # structure of HTML pages, so this is the only link signal they have.
    links: list[str] = Field(default_factory=list)
    # §7: optional prompt-injection guard block (present only when the
    # guard is enabled and a scanned scope was checked).
    guard: Optional[dict] = None


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
    # Tier 1.4: True when this read's cached entry had expired and a
    # conditional request (If-None-Match / If-Modified-Since) answered
    # 304 Not Modified, so the stored copy was re-freshened and re-served
    # without a body download. Content and fetched_at are the originals.
    revalidated: bool = False
    metadata: dict = Field(default_factory=dict)
    # Tier 1.1: set only when the caller passed a research query and the
    # page did not fit the output budget — the delivered markdown is then
    # the query-relevant subset of the page's sections.
    query: Optional[str] = None
    sections_available: int = 0
    sections_selected: int = 0
    section_anchors: list[str] = Field(default_factory=list)
    # Tier 1.2: paging metadata, set on every chunked (non-selection)
    # read. offset echoes the requested start; chars_total is the full
    # page length; next_offset is the character position to pass as
    # offset next to continue where this read stopped; has_more tells
    # whether anything follows this slice.
    offset: int = 0
    next_offset: Optional[int] = None
    has_more: bool = False
    chars_total: int = 0
    # Tier 1.3: provenance — which fetch this read was served from.
    # fetched_at is the original fetch time (the page cache preserves it
    # across chunked reads and cache hits); final_url is the URL after
    # redirects; content_hash is the SHA-256 of the full page markdown,
    # so every chunk of one page shares the same hash. cache_hit (above)
    # is the from_cache flag.
    fetched_at: Optional[str] = None
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    content_type: Optional[str] = None
    content_hash: Optional[str] = None
    # §7: optional prompt-injection guard block (present only when the
    # guard is enabled and a scanned scope was checked).
    guard: Optional[dict] = None


# ───────────────────────────────
# Provenance helpers (Tier 1.3)
# ───────────────────────────────

def _utc_now_iso() -> str:
    """Current time in ISO-8601 UTC (fetch timestamps)."""
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(text: str) -> str:
    """SHA-256 of a string, for content provenance hashes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provenance_from_fetch_meta(
    meta: tuple,
    requested_url: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> dict:
    """Normalize the Rust fetch's provenance tuple (http_status,
    final_url, content_type) into the payload dict form.

    Tier 1.4: when the server advertised validators, ``etag`` and
    ``last_modified`` are stored inside the provenance dict (the compact
    metadata whitelist keeps them out of the LLM payload) so an expired
    page can later be revalidated with If-None-Match / If-Modified-Since
    instead of re-downloaded.
    """
    status, final_url, content_type = meta
    prov = {
        "fetched_at": _utc_now_iso(),
        "http_status": status,
        "final_url": final_url or requested_url,
        "content_type": content_type,
    }
    if etag:
        prov["etag"] = etag
    if last_modified:
        prov["last_modified"] = last_modified
    return prov


def _browser_provenance(requested_url: str) -> dict:
    """Best-effort provenance for stealth-browser fetches. The browser
    layer does not surface the HTTP status or post-redirect URL, so a
    successful navigation is reported as 200 on the requested URL and
    content_type is left unknown."""
    return {
        "fetched_at": _utc_now_iso(),
        "http_status": 200,
        "final_url": requested_url,
        "content_type": None,
    }


# ───────────────────────────────
# Batch result record (M10, CODE_REVIEW_2026-08-27)
# ───────────────────────────────

@dataclass(frozen=True)
class BatchEntry:
    """Tagged result of one URL in a batch fetch.

    The raw batch engine reports ``(url, md_or_error, links_or_None)``
    triples in which the failure marker is the links slot being None.
    This record makes that explicit: on success ``markdown``/``links``
    are set and ``error`` is None; on failure ``error`` is set and
    ``markdown``/``links`` are None. Callers branch on ``ok`` instead of
    guessing which slot carries what.
    """

    url: str
    markdown: Optional[str] = None
    links: Optional[List[str]] = None
    error: Optional[str] = None
    # Raw HTML of a successful fetch, so batch entries can run the same
    # meta-oxide extraction single-page reads do (bugfix 5).
    html: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _normalize_batch_results(results) -> List[BatchEntry]:
    """Convert raw engine tuples into tagged ``BatchEntry`` records (M10).

    Success: ``(url, html, markdown, links)`` with all three slots non-None.
    Failure: ``(url, None, error_message, None)`` (the message may be empty).
    """
    entries: List[BatchEntry] = []
    for url, html_opt, md_opt, links_opt in results:
        if md_opt is not None and links_opt is not None:
            entries.append(
                BatchEntry(
                    url=url, markdown=md_opt, links=links_opt, html=html_opt
                )
            )
        else:
            entries.append(BatchEntry(url=url, error=md_opt or "Unknown error"))
    return entries


# ───────────────────────────────
# Markdown link absolutization (M12, CODE_REVIEW_2026-08-27)
# ───────────────────────────────

# Smallest per-field character budget ``_fit_json`` will try before giving
# up and returning an overflow envelope. Below this a payload carries no
# usable content anyway, and halving further just burns tokenizer passes.
_JSON_FIT_FLOOR = 240

# Inline markdown link: [text](target) or [text](target "title"). The
# negative lookbehind skips image markers so ![alt](...) is untouched.
_MD_INLINE_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]*)\]\(([^()\s]+)(?:\s+\"([^\"]*)\")?\)"
)

# Targets that are already self-contained or must not be rewritten.
_MD_NON_LINK_PREFIXES = (
    "#", "//", "mailto:", "tel:", "javascript:", "data:", "ftp:"
)


def _absolutize_markdown_links(markdown: str, base_url: str) -> str:
    """Rewrite relative hrefs in a markdown body to absolute URLs (M12).

    The Rust core's markdown conversion keeps hrefs exactly as written
    (e.g. ``[A](/a)``); only the separate ``follow_up_links`` list is
    absolute. A model copying a markdown link would get an unresolvable
    URL, so the body is made self-contained via ``urljoin``. Absolute
    URLs, protocol-relative (``//``), fragment-only (``#``) and
    non-resource schemes (mailto/tel/data/javascript/ftp) pass through
    unchanged. Idempotent: re-running on absolutized text is a no-op.
    """
    if not markdown or "(" not in markdown:
        return markdown

    def _rewrite(m: re.Match[str]) -> str:
        target = m.group(2)
        if target.lower().startswith(_MD_NON_LINK_PREFIXES):
            return m.group(0)
        absolute = urljoin(base_url, target)
        if absolute == target:
            return m.group(0)
        title = m.group(3)
        if title is not None:
            return f"[{m.group(1)}]({absolute} \"{title}\")"
        return f"[{m.group(1)}]({absolute})"

    return _MD_INLINE_LINK_RE.sub(_rewrite, markdown)


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
    # Tier 1.3: best-effort provenance (the browser layer does not
    # surface the HTTP status or the post-redirect URL).
    metadata["provenance"] = _browser_provenance(url)
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
    html, md, links, removed, prov = fetch_html_full(url, 100)
    metadata = meta_extractor.extract_all(html, url)
    if removed:
        metadata["hidden_blocks_removed"] = removed
    metadata["provenance"] = _provenance_from_fetch_meta(prov, url)
    return md, links, metadata


# ───────────────────────────────
# WebResearcherToolbox
# ───────────────────────────────

# ───────────────────────────────
# P8: Tool registry — single source of truth
# ───────────────────────────────
# Every surface (LLM function-calling definitions, MCP server tools, and
# the execute_tool dispatcher) is generated from TOOL_REGISTRY, so the
# tool surface cannot drift across entry points.

_MISSING = object()  # sentinel: parameter is required (no default)


class ToolParam:
    """One parameter of a registry tool."""

    __slots__ = ("name", "type", "default", "description", "enum")

    def __init__(self, name, type, default=_MISSING, description="", enum=None):
        self.name = name
        self.type = type  # Python annotation: str / int / bool / list[str]
        self.default = default  # _MISSING when required
        self.description = description
        self.enum = enum  # optional list of allowed values

    @property
    def required(self) -> bool:
        return self.default is _MISSING

    @property
    def json_schema(self) -> dict:
        if self.type is str:
            schema = {"type": "string"}
        elif self.type is int:
            schema = {"type": "integer"}
        elif self.type is bool:
            schema = {"type": "boolean"}
        else:  # list[str]
            schema = {"type": "array", "items": {"type": "string"}}
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.description:
            schema["description"] = self.description
        if not self.required:
            schema["default"] = self.default
        return schema


class ToolSpec:
    """One registry tool: surface description plus the
    ``WebResearcherToolbox`` method it dispatches to."""

    __slots__ = ("name", "description", "method", "params")

    def __init__(self, name, description, method, params):
        self.name = name
        self.description = description
        self.method = method
        self.params = tuple(params)

    def llm_definition(self) -> dict:
        """Function-calling definition for LLM consumers."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.json_schema for p in self.params},
                    "required": [p.name for p in self.params if p.required],
                },
            },
        }

    def kwargs(self, arguments: Optional[dict] = None) -> dict:
        """Registry defaults plus caller-supplied arguments."""
        merged = {p.name: p.default for p in self.params if not p.required}
        merged.update(arguments or {})
        return merged


TOOL_REGISTRY = (
    ToolSpec(
        "search_web",
        "Search the web using one or more search providers. Set provider to choose a specific engine; falls back through others on failure.",
        "search_web",
        (
            ToolParam("query", str, description="The search query"),
            ToolParam(
                "max_results",
                int,
                5,
                "Maximum number of results to return (default: 5)",
            ),
            ToolParam(
                "provider",
                str,
                "duckduckgo",
                "Search engine to prefer. Falls back through other providers on failure.",
                enum=["duckduckgo", "google", "bing", "exa", "browser"],
            ),
        ),
    ),
    ToolSpec(
        "inspect_html_page",
        "Fetch and extract markdown content from a web page. Set use_smart=True for JS-rendered pages (SPA, anti-bot). When the page exceeds the output budget, pass the research query to keep the most relevant sections instead of truncating head-first; or pass offset / max_chunks to page through the full document in budget-sized chunks. Returns markdown text, follow-up links, and provenance (fetched_at, http_status, final_url, content_type, content_hash; cache_hit flags from-cache reads).",
        "inspect_html_page",
        (
            ToolParam("url", str, description="The URL to inspect"),
            ToolParam(
                "use_smart",
                bool,
                False,
                "If true, use headless browser rendering (browser_oxide) for JS-heavy pages",
            ),
            ToolParam(
                "query",
                str,
                None,
                "The research query. When the page does not fit the output budget, only the sections most relevant to this query are returned; the payload reports sections_available / sections_selected / section_anchors.",
            ),
            ToolParam(
                "offset",
                int,
                0,
                "Character offset into the full page markdown to start reading from. Pass the previous call's next_offset to resume where it stopped. Explicit paging takes precedence over query.",
            ),
            ToolParam(
                "max_chunks",
                int,
                1,
                "Number of consecutive budget-sized chunks to return (each chunk respects the output budget; the total may exceed it).",
            ),
        ),
    ),
    ToolSpec(
        "batch_inspect_pages",
        "Fetch multiple web pages concurrently. Returns markdown and links for each.",
        "batch_inspect_pages",
        (
            ToolParam("urls", list[str], description="List of URLs to inspect"),
        ),
    ),
    ToolSpec(
        "extract_document",
        "Extract text content from documents via URL or local path: PDF, DOCX, XLSX, PPTX, plus text formats TXT, MD, CSV, JSON, XML, and RSS/Atom feeds (returned as readable entry lists). Extension-less URLs with a text Content-Type are also handled. For large documents, pass pages (e.g. '10-20') to read a page range instead of the whole file.",
        "extract_document",
        (
            ToolParam(
                "source",
                str,
                description="URL or local file path to the document",
            ),
            ToolParam(
                "pages",
                str,
                None,
                "1-based inclusive page range for PDFs ('10', '10-20', '10-', '-20'); for XLSX the range selects sheets. Without it, the whole document is returned (subject to the output budget).",
            ),
        ),
    ),
    ToolSpec(
        "extract_document_structured",
        "Extract structured content (metadata, pages, tables) from PDF, DOCX, XLSX, or PPTX documents via URL or local path. Returns a validated ParsedDocumentPayload as JSON.",
        "extract_document_structured",
        (
            ToolParam(
                "source",
                str,
                description="URL or local file path to the document",
            ),
        ),
    ),
    ToolSpec(
        "inspect_html_structured",
        "Fetch a web page and return it as a structured ParsedDocumentPayload with metadata (OG, Twitter, JSON-LD), markdown content, tables (structured grids extracted from HTML <table> elements, static fetches only), and links. Set use_smart=True for JS-rendered pages.",
        "inspect_html_structured",
        (
            ToolParam("url", str, description="The URL to inspect"),
            ToolParam(
                "use_smart",
                bool,
                False,
                "If true, use headless browser rendering (browser_oxide) for JS-heavy pages",
            ),
        ),
    ),
    ToolSpec(
        "discover_resources",
        "Discover a site's structured resources without crawling the link graph (Tier 3.12). Finds feed declarations (<link rel=alternate> RSS/Atom/Feed-JSON) on the page and probes the site root for /sitemap.xml, following bounded sitemap indexes. Returns deduplicated, budgeted lists of feed URLs and sitemap page URLs.",
        "discover_resources",
        (
            ToolParam(
                "url",
                str,
                description="A page or site URL to discover resources for",
            ),
        ),
    ),
    ToolSpec(
        "research",
        "Run a small orchestrated research pass (Tier 3.13): search the topic, dedupe the top results, and fetch up to depth pages through the normal cache/robots/rate-limit/provenance pipeline. Returns one record per source with status, markdown content, and provenance - everything the caller needs to write a cited synthesis (the synthesis itself is the caller's job).",
        "research",
        (
            ToolParam("topic", str, description="Research topic / search query"),
            ToolParam(
                "depth", int, 5, description="How many pages to fetch (1-10)"
            ),
            ToolParam(
                "max_tokens",
                int,
                0,
                description="Token budget for the whole response (0 = toolbox default)",
            ),
        ),
    ),
    ToolSpec(
        "crawl",
        "Bounded focused crawl over a site's link graph: BFS from root_url, but the frontier is ranked by relevance (score x 0.7^depth), so the page budget goes to the most relevant links; flat scores degrade to plain BFS. Returns per-page title, relevance score, and a content skim (full pages stay in the page cache and can be re-read in full via inspect_html_page), plus documents (PDF/DOCX links, never fetched here), skipped links with reasons, and counters.",
        "crawl",
        (
            ToolParam(
                "root_url",
                str,
                description="Seed URL to start the crawl from",
            ),
            ToolParam(
                "query",
                str,
                None,
                "Relevance focus for the frontier ranking. When omitted, the root page's own title and content words stand in for it.",
            ),
            ToolParam(
                "max_depth",
                int,
                3,
                "Maximum link hops from the root (0 = root only; hard cap 5)",
            ),
            ToolParam(
                "max_pages",
                int,
                15,
                "Total pages fetched across all depths (1-50); failed fetches do not count",
            ),
            ToolParam(
                "same_host",
                bool,
                False,
                "When true, follow only links on the root's host (www. ignored); false follows external links too",
            ),
            ToolParam(
                "min_score",
                float,
                0.05,
                "Minimum relevance score for a candidate to enter the frontier (0 = follow everything that passes the boilerplate filters)",
            ),
        ),
    ),
    ToolSpec(
        "clear_cache",
        "Clear both the in-memory and disk research caches and the visited-URL set. Use when you want to force fresh fetches (e.g., starting a new research session or suspecting stale content). Returns confirmation with post-clear statistics.",
        "clear_cache",
        (),
    ),
    ToolSpec(
        "prune_cache",
        "Remove expired cache entries and evict least-recently-used entries to stay under the configured size cap. Unlike clear_cache this keeps valid in-TTL entries and does not reset the visited-URL set. Returns a summary of what was removed.",
        "prune_cache",
        (),
    ),
    ToolSpec(
        "reset_visited",
        "Forget all previously visited URLs so they can be fetched again (caches are NOT cleared). Use after a fetch failure you want to retry, or when starting a new research session on the same pages.",
        "reset_visited",
        (),
    ),
    ToolSpec(
        "get_stats",
        "Return toolbox statistics: visited URLs, cache hit rate and size, token budget settings.",
        "get_stats",
        (),
    ),
)

# Module-level LLM function-calling tool definitions — derived from the
# registry (P8) so the LLM surface can never drift from it.
_LLM_TOOL_DEFINITIONS = tuple(spec.llm_definition() for spec in TOOL_REGISTRY)



def normalize_url(raw: str, base: Optional[str] = None) -> str:
    """Auto-convert URL-like strings into proper absolute URLs.

    Handles the messy inputs an LLM tends to produce:
      - surrounding whitespace / quotes / angle brackets
      - missing scheme ("example.com/doc.pdf", "www.example.com/a")
      - protocol-relative ("//cdn.example.com/x")
      - page-relative paths ("/files/report.pdf") when *base* is given

    Returns a clean absolute http(s) URL; raises ValueError for strings
    that cannot be interpreted as one — including explicit relative paths
    and existing local files, which are paths on disk, not URLs (M1).
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
        # M1: an explicit relative path ("./report.pdf", "../a/b") or an
        # existing local file ("report.pdf") must never be promoted to a
        # URL — "report.pdf" is a path on disk, not the domain "report.pdf".
        # Callers (e.g. extract_document) catch ValueError and fall back
        # to disk.
        if s.startswith(("./", "../", ".\\", "..\\")) or Path(s).exists():
            raise ValueError(
                f"{raw!r} looks like a local file path, not a URL"
            )
        # Bare domain like "example.com/a" or "www.example.com".
        # Only auto-prefix when the host looks domain-like; a bare word
        # is more likely a mistake than an intranet hostname.
        candidate_host = parsed.path.split("/")[0]
        if "." not in candidate_host and candidate_host != "localhost":
            raise ValueError(f"{raw!r} does not look like a URL")
        # A single segment ending in a document extension is a filename,
        # not a host: "report.pdf" is a path on disk even when the file is
        # not in the current directory, so the Path.exists() check above
        # cannot catch it. ".pdf" is not a TLD.
        if "/" not in s and any(
            candidate_host.lower().endswith(ext) for ext in DOCUMENT_EXTENSIONS
        ):
            raise ValueError(
                f"{raw!r} looks like a local file path, not a URL"
            )
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
    # Tier 2.5: byte cap for the disk cache (0 = unlimited). Least-recently-
    # used entries are evicted to stay under it; see Cache.max_disk_bytes.
    cache_max_bytes: int = 0
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
    # S3: cap (bytes) on response bodies fetched through the Rust core.
    # Bodies larger than this are rejected before being fully read into
    # memory (Content-Length early-reject + streaming chunk cap).
    max_response_bytes: int = 5 * 1024 * 1024
    # Fraction of the output budget (chars/tokens) reserved for the
    # follow-up link list and JSON envelope, so budget enforcement never
    # starves link delivery on content-rich pages (C1).
    link_budget_ratio: float = 0.25
    # S4: honor robots.txt (Disallow/Allow/Crawl-delay) for fetched URLs.
    # Set False for an explicit opt-out (e.g. private test targets).
    respect_robots: bool = True
    # Tier 1.4: when a cached page has expired, revalidate it with a cheap
    # ETag / Last-Modified conditional request before re-downloading. A 304
    # re-freshens the entry for free; a 200 stores the new content. Default
    # True; opt out with STITCH_CONDITIONAL_REVALIDATE=0.
    conditional_revalidation: bool = True
    # §7: optional prompt-injection guard (off by default). Pass a
    # GuardConfig to enable; see stitch_web_researcher.guard.
    guard: Optional[GuardConfig] = None
    # Tier 2.6: size of the fetch-latency sliding window (samples) kept in
    # memory for percentile computation in get_stats()["fetches"].
    fetch_stats_window: int = 1024
    # Tier 2.7: HTTP transport overrides for the static (Rust) fetch path.
    # Baked into the lazily-built shared client at first use, so these are
    # process-level settings (last non-empty value wins). See configure_http.
    http_proxy: Optional[str] = None
    user_agent: Optional[str] = None
    custom_headers: dict = field(default_factory=dict)
    cookies: dict = field(default_factory=dict)
    # Tier 2.8: cross-provider merge for search_web (item 8). When False
    # (the default) providers are strict failover (first success wins); when
    # True every provider is queried and results are merged + deduped by
    # URL, preserving provider priority order, up to max_results.
    search_merge: bool = False

    def __post_init__(self):
        if self.fetch_mode not in ("auto", "browser", "static"):
            raise ValueError(
                f"Invalid fetch_mode {self.fetch_mode!r}; expected 'auto', 'browser', or 'static'"
            )
        if self.candidate_cap < 1:
            raise ValueError("candidate_cap must be >= 1")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be >= 1")
        if not 0.0 <= self.link_budget_ratio < 0.9:
            raise ValueError("link_budget_ratio must be in [0.0, 0.9)")


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

    @staticmethod
    def _shrink_parsed_payload(payload_json: str, budget: Optional[int]) -> str:
        """Re-serialize a ParsedDocumentPayload with page text capped.

        ``budget`` of ``None`` returns the payload unchanged. The bulk of a
        parsed document is ``pages[].raw_text`` / ``pages[].markdown``, so
        those are what shrink; metadata, links and tables are small and stay
        intact because they are what the model navigates by.
        """
        if budget is None:
            return payload_json
        payload = ParsedDocumentPayload.model_validate_json(payload_json)
        for page in payload.pages:
            if len(page.raw_text) > budget:
                page.raw_text = page.raw_text[:budget] + "\n\n... [truncated]"
            if len(page.markdown) > budget:
                page.markdown = page.markdown[:budget] + "\n\n... [truncated]"
        return payload.to_json()

    @staticmethod
    def _shrink_research(result: dict, budget: Optional[int]) -> str:
        """Serialize a research result with per-source content capped.

        Shrinks each source's markdown first; if the budget is tight enough
        that even trimmed sources do not fit, whole sources are dropped from
        the tail and ``sources_omitted`` records how many, so the model can
        tell a short answer from a truncated one.
        """
        out = copy.deepcopy(result)
        if budget is None:
            return json.dumps(out, indent=2)
        for source in out.get("sources", []):
            page = source.get("result")
            if isinstance(page, dict):
                md = page.get("markdown")
                if isinstance(md, str) and len(md) > budget:
                    page["markdown"] = md[:budget] + "\n\n... [truncated]"
                if isinstance(page.get("follow_up_links"), list):
                    page["follow_up_links"] = page["follow_up_links"][:5]
            snippet = source.get("snippet")
            if isinstance(snippet, str) and len(snippet) > budget:
                source["snippet"] = snippet[:budget] + "..."
        # A very small budget means even trimmed sources will not all fit;
        # drop from the tail rather than emit a cut document.
        keep = max(1, budget // 120)
        if len(out.get("sources", [])) > keep:
            out["sources_omitted"] = len(out["sources"]) - keep
            out["sources"] = out["sources"][:keep]
        return json.dumps(out, indent=2)

    def _json_fits(self, text: str, char_limit: int, token_limit: int) -> bool:
        """True when *text* is inside both budgets."""
        if char_limit and len(text) > char_limit:
            return False
        if token_limit and count_tokens(text, self.model_name) > token_limit:
            return False
        return True

    def _fit_json(
        self,
        build,
        char_limit: int,
        token_limit: int,
        overflow: dict,
    ) -> str:
        """Shrink a payload's text fields until its *serialized* form fits.

        ``build(budget)`` must return the payload serialized with every large
        text field truncated to ``budget`` characters (``None`` meaning no
        per-field cap).

        Cutting the serialized JSON instead — which is what ``_truncate`` does,
        and what these paths used to do — yields an unparseable payload, which
        is the LLM's entire reason for calling the tool. So the budget is
        applied to the *content* before serialization, and a payload that still
        will not fit is replaced by the small, valid ``overflow`` envelope
        rather than a cut. The invariant is absolute: this returns JSON.
        """
        out = build(None)
        if self._json_fits(out, char_limit, token_limit):
            return out

        budget = char_limit if char_limit > 0 else len(out)
        while budget > _JSON_FIT_FLOOR:
            budget //= 2
            out = build(budget)
            if self._json_fits(out, char_limit, token_limit):
                return out

        logger.warning(
            "Payload could not be shrunk into the output budget; "
            "returning an overflow envelope instead of invalid JSON"
        )
        return json.dumps(overflow, indent=2)

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

    def _rate_limit_domain(self, url: str) -> None:
        """Enforce per-domain rate limiting for content fetching.

        The minimum gap between same-domain fetches is the resolved
        ``_fetch_interval`` plus a random 0–1 s jitter (only when the
        interval is non-zero), which desynchronizes access patterns. A
        Crawl-delay requested in the site's robots.txt (S4) raises the
        gap floor.
        """
        parsed = urlparse(url)
        domain = parsed.netloc
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
                gap += random.uniform(0.0, 1.0)
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
    # Search
    # ───────────────────────────────

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
            self._guard, [("search_results", text)], main_scope="search_results"
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
                {getattr(p, "name", "?") for p in self.providers if hasattr(p, "name")}
            )
            return json.dumps(
                {
                    "error": f"Unknown search provider: {provider!r}",
                    "available_providers": known,
                },
                indent=2,
            )

        providers_to_try = self._resolve_providers(provider)

        if self._search_merge:
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
        material = f"{normalized_query}|{max_results}|{provider_part}|merge={self._search_merge}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _search_cache_get(self, key: str) -> Optional[list]:
        """Return cached search results for *key*, or None on miss/expired.

        Tier 2.8: in-memory, session-scoped (see ``__init__``).
        """
        with self._search_mem_lock:
            item = self._search_mem.get(key)
            if item is None:
                return None
            timestamp, value = item
            if time.time() - timestamp > self._search_mem_ttl:
                del self._search_mem[key]
                return None
            self._search_mem.move_to_end(key)
            return value

    def _search_cache_put(self, key: str, results: list) -> None:
        """Cache a successful search-result list under its result-level key."""
        with self._search_mem_lock:
            self._search_mem[key] = (time.time(), results)
            self._search_mem.move_to_end(key)
            while len(self._search_mem) > 256:
                self._search_mem.popitem(last=False)

    def _search_cache_clear(self) -> None:
        """Drop all cached search results (invoked by ``clear_cache``)."""
        with self._search_mem_lock:
            self._search_mem.clear()

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
        registered = [getattr(p, "name", "?") for p in self.providers]
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
            return list(self.providers)

        canonical = resolve_provider_name(provider_name)
        if canonical:
            # Match on the provider's explicit canonical name (M2: the old
            # __class__.__name__ derivation made aliases like "ddg" and
            # BrowserOxideSearchProvider unselectable).
            matched = [
                p for p in self.providers
                if getattr(p, "name", None) == canonical
            ]
            if matched:
                others = [p for p in self.providers if p not in matched]
                return matched + others

        # A recognized name whose provider is not registered is ordinary
        # failover: log it so an operator can see the substitution, and
        # search on. An *unrecognized* name never reaches here -- search_web
        # rejects it up front so the model can correct its own call.
        logger.warning(
            "Search provider %r is not registered; falling back to %s",
            provider_name,
            ", ".join(getattr(p, "name", "?") for p in self.providers) or "none",
        )
        return list(self.providers)

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
        html_metadata: Optional[dict] = None,
        page_markdown: Optional[str] = None,
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
        # Tier 1.3: provenance is attached BEFORE the budget loop so the
        # M11 invariant holds on exactly what is delivered — the hash and
        # timestamps count against the budget, not just the rest.
        if html_metadata is not None:
            self._apply_provenance(
                result, html_metadata, page_markdown if page_markdown is not None else markdown
            )
        # §7: the prompt-injection guard runs after truncation, before the
        # budget loop, so the guard block counts against the M11 invariant.
        if self._scan_inspection_guard(result):
            return result

        candidates = result.follow_up_links
        n_total = len(candidates)
        # M11: the old loop re-tokenized the ENTIRE payload on every
        # halving pass (up to ~9 full tokenizations of a large JSON
        # string). Instead, tokenize exactly two payloads up front — the
        # envelope with no links and the full payload — and interpolate
        # the cost of the shrinking link list per pass. Any pass the
        # estimate says fits is verified with one exact tokenization
        # before being accepted, so the final payload always satisfies
        # the token budget.
        envelope_tokens = 0
        if self.max_tokens > 0 and n_total:
            result.follow_up_links = []
            envelope_tokens = count_tokens(
                result.model_dump_json(), self.model_name
            )
            result.follow_up_links = candidates

        full_tokens = 0
        while True:
            payload = result.model_dump_json()
            over_chars = len(payload) > self.max_markdown_chars
            over_tokens = False
            if self.max_tokens > 0:
                n_now = len(result.follow_up_links)
                if n_now == n_total:
                    full_tokens = count_tokens(payload, self.model_name)
                    over_tokens = full_tokens > self.max_tokens
                elif n_now == 0:
                    over_tokens = envelope_tokens > self.max_tokens
                else:
                    est = envelope_tokens + (full_tokens - envelope_tokens) * n_now / n_total
                    if est > self.max_tokens:
                        over_tokens = True
                    else:
                        # Boundary safety: the linear estimate can be off
                        # by a token or two; verify exactly before accepting.
                        over_tokens = count_tokens(payload, self.model_name) > self.max_tokens
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

    def _scan_inspection_guard(self, result: InspectionResult) -> bool:
        """§7: scan the inspection output for prompt injection.

        Runs the guard over the configured scopes (page_markdown /
        page_metadata / follow_up_titles) after truncation. Attaches
        ``result.guard`` and applies the mode: ``annotate`` wraps the
        delivered markdown in an untrusted-content marker, ``redact``
        replaces flagged chunks, ``block`` empties the content (the caller
        withholds the result). Returns True when the content was withheld.
        """
        meta_text = " ".join(
            str(v) for v in (result.metadata or {}).values() if v
        )
        title_text = " ".join(c.title for c in result.follow_up_links)
        block, redacted, withheld = evaluate(
            self._guard,
            [
                ("page_markdown", result.markdown),
                ("page_metadata", meta_text),
                ("follow_up_titles", title_text),
            ],
            main_scope="page_markdown",
        )
        if block is None:
            return False
        result.guard = block
        if withheld:
            result.markdown = ""
            result.markdown_tokens = 0
            result.follow_up_links = []
            result.delivered_links = 0
            return True
        if redacted is not None:
            result.markdown = redacted
        elif (
            block.get("action") == "annotate"
            and "page_markdown" in block.get("scopes", [])
            and result.markdown
        ):
            result.markdown = wrap_untrusted(result.markdown, result.url)
        if result.markdown:
            result.markdown_tokens = count_tokens(result.markdown, self.model_name)
        return False

    def _scan_structured_guard(self, payload, url: str) -> bool:
        """§7: scan a structured payload for prompt injection.

        Scans the page_markdown (pages[0].markdown), page_metadata, and
        follow_up_titles scopes. Attaches ``payload.guard`` and applies the
        mode to the main markdown. Returns True when the content was withheld.
        """
        main_md = payload.pages[0].markdown if payload.pages else ""
        meta = payload.metadata.model_dump() if payload.metadata else {}
        meta_text = " ".join(str(v) for v in meta.values() if v)
        title_text = " ".join(
            (getattr(cand, "title", None) or "") for cand in payload.links
        )
        block, redacted, withheld = evaluate(
            self._guard,
            [
                ("page_markdown", main_md),
                ("page_metadata", meta_text),
                ("follow_up_titles", title_text),
            ],
            main_scope="page_markdown",
        )
        if block is None:
            return False
        payload.guard = block
        if withheld:
            for page in payload.pages:
                page.markdown = ""
            return True
        if payload.pages:
            if redacted is not None:
                payload.pages[0].markdown = redacted
            elif (
                block.get("action") == "annotate"
                and "page_markdown" in block.get("scopes", [])
                and main_md
            ):
                payload.pages[0].markdown = wrap_untrusted(main_md, url)
        return False

    # ── Fetch strategies ─────────────────────────────
    # Each returns the full (markdown, anchored_links, metadata, method) tuple.

    def _static_fetch(self, url: str, keep_html: bool = False):
        """Plain HTTP fetch via the Rust core.

        C2: the Rust core returns the raw HTML alongside the markdown and
        links, so the static path runs the same meta-oxide metadata
        extraction as the browser path — no second network round-trip.

        Tier 3.11: ``keep_html=True`` also returns the raw HTML (5-tuple)
        so table extraction can run on it; the default keeps the M8-pinned
        4-tuple contract.
        """
        (
            _not_modified,
            html,
            md,
            links,
            removed,
            prov,
            etag,
            last_modified,
        ) = fetch_html_conditional(url, self.link_cap, self.max_response_bytes)
        metadata = meta_extractor.extract_all(html, url)
        if removed:
            metadata["hidden_blocks_removed"] = removed
        # Tier 1.3: provenance — status, final URL after redirects,
        # content type, and the fetch time.
        metadata["provenance"] = _provenance_from_fetch_meta(
            prov, url, etag=etag, last_modified=last_modified
        )
        if keep_html:
            return md, links, metadata, "static", html
        return md, links, metadata, "static"

    def _browser_fetch(self, url: str):
        """Stealth-browser fetch; failures propagate (strict)."""
        md, links, meta = _fetch_with_browser_oxide(url)
        meta.setdefault("provenance", _browser_provenance(url))
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

        M12: the returned markdown has relative hrefs rewritten to
        absolute URLs so the body is self-contained for the model.
        """
        md, links, meta, method = self._fetch_html_dispatch(url, use_smart)
        return _absolutize_markdown_links(md, url), links, meta, method

    def _fetch_html_with_html(
        self, url: str, use_smart: Optional[bool] = None
    ) -> tuple:
        """Fetch for ``inspect_html_structured`` (Tier 3.11).

        Same dispatch and fetch instrumentation as ``_fetch_html``, but
        the static path also returns the raw HTML so tables can be
        extracted from it. Returns ``(markdown, links, meta, method,
        html)`` with markdown absolutized (M12); ``html`` is None when the
        browser path served the page (the renderer exposes no raw DOM).
        """
        domain = _domain_of(url)
        started = time.perf_counter()
        try:
            result = self._dispatch_fetch(url, use_smart, keep_html=True)
        except Exception as e:
            self._fetch_stats.record_error(domain, time.perf_counter() - started, e)
            raise
        nbytes = (
            len(result[0].encode("utf-8"))
            if result and isinstance(result[0], str)
            else 0
        )
        self._fetch_stats.record_success(
            domain, time.perf_counter() - started, nbytes
        )
        md, links, meta, method, html = result
        return _absolutize_markdown_links(md, url), links, meta, method, html

    def _fetch_html_dispatch(self, url: str, use_smart: Optional[bool] = None):
        """Instrumented dispatch wrapper (Tier 2.6).

        Records per-fetch latency, bytes, domain, and error class into
        ``self._fetch_stats`` before delegating to :meth:`_dispatch_fetch`.
        """
        domain = _domain_of(url)
        started = time.perf_counter()
        try:
            result = self._dispatch_fetch(url, use_smart)
        except Exception as e:
            self._fetch_stats.record_error(domain, time.perf_counter() - started, e)
            raise
        nbytes = (
            len(result[0].encode("utf-8"))
            if result and isinstance(result[0], str)
            else 0
        )
        self._fetch_stats.record_success(
            domain, time.perf_counter() - started, nbytes
        )
        return result

    def _dispatch_fetch(
        self, url: str, use_smart: Optional[bool] = None, keep_html: bool = False
    ):
        """Dispatch a fetch per ``self.fetch_mode`` / ``use_smart`` (raw,
        with relative markdown hrefs). See ``_fetch_html``.

        Tier 3.11: ``keep_html=True`` extends the result with the raw HTML
        (5-tuple). Only the static path has it — browser renders do not
        expose the raw DOM, so that slot is None there.
        """

        def _static():
            # Keep the default-path call shape (single positional arg, M8)
            # so existing _static_fetch test spies keep working; only the
            # keep_html=True path passes the extra argument.
            return (
                self._static_fetch(url, keep_html=True)
                if keep_html
                else self._static_fetch(url)
            )

        if self.fetch_mode == "browser":
            if use_smart is False:
                return _static()
                return self._static_fetch(url, keep_html=keep_html)
            md, links, meta, method = self._browser_fetch(url)
            return (md, links, meta, method, None) if keep_html else (md, links, meta, method)

        if use_smart is True:
            try:
                md, links, meta, method = self._browser_fetch(url)
                return (
                    (md, links, meta, method, None)
                    if keep_html
                    else (md, links, meta, method)
                )
            except Exception as e:
                logger.warning(
                    "Stealth fetch failed for %s: %s -- falling back to static", url, e
                )

        if self.fetch_mode == "static" or use_smart is False:
            return _static()

        # auto: static first, stealth fallback on failure or non-text content
        try:
            result = _static()
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
        meta.setdefault("provenance", _browser_provenance(url))
        result = (md, links, meta, "stealth-fallback")
        return result + (None,) if keep_html else result

    @staticmethod
    def _looks_like_text(md: str) -> bool:
        """Heuristic: reject empty or binary-garbage payloads (e.g. undecoded
        compressed responses), which would otherwise poison LLM context.

        Uses Unicode categories rather than printability: legitimate text in
        any language (incl. CJK) contains almost no control/format/unassigned
        codepoints, while binary bytes mis-decoded as text are full of them.

        M14: samples head, middle and tail (not just the first 2000 chars),
        because a payload that starts clean and degenerates later (e.g. a
        partially decoded gzip) must also trip the gate. A page is rejected
        if any sampled window exceeds the 2% bad-codepoint ratio.
        """
        if not md or not md.strip():
            return False
        import unicodedata

        def _bad_ratio(chunk: str) -> float:
            if not chunk:
                return 0.0
            bad = sum(
                unicodedata.category(c) in ("Cc", "Cn", "Co", "Cs")
                and c not in "\n\r\t"
                for c in chunk
            )
            return bad / len(chunk)

        n = len(md)
        samples = (
            md[:2000],
            md[n // 2 - 1000 : n // 2 + 1000],
            md[-2000:],
        )
        return all(_bad_ratio(s) < 0.02 for s in samples)

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

    def _stale_page_entry(self, url: str) -> Optional[dict]:
        """Tier 1.4: read the raw page-cache entry ignoring TTL, without
        purging it, so an expired entry's ETag / Last-Modified can drive a
        cheap conditional revalidation. Returns the parsed entry dict or
        None when the key was never stored.

        Called *before* ``_page_cache_get`` (whose ``Cache.get`` purges
        expired entries), so the validators are still on disk.
        """
        raw = self.cache.get_stale("page:" + self._cache_key(url))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def _revalidate_stale_entry(self, url: str, entry: dict):
        """Tier 1.4: conditionally revalidate a stale (expired) static page.

        ``entry`` is the parsed raw page-cache entry captured before the
        normal lookup purged it. Returns ``(was_304, (markdown, links,
        meta, method))`` on success, or None when there is nothing to
        revalidate (non-static entry, no validators, or the conditional
        request failed) - the caller then falls back to a full fetch.

        A 304 keeps the stored content and the original fetched_at /
        http_status / content_type / hash, and only adopts any rotated
        validators (and, if changed, the final URL). A 200 returns the
        freshly fetched content as a normal new entry.
        """
        if not self.conditional_revalidation:
            return None
        method = entry.get("method")
        if method != "static":
            return None
        meta = entry.get("meta") or {}
        prov = meta.get("provenance") or {}
        etag = prov.get("etag")
        last_modified = prov.get("last_modified")
        if not etag and not last_modified:
            return None
        try:
            (
                not_modified,
                html,
                md,
                links,
                removed,
                prov_meta,
                new_etag,
                new_lm,
            ) = fetch_html_conditional(
                url,
                self.link_cap,
                self.max_response_bytes,
                etag=etag,
                last_modified=last_modified,
            )
        except Exception as e:
            logger.debug("Conditional revalidation error for %s: %s", url, e)
            return None
        if not_modified:
            # 304 Not Modified: content unchanged - keep the stored copy and
            # its original provenance; adopt any rotated validators and, if
            # the hop reported a different URL, the refined final_url.
            fresh_prov = dict(prov)
            if new_etag:
                fresh_prov["etag"] = new_etag
            if new_lm:
                fresh_prov["last_modified"] = new_lm
            final_url = prov_meta[1]
            if final_url and final_url != fresh_prov.get("final_url"):
                fresh_prov["final_url"] = final_url
            fresh_meta = dict(meta)
            fresh_meta["provenance"] = fresh_prov
            return True, (
                entry.get("markdown", ""),
                [tuple(p) for p in entry.get("links", [])],
                fresh_meta,
                method,
            )
        # 200: the server has new content - store it as a fresh fetch.
        metadata = meta_extractor.extract_all(html, url)
        if removed:
            metadata["hidden_blocks_removed"] = removed
        metadata["provenance"] = _provenance_from_fetch_meta(
            prov_meta, url, etag=new_etag, last_modified=new_lm
        )
        return False, (md, links, metadata, "static")

    def _apply_provenance(
        self, result: InspectionResult, metadata: dict, markdown: str
    ) -> None:
        """Tier 1.3: stamp provenance onto an inspection result.

        fetched_at / http_status / final_url / content_type come from the
        fetch metadata — the page cache stores that dict, so a cache hit
        reports the ORIGINAL fetch, not the read time. content_hash covers
        the full untruncated markdown, so every chunked read of one page
        shares the same hash (a citation can tie a slice to its source).
        """
        prov = metadata.get("provenance") or {}
        result.fetched_at = prov.get("fetched_at")
        result.http_status = prov.get("http_status")
        result.final_url = prov.get("final_url")
        result.content_type = prov.get("content_type")
        result.content_hash = _sha256_hex(markdown)

    def _inspect_html_page_impl(
        self,
        url: str,
        use_smart: Optional[bool] = None,
        query: Optional[str] = None,
        offset: int = 0,
        max_chunks: int = 1,
    ) -> str:
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
        url, url_error = self._prepare_url(url)
        if url_error is not None:
            return json.dumps(url_error, indent=2)

        # Tier 1.4: capture the raw (possibly expired) page entry before
        # the normal cache lookup purges it - an expired entry's ETag /
        # Last-Modified let us revalidate cheaply (304) instead of
        # re-downloading the whole page.
        stale_entry = None
        if self.conditional_revalidation:
            stale_entry = self._stale_page_entry(url)

        cached = self._page_cache_get(url)
        from_cache = cached is not None
        revalidated = False
        if cached is None:
            # S4: robots.txt compliance -- only on the fetch path; a cache
            # hit performs no network fetch, so it is unaffected.
            if self._robots_disallows(url):
                logger.warning("URL disallowed by robots.txt: %s", url)
                return json.dumps(
                    {"warning": "URL disallowed by robots.txt", "url": url},
                    indent=2,
                )
            # S5: claim the URL so a concurrent call for the same page is
            # rejected here instead of double-fetching; C3 semantics are
            # kept because release on failure un-claims it.
            if not self._claim_in_flight(url):
                logger.warning(
                    "URL already visited or in flight: %s", url
                )
                return json.dumps(
                    {"warning": "URL already visited", "url": url}, indent=2
                )
            try:
                # Politeness delay applies only when we will actually fetch
                # (a conditional revalidation counts as a fetch too).
                self._rate_limit_domain(url)
                if stale_entry is not None:
                    outcome = self._revalidate_stale_entry(url, stale_entry)
                    if outcome is not None:
                        revalidated, cached = outcome
                if cached is None:
                    cached = self._fetch_html(url, use_smart)
            except Exception as e:
                self._release_in_flight(url)
                logger.error("HTML inspection failed for %s: %s", url, e)
                return json.dumps(
                    {"error": f"HTML inspection failed: {str(e)}"}, indent=2
                )
            # Release the in-flight claim now that the fetch is done. Visited
            # marking and the page-cache write are deferred to after the §7
            # guard decision so withheld content is neither stored nor marked
            # visited (a 304 re-put still re-freshens the entry there).
            self._release_in_flight(url)

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
        # Tier 1.2: paging operates at read time on the full cached
        # markdown, so resuming never re-fetches. Explicit paging
        # (offset > 0 or max_chunks != 1) takes precedence over query-
        # based section selection — the caller asked for exact positions,
        # so honor them. Even the default read (no paging, no query) is
        # chunked, so every payload carries resume metadata
        # (next_offset / has_more / chars_total) and any long page stays
        # continuable.
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        try:
            max_chunks = max(1, int(max_chunks))
        except (TypeError, ValueError):
            max_chunks = 1
        explicit_paging = offset > 0 or max_chunks != 1
        query = (query or "").strip()
        selection = None
        next_offset = None
        has_more = False
        if query and not explicit_paging:
            # Tier 1.1: when a research query is supplied and the page
            # does not fit the budget, keep the query-relevant sections
            # instead of truncating head-first. Selection happens at read
            # time on the full cached markdown, so different queries over
            # the same URL select different sections without re-fetching.
            selection = select_relevant_sections(markdown, query, md_chars)
        if selection is not None:
            # The selection already fits the char budget; _truncate here
            # only enforces the token budget as a backstop (and is a no-op
            # when max_tokens is 0).
            truncated_md = self._truncate(selection.markdown, md_chars, md_tokens)
            markdown_truncated = True
        else:
            # Tier 1.2: slice the full markdown at the requested offset
            # (default: the head-first chunk with resume metadata).
            truncated_md, next_offset, has_more = self._slice_markdown(
                markdown, offset, max_chunks, md_chars, md_tokens
            )
            markdown_truncated = (
                offset > 0 or has_more or truncated_md != markdown
            )

        # Build compact metadata summary for LLM output
        meta_summary = self._compact_metadata(html_metadata)

        # Tier 1.3: provenance is applied inside _build_inspection_result
        # (before the M11 budget loop); the hash covers the full page
        # markdown, not the delivered slice.
        result = self._build_inspection_result(
            url, truncated_md, links, meta_summary, fetch_method,
            markdown_truncated=markdown_truncated,
            html_metadata=html_metadata,
            page_markdown=markdown,
        )
        if from_cache or revalidated:
            result.cache_hit = True
        if revalidated:
            result.revalidated = True
        if selection is not None:
            result.query = query
            result.sections_available = selection.total_sections
            result.sections_selected = selection.selected_count
            result.section_anchors = list(selection.anchors)
        else:
            # Tier 1.2: chunked read — report the slice served so the
            # caller can resume where it stopped.
            result.offset = offset
            result.next_offset = next_offset
            result.has_more = has_more
            result.chars_total = len(markdown)
        if result.guard and result.guard.get("withheld"):
            # Withheld content is neither cached nor marked visited: the
            # request did not complete, so a retry can re-evaluate it.
            return json.dumps(
                {
                    "error": "content withheld by prompt-injection guard",
                    "url": url,
                    "guard": result.guard,
                },
                indent=2,
            )
        if not from_cache:
            # Only now that the guard has decided to deliver the content do
            # we mark it visited (C3) and store it (a 304 re-put re-freshens).
            self._mark_visited(url)
            self._page_cache_put(url, *cached)
        return result.model_dump_json()

    def _slice_markdown(
        self,
        markdown: str,
        offset: int,
        max_chunks: int,
        md_chars: int,
        md_tokens: int,
    ) -> tuple[str, int, bool]:
        """Serve consecutive budget-sized chunks of the full markdown.

        Tier 1.2: each chunk is at most ``md_chars`` and breaks at a
        paragraph boundary (double newline) when one sits in the back
        half of the window, so resumes land between paragraphs rather
        than mid-sentence. Each chunk is token-backstopped by ``_truncate``
        (a no-op when ``max_tokens`` is 0).

        Returns ``(delivered_content, next_offset, has_more)``. ``next_offset``
        is the end of the raw slice — not the delivered length — so a
        token-backstop cut can never cause overlap or marker contamination
        on resume.
        """
        md_chars = max(1, md_chars)  # a zero budget must not stall the loop
        total = len(markdown)
        pos = max(0, min(offset, total))
        parts: list[str] = []
        for _ in range(max(1, max_chunks)):
            if pos >= total:
                break
            window = markdown[pos : pos + md_chars]
            if len(window) < md_chars:
                end = total
            else:
                cut = window.rfind("\n\n")
                # Only break at a paragraph boundary found in the back
                # half of the window; otherwise cut at full width. (The
                # strict comparison also keeps tiny budgets from producing
                # zero-length chunks and stalling the loop.)
                end = pos + (cut if cut > md_chars // 2 else md_chars)
            parts.append(self._truncate(markdown[pos:end], md_chars, md_tokens))
            pos = end
        return "".join(parts), pos, pos < total

    def inspect_html_page(
        self,
        url: str,
        use_smart: Optional[bool] = None,
        query: Optional[str] = None,
        offset: int = 0,
        max_chunks: int = 1,
    ) -> str:
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
        """
        return self._inspect_html_page_impl(url, use_smart, query, offset, max_chunks)

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

    async def inspect_html_page_async(
        self,
        url: str,
        use_smart: Optional[bool] = None,
        query: Optional[str] = None,
        offset: int = 0,
        max_chunks: int = 1,
    ) -> str:
        """Async version of inspect_html_page.

        "Async" here means *thread pool*: the shared blocking implementation
        is offloaded to the default executor via run_in_executor so the event
        loop stays responsive. The underlying fetch remains synchronous --
        see the README "Async" note.
        """
        # M6: get_running_loop() replaces the deprecated event-loop lookup.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._inspect_html_page_impl, url, use_smart, query,
            offset, max_chunks,
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
        rejected: dict[str, dict] = {}
        seen = set()
        for raw in urls:
            # A URL the policy refuses (SSRF, bad scheme, local path) is one
            # bad *entry*, not a failed batch: record it and keep going, so a
            # single poisoned link in a scraped list cannot discard every
            # good result alongside it.
            url, url_error = self._prepare_url(raw)
            if url_error is not None:
                rejected[raw] = url_error
                continue
            if url in seen:
                continue
            seen.add(url)
            cached = self._page_cache_get(url)
            if cached is not None:
                cached_entries[url] = cached
                continue
            if url in self.visited_urls:
                logger.warning("Skipping already-visited URL in batch: %s", url)
                continue
            # S4: robots.txt says no -- skip without claiming so the URL
            # stays available if the site changes its rules.
            if self._robots_disallows(url):
                logger.warning("Skipping robots-disallowed URL in batch: %s", url)
                continue
            if not self._claim_in_flight(url):
                # A concurrent single-page call claimed this URL between
                # the check and the claim (S5); skip it rather than
                # double-fetch.
                logger.warning("URL already in flight: %s", url)
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
                        self._mark_visited(url)  # success only (C3)
                        self._page_cache_put(url, *entry)  # C6
                        self._release_in_flight(url)  # S5
                        fetched[url] = self._batch_result(
                            url, *entry, cache_hit=False
                        )
                    except Exception as e:
                        self._release_in_flight(url)  # S5: stays retryable
                        fetched[url] = {"url": url, "error": str(e)}
            else:
                results = []
                if pending:
                    try:
                        results = batch_research(
                            pending,
                            max_links=self.link_cap,
                            max_concurrency=self.max_concurrency,
                            # Same-domain staggering inside the batch engine (0 disables).
                            domain_gap_ms=int(self._fetch_interval * 1000),
                            max_bytes=self.max_response_bytes,
                        )
                    except Exception:
                        # The engine call failed wholesale (e.g. every URL
                        # rejected at validation); release every claim so
                        # the URLs remain retryable (S5).
                        for claimed in pending:
                            self._release_in_flight(claimed)
                        raise
                for entry in _normalize_batch_results(results):
                    if entry.ok:
                        # M12: the Rust batch engine returns raw markdown
                        # with relative hrefs; make the body self-contained.
                        md = _absolutize_markdown_links(
                            entry.markdown or "", entry.url
                        )
                        self._mark_visited(entry.url)  # success only (C3)
                        # Bugfix 5: run the same meta-oxide extraction the
                        # static single-page path runs, so a batch entry and
                        # a single read of the same URL carry identical
                        # metadata instead of the batch shipping {}.
                        meta = self._extract_html_metadata(entry.html, entry.url)
                        # C6: store back into the shared page cache.
                        self._page_cache_put(
                            entry.url, md, entry.links, meta, "static"
                        )
                        self._release_in_flight(entry.url)  # S5
                        fetched[entry.url] = self._batch_result(
                            entry.url, md, entry.links, meta,
                            "static", cache_hit=False
                        )
                    else:
                        self._release_in_flight(entry.url)  # S5: stays retryable
                        fetched[entry.url] = {"url": entry.url, "error": entry.error}

            # Merge cached + fetched entries back in input order (C6).
            output = []
            emitted = set()
            for raw in urls:
                if raw in rejected:
                    if raw not in emitted:
                        emitted.add(raw)
                        output.append(rejected[raw])
                    continue
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
        # Tier 1.3: batch entries carry the same provenance as single
        # page reads (meta comes from the fresh fetch or the page cache),
        # attached before the budget loop.
        result = self._build_inspection_result(
            url, truncated_md, links, self._compact_metadata(meta), method,
            markdown_truncated=truncated_md != md,
            html_metadata=meta,
            page_markdown=md,
        )
        if cache_hit:
            result.cache_hit = True
        return json.loads(result.model_dump_json())

    # ───────────────────────────────
    # Document Extraction
    # ───────────────────────────────

    def _finish_document(self, result: ExtractionResult) -> str:
        """§7: run the guard over document content and serialize the result.

        Applies the guard mode (annotate wrap / redact / block withhold) to
        the delivered content, attaches ``result.guard``, and returns the
        JSON string.
        """
        block, redacted, withheld = evaluate(
            self._guard,
            [("document_text", result.content)],
            main_scope="document_text",
        )
        if block is None:
            return result.model_dump_json()
        result.guard = block
        if withheld:
            return json.dumps(
                {
                    "error": "content withheld by prompt-injection guard",
                    "source": result.source,
                    "guard": block,
                },
                indent=2,
            )
        if redacted is not None:
            result.content = redacted
        elif block.get("action") == "annotate" and result.content:
            result.content = wrap_untrusted(result.content, result.source)
        result.content_tokens = count_tokens(result.content, self.model_name)
        return result.model_dump_json()

    def extract_document(self, source: str, pages: Optional[str] = None) -> str:
        """Extract text content from documents.

        Structured: PDF, DOCX, XLSX, PPTX. Text (Tier 3.10): TXT, MD, CSV,
        JSON (pretty-printed), XML, and RSS/Atom feeds (surfaced as readable
        entry lists). Extension-less URLs whose Content-Type is text-like
        (e.g. text/plain) are also delivered as text.

        Parameters
        ----------
        source : str
            URL or local file path.
        pages : str, optional
            1-based inclusive page range: ``"10"``, ``"10-20"``, ``"10-"``
            or ``"-20"``. For PDFs this selects pages; for XLSX it
            selects sheets (the parser's page blocks). Supported for
            formats the structured parser yields per-page data for
            (PDF, XLSX); for other formats the call errors with an
            actionable message. The full document stays cached under its
            own key, so range reads do not evict whole-document reads.
        """
        try:
            source = normalize_url(source)  # may still be a local path
            is_url = True
        except ValueError:
            is_url = urlparse(source).scheme in ("http", "https")

        if is_url:
            try:
                self._validate_url(source)
            except (ValueError, SsrfBlockedError) as e:
                return json.dumps(self._url_error(source, e), indent=2)
            # S4: robots gate before rate-limiting, so a disallowed fetch
            # does not burn a politeness delay.
            if self._robots_disallows(source):
                return json.dumps(
                    {
                        "error": (
                            f"Document fetch disallowed by robots.txt: {source}"
                        )
                    },
                    indent=2,
                )
            self._rate_limit_domain(source)

        # Tier 1.2: explicit page-range reads go through the structured
        # parser (the only path with per-page structure) and are cached
        # under a range-specific key.
        if pages is not None and str(pages).strip():
            return self._extract_document_pages(source, str(pages).strip(), is_url)

        cache_key = self._cache_key(source) if is_url else source
        cached = self.cache.get(cache_key)
        if cached is not None:
            # Re-apply the budget on every read (C4): the cache holds
            # untruncated content and the budget may have changed since the
            # entry was stored — same read-time truncation as the page cache.
            truncated = self._truncate(cached, self.max_markdown_chars, self.max_tokens)
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self.model_name),
                    cache_hit=True,
                    # Tier 1.3: the stored content carries no fetch timestamp,
                    # so fetched_at stays None; the hash still ties the read
                    # back to the stored bytes.
                    content_hash=_sha256_hex(cached),
                    links=extract_links(cached),
                )
            )

        try:
            if is_url:
                content, prov = self._download_and_extract(source)
            else:
                content = self._extract_local(source)
                prov = {"fetched_at": _utc_now_iso()}

            self.cache.put(cache_key, content)
            truncated = self._truncate(content, self.max_markdown_chars, self.max_tokens)
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self.model_name),
                    cache_hit=False,
                    # Tier 1.3: provenance of the download (or parse time for
                    # local files) plus a hash of the full extracted content.
                    content_hash=_sha256_hex(content),
                    # Link detection runs on the full content, not the
                    # truncated delivery — a budget cut must never lose links.
                    links=extract_links(content),
                    **prov,
                )
            )
        except Exception as e:
            logger.error("Document extraction failed for %s: %s", source, e)
            return json.dumps(
                {"error": f"Document extraction failed: {str(e)}"}, indent=2
            )

    def _fetch_document_url(self, url: str) -> tuple[bytes, dict]:
        """Tier 1.3: download a document URL; return (bytes, provenance).

        Redirects are followed so provenance.final_url is the URL that
        actually served the bytes — final_url != url tells the model the
        content moved.
        """
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(url, headers=self._next_headers())
            response.raise_for_status()
        prov = {
            "fetched_at": _utc_now_iso(),
            "http_status": response.status_code,
            "final_url": str(response.url),
            "content_type": response.headers.get("content-type"),
        }
        return response.content, prov

    def _download_and_extract(self, url: str) -> tuple[str, dict]:
        """Download a document from URL; returns (content, provenance).

        Tier 3.10: when the URL carries no usable extension (or an
        unrecognized one) but the server says the body is text-like
        (Content-Type: text/plain, application/json, ...), the bytes are
        extracted as text instead of raising.
        """
        data, prov = self._fetch_document_url(url)
        try:
            return self._extract_from_bytes(data, url), prov
        except ValueError:
            ct = (prov.get("content_type") or "").split(";", 1)[0].strip().lower()
            kind = self._TEXT_LIKE_CONTENT_TYPES.get(ct)
            if kind is None:
                raise
            if kind == "json":
                return self._extract_json_text(data), prov
            if kind == "xml":
                return self._extract_xml_feed(data), prov
            return data.decode("utf-8-sig", errors="replace"), prov

    # Tier 1.2: page-range reads. The flat extractor joins all pages into
    # one string, so a range can only be served by re-deriving the
    # per-page structure via StructuredOxideParser (Rust, fast). Formats
    # without per-page structure (DOCX/PPTX parse as one page, text
    # formats have no pages) are refused with an actionable message.
    _PAGED_EXTRACT_SUFFIXES = (".pdf", ".xlsx")

    def _extract_document_pages(
        self, source: str, pages_spec: str, is_url: bool
    ) -> str:
        """Serve one page range of a document (see extract_document)."""
        from stitch_web_researcher.structured_parser import parse_page_range

        try:
            start, end = parse_page_range(pages_spec)
        except ValueError as e:
            return json.dumps({"error": str(e)}, indent=2)

        suffix = (
            Path(urlparse(source).path).suffix if is_url else Path(source).suffix
        ).lower()
        if suffix not in self._PAGED_EXTRACT_SUFFIXES:
            return json.dumps(
                {
                    "error": (
                        f"Page selection is supported for PDF (pages) and "
                        f"XLSX (sheets); this source is "
                        f"{suffix or 'extensionless'}. Call extract_document "
                        f"without pages, or convert the file to PDF."
                    )
                },
                indent=2,
            )

        base_key = self._cache_key(source) if is_url else source
        cache_key = f"{base_key}#pages={pages_spec}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            truncated = self._truncate(
                cached, self.max_markdown_chars, self.max_tokens
            )
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self.model_name),
                    cache_hit=True,
                    page_range=pages_spec,
                    # Tier 1.3: hash of the stored range content (the store
                    # keeps no fetch timestamp, so fetched_at stays None).
                    content_hash=_sha256_hex(cached),
                )
            )

        try:
            payload, prov = self._parse_document_pages(source, is_url)
            total = len(payload.pages)
            if total == 0:
                return json.dumps(
                    {"error": f"Document has no pages: {source}"}, indent=2
                )
            start = max(start, 1)
            if start > total:
                return json.dumps(
                    {
                        "error": (
                            f"Page range {pages_spec!r} out of bounds: "
                            f"document has {total} page(s)."
                        )
                    },
                    indent=2,
                )
            end = min(end if end is not None else total, total)
            end = max(end, start)
            selected = payload.pages[start - 1 : end]
            content = "\n\n".join(p.markdown for p in selected).strip()
            if not content:
                return json.dumps(
                    {
                        "error": (
                            f"Page range {pages_spec!r} produced no text."
                        )
                    },
                    indent=2,
                )

            self.cache.put(cache_key, content)
            truncated = self._truncate(
                content, self.max_markdown_chars, self.max_tokens
            )
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self.model_name),
                    cache_hit=False,
                    page_range=pages_spec,
                    page_start=start,
                    page_end=end,
                    total_pages=total,
                    # Tier 1.3: hash of the delivered range plus download
                    # provenance (URL reads) or parse time (local reads).
                    content_hash=_sha256_hex(content),
                    **prov,
                )
            )
        except Exception as e:
            logger.error("Page-range extraction failed for %s: %s", source, e)
            return json.dumps(
                {"error": f"Page-range extraction failed: {str(e)}"}, indent=2
            )

    def _parse_document_pages(self, source: str, is_url: bool):
        """Parse a document into a ParsedDocumentPayload (URL or local).

        Returns (payload, provenance); for local files the provenance
        carries only the parse time.
        """
        import os
        import tempfile as tf

        parser = StructuredOxideParser()
        if not is_url:
            return parser.parse_file(source), {"fetched_at": _utc_now_iso()}
        data, prov = self._fetch_document_url(source)
        suffix = Path(urlparse(source).path).suffix.lower() or ".pdf"
        tmp_path = None
        try:
            with tf.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            return parser.parse_file(tmp_path), prov
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _extract_local(self, path: str) -> str:
        """Extract content from a local document file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        content = file_path.read_bytes()
        return self._extract_from_bytes(content, str(file_path))

    # M16: plain-text formats the extractor can really deliver — these are
    # exactly what DOCUMENT_EXTENSIONS (structured_parser) may advertise in
    # addition to pdf/OOXML. classify_link and _extract_from_bytes must stay
    # in sync, or the model is promised an extraction that would raise.
    # Tier 3.10 (item 10): JSON joins the text family (pretty-printed when
    # valid); XML/RSS/Atom get feed-aware extraction with a raw-text
    # fallback, so every advertised text format always delivers content.
    _TEXT_SUFFIXES = (".csv", ".txt", ".md", ".json")
    _XML_FEED_SUFFIXES = (".xml", ".rss", ".atom")
    # Content-types that mark a body as extractable text when the URL gives
    # no usable extension (extension-less API/feed/plain-text URLs).
    _TEXT_LIKE_CONTENT_TYPES = {
        "text/plain": "text",
        "text/markdown": "text",
        "text/csv": "text",
        "application/json": "json",
        "text/xml": "xml",
        "application/xml": "xml",
        "application/rss+xml": "xml",
        "application/atom+xml": "xml",
    }
    # Cap on entries surfaced from a feed; the output budget truncation
    # still applies on top.
    _FEED_MAX_ENTRIES = 50
    _UNSUPPORTED_FORMAT_HINTS = {
        ".doc": "convert the file to .docx",
        ".xls": "convert the file to .xlsx",
        ".ppt": "convert the file to .pptx",
        ".odt": "convert the file to .docx",
        ".ods": "convert the file to .xlsx",
        ".odp": "convert the file to .pptx",
        ".rtf": "convert the file to .docx or PDF",
        ".epub": "convert the file to PDF",
    }

    def _extract_from_bytes(self, data: bytes, source: str) -> str:
        """Extract text from document bytes based on file type."""
        suffix = Path(source).suffix.lower()

        if suffix == ".pdf":
            doc = require_pdf_oxide().from_bytes(data)
            return doc.to_markdown_all()
        elif suffix in (".docx", ".xlsx", ".pptx"):
            doc = require_office_oxide().from_bytes(data)
            return doc.to_markdown()
        elif suffix in self._TEXT_SUFFIXES:
            # M16: plain text is trivial to support and covers the
            # CSV/TXT/MD/JSON links that classify_link advertises.
            if suffix == ".json":
                return self._extract_json_text(data)
            return data.decode("utf-8-sig", errors="replace")
        elif suffix in self._XML_FEED_SUFFIXES:
            # Tier 3.10: RSS/Atom feeds become readable entry lists; any
            # other (or malformed) XML falls back to the raw text.
            return self._extract_xml_feed(data)
        elif suffix in self._UNSUPPORTED_FORMAT_HINTS:
            # M16: honest, actionable failure for formats we cannot parse.
            hint = self._UNSUPPORTED_FORMAT_HINTS[suffix]
            raise ValueError(
                f"Unsupported document format: {suffix} ({hint})."
            )
        else:
            raise ValueError(f"Unsupported document format: {suffix}")

    @staticmethod
    def _extract_json_text(data: bytes) -> str:
        """Tier 3.10: JSON as text — pretty-printed when valid, raw otherwise."""
        import json as _json

        raw = data.decode("utf-8-sig", errors="replace")
        try:
            obj = _json.loads(raw)
        except ValueError:
            return raw
        return _json.dumps(obj, indent=2, ensure_ascii=False)

    @staticmethod
    def _extract_xml_feed(data: bytes) -> str:
        """Tier 3.10: RSS/Atom/RDF feeds as readable entries.

        Uses the stdlib ElementTree (no new dependency). Feeds are detected
        by local tag name so namespaces are irrelevant. Any parse failure —
        or an XML document with no item/entry elements (e.g. a sitemap) —
        falls back to the raw text so a .xml/.rss/.atom source always
        delivers *something* readable.
        """
        import xml.etree.ElementTree as ET

        raw = data.decode("utf-8-sig", errors="replace")
        try:
            root = ET.fromstring(data)
        except (ET.ParseError, UnicodeDecodeError, ValueError):
            return raw

        def _local(tag) -> str:
            return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

        def _text(el, names) -> str:
            for child in el.iter():
                if _local(child.tag) in names and child.text and child.text.strip():
                    return child.text.strip()
            return ""

        entries = []
        for el in root.iter():
            if _local(el.tag) not in ("item", "entry"):
                continue
            title = _text(el, {"title"})
            link = ""
            for child in el.iter():
                if _local(child.tag) == "link":
                    href = child.get("href")
                    if href:
                        link = href
                    elif child.text and child.text.strip():
                        link = child.text.strip()
                    break
            desc = _text(el, {"description", "summary", "content"})
            date = _text(el, {"pubDate", "published", "updated", "date"})
            entries.append((title, link, date, desc))

        if not entries:
            return raw

        feed_title = ""
        for child in root:  # direct children: channel / feed
            feed_title = _text(child, {"title"})
            if feed_title:
                break

        lines = []
        if feed_title:
            lines.append(f"# {feed_title}")
            lines.append("")
        lines.append(f"Feed entries: {min(len(entries), 50)}")
        lines.append("")
        shown = 0
        for title, link, date, desc in entries:
            if shown >= 50:
                break
            shown += 1
            lines.append(f"- **{title or '(untitled)'}**")
            if link:
                lines.append(f"  Link: {link}")
            if date:
                lines.append(f"  Date: {date}")
            if desc:
                lines.append(f"  {desc}")
        if len(entries) > 50:
            lines.append("")
            lines.append(
                f"… {len(entries) - 50} more entries not shown "
                f"(capped at 50)."
            )
        return "\n".join(lines)

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
            try:
                self._validate_url(source)
            except (ValueError, SsrfBlockedError) as e:
                return json.dumps(self._url_error(source, e), indent=2)
            # S4: robots gate before rate-limiting.
            if self._robots_disallows(source):
                return json.dumps(
                    {
                        "error": (
                            f"Document fetch disallowed by robots.txt: {source}"
                        )
                    },
                    indent=2,
                )
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

            # Files lose <a href> structure: detect URLs written into the
            # extracted text so documents get the same follow-up signal
            # HTML pages get. (Anchored links, when the parser provides
            # them, take precedence.)
            if not payload.links:
                full_text = "\n".join(p.raw_text for p in payload.pages)
                text_urls = extract_links(full_text)
                if text_urls:
                    payload.links = build_follow_up_candidates(
                        [(u, "(text)") for u in text_urls]
                    )

            if is_url and tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            # Budget the page text, never the serialized JSON: a string
            # cut here would hand the model unparseable output.
            payload_json = payload.to_json()
            return self._fit_json(
                lambda b: self._shrink_parsed_payload(payload_json, b),
                self.max_markdown_chars,
                self.max_tokens,
                {
                    "source": source,
                    "error": "document too large for the output budget",
                    "hint": "narrow the read with the pages parameter",
                },
            )

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

    # Tier 3.11: caps for HTML table extraction (a page with a 10,000-row
    # table must not drown the token budget in table JSON).
    _HTML_MAX_TABLES = 20
    _HTML_MAX_TABLE_ROWS = 500

    def _extract_html_tables(self, html: str):
        """Tier 3.11: extract HTML tables into ExtractedTable objects.

        Best effort: extraction errors are logged and yield no tables; the
        page content itself is never affected. Returns a list of
        ExtractedTable (name, headers, rows) in document order.
        """
        from stitch_web_researcher.structured_parser import ExtractedTable

        try:
            raw = extract_tables_from_html(
                html, self._HTML_MAX_TABLES, self._HTML_MAX_TABLE_ROWS
            )
        except Exception as e:
            logger.warning("HTML table extraction failed: %s", e)
            return []
        return [
            ExtractedTable(name=name, headers=headers, rows=rows)
            for name, headers, rows in raw
        ]

    def inspect_html_structured(self, url: str, use_smart: Optional[bool] = None) -> str:
        """
        Fetch a web page and return it as a structured ParsedDocumentPayload
        with metadata (OG, Twitter, JSON-LD), markdown content, tables,
        and links.

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
        url, url_error = self._prepare_url(url)
        if url_error is not None:
            return json.dumps(url_error, indent=2)

        # Cache stores the untruncated payload JSON; budgets are re-applied
        # on every read so changed limits are honored. A cached result is
        # served on repeat visits too (C3: data beats a warning).
        cached_json = self.cache.get("structured:" + self._cache_key(url))

        try:
            if cached_json is not None:
                logger.info("Cache hit (structured) for %s", url)
                return self._fit_json(
                    lambda b: self._shrink_parsed_payload(cached_json, b),
                    self.max_markdown_chars,
                    self.max_tokens,
                    {
                        "url": url,
                        "error": "page too large for the output budget",
                        "cache_hit": True,
                    },
                )
            # S4: robots.txt compliance -- only on the fetch path.
            if self._robots_disallows(url):
                logger.warning("URL disallowed by robots.txt: %s", url)
                return json.dumps(
                    {"warning": "URL disallowed by robots.txt", "url": url}, indent=2
                )
            if not self._claim_in_flight(url):
                logger.warning("URL already visited or in flight: %s", url)
                return json.dumps(
                    {"warning": "URL already visited", "url": url}, indent=2
                )
            self._rate_limit_domain(url)

            markdown, links, html_metadata, fetch_method, html = (
                self._fetch_html_with_html(url, use_smart)
            )

            # Tier 3.11: extract tables from the raw HTML (static path
            # only; browser renders expose no raw DOM).
            tables = self._extract_html_tables(html) if html else []

            # Build structured payload via unified parser
            parser = StructuredOxideParser()
            payload = parser.parse_html(
                markdown=markdown,
                links=links,
                html_metadata=html_metadata,
                url=url,
                max_links=self.max_links,
                tables=tables,
            )
            # §7: guard the payload before caching so repeat reads carry
            # the already-scanned result.
            if self._scan_structured_guard(payload, url):
                self._release_in_flight(url)  # S5: stays retryable
                return json.dumps(
                    {
                        "error": "content withheld by prompt-injection guard",
                        "url": url,
                        "guard": payload.guard,
                    },
                    indent=2,
                )

            payload_json = payload.to_json()
            self.cache.put("structured:" + self._cache_key(url), payload_json)
            self._mark_visited(url)  # success only (C3)
            self._release_in_flight(url)  # S5
            return self._fit_json(
                lambda b: self._shrink_parsed_payload(payload_json, b),
                self.max_markdown_chars,
                self.max_tokens,
                {"url": url, "error": "page too large for the output budget"},
            )
        except Exception as e:
            self._release_in_flight(url)  # S5: stays retryable
            logger.error("Structured HTML inspection failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"Structured HTML inspection failed: {str(e)}"}, indent=2
            )

    # ───────────────────────────────
    # Sitemap-aware discovery (Tier 3.12)
    # ───────────────────────────────

    # Tier 3.12: discovery caps -- a sitemap index fan-out must not turn
    # "find the site's pages" into an unbounded crawl.
    _DISCOVER_MAX_SITEMAP_FETCHES = 10
    _DISCOVER_MAX_INDEX_HOPS = 3
    _DISCOVER_MAX_URLS_PER_SITEMAP = 500
    _DISCOVER_MAX_URLS = 1000
    _FEED_TYPE_PREFIXES = (
        "application/rss+xml",
        "application/atom+xml",
        "application/feed+json",
    )
    _FEED_LINK_RE = re.compile(
        r"<link\b[^>]*rel\s*=\s*[\"']?alternate[\"']?[^>]*>",
        re.IGNORECASE | re.DOTALL,
    )
    _LINK_ATTR_RE = re.compile(
        r"(?P<attr>type|href)\s*=\s*[\"'](?P<value>[^\"']*)[\"']",
        re.IGNORECASE,
    )

    def discover_resources(self, url: str) -> str:
        """Discover a site's structured resources (Tier 3.12).

        A cheaper alternative to link-graph crawling: fetch the page once
        and look for ``<link rel="alternate">`` feed declarations, then
        probe the site root for ``/sitemap.xml`` (following sitemap
        indexes with bounded hops and fetch counts). Returns deduplicated,
        budgeted lists of feed URLs and sitemap page URLs.

        All probes are best-effort: a missing sitemap, a malformed feed,
        or a failed page fetch degrades the result instead of raising.

        Parameters
        ----------
        url : str
            A page or site URL to discover resources for.

        Returns
        -------
        str
            JSON with ``url``, ``site_root``, ``feeds`` (list of
            ``{url, type}``), ``sitemaps`` (list of ``{url, kind, count}``
            with kind ``urlset``/``index``), the merged deduplicated
            ``urls`` list, ``count``, and ``truncated`` (true when a
            budget cap cut the list short).
        """
        url, url_error = self._prepare_url(url)
        if url_error is not None:
            return json.dumps(url_error, indent=2)

        if self._robots_disallows(url):
            logger.warning("URL disallowed by robots.txt: %s", url)
            return json.dumps(
                {"warning": "URL disallowed by robots.txt", "url": url}, indent=2
            )
        if not self._claim_in_flight(url):
            logger.warning("URL already visited or in flight: %s", url)
            return json.dumps(
                {"warning": "URL already visited", "url": url}, indent=2
            )
        self._rate_limit_domain(url)

        try:
            feeds: list = []
            sitemaps: list = []
            found: dict = {}  # ordered dedupe of discovered page URLs
            truncated = False

            # 1) Page fetch: feed alternates from the raw HTML (static
            #    path only; browser renders expose no raw DOM).
            _md, _links, _meta, _method, page_html = (
                self._fetch_html_with_html(url)
            )
            if page_html:
                feeds = self._find_feed_links(page_html, url)

            # 2) Sitemap probe at the site root (same origin as the
            #    already-validated input URL).
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                site_root = f"{parsed.scheme}://{parsed.netloc}"
            else:
                site_root = None
            sitemaps, found, truncated = self._probe_sitemaps(site_root)

            return json.dumps(
                {
                    "url": url,
                    "site_root": site_root,
                    "feeds": feeds,
                    "sitemaps": sitemaps,
                    "urls": list(found.keys()),
                    "count": len(found),
                    "truncated": truncated,
                },
                indent=2,
            )
        except Exception as e:
            logger.error("Discovery failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"Discovery failed: {str(e)}", "url": url}, indent=2
            )
        finally:
            # S5: release exactly once on every exit path. Discovery does
            # not mark the page visited -- it is metadata-level, so the
            # page stays inspectable afterwards.
            self._release_in_flight(url)

    def _find_feed_links(self, html: str, base_url: str) -> list:
        """Tier 3.12: find ``<link rel="alternate">`` feed declarations.

        Only feed content-types (RSS/Atom/Feed-JSON) count; language
        alternates (``hreflang``) are ignored. Relative hrefs are
        absolutized against *base_url*. Regex-based on purpose: the page
        is arbitrary HTML, not well-formed XML.
        """
        feeds = []
        for tag in self._FEED_LINK_RE.findall(html):
            attrs = {
                m.group("attr").lower(): m.group("value")
                for m in self._LINK_ATTR_RE.finditer(tag)
            }
            link_type = attrs.get("type", "").lower().split(";")[0].strip()
            if not any(
                link_type.startswith(prefix)
                for prefix in self._FEED_TYPE_PREFIXES
            ):
                continue
            href = (attrs.get("href") or "").strip()
            if not href:
                continue
            feeds.append({"url": urljoin(base_url, href), "type": link_type})
        return feeds

    def _probe_sitemaps(self, site_root: Optional[str]):
        """Tier 3.12: probe ``/sitemap.xml`` and follow indexes (bounded).

        Returns ``(sitemaps, found, truncated)`` where *sitemaps* is a
        list of ``{url, kind, count}`` records in fetch order, *found* is
        an ordered dict of discovered page URLs, and *truncated* is true
        when the total-URL cap cut the list short. Fetch/parse failures
        are logged and skipped (best effort).
        """
        sitemaps: list = []
        found: dict = {}
        truncated = False
        if not site_root:
            return sitemaps, found, truncated

        first = site_root.rstrip("/") + "/sitemap.xml"
        try:
            self._validate_url(first)
        except Exception:
            return sitemaps, found, truncated

        import xml.etree.ElementTree as ET

        queue = [first]
        hops = {first: 0}
        seen = set()
        fetched = 0
        while queue and fetched < self._DISCOVER_MAX_SITEMAP_FETCHES:
            sm_url = queue.pop(0)
            if sm_url in seen:
                continue
            seen.add(sm_url)
            fetched += 1

            try:
                _md, _links, _meta, _method, xml_text = self._static_fetch(
                    sm_url, keep_html=True
                )
            except Exception as e:
                logger.info("Sitemap probe failed for %s: %s", sm_url, e)
                continue
            if not xml_text:
                continue
            try:
                # lstrip: whitespace before the <?xml?> declaration is
                # legal XML but expat rejects it; some hosts emit it.
                root = ET.fromstring(xml_text.lstrip())
            except ET.ParseError as e:
                logger.info("Sitemap parse failed for %s: %s", sm_url, e)
                continue

            kind_tag = root.tag.rsplit("}", 1)[-1]  # namespace-safe
            locs = [
                (el.text or "").strip()
                for el in root.iter()
                if el.tag.rsplit("}", 1)[-1] == "loc" and (el.text or "").strip()
            ]
            if kind_tag not in ("urlset", "sitemapindex") or not locs:
                continue
            if len(locs) > self._DISCOVER_MAX_URLS_PER_SITEMAP:
                locs = locs[: self._DISCOVER_MAX_URLS_PER_SITEMAP]
                truncated = True
            kind = "index" if kind_tag == "sitemapindex" else "urlset"
            locs = [urljoin(sm_url, loc) for loc in locs]
            sitemaps.append({"url": sm_url, "kind": kind, "count": len(locs)})

            hop = hops.get(sm_url, 0)
            if kind == "index" and hop + 1 <= self._DISCOVER_MAX_INDEX_HOPS:
                for child in locs:
                    if child not in seen and child not in queue:
                        hops[child] = hop + 1
                        queue.append(child)
            else:
                for page_url in locs:
                    if page_url not in found:
                        if len(found) >= self._DISCOVER_MAX_URLS:
                            truncated = True
                            break
                        found[page_url] = None
        return sitemaps, found, truncated

    # ------------------------------------------------------------------
    # Research orchestration (Tier 3.13)
    # ------------------------------------------------------------------

    #: Hard cap on pages fetched per research run.
    _RESEARCH_MAX_PAGES = 10

    # ── Focused crawl (deep-research support) ─────────────────────
    # A bounded best-first crawl over the link graph: the frontier is
    # ranked by relevance (score * decay^depth) instead of blind BFS
    # order, so the page budget is spent on what looks like the
    # answer. With flat scores the order degrades to plain BFS (ties
    # break by discovery order), so hop 1 can never outrank depth 2+
    # unless the links there are actually more relevant.
    _CRAWL_MAX_DEPTH = 5        # hard cap for the max_depth parameter
    _CRAWL_MAX_PAGES = 50       # hard cap for the max_pages parameter
    _CRAWL_PAGE_CHARS = 300     # per-page skim kept in the crawl payload
    _CRAWL_QUEUE_CAP = 200      # bounded frontier; lowest scores dropped
    _CRAWL_DEPTH_DECAY = 0.7    # a depth-d link must outscore shallow ones ~1/0.7^d
    _CRAWL_QUERY_WEIGHT = 0.7   # weight of query coverage in the score
    _CRAWL_CONTEXT_WEIGHT = 0.3  # weight of containing-page topic coverage
    _CRAWL_TOPIC_WORDS = 40     # size of the per-page topic vocabulary
    _CRAWL_MIN_SCORE = 0.05     # default relevance floor (parameter)
    _CRAWL_LIST_CAP = 30        # cap for auxiliary lists in the payload
    _CRAWL_SKIP_EXTENSIONS = frozenset({
        ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg",
        ".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".mp3", ".mp4", ".webm", ".avi", ".mov", ".zip", ".gz", ".tar",
        ".rar", ".exe", ".dmg",
    })
    _CRAWL_SKIP_PATH_PREFIXES = (
        "/login", "/signin", "/sign-in", "/signout", "/logout",
        "/signup", "/register", "/account", "/profile",
        "/cart", "/checkout", "/search", "/tag/", "/tags/",
        "/author/", "/feed", "/track",
    )
    _CRAWL_STOPWORDS = frozenset(
        "a about above after again against all am an and any are as at be "
        "because been before below being between both but by can did do does "
        "doing down during each few for from further had has have having he "
        "her here hers him his how i if in into is it its just me more most "
        "my no nor not of off on once only or other our out over own same "
        "she should so some such than that the their them then there these "
        "they this those through to too under until up very was we were what "
        "when where which while who why will with you your yours".split()
    )

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

        # 2) Dedupe + validate candidate URLs (first comes, first served).
        candidates = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            try:
                url = normalize_url(str(item.get("url") or ""))
            except ValueError:
                continue  # non-http scheme or malformed: skip
            if not url or url in seen:
                continue
            try:
                self._validate_url(url)
            except Exception:
                continue  # SSRF guard: skip quietly
            seen.add(url)
            candidates.append(
                (
                    url,
                    str(item.get("title") or ""),
                    str(item.get("snippet") or ""),
                )
            )
            if len(candidates) >= depth:
                break

        # 3) Fan out through the normal page pipeline. Each source gets
        # the toolbox default budget; the final global budget is applied
        # to the whole response below, so later sources are the first to
        # give ground under budget pressure.
        sources = []
        for url, title, snippet in candidates:
            record = {"url": url, "title": title, "snippet": snippet}
            try:
                raw_page = self._inspect_html_page_impl(
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
        }
        return self._fit_json(
            lambda b: self._shrink_research(result, b),
            self.max_markdown_chars,
            budget,
            {
                "topic": topic,
                "depth": depth,
                "error": "research result too large for the output budget",
                "hint": "lower depth or raise max_tokens",
            },
        )

    # Focused crawl (deep-research support)
    # ───────────────────────────────

    @staticmethod
    def _crawl_host_key(url: str) -> str:
        """Host with a leading ``www.`` stripped, for same-host checks."""
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    @classmethod
    def _crawl_tokens(cls, text: str) -> set:
        """Content words of *text* (lowercase alnum, stopwords removed)."""
        return {
            t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in cls._CRAWL_STOPWORDS
        }

    @classmethod
    def _crawl_topic_words(cls, text: str) -> set:
        """Top content words of a page (TF-ranked, capped, deterministic).

        Runs over the page's full delivered text (not just the title or
        first lines) — that is the neighbourhood signal its outgoing
        links are scored against.
        """
        counts: dict = {}
        for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
            if t in cls._CRAWL_STOPWORDS:
                continue
            counts[t] = counts.get(t, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return {t for t, _ in ranked[: cls._CRAWL_TOPIC_WORDS]}

    @classmethod
    def _crawl_score(
        cls,
        url: str,
        anchor: str,
        depth: int,
        query_terms: set,
        page_terms: set,
    ) -> float:
        """Relevance score of a frontier candidate (see ``crawl``).

        ``score = QUERY_WEIGHT * cover(label, query)``
        ``        + CONTEXT_WEIGHT * cover(label, page_topic)``
        where the label is the candidate's anchor text plus its URL path
        tokens, and cover(T, S) measures how much of the relevant side
        the label echoes. The depth decay is applied by the caller so
        the reported per-page score is depth-independent and comparable.
        """
        label = cls._crawl_tokens(anchor)
        label |= cls._crawl_tokens(urlparse(url).path)
        score = 0.0
        if query_terms:
            score += cls._CRAWL_QUERY_WEIGHT * len(label & query_terms) / len(query_terms)
        if label and page_terms:
            score += cls._CRAWL_CONTEXT_WEIGHT * len(label & page_terms) / len(label)
        return score

    @staticmethod
    def _shrink_crawl(result: dict, budget: Optional[int]) -> str:
        """Serialize a crawl result with the page list capped to fit.

        Per-page content is already skims (``_CRAWL_PAGE_CHARS``); the
        only remaining lever under a tight budget is dropping pages from
        the tail — which matches their (descending) priority anyway.
        """
        out = copy.deepcopy(result)
        if budget is None:
            return json.dumps(out, indent=2)
        # A page record is at most _CRAWL_PAGE_CHARS plus ~200 chars of
        # envelope, so 500 chars per kept page is a safe unit.
        keep = max(1, budget // 500)
        pages = out.get("pages") or []
        if len(pages) > keep:
            out["pages_omitted"] = len(pages) - keep
            out["pages"] = pages[:keep]
        return json.dumps(out, indent=2)

    def crawl(
        self,
        root_url: str,
        query: Optional[str] = None,
        max_depth: int = 3,
        max_pages: int = 15,
        same_host: bool = False,
        min_score: float = 0.05,
    ) -> str:
        """Bounded focused crawl over a site's link graph.

        BFS from *root_url*, but the frontier is a priority queue ranked
        by relevance, so the page budget goes to the most relevant links
        instead of the first ones in the HTML:

        1. Fetch the root through the normal page pipeline (cache,
           robots, SSRF, rate limits, provenance) — depth 0.
        2. Score each outgoing link: query coverage (0.7) plus
           containing-page topic coverage (0.3); the topic vocabulary
           is the page's full delivered text, not just its title.
        3. Pop the highest ``score * 0.7**depth`` (ties: discovery
           order — flat scores therefore degrade to plain BFS) and
           fetch it, until *max_pages* pages are fetched, the frontier
           is exhausted, or *max_depth* is reached.

        *query* focuses the ranking; when omitted the root page's own
        title and content words stand in for it. Links to documents
        (PDF/DOCX/...) are never fetched here — they are collected in
        ``documents`` so the agent can read them via extract_document
        (which surfaces the URLs written inside them). Failed fetches
        do not count against *max_pages*. Every fetched page stays in
        the page cache in full, so a later ``inspect_html_page`` of the
        same URL is a cache hit delivering the complete content.

        Returns
        -------
        str
            JSON: root, query echo, parameters, per-page records
            (url, depth, title, score, markdown skim, links_total),
            errors, documents, skipped (with reasons), counters, and
            the stop reason.
        """
        try:
            root = normalize_url(root_url)
        except ValueError as e:
            return json.dumps({"error": f"crawl: {e}"}, indent=2)
        try:
            self._validate_url(root)
        except Exception as e:
            return json.dumps({"error": f"crawl: {e}"}, indent=2)

        try:
            max_depth = int(max_depth)
        except (TypeError, ValueError):
            max_depth = 3
        max_depth = max(0, min(max_depth, self._CRAWL_MAX_DEPTH))
        try:
            max_pages = int(max_pages)
        except (TypeError, ValueError):
            max_pages = 15
        max_pages = max(1, min(max_pages, self._CRAWL_MAX_PAGES))
        try:
            min_score = float(min_score)
        except (TypeError, ValueError):
            min_score = self._CRAWL_MIN_SCORE
        min_score = max(0.0, min_score)

        root_key = self._crawl_host_key(root)
        queue: list = []  # (effective score, seq, url, depth, anchor)
        seq = 0
        queue_dropped = 0
        documents_total = 0
        skipped_total = 0
        visited: set = set()
        doc_seen: set = set()
        skip_seen: set = set()
        pages: list = []
        errors: list = []
        skipped: list = []
        documents: list = []

        def note_skipped(url: str, reason: str) -> None:
            nonlocal skipped_total
            mark = (url, reason)
            if mark in skip_seen:
                return
            skip_seen.add(mark)
            skipped_total += 1
            if len(skipped) < self._CRAWL_LIST_CAP:
                skipped.append({"url": url, "reason": reason})

        def expand(page: dict, page_url: str, depth: int) -> None:
            """Score a fetched page's links and push the survivors on."""
            nonlocal seq, queue, queue_dropped, documents_total
            if depth >= max_depth:
                return
            title = str((page.get("metadata") or {}).get("title") or "")
            page_terms = self._crawl_topic_words(
                (page.get("markdown") or "") + " " + title
            )
            for cand in page.get("follow_up_links") or []:
                if not isinstance(cand, dict):
                    continue
                raw_url = str(cand.get("url") or "")
                if not raw_url:
                    continue
                try:
                    url = normalize_url(raw_url, base=page_url)
                except ValueError:
                    continue  # non-http or malformed: never fatal
                key = url.split("#", 1)[0]
                if key in visited:
                    continue
                if cand.get("type") == "document":
                    if key not in doc_seen:
                        doc_seen.add(key)
                        documents_total += 1
                        if len(documents) < self._CRAWL_LIST_CAP:
                            documents.append(url)
                    continue
                if same_host and self._crawl_host_key(url) != root_key:
                    note_skipped(url, "external host")
                    continue
                path = (urlparse(url).path or "").lower()
                if any(path.startswith(p) for p in self._CRAWL_SKIP_PATH_PREFIXES):
                    note_skipped(url, "boilerplate path")
                    continue
                if os.path.splitext(path)[1] in self._CRAWL_SKIP_EXTENSIONS:
                    note_skipped(url, "asset")
                    continue
                score = self._crawl_score(
                    url, str(cand.get("title") or ""), depth + 1,
                    query_terms, page_terms,
                )
                if score < min_score:
                    note_skipped(url, "below min score")
                    continue
                visited.add(key)
                seq += 1
                queue.append((
                    score * (self._CRAWL_DEPTH_DECAY ** (depth + 1)),
                    seq,
                    key,
                    depth + 1,
                    str(cand.get("title") or ""),
                ))
            if len(queue) > self._CRAWL_QUEUE_CAP:
                # Evict the weakest candidates, keeping the frontier bounded.
                queue.sort(key=lambda e: (e[0], e[1]))
                queue_dropped += len(queue) - self._CRAWL_QUEUE_CAP
                queue = queue[-self._CRAWL_QUEUE_CAP:]

        def fetch_record(url: str, depth: int, score_eff: float) -> None:
            """Fetch one candidate through the normal page pipeline."""
            record = {
                "url": url,
                "depth": depth,
                "score": round(score_eff, 3),
            }
            try:
                raw_page = self._inspect_html_page_impl(url, None, "", 0, 1)
                try:
                    page = json.loads(raw_page)
                except json.JSONDecodeError:
                    page = raw_page
            except Exception as e:
                logger.warning("crawl fetch failed for %s: %s", url, e)
                errors.append({"url": url, "depth": depth, "error": str(e)})
                return
            # The impl reports failures (fetch errors, robots disallow,
            # already visited) as {"error"|"warning": ...} dicts.
            if isinstance(page, dict) and ("error" in page or "warning" in page):
                errors.append({
                    "url": url,
                    "depth": depth,
                    "error": str(page.get("error") or page.get("warning")),
                })
                return
            md = page.get("markdown") or ""
            record["status"] = "ok"
            record["title"] = str(
                (page.get("metadata") or {}).get("title") or ""
            )
            record["markdown"] = md[: self._CRAWL_PAGE_CHARS]
            record["links_total"] = int(page.get("total_links") or 0)
            pages.append(record)
            expand(page, url, depth)

        # Root: always fetched (depth 0); a root failure kills the crawl.
        try:
            raw_root = self._inspect_html_page_impl(root, None, "", 0, 1)
            try:
                root_page = json.loads(raw_root)
            except json.JSONDecodeError:
                root_page = raw_root
        except Exception as e:
            return json.dumps(
                {"error": f"crawl: root fetch failed: {e}", "root": root},
                indent=2,
            )
        if isinstance(root_page, dict) and (
            "error" in root_page or "warning" in root_page
        ):
            return json.dumps(
                {
                    "error": "crawl: root fetch failed: "
                    + str(root_page.get("error") or root_page.get("warning")),
                    "root": root,
                },
                indent=2,
            )
        root_md = root_page.get("markdown") or ""
        root_title = str(
            (root_page.get("metadata") or {}).get("title") or ""
        )
        pages.append({
            "url": root,
            "depth": 0,
            "status": "ok",
            "title": root_title,
            "score": 1.0,
            "markdown": root_md[: self._CRAWL_PAGE_CHARS],
            "links_total": int(root_page.get("total_links") or 0),
        })
        visited.add(root.split("#", 1)[0])

        # Effective query: the caller's focus, or the root page itself.
        query = (query or "").strip()
        if query:
            query_terms = self._crawl_tokens(query)
            query_echo = query
        else:
            query_terms = self._crawl_topic_words(root_md + " " + root_title)
            query_echo = "derived from root page"
        expand(root_page, root, 0)

        stop = "frontier exhausted"
        while queue:
            if len(pages) >= max_pages:
                stop = "max_pages reached"
                break
            # Best-first: highest effective score, ties by discovery
            # order (so flat scores degrade to plain BFS).
            queue.sort(key=lambda e: (-e[0], e[1]))
            score_eff, _s, url, depth, _anchor = queue.pop(0)
            fetch_record(url, depth, score_eff)

        result = {
            "root": root,
            "query": query_echo,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "same_host": same_host,
            "min_score": min_score,
            "pages": pages,
            "errors": errors[: self._CRAWL_LIST_CAP],
            "errors_total": len(errors),
            "documents": documents,
            "documents_total": documents_total,
            "skipped": skipped,
            "skipped_total": skipped_total,
            "queue_dropped": queue_dropped,
            "count": len(pages),
            "stop": stop,
        }
        return self._fit_json(
            lambda b: self._shrink_crawl(result, b),
            self.max_markdown_chars,
            self.max_tokens,
            {
                "root": root,
                "error": "crawl result too large for the output budget",
                "hint": "lower max_pages or raise max_tokens",
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
        self._search_cache_clear()
        self.reset_visited()
        return json.dumps({"cache_cleared": True, "stats": self.cache.stats()}, indent=2)

    def prune_cache(self) -> str:
        """Remove expired cache entries and evict to the size cap (Tier 2.5).

        Unlike ``clear_cache`` this keeps valid, in-TTL entries and does not
        touch the visited-URL set -- it only sweeps entries that expired while
        never being requested again and trims the disk cache to
        ``cache_max_bytes``. Returns a summary of what was removed."""
        return json.dumps({"prune": self.cache.prune()}, indent=2)
