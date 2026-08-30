"""Configuration, tool-schema, and URL-normalization helpers.

Extracted from ``agent_tools.py`` as part of the composition split
(see ``docs/handoff.md``). Pure, dependency-light pieces only: the
``FetchMode`` enum plus per-call strategy resolvers, the tool registry
(``ToolParam`` / ``ToolSpec`` / ``TOOL_REGISTRY``), the ``ToolboxConfig``
dataclass, and ``normalize_url``.
"""


from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse, urljoin, urlunparse

from stitch_web_researcher.guard import GuardConfig
from stitch_web_researcher.structured_parser import DOCUMENT_EXTENSIONS
# ── Fetch strategy: FetchMode enum + per-call resolution ─────────
class FetchMode(str, Enum):
    """Per-call render-strategy override for page fetches.

    Mirrors the three ``fetch_mode`` values but applies to a single call.
    ``AUTO`` (the default) defers to :attr:`ToolboxConfig.fetch_mode`;
    ``BROWSER`` renders with the headless browser first and falls back to
    static on failure; ``STATIC`` is static-only. Being a ``str`` subclass
    means ``FetchMode.AUTO == "auto"`` so callers may pass either form.
    """

    AUTO = "auto"
    BROWSER = "browser"
    STATIC = "static"


_FETCH_MODE_VALUES = tuple(m.value for m in FetchMode)  # ("auto", "browser", "static")


def _coerce_fetch_mode(value):
    """Coerce a per-call ``use_smart`` value into a strategy string.

    Accepts the :class:`FetchMode` enum, its string value, or ``None``
    (→ ``"auto"``). Anything else raises so a typo in a tool call fails
    loudly instead of silently defaulting.
    """
    if value is None:
        return FetchMode.AUTO.value
    if isinstance(value, FetchMode):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _FETCH_MODE_VALUES:
            return normalized
    raise ValueError(
        f"Invalid use_smart {value!r}; expected one of {_FETCH_MODE_VALUES}"
    )


def _resolve_fetch_strategy(fetch_mode, use_smart):
    """Resolve ``(fetch_mode, use_smart)`` into one of four strategies.

    ``use_smart`` is the per-call override; ``"auto"`` defers to
    ``fetch_mode``. Returns one of ``"static-only"``, ``"browser-only"``,
    ``"browser-first"`` (browser with static fallback) or ``"auto"``
    (static-first with stealth-browser fallback on failure/non-text).
    """
    if use_smart == FetchMode.STATIC.value:
        return "static-only"
    if use_smart == FetchMode.BROWSER.value:
        return "browser-first"
    # use_smart == "auto": defer to fetch_mode
    if fetch_mode == "static":
        return "static-only"
    if fetch_mode == "browser":
        return "browser-only"
    return "auto"  # fetch_mode == "auto"


