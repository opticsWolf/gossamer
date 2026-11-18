# gossamer — High-Performance LLM Web Researcher

> A hybrid LLM web researcher combining a **Rust parsing core** (PyO3)
> with **Oxide extractors** for documents and a **Python orchestration
> layer** for caching, rate limiting, budgets, tool routing, and
> multi-provider search.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![PyPI](https://img.shields.io/pypi/v/gossamer-web.svg)](https://pypi.org/project/gossamer-web/)
[![Rust](https://img.shields.io/badge/Rust-1.82%2B-orange)](https://rustup.rs)
[![License](https://img.shields.io/badge/License-MIT%2FApache--2.0-green)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-11365%20passing%2C%2034%20skipped-brightgreen)](tests/)
[![Website](https://img.shields.io/badge/website-gossamer-blue)](https://opticswolf.github.io/gossamer/)

**Docs:** [Quick reference](./docs/QUICKREF.md) · [Architecture](./docs/ARCHITECTURE.md) · [Changelog](./CHANGELOG.md)

---

## Architecture

```
LLM Agent / User
       │  MCP (mcp_server.py) · CLI (cli.py) · skills (bundled)
       ▼
WebResearcherToolbox (agent_tools.py) — facade, no logic
       │  TOOL_REGISTRY (config.py): one source of truth
       ▼
Collaborators (Python: HTTP, keys, rate limits, orchestration)
fetch · search · crawl · document · discovery · 40 domain adapters
       │  JSON strings down, JSON strings up
       ▼
_core (Rust): all response parsers, HTML metadata (in-core meta_oxide
crate), SSRF/robots, budgets, guard, citations, tokens, scoring
```

Python decides, Rust parses. Details: [Architecture](./docs/ARCHITECTURE.md).

---

## Features

- **Zero API Keys**: DuckDuckGo plus 20+ keyless domain adapters (OpenAlex, Eurostat, Bundesbank, HUDOC, …)
- **Multi-Provider Search**: Google, Bing, Exa alongside DuckDuckGo with failover or merged results, plus an optional `browser_oxide` stealth engine (`provider="browser"`)
- **Domain Providers**: `research_by_category` classifies queries (incl. German/EU terms like *Leitzins*, *BVerfG*, *HICP*) into scholarly / legal / patent / financial / geo — keyless-first
- **Patent Providers**: EPO OPS, KIPRIS, PatentsView, Lens (all key-gated, fail fast with the exact variable name; Lens aggregates WO/EP/DE/CN/US, trial is non-commercial/academic) + keyless Google Patents number lookup
- **Documents**: PDF/DOCX/XLSX/PPTX plus legacy DOC/XLS/PPT/XLSB (office-oxide ≥ 0.1.16) plus TXT/MD/CSV/JSON/XML/feeds; tables render as markdown by default; `store=True, include_images=True` saves PDF figures; scholarly extraction prefers publisher JATS XML (EuropePMC, then PLOS, via the in-house `_core` JATS kernel, fused math + `\tag{}` displays) when the source is DOI-resolvable (`prefer_jats`, default on)
- **Crawl**: bounded relevance-ranked BFS (BM25 idfs + thesaurus + anchor context); documents collected, never fetched
- **Citations**: BibTeX / CSL-JSON / APA / MLA from search results, no extra network calls
- **HTML Metadata**: 13 formats via the in-core `meta_oxide` crate — no separate install, no PyPI blocker
- **Guard (optional, off)**: JailGuard ONNX detector — annotate / redact / block
- **Production-Ready**: TTL + size-cap caching, per-domain rate limits, robots/SSRF compliance, retries, observability

---

## Quick Start

### Prerequisites

- **Rust 1.82+** ([rustup](https://rustup.rs/)), **Python 3.10+**, **maturin**

### Build & Install

```bash
git clone https://github.com/opticsWolf/gossamer && cd gossamer
pip install -r requirements.txt
maturin develop --release
# Optional: headless-browser rendering for JS-heavy pages
# (`use_smart="browser"`). Windows/macOS only — no Linux wheels exist,
# so this stays an extra (never a hard dependency) and static fetch
# remains the default.
pip install -e ".[browser]"
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

### Tools (twelve MCP tools, everywhere)

MCP tools, CLI commands (`gossamer …`), and `execute_tool(name, args)`
are the same surface, param-for-param: `web_search`,
`inspect_html_page`, `batch_inspect_pages`, `download_file`, `locate_pdf`,
`extract_document`, `discover_resources`, `crawl`, `manage_cache`, `research_by_category`,
`export_citations`, `check_sources`. `download_file` saves an opaque remote file
without requiring extraction; caller-supplied `fallback_urls` are tried
sequentially with normal robots/SSRF checks, and `resume=true` continues a
partial file with Range/If-Range (restart when the server ignores Range). Use `extract_document` when you
also want parsed text. `locate_pdf` resolves a DOI to OpenAlex OA PDF/landing-page candidates (plus Unpaywall v2 when `GOSSAMER_UNPAYWALL_EMAIL` is configured)
without downloading them. The CLI adds `gossamer categories`
(routing table) and `gossamer skills list|show|install` (bundled skill
management) — neither is an MCP tool. Parameters:
[Quick reference](./docs/QUICKREF.md).

```python
tools.get_llm_definitions()  # OpenAI-compatible function definitions
tools.execute_tool("inspect_html_page", {"url": "https://example.com"})
```

### JS rendering / stealth browser

`use_smart` selects how a page is rendered. It is available on every
page-fetching tool:

| Value | Behavior |
|---|---|
| `auto` (default) | static Rust fetch first; stealth browser fallback when the static result is an error or non-text |
| `browser` | stealth browser first, static fallback on failure |
| `static` | static only; never launches the browser |

The baseline is `fetch_mode` (`auto`/`browser`/`static`):
`GOSSAMER_FETCH_MODE` env > `gossamer.json` `"fetch_mode"` >
`ToolboxConfig(fetch_mode=...)` > `auto`. `use_smart` overrides it per call.
The CLI also accepts `--fetch-mode` as a run-wide baseline and `--use-smart`
per command.

```bash
gossamer inspect https://app.example.com --use-smart browser
gossamer batch URL1 URL2 --use-smart static
gossamer crawl https://example.com --use-smart browser --max-pages 5
gossamer search "query" --use-smart browser   # applies to the fetch phase
```

```python
tools.inspect_html_page(url, use_smart="browser")
tools.batch_inspect_pages(urls, use_smart="static")
tools.web_search("query", search_only=False, use_smart="browser")
tools.crawl(root_url, use_smart="browser")
```

MCP exposes the same parameter: `inspect_html_page(use_smart=...)`,
`batch_inspect_pages(..., use_smart=...)`, `crawl(..., use_smart=...)`, and
`web_search(..., use_smart=...)`. Set the MCP process default with
`GOSSAMER_FETCH_MODE=browser` in `mcp.json` or `gossamer.json`
`"fetch_mode"`.

`provider="browser"` is a separate stealth *search* engine (DuckDuckGo's
HTML endpoint rendered in browser_oxide); it needs the same extra and is
slower than DDG's plain HTTP path. It is registered last in the default
failover order, so it only answers when the other configured engines fail
(or when selected explicitly).

Notes:

- Install the optional extra with `pip install -e ".[browser]"`
  (Windows/macOS; the upstream package has no Linux wheels). Without it,
  `auto` never renders, `browser` falls back to static, and an explicit
  global `browser` mode raises a clear missing-dependency error.
- Browser batches are sequential and much slower than the static Rust batch.
- A cached page is reused only when its render strategy satisfies the
  request. An explicit `browser`/`static` request bypasses an incompatible
  cache entry; `auto` accepts any. The result's `fetch_method` is `static`,
  `browser`, or `stealth-fallback` (auto fallback).
- Bot walls (Cloudflare challenges, CAPTCHAs) are not bypassed.
- Browser renders run in a short-lived child process by default. A native
  `browser_oxide` abort (e.g. the V8 deadline / 64 GiB allocation failure on
  `example.com`) or hang becomes a normal fetch error: `browser-first` falls
  back to static, other browser strategies return an error, and the CLI/MCP
  server stays alive.
  Tune the per-render timeout with `GOSSAMER_BROWSER_TIMEOUT` (default 45s).
  `GOSSAMER_BROWSER_ISOLATION=0` restores in-process rendering for debugging
  and also restores the host-abort risk. Details and the upstream repro:
  [`docs/browser_oxide_crash.md`](docs/browser_oxide_crash.md).

---

## Domain Providers (`research_by_category`)

| Category | Providers (first = default) |
|----------|------------------------------|
| scholarly | OpenAlex (default), Crossref, arXiv, Zenodo, Semantic Scholar, Hugging Face models + Daily Papers (opt-in; keys recommended for rate limits) |
| legal | CourtListener, eCFR, Federal Register, Open Legal Data, HUDOC (ECtHR), GovInfo |
| patent | EPO OPS, KIPRIS, PatentsView, Lens 🔑 + keyless Google Patents lookup |
| financial | Yahoo, Frankfurter (FX), Eurostat, Bundesbank, BIS, CoinGecko, AlphaVantage 🔑 |
| geo | Open-Meteo, Overpass |
| general | DuckDuckGo (Google/Bing/Exa 🔑 opt-in; `browser` stealth engine opt-in via `[browser]`) |

Euro terms route automatically (`EZB`, `Leitzins`, `HICP`, `EGMR`, `BVerfG`, `DSGVO`, …). For precise OpenAlex queries, `gossamer research QUERY --provider openalex --filter 'type:article' --select 'id,title,doi' --title 'gradient index' --author 'Smith'` passes provider-native controls; those options are rejected for other providers. `--title`/`--author` map to the verified `title.search`/`raw_author_name.search` filters and combine with `--filter`. Explicit scholarly multi-search is opt-in via `gossamer research QUERY --providers openalex arxiv`; it merges by DOI/arXiv ID, preserves each source record, and never fans out by default.

**Paper collection:** `research` → `check --mode status` → optional `locate-pdf DOI` → `download URL -o FILE --expect-format pdf` → `extract DOI` (JATS-first: publisher full text when resolvable) or `extract FILE --no-prefer-jats` → `cite DOI` (or `cite FILE.pdf [--from-pdf]` for a DOI detected from the file). Supplied mirror URLs are checked independently; gossamer respects robots/SSRF policy and does not bypass bot walls. The [skill](./gossamer/skills/gossamer/SKILL.md) has the full recipe — it ships inside the wheel, and `gossamer skills install` copies it into your agent's discovery directory.

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

OpenAlex works without a key for casual use. Set `GOSSAMER_OPENALEX_KEY`
(in the keystore or environment) for the higher daily budget. If you want to
identify your client by email, set `GOSSAMER_OPENALEX_EMAIL`; it is sent as
`mailto` and in the request headers. No placeholder email is sent when unset.
Semantic Scholar is also callable without a key, but keyless requests share a
pool and may receive 429s; set `GOSSAMER_SEMANTICSCHOLAR_API_KEY` for its
individual one-request-per-second allowance. Hugging Face Hub search is
keyless; set `GOSSAMER_HF_TOKEN` to raise the rate limit. Papers found via
`huggingface-papers` merge with `arxiv`/`semanticscholar` records on arXiv ID.

---

## Harness Integration (pi, Codex, Claude Code)

Same stdio server everywhere (`python -m gossamer.mcp_server`); keys stay
in the keystore, never in client configs. Shallowest first: direct CLI
(`gossamer search|research|inspect|download|locate-pdf|extract|…`, 1:1 with MCP) → MCP
(`directTools`) → `gossamer/skills/gossamer/SKILL.md` (bundled in the wheel
and served as the `skills://gossamer` MCP resource; `gossamer skills
list|show|install` manages it).

**pi** (`mcp.json`, then reload):

```json
{ "mcpServers": { "gossamer": {
  "command": "D:/User/Documents/Python/gossamer/.venv/Scripts/python.exe",
  "args": ["-m", "gossamer.mcp_server"],
  "env": { "GOSSAMER_CACHE_DIR": "D:/User/Documents/Python/gossamer/.gossamer_cache",
            "GOSSAMER_LOG_LEVEL": "WARNING" },
  "directTools": true } } }
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.gossamer]
command = "D:/User/Documents/Python/gossamer/.venv/Scripts/python.exe"
args = ["-m", "gossamer.mcp_server"]
startup_timeout_sec = 30
```

**Claude Code:**

```bash
claude mcp add gossamer -- D:/User/Documents/Python/gossamer/.venv/Scripts/python.exe -m gossamer.mcp_server
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
