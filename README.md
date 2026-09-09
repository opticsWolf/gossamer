# gossamer — High-Performance LLM Web Researcher

> A hybrid LLM web researcher combining a **Rust parsing core** (PyO3)
> with **Oxide extractors** for documents and a **Python orchestration
> layer** for caching, rate limiting, budgets, tool routing, and
> multi-provider search.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![PyPI](https://img.shields.io/pypi/v/gossamer-web.svg)](https://pypi.org/project/gossamer-web/)
[![Rust](https://img.shields.io/badge/Rust-1.70%2B-orange)](https://rustup.rs)
[![License](https://img.shields.io/badge/License-MIT%2FApache--2.0-green)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-10983%20passing%2C%202%20skipped-brightgreen)](tests/)

**Docs:** [Quick reference](./docs/QUICKREF.md) · [Architecture](./docs/ARCHITECTURE.md) · [Changelog](./CHANGELOG.md)

---

## Architecture

```
LLM Agent / User
       │  MCP (mcp_server.py) · CLI (cli.py) · skills/SKILL.md
       ▼
WebResearcherToolbox (agent_tools.py) — facade, no logic
       │  TOOL_REGISTRY (config.py): one source of truth
       ▼
Collaborators (Python: HTTP, keys, rate limits, orchestration)
fetch · search · crawl · document · discovery · 35 domain adapters
       │  JSON strings down, JSON strings up
       ▼
_core (Rust): all response parsers, HTML metadata (in-core meta_oxide
crate), SSRF/robots, budgets, guard, citations, tokens, scoring
```

Python decides, Rust parses. Details: [Architecture](./docs/ARCHITECTURE.md).

---

## Features

- **Zero API Keys**: DuckDuckGo plus 20+ keyless domain adapters (OpenAlex, Eurostat, Bundesbank, HUDOC, …)
- **Multi-Provider Search**: Google, Bing, Exa alongside DuckDuckGo with failover or merged results
- **Domain Providers**: `research_by_category` classifies queries (incl. German/EU terms like *Leitzins*, *BVerfG*, *HICP*) into scholarly / legal / patent / financial / geo — keyless-first
- **Patent Providers**: EPO OPS, KIPRIS, PatentsView — all key-gated, fail fast with the exact variable name
- **Documents**: PDF/DOCX/XLSX/PPTX plus TXT/MD/CSV/JSON/XML/feeds; tables render as markdown by default; `store=True, include_images=True` saves PDF figures
- **Crawl**: bounded relevance-ranked BFS (BM25 idfs + thesaurus + anchor context); documents collected, never fetched
- **Citations**: BibTeX / CSL-JSON / APA / MLA from search results, no extra network calls
- **HTML Metadata**: 13 formats via the in-core `meta_oxide` crate — no separate install, no PyPI blocker
- **Guard (optional, off)**: JailGuard ONNX detector — annotate / redact / block
- **Production-Ready**: TTL + size-cap caching, per-domain rate limits, robots/SSRF compliance, retries, observability

---

## Quick Start

### Prerequisites

- **Rust 1.70+** ([rustup](https://rustup.rs/)), **Python 3.10+**, **maturin**

### Build & Install

```bash
git clone https://github.com/opticsWolf/gossamer && cd gossamer
pip install -r requirements.txt
maturin develop --release
```

### Basic Usage

```python
from gossamer import ToolboxConfig, WebResearcherToolbox

tools = WebResearcherToolbox(ToolboxConfig(
    cache_dir="./cache",
    max_tokens=4000,
    model_name="gpt-4o",
))

results = tools.web_search("latest AI research papers", max_results=5, search_only=True)
content = tools.inspect_html_page("https://arxiv.org/abs/1234.5678")
pdf = tools.extract_document("https://example.com/paper.pdf")
with_figs = tools.extract_document("https://example.com/paper.pdf",
                                    store=True, include_images=True)
report = tools.research_by_category("EZB Leitzins", max_results=5)
```

### Async Usage

```python
results = await tools.search_web_async("rust programming")
```

> **What "async" means here (thread pool).** The `*_async` wrappers
> offload the shared **blocking** implementation to Python's default
> thread-pool executor (`loop.run_in_executor(None, …)`), keeping the
> event loop responsive — but the underlying network I/O is still
> **synchronous**. Use them inside `asyncio` apps to avoid blocking
> the loop; call the sync methods otherwise. Full model:
> [Architecture](./docs/ARCHITECTURE.md#12-async--threading-model).

### Tools (all ten, everywhere)

MCP tools, CLI commands (`gossamer …`), and `execute_tool(name, args)`
are the same surface: `web_search`, `research_by_category`,
`research_categories`, `inspect_html_page`, `batch_inspect_pages`,
`extract_document`, `discover_resources`, `crawl`, `manage_cache`,
`export_citations`, `check_sources`. Parameters:
[Quick reference](./docs/QUICKREF.md#tools-mcp--cli--execute_tool).

```python
tools.get_llm_definitions()  # OpenAI-compatible function definitions
tools.execute_tool("inspect_html_page", {"url": "https://example.com"})
```

---

## Domain Providers (`research_by_category`)

| Category | Providers (first = default) |
|----------|------------------------------|
| scholarly | OpenAlex, Crossref, arXiv, Zenodo |
| legal | CourtListener, eCFR, Federal Register, Open Legal Data, HUDOC (ECtHR), GovInfo |
| patent | EPO OPS, KIPRIS, PatentsView 🔑 (all key-gated) |
| financial | Yahoo, Frankfurter (FX), Eurostat, Bundesbank, BIS, CoinGecko, AlphaVantage 🔑 |
| geo | Open-Meteo, Overpass |
| general | DuckDuckGo (Google/Bing/Exa 🔑 opt-in) |

Euro terms route automatically (`EZB`, `Leitzins`, `HICP`, `EGMR`, `BVerfG`, `DSGVO`, …).

---

## Configuration

Env wins over file, always. Keys: `GOSSAMER_*` env > legacy `STITCH_*` >
keystore (`$GOSSAMER_KEYSTORE` > `gossamer.json:keystore` >
`~/.gossamer/keys.json`) > `gossamer.json` `"keys"`. Config file:
explicit > `$GOSSAMER_CONFIG` > `./gossamer.json` >
`~/.gossamer/config.json`.

```bash
python -m gossamer.keystore --init          # 0600 template, fill it in
python -m gossamer.keystore --init-config   # gossamer.json template
python -m gossamer.keystore --check         # validate, never prints secrets
```

```json
{ "max_tokens": 4000, "model_name": "gpt-4o", "fetch_mode": "auto" }
```

---

## Harness Integration (pi, Codex, Claude Code)

Same stdio server everywhere (`python -m gossamer.mcp_server`); keys stay
in the keystore, never in client configs. Shallowest first: direct CLI
(`gossamer search|research|inspect|extract|…`, 1:1 with MCP) → MCP
(`directTools`) → `skills/gossamer/SKILL.md`.

**pi** (`mcp.json`, then reload):

```json
{ "mcpServers": { "gossamer": {
  "command": "D:/User/Documents/Python/stitch-web-researcher/.venv/Scripts/python.exe",
  "args": ["-m", "gossamer.mcp_server"],
  "env": { "GOSSAMER_CACHE_DIR": "D:/User/Documents/Python/stitch-web-researcher/.gossamer_cache",
            "GOSSAMER_LOG_LEVEL": "WARNING" },
  "directTools": true } } }
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.gossamer]
command = "D:/User/Documents/Python/stitch-web-researcher/.venv/Scripts/python.exe"
args = ["-m", "gossamer.mcp_server"]
startup_timeout_sec = 30
```

**Claude Code:**

```bash
claude mcp add gossamer -- D:/User/Documents/Python/stitch-web-researcher/.venv/Scripts/python.exe -m gossamer.mcp_server
```

Keep crawls modest (`max_pages ≤ 15`) — long runs outlast harness timeouts.

---

## Running Tests

Hermetic by default (no network, SSRF on):

```bash
.venv/Scripts/python.exe -m pytest -q -n auto --ignore=tests/test_live_smoke.py
GOSSAMER_LIVE=1 pytest tests/test_live_smoke.py   # opt-in endpoint-drift check
```

Subset markers (`-m area_search|area_fetch|area_crawl|…`) are assigned by
filename in `tests/conftest.py`. Details:
[Architecture](./docs/ARCHITECTURE.md#10-testing).

## License

Dual-licensed under **MIT** OR **Apache-2.0** — zero copyleft, zero JVM.
