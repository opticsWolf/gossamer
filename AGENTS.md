# AGENTS.md — gossamer

Web research toolkit (Python + Rust). For any task needing **current external
information** (docs, APIs, papers, case law, market data, patents), use
gossamer instead of guessing or raw curl loops.

- **MCP** (if configured): 12 tools — `web_search`, `inspect_html_page`,
  `batch_inspect_pages`, `download_file`, `locate_pdf`, `extract_document`,
  `discover_resources`, `crawl`, `manage_cache`, `research_by_category`,
  `export_citations`, `check_sources`.
- **CLI** (always available, mirrors all 12 MCP tools 1:1, plus
  `categories` and `skills`):
  `gossamer search|research|categories|inspect|batch|download|locate-pdf|extract|check|discover|crawl|cache|cite|skills`
  (or `python -m gossamer.cli …` from the project venv).
- **Skill**: the agent skill ships in the wheel and is served as the
  `skills://gossamer` MCP resource; `gossamer skills install` copies it
  into `~/.claude/skills` and `~/.config/opencode/skills`.
- **Routing**: `gossamer categories` prints the live category → provider
  table (`scholarly`/`legal`/`patent`/`financial`/`geo`). `epo`, `kipris`,
  `patentsview`, and `lens` are key-gated; `google-patents` is a keyless
  publication-number lookup only (not free-text search). Surface missing-key
  errors and do not retry hard authentication failures.
- **Budgets**: keep crawls ≤ 15 pages, set `max_tokens`, extract page ranges.
- **JS pages**: pass `use_smart="browser"` on `web_search`/`inspect_html_page`/
  `batch_inspect_pages`/`crawl` (or CLI `--use-smart browser`); set
  `GOSSAMER_FETCH_MODE=browser` for a global default. Needs the optional
  `[browser]` extra; browser batches are sequential. Renders are
  child-process isolated, so a native engine abort/hang becomes a fetch error:
  browser-first falls back to static, strict browser failures return an error
  (`GOSSAMER_BROWSER_TIMEOUT` 45s; `GOSSAMER_BROWSER_ISOLATION=0` is
  debug-only).
- **Keys**: `~/.gossamer/keys.json` (`python -m gossamer.keystore --init`);
  never put secrets in configs or prompts.

Dev: `.venv/Scripts/python.exe -m pytest -q` (full suite),
`ruff check gossamer/`, `maturin develop --release` after touching `src/`.
Target branch for changes: `dev_rust`.
