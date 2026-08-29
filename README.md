# stitch_web_researcher — High-Performance LLM Web Researcher

> A hybrid LLM web researcher combining a **Rust async core** (PyO3) for fetching, the **Oxide SDK family** for document extraction, and a **Python orchestration layer** for caching, rate limiting, structured parsing, token-aware budgeting, LLM tool routing, and multi-provider search.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Rust](https://img.shields.io/badge/Rust-1.70%2B-orange)](https://rustup.rs)
[![License](https://img.shields.io/badge/License-MIT%2FApache--2.0-green)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-860%20passing%2C%207%20slow%20live-brightgreen)](tests/)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        LLM Agent / User                          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        │   WebResearcherToolbox (Python Layer)    │
        │  ┌─────────┬─────────┬─────────┬──────┐ │
        │  │ Search  │Token    │ Struct  │ Meta │ │
        │  │Provider │Budget   │ Parser  │ Oxide│ │
        │  └────┬────┴────┬────┴────┬────┴──┬───┘ │
        └───────┼──────────┼────────┼───────┼─────┘
                │          │        │       │
     ┌──────────┴──┐  ┌────┴────┐  ┌────┴───┴──┐
     │  Search     │  │  Rust   │  │  Oxide    │
     │  Engines    │  │  Core   │  │  Extractors│
     │  ┌───────┐  │  │ (_core) │  │           │
     │  │ DDG   │  │  │ reqwest│  │ pdf_oxide │
     │  │ Google│  │  │ scraper│  │ office_ox │
     │  │ Bing  │  │  │ html2md│  │ browser_ox│
     │  │ Exa   │  │  │ tokio  │  │ meta_ox   │
     │  └───────┘  │  └────────┘  └───────────┘
     └─────────────┘
```

### Layer Breakdown

| Layer | Module | Purpose |
|-------|--------|---------|
| **Rust Core** | `_core` (PyO3) | Async HTTP fetching (`reqwest`), HTML parsing (`scraper`), markdown conversion (`html2md`), shared Tokio runtime |
| **Python Orchestration** | `agent_tools.py` | LLM toolbox, caching, rate limiting, retry logic, smart/fallback routing |
| **Search** | `search_providers.py` | Multi-provider search abstraction (DDG, Google, Bing, Exa) with fallback chaining |
| **Document Parsing** | `structured_parser.py` | Pydantic v2 schemas, PDF/DOCX/XLSX/PPTX extraction via `pdf_oxide` + `office_oxide` |
| **Token Budgeting** | `token_budget.py` | Token-aware truncation via `tiktoken` for GPT-4, Claude, and other models |
| **HTML Metadata** | `meta_extractor.py` | Open Graph, Twitter Cards, JSON-LD, Microdata, Dublin Core via `meta-oxide` |
| **Smart Fetch** | `agent_tools.py` | Headless JS rendering via `browser_oxide` with static fallback |

---

## Features

- **Zero API Keys**: DuckDuckGo search requires no registration or configuration
- **Multi-Provider Search**: Plug in Google, Bing, Exa alongside DuckDuckGo with automatic fallback chaining
- **Async Rust Core**: Tokio-based concurrent fetching with browser impersonation, brotli decompression, and exponential backoff retries
- **Smart/Fallback Routing**: `use_smart=True` attempts headless JS rendering (`browser_oxide`) first, falls back to static `reqwest` on failure
- **High-Speed Document Extraction**: `pdf_oxide` (~0.8ms mean) and `office_oxide` (up to 100x faster than python-docx)
- **More Input Formats (Tier 3.10)**: `extract_document` also handles TXT, MD, CSV, JSON (pretty-printed), XML, and RSS/Atom feeds (surfaced as readable entry lists); extension-less URLs are detected via Content-Type
- **HTML Table Extraction (Tier 3.11)**: `inspect_html_structured` extracts top-level `<table>` grids into structured `tables` (colspan/rowspan expanded, `<th>` headers, caption names) — web tables reach the model as tables, not ragged markdown
- **Sitemap-Aware Discovery (Tier 3.12)**: `discover_resources(url)` finds a site's structured resources without crawling the link graph — feed declarations (`<link rel=alternate>` RSS/Atom/Feed-JSON) plus a bounded `/sitemap.xml` probe (sitemap indexes followed up to 3 hops, deduplicated and capped at 1000 URLs)
- **Research Orchestration (Tier 3.13)**: `research(topic, depth=5, max_tokens=0)` plans, fans out, and dedupes a small research run in one call — search the topic, keep the top *depth* validated URLs (hard cap 10), fetch each through the normal cache/robots/rate-limit/provenance pipeline, and return per-source status, content, and provenance for a cited synthesis by the calling agent
- **Document Link Detection (v0.4.5)**: `extract_document` / `extract_document_structured` also surface the URLs *written inside* the document text (bare `www.` promoted to `http://`, trailing Latin and CJK punctuation stripped, deduped, capped) — so reports and PDFs yield follow-up targets even though their hyperlink annotations are not exposed by the extractor
- **Focused Crawl (v0.4.6)**: `crawl(root_url, ...)` runs a bounded BFS over the site's link graph with a relevance-ranked frontier (`score × 0.7^depth`; score = query coverage + containing-page topic coverage computed from the page's full delivered text). The page budget therefore goes to the most relevant links, and with flat scores the order degrades to plain BFS. Per-page 300-char skims are returned while the full page stays in the page cache for a later in-full `inspect_html_page` re-read; document links are collected, never fetched
- **HTML Metadata Extraction**: `meta-oxide` extracts 13 metadata formats (OG, Twitter, JSON-LD, Microdata, Dublin Core, RDFa, etc.) at ~233x BeautifulSoup speed
- **Token-Aware Truncation**: Precise token budgets via `tiktoken` for GPT-4, Claude, and other models — two-pass truncation (tokens first, then character safety cap)
- **Structured Document Parsing**: Pydantic v2 schemas for validated `DocumentMetadata`, `ExtractedPage`, `ExtractedTable`, and `ParsedDocumentPayload`
- **Production-Ready**:
  - TTL + size-cap LRU disk caching (eviction of least-recently-used entries)
  - Per-domain rate limiting
  - User-Agent rotation
  - Visited URL deduplication
  - Comprehensive logging
  - URL validation
  - Retry logic (Rust + Python layers)
- **Permissive Licensing**: MIT/Apache-2.0 throughout — no AGPL, no JVM, no copyleft

---

## Quick Start

### Prerequisites

- **Rust 1.70+** ([rustup](https://rustup.rs/))
- **Python 3.10+**
- **maturin**: `pip install maturin`

### Build & Install

```bash
cd stitch-web-researcher

# Install Python dependencies
pip install -r requirements.txt

# Build Rust core and install in development mode
maturin develop --release
```

### Basic Usage

```python
from stitch_web_researcher import WebResearcherToolbox

tools = WebResearcherToolbox(
    cache_dir="./cache",
    cache_ttl_seconds=3600,
    cache_max_bytes=0,       # disk cache byte cap (0 = unlimited; LRU eviction)
    ddgs_delay=1.0,
    domain_delay=0.5,
    max_tokens=4000,          # token-aware truncation
    model_name="gpt-4o",     # tiktoken encoding
)

# Search the web
results = tools.search_web("latest AI research papers", max_results=5)
print(results)

# Inspect a page (static fetch)
content = tools.inspect_html_page("https://arxiv.org/abs/1234.5678")
print(content)

# Inspect a JS-rendered page (headless browser)
content = tools.inspect_html_page("https://react-app.example.com", use_smart=True)
print(content)

# Batch inspect multiple pages concurrently
batch = tools.batch_inspect_pages([
    "https://example.com/page1",
    "https://example.com/page2",
])
print(batch)

# Extract PDF content
pdf_content = tools.extract_document("https://example.com/paper.pdf")
print(pdf_content)

# Extract structured document (metadata + pages + tables)
structured = tools.extract_document_structured("https://example.com/report.pdf")
print(structured)
```

### Async Usage

```python
import asyncio

async def research():
    tools = WebResearcherToolbox()

    results = await tools.search_web_async("rust programming")
    content = await tools.inspect_html_page_async("https://example.com")
    batch = await tools.batch_inspect_pages_async(
        ["https://example.com", "https://docs.python.org"]
    )

    return results, content, batch

asyncio.run(research())
```

> **What "async" means here (thread pool).** The `*_async` wrappers
> (`search_web_async`, `inspect_html_page_async`, and
> `batch_inspect_pages_async`) offload the shared **blocking**
> implementation to Python's default thread-pool executor via
> `loop.run_in_executor(None, ...)`. This keeps the event loop
> responsive (other coroutines can run while a fetch or search is in
> flight), but the underlying network I/O is still **synchronous** --
> there is no native async I/O in the fetch/search layer. Use the async
> wrappers when you are already inside an `asyncio` application and want
> to avoid blocking the loop; call the sync methods directly otherwise.

### LLM Tool Definitions

```python
tools = WebResearcherToolbox()
llm_tools = tools.get_llm_definitions()

# Returns OpenAI-compatible function definitions for all ten tools:
# search_web, inspect_html_page, batch_inspect_pages, extract_document,
# extract_document_structured, inspect_html_structured, clear_cache,
# prune_cache, reset_visited, get_stats.

for tool in llm_tools:
    print(tool["function"]["name"], "—", tool["function"]["description"])

# All surfaces (LLM defs, MCP tools) are generated from one tool
# registry, and the same toolbox can be driven directly by name:
result = tools.execute_tool("inspect_html_page", {"url": "https://example.com"})
```

### Multi-Provider Search

```python
from stitch_web_researcher import GoogleProvider, BingProvider, DuckDuckGoProvider

# Configure providers (auto-detects API keys from environment)
providers = [
    GoogleProvider(),          # needs GOOGLE_API_KEY + GOOGLE_CX
    BingProvider(),            # needs BING_API_KEY
    DuckDuckGoProvider(),      # no key needed
]

tools = WebResearcherToolbox(search_providers=providers)

# Prefer Google, fall back to Bing, then DuckDuckGo
results = tools.search_web("quantum computing", provider="google")
```

### Token Budgeting

```python
from stitch_web_researcher import count_tokens, truncate_to_tokens, fit_context_window

tokens = count_tokens("Hello world", model_name="gpt-4o")
print(f"Tokens: {tokens}")

truncated = truncate_to_tokens(long_text, 1000, model_name="claude-3-sonnet")
print(f"Truncated to ~{count_tokens(truncated, 'claude-3-sonnet')} tokens")

# Fit multiple text chunks into a token budget (greedy packing;
# the last kept chunk may be truncated to fit)
chunks = [chunk1, chunk2, chunk3]
final = fit_context_window(chunks, 8000, model_name="gpt-4o")
```

### MCP Server

Expose the toolbox as [Model Context Protocol](https://modelcontextprotocol.io) tools so any MCP client (pi, Claude Desktop, Cursor, …) can use them directly:

```bash
# install with the optional MCP dependency (v2 SDK)
uv pip install "mcp>=2.0"

# run over stdio
python -m stitch_web_researcher.mcp_server
```

Exposed tools: `search_web`, `inspect_html_page`, `batch_inspect_pages`,
`extract_document`, `extract_document_structured`, `inspect_html_structured`,
`clear_cache`, `prune_cache`, `reset_visited`, `get_stats`.

Example client config (Claude Desktop / generic MCP JSON):

```json
{
  "mcpServers": {
    "stitch-web-researcher": {
      "command": "python",
      "args": ["-m", "stitch_web_researcher.mcp_server"],
      "env": {
        "STITCH_MAX_TOKENS": "4000",
        "STITCH_MODEL_NAME": "gpt-4o"
      }
    }
  }
}
```

All toolbox options are configurable via `STITCH_*` environment variables —
see the module docstring in `stitch_web_researcher/mcp_server.py`.

### HTML Metadata Extraction

```python
from stitch_web_researcher import extract_all, merge_into_document_metadata

html = "<html><head><title>My Page</title>...</head>...</html>"

# Extract all metadata formats
metadata = extract_all(html, "https://example.com/page")
print(metadata["meta"])       # title, description, canonical, ...
print(metadata["opengraph"])  # og:title, og:image, ...
print(metadata["twitter"])    # twitter:card, twitter:title, ...
print(metadata["jsonld"])     # Schema.org structured data

# Merge into DocumentMetadata
base = {"title": "Base Title", "format": "html"}
merged = merge_into_document_metadata(metadata, base)
```

### Observability (Tier 2.6)

`get_stats()` now includes a `fetches` section with thread-safe telemetry
collected at the single fetch-dispatch choke point (every `inspect_html_page`
and batch fetch):

```json
{
  "fetches": 128,
  "errors": 3,
  "bytes_downloaded": 2457600,
  "latency_ms": {"p50": 210.4, "p95": 890.1, "p99": 1450.7, "max": 2100.9},
  "requests_by_domain": {"example.com": 40, "docs.python.org": 12},
  "errors_by_class": {"TimeoutError": 2, "SSLError": 1}
}
```

Latency percentiles are computed over a bounded sliding window
(`ToolboxConfig.fetch_stats_window`, default 1024 samples), so memory is
constant regardless of session length.

Rust-side HTTP logging is **off by default** and carries zero cost until
enabled. Set `STITCH_RUST_LOG` to bridge Rust `log` events (per-hop HTTP
status, 304 revalidations, errors, fetched byte counts) into Python
`logging`:

```
STITCH_RUST_LOG=debug   # error | warn | info | debug
```

The bridge initialises once (idempotent) and emits a single
`rust logging bridge initialized` record so operators can confirm it is live.

### HTTP Transport Overrides (Tier 2.7)

For authenticated or proxied sources, the static (Rust) fetch path supports
process-level transport overrides, baked into the lazily-built shared client
at first use (last non-empty value wins):

| Knob | Type | Example |
|------|------|---------|
| `http_proxy` / `STITCH_HTTP_PROXY` | string | `http://proxy:8080` |
| `user_agent` / `STITCH_USER_AGENT` | string | `ResearchBot/1.0` |
| `custom_headers` / `STITCH_CUSTOM_HEADERS` | JSON object | `{"Authorization": "Bearer ..."}` |
| `cookies` / `STITCH_COOKIES` | JSON object | `{"session": "abc123"}` |

```python
from stitch_web_researcher import ToolboxConfig, WebResearcherToolbox

tb = WebResearcherToolbox(ToolboxConfig(
    http_proxy="http://proxy:8080",
    custom_headers={"Authorization": "Bearer ..."},
    cookies={"session": "abc123"},
))
```

These are process-level (the shared client is a connection-pooled singleton),
not per-request. An invalid proxy URL or header name is logged and ignored
rather than fatal. robots.txt compliance (S4), politeness delay
(`domain_delay`), and per-host concurrency (S5) already cover the rest of
review item 7.

### Search Result Caching & Cross-Provider Merge (Tier 2.8)

`search_web` now caches successful results at the result level and can
optionally merge across providers:

- **Result-level cache (default):** every successful search is cached
  in-memory (bounded, TTL = `cache_ttl_seconds`) keyed by a normalized
  `(query, max_results, provider, merge-mode)` hash. A repeated call within
  a toolbox session is served from cache without hitting any provider.
  Errors are never cached. The cache is per-toolbox-instance and is cleared
  by `clear_cache`.
- **Within-provider dedup (default):** duplicate URLs (normalized: scheme
  case, default ports, fragments, trailing slash) are collapsed in the
  first successful provider's result list.
- **Cross-provider merge (opt-in):** set `search_merge=True` (or
  `STITCH_SEARCH_MERGE=1`) to query *every* provider in priority order and
  merge + dedup their results up to `max_results`, instead of the default
  strict failover (first success wins).

```python
from stitch_web_researcher import ToolboxConfig, WebResearcherToolbox

tb = WebResearcherToolbox(ToolboxConfig(search_merge=True))
results = tb.search_web("quantum computing", max_results=10)
```

### Real Async Path (Tier 2.9)

Every blocking toolbox method has an async counterpart:
`search_web_async`, `inspect_html_page_async`, and (new)
`batch_inspect_pages_async`. "Async" means **thread pool**: each wrapper
offloads the shared blocking implementation to Python's default executor
(`loop.run_in_executor(None, ...)`) so the event loop stays responsive while
the work runs on a worker thread. The underlying network I/O remains
synchronous (no native async I/O in the fetch/search layer) -- see the
[Async Usage](#async-usage) section above for a runnable example.

### More Input Formats (Tier 3.10)

`extract_document` previously delivered PDF/DOCX/XLSX/PPTX and plain
TXT/MD/CSV; anything else errored (M16) or got scraped as HTML. Now:

- **JSON** — pretty-printed when valid, raw text otherwise.
- **XML / RSS / Atom** (`.xml`, `.rss`, `.atom`) — feeds are surfaced as a
  readable markdown entry list (title, link, date, summary; capped at 50
  entries). Non-feed XML (e.g. a sitemap) and malformed XML fall back to
  the raw text, so these sources always deliver content.
- **Extension-less text URLs** — when the URL gives no usable extension,
  the response Content-Type decides: `text/plain`, `text/markdown`,
  `text/csv`, `application/json`, `text/xml`, `application/xml`,
  `application/rss+xml`, and `application/atom+xml` bodies are extracted
  as text (feeds via the feed extractor above). Binary content-types still
  error as before.

```python
feed = tools.extract_document("https://example.com/feeds/atom.xml")
plain = tools.extract_document("https://example.com/raw-data")  # text/plain
```

`classify_link` now routes `.json` / `.xml` / `.rss` / `.atom` URLs to
`extract_document` instead of page scraping, so discovered links of these
types go to the right tool.

### HTML Table Extraction (Tier 3.11)

`inspect_html_structured` previously delivered HTML pages as markdown only:
`ExtractedTable` existed for PDF/Office documents, but web-page tables
came through as ragged markdown lines. Now the raw HTML (static fetches
only) goes through the Rust extractor `extract_tables_from_html`, which
produces rectangular grids:

- **Top-level tables only** — tables nested inside another table are
  skipped.
- **Header detection** — a first row containing `<th>` cells becomes the
  header row; otherwise the table is headerless.
- **colspan / rowspan expansion** — spanned cells are expanded into
  rectangular grids with empty fill cells.
- **Cell hygiene** — markup-free text, whitespace collapsed, each cell
  capped at 1000 characters.
- **Naming** — a collapsed `<caption>` becomes the table name, falling
  back to `table-N` in document order.
- **Budgeted** — at most 20 tables and 500 rows per page so a giant
  table cannot drown the token budget.

Tables attach to both the payload and its single page (same shape as the
PDF/XLSX paths). The browser path exposes no raw DOM, so its `tables`
are empty, and the page path (`inspect_html_page`) is untouched (M8).

```python
payload = json.loads(tools.inspect_html_structured("https://example.com/report"))
for table in payload["tables"]:
    print(table["name"], table["headers"], table["rows"][:3])
```

Extraction is best-effort: a failure logs a warning and the page is
delivered with `tables: []` rather than failing the whole call.

### Sitemap-Aware Discovery (Tier 3.12)

`discover_resources(url)` is a cheaper alternative to link-graph
crawling when a research task needs "what does this site contain?":

- **Feed discovery** — the page is fetched once (static path) and its
  `<link rel="alternate">` declarations are scanned; only feed
  content-types (RSS/Atom/Feed-JSON) count, language alternates
  (`hreflang`) are ignored, and relative hrefs are absolutized.
- **Sitemap probe** — the site root is probed for `/sitemap.xml`.
  Sitemap *indexes* are followed with bounded depth (3 hops, at most
  10 sitemap fetches total) so an index fan-out cannot turn into an
  unbounded crawl; every `<loc>` in a `urlset` becomes a discovered
  page URL.
- **Budgeted output** — discovered URLs are deduplicated (ordered) and
  capped (500 per sitemap, 1000 total; `truncated` flags a cap hit).

The probe is best-effort: a missing sitemap, malformed XML, or a
non-sitemap document degrades the result instead of failing the call.
Discovery is metadata-level — it does **not** mark the page visited,
so the same URL stays inspectable afterwards.

```python
result = json.loads(tools.discover_resources("https://example.com"))
print(result["feeds"])    # [{url, type}, ...]
print(result["urls"][:5]) # sitemap page URLs
```

### Research Orchestration (Tier 3.13)

`research(topic, depth=5, max_tokens=0)` chains the toolbox verbs into
one orchestrated research pass — plan, fan out, dedupe — so an agent
can run a multi-source research task with a single call:

- **Plan** — the topic is searched through the configured providers
  (up to `depth * 2` candidates, hard cap 20 results).
- **Dedupe** — result URLs are normalized, deduped (trailing slashes,
  scheme), validated through the SSRF guard, and capped at *depth*
  pages (hard cap 10).
- **Fan out** — each candidate is fetched through the normal page
  pipeline (page cache, robots, rate limits, provenance), so repeated
  `research()` calls are cheap: cached pages are re-served without
  re-fetching.
- **Cited-synthesis input** — the response carries one record per
  source: `url`, the search `title`/`snippet`, `status`, and either
  the full page result (markdown + metadata + provenance) or the error
  message. The whole response obeys the global budget (`max_tokens`
  or the toolbox default); under budget pressure later sources give
  ground first.

The toolbox holds no LLM: the prose synthesis is the calling agent's
job. `research` supplies the evidence, citation metadata, and
provenance — the orchestration the agent would otherwise hand-roll.

```python
report = json.loads(tools.research("Rust async runtimes", depth=5))
for s in report["sources"]:
    print(s["url"], s["status"])
    # ... the agent writes the cited synthesis ...
```

### Focused Crawl (v0.4.6)

`crawl(root_url, query=None, max_depth=3, max_pages=15, same_host=False,
min_score=0.05)` answers "what does this site contain?" with one bounded
run. It is BFS over the link graph, but the frontier is a priority
queue, so hop 1 cannot exhaust the budget before relevant depth-2/3
pages are seen:

- **Relevance score** — each candidate is scored
  `0.7 × cover(label, query) + 0.3 × cover(label, page_topic)`, where
  the label is the anchor text plus the link's path tokens. The
  containing page's topic vocabulary comes from its *full delivered
  text* (top content words, TF-ranked), not just the title or first
  lines. When *query* is omitted, the root page's own title and
  content stand in for it, so a query-less crawl still knows what its
  neighbourhood is about.
- **Depth decay** — the frontier pops the highest
  `score × 0.7^depth`; ties break by discovery order, so flat scores
  degrade exactly to plain BFS and depth 1 keeps priority over depth 2+
  unless the deeper links are genuinely more relevant.
- **Budget** — `max_pages` (default 15, hard cap 50) counts *total
  successful* pages across all depths; failed fetches do not consume
  it. `max_depth` (default 3, hard cap 5) bounds hops from the root.
- **Filters** — boilerplate paths (`/login`, `/cart`, `/tag/`, …) and
  static assets (`.css`, `.js`, images, fonts, …) are skipped without
  costing budget; candidates below `min_score` are skipped and
  reported with their reason.
- **Documents** — links to PDF/DOCX/… are never fetched by the crawl;
  they are collected in `documents` for the agent to read via
  `extract_document` (which surfaces the URLs written inside them).
- **Full re-reads** — every fetched page stays in the page cache in
  full; the crawl's 300-char skim is presentation-only, so a later
  `inspect_html_page` of any crawled URL is a cache hit with the
  complete content.

```python
crawl = json.loads(
    tools.crawl(
        "https://example.com/docs",
        query="offline caching",
        max_depth=3,
        max_pages=15,
    )
)
for p in crawl["pages"]:
    print(p["depth"], p["score"], p["title"], p["url"])
print(crawl["documents"])  # PDFs to read via extract_document
print(crawl["stop"])       # max_pages reached | frontier exhausted
```

### Document Link Detection (v0.4.5)

`extract_document` (and `_structured`) parse documents with the Oxide
family, which does not expose PDF hyperlink annotations. Many reports
and PDFs still carry their sources as *text* ("see https://… or www.…"),
so the extractor also runs a text-level link detector over the page
content: `http://` and `https://` URLs are matched directly, bare
`www.` hosts are promoted to `http://`, trailing sentence punctuation
(Latin and CJK) is stripped, results are deduped and capped at 50. The
results land in `links` of the readable extract (plain URL strings)
and in `payload.links` of the structured extract (`{title, url, type}`
with `type` = `page` or `document`) — so the agent can chase a
document's cited sources with the normal page pipeline.

```python
doc = json.loads(tools.extract_document("https://example.com/report.pdf"))
for u in doc.get("links", []):
    print(u)  # URLs written inside the document text
```

### Prompt-Injection Guard (optional, §7)

Fetched web content is *untrusted*. The optional guard runs a
[JailGuard](https://github.com/yfedoseev/jailguard) ONNX detector over the
untrusted scopes of each payload and attaches an additive `guard` block. It is
**off by default** (no import, no latency) and only activates when enabled.

```bash
pip install "stitch-web-researcher[guard]"   # pulls the jailguard model
```

Enable via environment (MCP server):

```
STITCH_GUARD_ENABLED=1
STITCH_GUARD_MODE=annotate    # annotate (default) | redact | block
STITCH_GUARD_SCOPES=page_markdown,document_text   # or: all / none
STITCH_GUARD_THRESHOLD=0.7
STITCH_GUARD_MAX_CHUNKS=40
```

…or programmatically:

```python
from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.guard import GuardConfig

tb = WebResearcherToolbox(ToolboxConfig(guard=GuardConfig(enabled=True, mode="annotate")))
```

* **annotate** (default) — attach the `guard` block and wrap the main content in
  an explicit `<untrusted-web-content source="…">` marker.
* **redact** — attach the block and replace flagged spans with placeholders.
* **block** — attach the block and withhold the content entirely (never cached).

Detection is chunked (the model caps input at ~256 tokens) with overlapping
windows so a payload cannot hide on a seam, and verdicts are cached by
`sha256(chunk)`. If `jailguard` or its model is unavailable, the guard fails
open (content passes through; `guard.risk` is `None`). `get_stats()` exposes a
`guard` section for A/B measurement.

---

## Project Structure

```
stitch-web-researcher/
├── Cargo.toml                        # Rust manifest (PyO3, reqwest, scraper, html2md)
├── pyproject.toml                    # Build config (maturin) + dependency metadata
├── requirements.txt                  # Dev/test dependencies (runtime deps live in pyproject)
├── README.md                         # This file
├── SPEC_AUDIT.md                     # Feature audit vs. the original spec
├── src/
│   └── lib.rs                        # Rust async fetcher (shared Tokio runtime)
├── stitch_web_researcher/
│   ├── __init__.py                   # Package exports
│   ├── agent_tools.py                # WebResearcherToolbox, smart fetch, robots, rate limiting
│   ├── cache.py                      # Two-tier cache: TTL + size-cap LRU eviction, scoped clears
│   ├── guard.py                      # Optional prompt-injection guard (§7, JailGuard)
│   ├── mcp_server.py                 # MCP server (stdio) exposing the toolbox
│   ├── robots.py                     # robots.txt compliance (per-host cache, UA groups)
│   ├── search_providers.py           # SearchProvider ABC + DuckDuckGo/Google/Bing/Exa
│   ├── ssrf.py                       # SSRF guard (blocks private/loopback targets)
│   ├── structured_parser.py          # Pydantic v2 schemas + StructuredOxideParser
│   ├── token_budget.py               # tiktoken-based token counting & truncation
│   └── meta_extractor.py             # meta-oxide wrapper for HTML metadata
└── tests/                            # 25+ modules (unit + integration)
```

## Dependencies

| Category | Package | Purpose |
|----------|---------|---------|
| **Rust Core** | `pyo3 0.27` | Python bindings |
| | `reqwest 0.12` | Async HTTP client (with brotli) |
| | `scraper 0.22` | HTML parsing |
| | `html2md 0.2` | HTML → Markdown conversion |
| | `tokio 1` | Async runtime (shared singleton) |
| **Python Layer** | `ddgs >=2.0` | DuckDuckGo search |
| | `httpx >=0.27` | Async HTTP for providers |
| | `pydantic >=2.7` | Data validation schemas |
| | `tiktoken >=0.5.0` | Token counting & truncation |
| **Oxide SDK** | `meta_oxide >=0.1` | HTML metadata extraction |
| | `browser_oxide >=0.1` | Headless JS rendering |
| **Optional — `[documents]`** | `pdf_oxide >=0.1` | High-speed PDF extraction |
| | `office_oxide >=0.1` | DOCX/XLSX/PPTX extraction (PyPI) |
| **Optional — `[mcp]`** | `mcp >=2.0` | MCP server runtime (Python 3.10+) |
| **Optional — `[guard]`** | `jailguard >=0.1.2` | Prompt-injection detection (ONNX, §7) |

## License

Dual-licensed under **MIT** OR **Apache-2.0**.

All dependencies are MIT/Apache-2.0 licensed — zero copyleft, zero JVM.
