# gossamer — quick review

5-minute orientation. Deep dive: `docs/ARCHITECTURE.md`, then `README.md`.
Full planning/audit history lives in local `planning/` (deliberately
unsynced — working notes, not repo record).

## What it is

Hybrid LLM web researcher at **v0.9.0**: Python toolbox + MCP server +
CLI over a Rust parsing core (`_core`, PyO3). 10 tools, 1:1 across
MCP / CLI / `execute_tool`: `web_search`, `research_by_category`,
`inspect_html_page`, `batch_inspect_pages`, `extract_document`,
`discover_resources`, `crawl`, `manage_cache`, `export_citations`,
`check_sources`.

## Health

- Python: **10983 passed / 2 skipped** (`pytest tests/ --ignore=tests/test_live_smoke.py`)
- Rust: **67 passed** (`cargo test --lib`)
- Live provider smoke (opt-in, key-optional): `GOSSAMER_LIVE=1 pytest tests/test_live_smoke.py`

## The split to remember

- **Rust** (`src/`, `_core`): every response parser (35 domain
  adapters), HTML metadata (in-core `meta_oxide` crate), SSRF/robots,
  budgets, guard scanning, citations, categories, tokens, sections,
  dedupe. JSON strings cross the boundary; `raw` is re-attached
  Python-side.
- **Python** (`gossamer/`): HTTP, keys, rate limits, retries,
  orchestration (fetch/search/crawl/document), cache + stores, MCP
  server, CLI, models. No logic except harness adaptation.
- Differential parity tests (`tests/test_rust_parity_*.py`) pin
  Rust == Python with vendored oracles + seeded fuzz.

## Paths that matter

- Keys: `GOSSAMER_*` env > `STITCH_*` env > keystore
  (`$GOSSAMER_KEYSTORE` > `gossamer.json:keystore` >
  `~/.gossamer/keys.json`) > `gossamer.json` `"keys"` > default.
- Config: explicit > `$GOSSAMER_CONFIG` > `./gossamer.json` >
  `~/.gossamer/config.json`. Cache default `./.gossamer_cache`.
- `python -m gossamer.keystore --init | --init-config | --check`.

## Build

```bash
pip install -r requirements.txt
maturin develop --release        # builds _core in place
pytest -q -n auto --ignore=tests/test_live_smoke.py
```

## Docs map

- `README.md` — full user reference (tools, providers, config).
- `CHANGELOG.md` — per-version record (M1–M24 Rust port milestones).
- `skills/gossamer/SKILL.md` — harness routing/budgets/auth.
- `AGENTS.md` — agent entry point.
