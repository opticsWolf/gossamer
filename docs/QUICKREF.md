# gossamer — quick reference

Command-level companion to [Architecture](./ARCHITECTURE.md). Full manual:
[README](../README.md). Per-version history: [Changelog](../CHANGELOG.md).
Planning history: local `planning/` (unsynced).

## Tools (twelve MCP tools; CLI = MCP 1:1 plus `categories`)

| Tool | CLI | Essentials |
|---|---|---|
| `web_search` | `gossamer search QUERY` | `--search-only` (no fetch), `--max-results 5`, `--max-tokens`, `--depth 5`, `--provider` |
| `research_by_category` | `gossamer research QUERY` | `--category`, `--provider`, `--providers` (explicit scholarly merge), `--max-results 5`; OpenAlex-only `--filter` / `--select` / `--title` / `--author`; empty query prints taxonomy |
| `inspect_html_page` | `gossamer inspect URL` | `--query` (focus slice), `--use-smart auto\|browser\|static`, `--offset 0`, `--max-chunks 1`, `--structured` |
| `batch_inspect_pages` | `gossamer batch URL…` | same shape per URL, caller order preserved |
| `download_file` | `gossamer download URL -o PATH` | `--min-bytes 1`, `--max-bytes 0` (configured cap), `--expect-format auto\|pdf`, `--overwrite`, `--resume`, `--try-mirrors URL…` (caller-supplied, sequential, policy-checked); fresh downloads stream to an atomic file, resume appends with Range/If-Range; does not extract |
| `locate_pdf` | `gossamer locate-pdf DOI` | Returns OpenAlex OA candidates (plus Unpaywall v2 when `GOSSAMER_UNPAYWALL_EMAIL` is set) with source/license/version provenance; does not download |
| `extract_document` | `gossamer extract SRC` | `--pages 10-20`, `--structured`, `--tables-as json\|markdown\|csv` (XLSX tables; default json, csv = one `## <sheet>` block per sheet), `--store [--store-dir D] [--include-images]` (PDF figures; needs `--store`) |
| `discover_resources` | `gossamer discover URL` | feeds + bounded `/sitemap.xml` probe |
| `crawl` | `gossamer crawl URL` | `--query`, `--max-depth 3`, `--max-pages 15`, `--min-score 0.05`, `--same-host`, `--excerpts`, `--search-prior`, `--seed-urls`, `--use-smart` |
| `manage_cache` | `gossamer cache` | `prune` (default) \| `clear` \| `reset` |
| `export_citations` | `gossamer cite` | `--style bibtex\|csl-json\|apa\|mla`, `--enrich`, `--no-dedupe` |
| `check_sources` | `gossamer check URL…` | `--mode status\|content` |

CLI-only: `gossamer categories` prints the routing table (not an MCP
tool; the toolbox method `research_categories()` backs it).

`extract --pages` cannot combine with `--store`. `include_images` is PDF-only. `download --resume` continues a partial file with Range/If-Range (servers that ignore Range restart; unsatisfiable ranges preserve the partial file); without `--resume`, unexpected partial-content responses are rejected.

## Providers (keyless unless 🔑)

- **scholarly** → `openalex` (default), `crossref`, `arxiv`, `zenodo`, `semanticscholar` (opt-in)
- **legal** → `courtlistener`, `ecfr`, `federalregister`, `oldp` (DE), `hudoc` (ECtHR), `govinfo`
- **patent** → `epo` 🔑, `kipris` 🔑, `patentsview` 🔑, `lens` 🔑, `google-patents` (keyless number lookup; others fail fast without keys; `lens` aggregates WO/EP/DE/CN/US, trial is non-commercial/academic)
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
- `use_smart="browser"` needs the `gossamer-web[browser]` extra (Windows/macOS only, no Linux wheels); without it browser requests fail and only static fetch runs.
- `~/.gossamer/` absent is normal (created only by `keystore --init`).
- OpenAlex is keyless for casual use; optional `GOSSAMER_OPENALEX_KEY` raises the daily budget. `GOSSAMER_OPENALEX_EMAIL` adds an operator-supplied `mailto` contact; no placeholder is sent.
- Semantic Scholar is opt-in and keyless-capable; its shared pool can 429. Configure `GOSSAMER_SEMANTICSCHOLAR_API_KEY` for an individual one-request-per-second allowance.

## Collection flow

`research QUERY --category scholarly` → `check URL --mode status` → `locate-pdf DOI` (optional) → `download URL -o FILE --expect-format pdf` → `extract FILE` → `cite DOI`. `--try-mirrors URL…` accepts only explicit caller-supplied OA/repository candidates; robots/SSRF checks apply to each. Review license/access terms before redistribution.
