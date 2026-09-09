# gossamer — quick reference

Command-level companion to [Architecture](./ARCHITECTURE.md). Full manual:
[README](../README.md). Per-version history: [Changelog](../CHANGELOG.md).
Planning history: local `planning/` (unsynced).

## Tools (MCP = CLI = `execute_tool`)

| Tool | CLI | Essentials |
|---|---|---|
| `web_search` | `gossamer search QUERY` | `--search-only` (no fetch), `--max-results`, `--depth`, `--provider` |
| `research_by_category` | `gossamer research QUERY` | `--category`, `--provider`; empty query prints the live taxonomy |
| `research_categories` | `gossamer categories` | routing table, no args |
| `inspect_html_page` | `gossamer inspect URL` | `--query` (focus slice), `--use-smart auto\|browser\|static` |
| `batch_inspect_pages` | `gossamer batch URL…` | same shape per URL, caller order preserved |
| `extract_document` | `gossamer extract SRC` | `--pages 10-20`, `--structured`, `--store [--store-dir D] [--include-images]` (PDF figures; needs `--store`) |
| `discover_resources` | `gossamer discover URL` | feeds + bounded `/sitemap.xml` probe |
| `crawl` | `gossamer crawl URL` | `--query`, `--max-depth 3`, `--max-pages 15`, `--same-host`, `--excerpts`, `--search-prior`, `--seed-urls` |
| `manage_cache` | `gossamer cache` | `prune` (default) \| `clear` \| `reset` |
| `export_citations` | `gossamer cite` | `--results`, `--style bibtex\|csl-json\|apa\|mla` |
| `check_sources` | `gossamer check URL…` | `--mode status\|content` |

`extract --pages` cannot combine with `--store`. `include_images` is PDF-only.

## Providers (keyless unless 🔑)

- **scholarly** → `openalex`, `crossref`, `arxiv`, `zenodo`
- **legal** → `courtlistener`, `ecfr`, `federalregister`, `oldp` (DE), `hudoc` (ECtHR), `govinfo`
- **patent** → `epo` 🔑, `kipris` 🔑, `patentsview` 🔑 (all key-gated, fail fast)
- **financial** → `yahoo`, `frankfurter`, `eurostat`, `bundesbank`, `bis`, `coingecko`, `alphavantage` 🔑
- **geo** → `open-meteo`, `overpass`
- **general** → `duckduckgo` (Google/Bing/Exa 🔑 opt-in via `search_providers=`)

Keyed failures name the exact variable (`GOSSAMER_EPO_KEY`) — surface it, don't retry.

## Config knobs (defaults)

`cache_dir ./.gossamer_cache` · `cache_ttl_seconds 3600` · `cache_max_bytes 0` (=∞)
· `ddgs_delay/jitter 1.0` · `domain_delay 0.5` · `max_markdown_chars 8000`
· `max_tokens 0` (=∞) · `model_name gpt-4o` · `fetch_mode auto`
· `respect_robots true` · `max_response_bytes 5 MiB` · `liveness_timeout 10s`
· `search_merge false` · `conditional_revalidation true`.
Env `GOSSAMER_*` beats file; `STITCH_*` legacy honored.

## Paths

- Keys: `$GOSSAMER_KEYSTORE` > `gossamer.json:keystore` > `~/.gossamer/keys.json`
- Config: explicit > `$GOSSAMER_CONFIG` > `./gossamer.json` > `~/.gossamer/config.json`
- `python -m gossamer.keystore --init` (0600 template) · `--init-config` · `--check`

## Build & test

```bash
pip install -r requirements.txt
maturin develop --release
pytest -q -n auto --ignore=tests/test_live_smoke.py   # hermetic default
cargo test --lib                                       # Rust kernels
GOSSAMER_LIVE=1 pytest tests/test_live_smoke.py        # opt-in drift check
```

## Gotchas

- Long crawls outlast harness timeouts: `max_pages ≤ 15` or raise the budget.
- `store=True` writes `<stem><ext>` + `<stem>.md` under `stored_documents/` (default).
- Tables render by default; figures need `--store --include-images`; vector-only figures have no bytes.
- Guard is off by default (`GuardConfig(enabled=True)` + `gossamer-web[guard]` extra to arm).
- `~/.gossamer/` absent is normal (created only by `keystore --init`).
