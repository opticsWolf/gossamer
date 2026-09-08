---
name: gossamer
description: Web research toolkit (search, fetch pages as markdown, extract documents, patent/legal/financial domain research). Use whenever the task needs current external information — docs, APIs, papers, case law, market data, patents — instead of guessing from training data. Prefers cached, rate-limited, token-budgeted tools over raw curl/fetch loops.
---

# Gossamer

Web research toolkit: search the web, fetch pages as LLM-friendly markdown
with follow-up links, and extract text/tables from documents (PDF, DOCX,
XLSX, PPTX, feeds). Domain adapters route scholarly / legal / patent /
financial / geo queries to the right APIs. Everything is cached,
per-domain rate-limited, and token-budgeted.

## Access (use whichever the harness provides)

- **MCP tools** (`gossamer_*` in pi with directTools, or plain names in
  Codex/Claude Code): `web_search`, `inspect_html_page`,
  `batch_inspect_pages`, `extract_document`, `discover_resources`,
  `crawl`, `manage_cache`, `research_by_category`,
  `export_citations`, `check_sources`.
- **CLI** (identical JSON, no MCP setup): `gossamer search QUERY`,
  `gossamer research QUERY [--provider P]`, `gossamer inspect URL`,
  `gossamer batch URL…`, `gossamer extract FILE|URL`, `gossamer check URL…`,
  `gossamer categories`. Run via the project venv
  (`…/.venv/Scripts/python.exe -m gossamer.cli …` on Windows).

## Routing (don't guess — classify first)

`research` auto-routes, or pick explicitly. Category → default provider:

- `scholarly` → `openalex` (papers, DOIs, citations)
- `legal` → `courtlistener` (US case law) · `oldp` (German cases) ·
  `hudoc` (ECtHR) · `ecfr`/`federalregister`/`govinfo` (US regs)
- `patent` → `epo` (worldwide via INPADOC) · `kipris` (Korea) ·
  `patentsview` (USPTO) — all key-gated, fail fast without keys
- `financial` → `yahoo` (quotes) · `frankfurter` (FX) · `eurostat` /
  `bundesbank` / `bis` (EU macro) · `coingecko` (crypto)
- `geo` → `open-meteo` / `overpass` (weather, places, coordinates)
- anything else → general web search (`duckduckgo`)

`gossamer categories` (or `research_categories`) prints this table live —
prefer it over memory when unsure.

## Budgets (avoid harness timeouts)

- Keep `max_pages` ≤ 15 on crawls; set an explicit `max_tokens` budget.
- Long extractions: `extract … --pages 10-20` instead of whole documents.
- `check` URLs with `--mode status` before fetching the shaky ones.

## Auth

API keys live in the keystore (`~/.gossamer/keys.json`;
`python -m gossamer.keystore --init`), never in harness configs or prompts.
Keyed providers raise an actionable error naming the exact variable
(e.g. `GOSSAMER_EPO_KEY`) — surface it to the user instead of retrying.

## Config / cache (where stuff actually is)

- Cache: `GOSSAMER_CACHE_DIR` (see `mcp.json`) > `gossamer.json:cache_dir` > `./.gossamer_cache`.
- Keys: `$GOSSAMER_KEYSTORE` > `gossamer.json:keystore` > `~/.gossamer/keys.json` (created only via `keystore --init`; absent = normal, not a broken install).
- Check effective paths in `mcp.json` + `gossamer keystore --check`, not `~/.gossamer`.

## Documents (PDF limits that matter)

- Tables render as markdown tables by default — no flag needed.
- Figures need `extract … --store --include-images` (PDF only): rasters land in `<stem>.files/` with a `## Figures` section; without the flag (or without `--store`) you get text-only and an empty manifest. Vector-only figures have no bytes to save.
- Large PDFs: use `--pages 10-20` ranges (cannot combine with `--store`).