# ───────────────────────────────
# Tier 2.6: fetch observability
# ── Tool registry: primitives, spec, registry, URL normalization ─

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
        "web_search",
        "Unified web search + research. With search_only=true (default false) this is a pure provider search across engines (duckduckgo/google/bing/exa/browser), returning a deduped list of results. With search_only=false it also dedupes the top results and fetches up to depth candidate pages through the normal cache/robots/rate-limit/provenance pipeline, returning one record per source with status, markdown, and provenance so the caller can write a cited synthesis.",
        "web_search",
        (
            ToolParam("query", str, description="The search query / research topic"),
            ToolParam(
                "search_only",
                bool,
                False,
                "true returns only provider search results; false also fetches up to depth pages and returns per-source records.",
            ),
            ToolParam(
                "max_results",
                int,
                5,
                "Maximum number of search results to consider (default: 5).",
            ),
            ToolParam(
                "depth",
                int,
                5,
                "Number of candidate pages to fetch when search_only=false (default: 5).",
            ),
            ToolParam(
                "max_tokens",
                int,
                0,
                "Global token budget for the whole response (0 = toolbox default).",
            ),
            ToolParam(
                "provider",
                str,
                "duckduckgo",
                "Search engine to prefer when search_only=true. Falls back through other providers on failure.",
                enum=["duckduckgo", "google", "bing", "exa", "browser"],
            ),
        ),
    ),
    ToolSpec(
        "inspect_html_page",
        "Fetch and extract markdown content from a web page. Set use_smart='browser' for JS-rendered pages (SPA, anti-bot); 'auto' (default) follows fetch_mode, 'static' is static-only. When the page exceeds the output budget, pass the research query to keep the most relevant sections instead of truncating head-first; or pass offset / max_chunks to page through the full document in budget-sized chunks. Returns markdown text, follow-up links, and provenance (fetched_at, http_status, final_url, content_type, content_hash; cache_hit flags from-cache reads).",
        "inspect_html_page",
        (
            ToolParam("url", str, description="The URL to inspect"),
            ToolParam(
                "use_smart",
                str,
                "auto",
                "Render strategy: 'auto' (default, follows fetch_mode), 'browser' (headless browser first, static on failure), or 'static' (static only).",
                enum=["auto", "browser", "static"],
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
            ToolParam(
                "structured",
                bool,
                False,
                "true returns a structured ParsedDocumentPayload (metadata, tables, links) instead of raw markdown; default false returns markdown text (query/offset/max_chunks paging apply).",
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
            ToolParam(
                "structured",
                bool,
                False,
                "true returns a validated ParsedDocumentPayload (metadata, pages, tables) as JSON instead of plain text.",
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
        "crawl",
        "Bounded focused crawl over a site's link graph: BFS from root_url, but the frontier is ranked by relevance (score x 0.7^depth), so the page budget goes to the most relevant links; flat scores degrade to plain BFS. Optionally seeds the frontier with a site-scoped web search (search_prior) and caller-supplied URLs (seed_urls). Returns per-page title, relevance score, a content skim, and richness stats (content_chars, term_hits; optional keyword-densest excerpt) (full pages stay in the page cache and can be re-read in full via inspect_html_page), plus a rank-ordered list of documents (PDF/DOCX links, never fetched here), skipped links with reasons, and counters.",
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
            ToolParam(
                "excerpts",
                bool,
                False,
                "Also return a keyword-densest 300-char excerpt per page (raises the payload size; pair with a lower max_pages)",
            ),
            ToolParam(
                "search_prior",
                bool,
                False,
                "Before crawling, run one site-scoped web search (site:host focus) and feed its top-5 results into the frontier at depth 1. They are exempt from min_score (the engine already ranked them) and a failed search is non-fatal (the crawl continues link-graph only)",
            ),
            ToolParam(
                "seed_urls",
                list[str],
                [],
                "Extra starting URLs, normalized against the root and SSRF-checked, pushed at depth 0 (their children are depth 1); they respect min_score (a below-floor seed is skipped with a reason)",
            ),
        ),
    ),
    ToolSpec(
        "manage_cache",
        "Unified cache maintenance. action='clear' wipes the memory and disk caches plus the visited-URL set (force fresh fetches); action='prune' (default) removes expired entries and evicts to the size cap while keeping valid ones; action='reset' forgets visited URLs without clearing caches (retry a failed page).",
        "manage_cache",
        (
            ToolParam(
                "action",
                str,
                "prune",
                "Cache operation: 'clear' (wipe caches + visited), 'prune' (default, evict expired/LRU to size cap), or 'reset' (forget visited URLs only).",
                enum=["clear", "prune", "reset"],
            ),
        ),
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
    ddgs_jitter: float = 1.0
    domain_delay: float = 0.5
    max_markdown_chars: int = 8000
    max_tokens: int = 0
    model_name: str = "gpt-4o"
    max_links: int = 20
    search_providers: Optional[list] = None
    default_provider_index: int = 0
    fetch_delay: Optional[float] = None
    # Max random seconds added to the per-domain fetch gap to
    # desynchronize concurrent tool calls (0 disables jitter).
    # Defaults to 1.0 s so the historical 0.5-1.5 s fetch gap is preserved
    # unless the caller opts out.
    fetch_jitter: float = 1.0
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
