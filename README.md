# stitch_web_researcher — High-Performance LLM Web Researcher

> A hybrid LLM web researcher combining a **Rust async core** (PyO3) for fetching, the **Oxide SDK family** for document extraction, and a **Python orchestration layer** for caching, rate limiting, structured parsing, token-aware budgeting, LLM tool routing, and multi-provider search.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Rust](https://img.shields.io/badge/Rust-1.70%2B-orange)](https://rustup.rs)
[![License](https://img.shields.io/badge/License-MIT%2FApache--2.0-green)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-361%20passing%2C%207%20slow%20live-brightgreen)](tests/)

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
- **HTML Metadata Extraction**: `meta-oxide` extracts 13 metadata formats (OG, Twitter, JSON-LD, Microdata, Dublin Core, RDFa, etc.) at ~233x BeautifulSoup speed
- **Token-Aware Truncation**: Precise token budgets via `tiktoken` for GPT-4, Claude, and other models — two-pass truncation (tokens first, then character safety cap)
- **Structured Document Parsing**: Pydantic v2 schemas for validated `DocumentMetadata`, `ExtractedPage`, `ExtractedTable`, and `ParsedDocumentPayload`
- **Production-Ready**:
  - TTL file caching
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

    return results, content

asyncio.run(research())
```

### LLM Tool Definitions

```python
tools = WebResearcherToolbox()
llm_tools = tools.get_llm_definitions()

# Returns OpenAI-compatible function definitions for all nine tools:
# search_web, inspect_html_page, batch_inspect_pages, extract_document,
# extract_document_structured, inspect_html_structured, clear_cache,
# reset_visited, get_stats.

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
`clear_cache`, `get_stats`.

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
│   ├── cache.py                      # Disk cache with scoped clears + budgeting
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

## License

Dual-licensed under **MIT** OR **Apache-2.0**.

All dependencies are MIT/Apache-2.0 licensed — zero copyleft, zero JVM.
