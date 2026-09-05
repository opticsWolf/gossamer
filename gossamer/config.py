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
from typing import Optional

from gossamer.guard import GuardConfig
from gossamer import _core as _rust
from gossamer.research_categories import describe_categories
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
        elif self.type is float:
            schema = {"type": "number"}
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
        """Registry defaults plus caller-supplied arguments.

        Mutable defaults (e.g. ``seed_urls=[]``) are copied per call so one
        invocation can never contaminate the next (review A.5).
        """
        merged = {}
        for p in self.params:
            if p.required:
                continue
            default = p.default
            if isinstance(default, list):
                default = list(default)
            merged[p.name] = default
        merged.update(arguments or {})
        return merged
TOOL_REGISTRY = (
    ToolSpec(
        "web_search",
        "Unified web search + research. search_only=true (default false) is a pure provider search across engines (duckduckgo/google/bing/exa/browser) returning a deduped list. search_only=false also fetches up to depth candidate pages, returning one record per source with status, markdown, and provenance for a cited synthesis.",
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
                "Search engine to prefer, in both modes. search_only=false plans the research run through this provider. Falls back through other providers on failure.",
                enum=["duckduckgo", "google", "bing", "exa", "browser"],
            ),
        ),
    ),
    ToolSpec(
        "inspect_html_page",
        "Fetch and extract markdown content from a web page. Set use_smart='browser' for JS-rendered pages (SPA, anti-bot); 'auto' (default) follows fetch_mode; 'static' is static-only. If the page exceeds the output budget, pass the research query to keep only the most relevant sections, or offset / max_chunks to page through it in chunks. Returns markdown, follow-up links, and provenance.",
        "inspect_html_page",
        (
            ToolParam("url", str, description="The URL to inspect"),
            ToolParam(
                "use_smart",
                str,
                "auto",
                "Render strategy: 'auto' (default), 'browser' (headless browser first, static on failure), or 'static'.",
                enum=["auto", "browser", "static"],
            ),
            ToolParam(
                "query",
                str,
                None,
                "The research query. When the page exceeds the output budget, only the most relevant sections are returned.",
            ),
            ToolParam(
                "offset",
                int,
                0,
                "Character offset into the page markdown to start from; pass the previous call's next_offset to resume.",
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
                "true returns a structured payload (metadata, tables, links) instead of raw markdown.",
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
        "Extract text content from documents via URL or local path: PDF, DOCX, XLSX, PPTX, plus text formats (TXT, MD, CSV, JSON, XML) and RSS/Atom feeds. For large documents, pass pages (e.g. '10-20') to read a page range instead of the whole file.",
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
                "1-based inclusive page range for PDFs ('10', '10-20', '10-', '-20'); for XLSX the range selects sheets. Omitted -> the whole document.",
            ),
            ToolParam(
                "structured",
                bool,
                False,
                "true returns a validated ParsedDocumentPayload (metadata, pages, tables) as JSON instead of plain text.",
            ),
            ToolParam(
                "store",
                bool,
                False,
                "true writes the original bytes and the full extracted text to disk under store_dir; the result includes a 'stored' object with paths and sizes. Cannot combine with pages=.",
            ),
            ToolParam(
                "store_dir",
                str,
                None,
                "Directory to write stored files into (created if missing). Only used when store=true.",
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
        "Relevance-ranked crawl of a URL's link graph (cross-domain by default) to find the pages most relevant to a query. Walks from root_url, scoring candidates by query relevance that decays with depth, so the page budget lands on the most relevant links. Optionally seed with a site-scoped search (search_prior) and/or extra URLs (seed_urls). Returns per page: title, relevance score, and a short skim; plus unfetched document links and skipped links. Full pages stay cached for later re-reads.",
        "crawl",
        (
            ToolParam(
                "root_url",
                str,
                description="Seed URL to start the traversal from",
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
                "Before traversing, run one site-scoped web search and feed its top results into the frontier at depth 1 (exempt from min_score; a failed search is non-fatal)",
            ),
            ToolParam(
                "seed_urls",
                list[str],
                [],
                "Extra starting URLs, pushed at depth 0 (children at depth 1); they respect min_score",
            ),
            ToolParam(
                "use_smart",
                str,
                "auto",
                "Render strategy for crawled pages: 'auto' (default), 'browser' (headless first), or 'static'.",
                enum=["auto", "browser", "static"],
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
    ToolSpec(
        "research_by_category",
        describe_categories(),
        "research_by_category",
        (
            ToolParam(
                "query",
                str,
                None,
                "The search query. Omit it to return the full category -> "
                "provider taxonomy as JSON instead, so you can discover which "
                "provider to use.",
            ),
            ToolParam(
                "category",
                str,
                None,
                "Optional category to use directly (e.g. 'scholarly', 'legal', "
                "'financial', 'geo', 'general'); skips classification. Pair with "
                "provider= to pick a source within it.",
            ),
            ToolParam(
                "max_results",
                int,
                5,
                "Maximum number of results to return.",
            ),
            ToolParam(
                "provider",
                str,
                None,
                "Optional provider id to call (see the taxonomy from a no-query "
                "call). Given alone, its owning category is used; with category= "
                "it must belong to that category. Omitted -> the category's "
                "default provider. No automatic fallback.",
            ),
        ),
    ),
    ToolSpec(
        "export_citations",
        "Reconstruct and export citations from search results. Pass a list of DOIs, URLs, or JSON-serialized adapter result dicts; returns them formatted as bibtex (default), csl-json, apa, or mla. DOIs and URLs are resolved via the scholarly adapters (openalex, crossref, arxiv, pubmed, doaj).",
        "export_citations",
        (
            ToolParam(
                "results",
                list[str],
                description=(
                    "List of DOIs ('10.xxxx/...'), URLs, or JSON-serialized "
                    "adapter result dicts to cite."
                ),
            ),
            ToolParam(
                "style",
                str,
                "bibtex",
                "Citation style: bibtex, csl-json, apa, or mla.",
                enum=["bibtex", "csl-json", "apa", "mla"],
            ),
            ToolParam(
                "enrich",
                bool,
                False,
                "When true, make one canonical DOI lookup per unique DOI to fill in a missing venue/abstract.",
            ),
            ToolParam(
                "dedupe",
                bool,
                True,
                "Collapse records that share a DOI or URL before formatting.",
            ),
        ),
    ),
    ToolSpec(
        "check_sources",
        "Probe whether source URLs are reachable without downloading their pages (Workstream 2). Takes a list of URLs (strings or {url} dicts), validates each through the SSRF guard, runs a lightweight status probe per URL (polite / parallel / bounded), and returns per-URL status: ok (2xx/3xx), unreachable (4xx/5xx), blocked (SSRF), or error (network/timeout). Reuses the shared fetch pipeline -- no new HTTP surface.",
        "check_sources",
        (
            ToolParam(
                "urls",
                list[str],
                description=(
                    "List of URLs to probe (plain strings or {url} dicts)."
                ),
            ),
            ToolParam(
                "mode",
                str,
                "status",
                "Probe mode: 'status' (HEAD/minimal GET, default) or 'content' (full fetch).",
                enum=["status", "content"],
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
    # Implemented in Rust (src/urls.rs); parity-pinned by
    # tests/test_rust_parity_urls.py (vendored original + seeded fuzz).
    return _rust.normalize_url(raw, base)

def canonical_url(url: str, *, query: str = "keep") -> str:
    """One canonical identity for a URL, shared by every dedup/cache/visited
    call site (review B.1).

    Normalization (all modes): the input is parsed with :func:`normalize_url`
    (so relative spellings collapse first), then scheme/host are lowercased,
    a leading ``www.`` is stripped, default ports are dropped, trailing
    slashes are stripped (except the root ``/``), and the fragment is dropped.

    ``query`` controls the query string:

    - ``"keep"`` — sorted, kept (search-result identity: ``?page=1`` and
      ``?page=2`` are different results);
    - ``"drop"`` — removed entirely (weak cross-tool identity);
    - ``"drop-tracking"`` — only tracking params (``utm_*``/``fbclid``/…)
      are removed (cache/visited identity: campaign tags never change the
      resource, other params might).
    """
    # Implemented in Rust (src/urls.rs); parity-pinned by
    # tests/test_rust_parity_urls.py (vendored original + seeded fuzz).
    return _rust.canonical_url(url, query)

def ensure_str_list(value, name: str) -> list:
    """Coerce a ``list[str]`` tool argument into a clean list (review A.3).

    A bare string is the most likely LLM shape mistake for a ``list[str]``
    parameter — iterating it would probe/fetch one *character* at a time.
    So: ``None`` → ``[]``, a bare ``str`` → ``[str]`` (empty/blank → ``[]``),
    a list/tuple → its items (each must be a string). Anything else raises
    ``TypeError`` naming the expected type, so callers can return one clear
    JSON error instead of confident-looking per-character garbage.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        items = list(value)
        for item in items:
            if not isinstance(item, str):
                raise TypeError(
                    f"{name} must be a list of strings, got an item of type "
                    f"{type(item).__name__}"
                )
        return items
    raise TypeError(
        f"{name} must be a list of strings (a bare string is also accepted "
        f"and wrapped), got {type(value).__name__}"
    )


@dataclass
class ToolboxConfig:
    """Construction options for :class:`WebResearcherToolbox`.

    Grouping the knobs in one object keeps the toolbox constructor stable as
    options grow. All fields have the same defaults the toolbox historically
    used.
    """

    cache_dir: str = ".gossamer_cache"
    cache_ttl_seconds: int = 3600
    # Tier 2.5: byte cap for the disk cache (0 = unlimited). Least-recently-
    # used entries are evicted to stay under it; see Cache.max_disk_bytes.
    cache_max_bytes: int = 0
    # Memory-tier entry cap for the two-tier cache (review C.4: previously
    # hardcoded to 100 inside the toolbox with no config path).
    cache_memory_entries: int = 100
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
    # Workstream 2: per-URL timeout (seconds) for the check_sources liveness
    # probe. A slow host is reported as a probe error, not left hanging a
    # whole parallel batch.
    liveness_timeout: float = 10.0
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
    # True; opt out with GOSSAMER_CONDITIONAL_REVALIDATE=0.
    conditional_revalidation: bool = True
    # §7: optional prompt-injection guard (off by default). Pass a
    # GuardConfig to enable; see gossamer.guard.
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

    @staticmethod
    def _coerce(default, value):
        """Lenient JSON value coercion guided by the field's default type."""
        if value is None or default is None:
            return value
        try:
            if isinstance(default, bool):
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on")
                return bool(value)
            if isinstance(default, int) and not isinstance(default, bool):
                return int(value)
            if isinstance(default, float):
                return float(value)
            if isinstance(default, str):
                return value if isinstance(value, str) else str(value)
        except (TypeError, ValueError):
            return value  # let __post_init__ / later use reject it loudly
        return value

    @classmethod
    def from_dict(cls, data: dict) -> "ToolboxConfig":
        """Build from a ``gossamer.json`` dict (see :mod:`gossamer.settings`).

        Known fields are mapped (``guard`` accepts a nested dict); unknown
        keys warn and are ignored so older files never break newer code.
        ``search_providers`` cannot come from JSON (provider objects are not
        serializable) and is likewise ignored with a warning.
        """
        import dataclasses
        import logging

        log = logging.getLogger(__name__)
        data = dict(data or {})
        kwargs: dict = {}
        known = {f.name: f for f in dataclasses.fields(cls)}
        for key, value in data.items():
            if key in ("keys", "keystore", "$comment"):
                continue  # settings-layer keys, not toolbox options
            if key not in known:
                log.warning("gossamer.json: ignoring unknown option %r", key)
                continue
            if key == "search_providers":
                log.warning(
                    "gossamer.json: 'search_providers' needs provider objects; "
                    "configure it in code instead"
                )
                continue
            if key == "guard" and isinstance(value, dict):
                guard_kwargs = {
                    k: v for k, v in value.items()
                    if k in GuardConfig.__dataclass_fields__
                }
                unknown_guard = set(value) - set(guard_kwargs)
                for ignored in sorted(unknown_guard):
                    log.warning("gossamer.json: ignoring unknown guard option %r", ignored)
                kwargs[key] = GuardConfig(**guard_kwargs)
                continue
            kwargs[key] = cls._coerce(known[key].default, value)
        return cls(**kwargs)

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
