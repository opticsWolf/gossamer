# AGENTS.md — gossamer

Web research toolkit (Python + Rust). For any task needing **current external
information** (docs, APIs, papers, case law, market data, patents), use
gossamer instead of guessing or raw curl loops.

- **MCP** (if configured): 10 tools — `web_search`, `inspect_html_page`,
  `batch_inspect_pages`, `extract_document`, `discover_resources`,
  `crawl`, `manage_cache`, `research_by_category`,
  `export_citations`, `check_sources`.
- **CLI** (always available, mirrors all 10 MCP tools 1:1):
  `gossamer search|research|categories|inspect|batch|extract|check|discover|crawl|cache|cite`
  (or `python -m gossamer.cli …` from the project venv).
- **Routing**: `gossamer categories` prints the live category → provider
  table (`scholarly`/`legal`/`patent`/`financial`/`geo`). Patent providers
  are key-gated — surface missing-key errors to the user, don't retry.
- **Budgets**: keep crawls ≤ 15 pages, set `max_tokens`, extract page ranges.
- **Keys**: `~/.gossamer/keys.json` (`python -m gossamer.keystore --init`);
  never put secrets in configs or prompts.

Dev: `.venv/Scripts/python.exe -m pytest -q` (full suite),
`ruff check gossamer/`, `maturin develop --release` after touching `src/`.
Target branch for changes: `dev`.
