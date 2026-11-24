# PORT — canonical port decisions and state

Small, canonical copy of the `rust-core-port` decisions for the remaining gossamer work.
Detail lives in `rust/DECISIONS.md` (ADRs), `rust/MAP.md` (waves/checkpoints),
`rust/HANDOFF.md` (session state) and `rust/PORT_MAP.md` (generated unit map).
If this file and those disagree, this file wins for *decisions*; the ADRs win for *rationale*.

## Decisions (agreed 2026-11-23)

1. **Crate layout.** Orchestration services are modules inside the existing pure core
   `rust/core` (`gossamer::config`, `gossamer::service::fetch`, `gossamer::service::search`,
   `gossamer::documents`, `gossamer::providers`). `rust/<unit>-rs` crates are reserved for
   standalone binaries only. No new per-service crates.
2. **Python wrappers.** Regenerate thin PyO3 wrappers per unit so Python can call the new Rust
   code incrementally before cutover. Wrappers stay thin: argument marshaling and exception
   mapping only, all logic in `rust/core`. Existing `gossamer._core` extension keeps working
   while units migrate.
3. **Equivalence bar.** Exact match for JSON shape and key order, counts, verdicts, rates,
   exit codes, and stderr text. No normalization except named noise: timestamps, temp paths,
   wall-clock timings. Provider HTTP is recorded as local fixtures and compared on the parsed
   payload.
4. **Checkpoints.** One unit at a time. Stop after each unit and report changed files/functions,
   gate result, diff count and bench numbers, differences/risks, and remaining ranked units.
   One reviewable commit per unit; batching only on explicit request.
5. **Docs + graph.** This file is canonical. CodeRadar is indexed on the repo for fan-in and
   blast-radius ranking, and the entry-point entity carries an annotation pointing here. Use
   `C:/Users/Main/.venv/Scripts/coderadar.exe` (0.16.0) — the `coderadar` on `PATH` is a
   stale 0.8.16 shim and must not be used.
6. **Scope.** CLI (`gossamer`), MCP server (12 tools) and the Python library API all stay in
   scope. Browser lane (`browser_isolation`, `browser_worker`, `browser_oxide`) and PDF/Office
   binary extraction stay in Python until their crates are proven.

## Chosen core units (ordered)

| # | Unit | Python source | LOC | Why |
|---|------|---------------|-----|-----|
| 1 | `config` | `config.py`, `settings.py`, `runtime_config.py` | ~1,280 | Highest fan-in; no network; currently duplicated ad hoc in `units/cache.rs` and `units/inspect.rs` |
| 2 | `documents` schema | `models.py`, `structured_parser.py` | ~1,230 | Payload schema shared by every CLI command and MCP tool |
| 3 | `service::fetch` | `FetchService` in `fetch.py` | ~1,450 | Hot path, largest blast radius, owns retries/cache/robots/budget |
| 4 | `service::search` + `providers` | `search.py`, `search_providers.py` | ~1,360 | 11 provider classes, throttling, key resolution |
| 5 | `providers` (research) | `research_providers.py`, `research_categories.py` | ~4,560 | Largest surface; last |
| 6 | `documents` extraction | `document.py`, `resource_store.py`, `open_access.py`, `citations.py` | ~2,750 | Depends on 1–3 |
| 7 | `MCP` tool bodies | `agent_tools.py`, `mcp_server.py` | ~1,620 | Needs 2–6 in place |
| 8 | CLI routing | `cli.py` | ~420 | Thin layer over the above |

Not in the ordered list: `crawl.py`, `downloader.py`, `discovery.py`, `liveness.py`
(ported after 4, before 7).

## Unit log

### Unit 1 — config resolution (done 2026-11-23)

- Core: `rust/core/src/config.rs` (discovery, precedence, keystore, templates,
  CPython-shaped JSON detail, `ConfigStore` mtime cache).
- Binding crate: `rust/config-rs` — thin delegator, switched on with
  `rust-anything enable` (marked block in `gossamer/env.py` and
  `gossamer/settings.py`, reversible with `disable`, kill switch
  `RUST_ANYTHING_UNIT_CONFIG=original`).
- Recorded from `tests/test_rust_parity_config.py` plus explicit inputs:
  **24/24 cases**, gate passed, bench 0.96x (parity; not a speed port).
- Python suite: 11397 passed / 36 skipped.
- Deviation to remember: `record --from-tests` on `tests/test_settings.py`
  captured calls whose results depend on `monkeypatch`ed env and `tmp_path`,
  so they cannot be replayed out of process. The replayable pins live in
  `tests/test_rust_parity_config.py`; environment precedence is proven by
  `tests/test_settings.py` itself, which now runs against the Rust code.
- `lookup_key` is intentionally not an exported case: its variadic signature
  is not representable in the recorded-case format. It stays in core and is
  exercised through `getenv` and the Python suite.

## Unit log

### Unit 3 — service::fetch kernels (done 2026-11-23)

- Core: `gossamer::service::fetch` owns strategy resolution/compatibility,
  cached-entry strategy checks, the M14 text heuristic, budgeted markdown
  slicing, metadata compaction and follow-up formatting.
- Binding crate: `rust/fetch-service-rs`, enabled over the module-level seams
  in `gossamer/fetch.py` and the `FetchService` methods; kill switch
  `RUST_ANYTHING_UNIT_FETCH_SERVICE=original`.
- Recorded from `tests/test_rust_parity_fetch_service.py`: **25/25 cases**,
  gate passed, bench 0.62x (the profitable kernels are `looks_like_text`
  2.89x and `format_follow_ups` 2.00x).
- Explicitly still in Python: `_dispatch_fetch`, `_inspect_html_page_impl`,
  `batch_inspect_pages_impl`, browser escalation, page-cache I/O and toolbox
  state. This unit is the deterministic decision layer those callers use,
  not a full transport port.

### Unit 4a — FetchService dispatch decision (done 2026-11-23)

- Core: `gossamer::service::fetch::dispatch_decision` chooses static/browser
  outcomes from already-computed results, keeping I/O in Python.
- Binding: `rust/fetch-service-rs` export `_service_dispatch_decision`, enabled
  with the same kill switch as unit 3.
- Recorded: 31/31 cases, gate passed, CodeRadar reindexed after review.
- Remaining in Python: actual HTTP dispatch, browser process calls, cache I/O
  and `batch_inspect_pages_impl`.

### Quota window rollover (done 2026-11-23)

- `gossamer::providers::base::quota_period` now labels UTC day/month quota
  windows for `_reset_quota_if_rolled`; time sampling stays in Python.
- Binding: extended `rust/adapter-base-rs` to 17/17 cases; gate passed.
- Full pytest: 11444 passed / 36 skipped; app replay 43/43.

### Retry decision policy (done 2026-11-23)

- `gossamer::providers::base::retry_decision` now owns the retry/raise/cap
  decision for the `@retry` decorator in `gossamer/search_providers.py`.
- Binding: extended `rust/adapter-base-rs` to 14/14 cases; gate passed.
- Full pytest: 11443 passed / 36 skipped; app replay 43/43.

### Research request planning (done 2026-11-23)

- `gossamer::service::research::research_plan` owns topic validation,
  depth clamping and plan-result budget clamping for `research()`.
- Binding: `rust/research-plan-rs` (3/3); gate passed; full pytest
  11442 passed / 36 skipped.

### Full two-tier cache (done 2026-11-23)

- `gossamer::cache::Cache` now backs `gossamer.cache.Cache` through a thin
  `PyCache` adapter. Python keeps a compatibility view for legacy `_memory`
  inspections; all hit/miss/LRU/disk/TTL/eviction behavior lives in Rust.
- Full pytest: 11441 passed / 36 skipped; app replay 43/43.
- Root `cargo clippy --all-targets` still has pre-existing stale adapter test
  imports; `cargo check`, workspace tests and workspace clippy are clean.

### Resource discovery (done 2026-11-23)

- `gossamer::discovery::find_feed_links` owns feed-alternate extraction and
  URL absolutization for `discover_resources`.
- Binding: `rust/discovery-rs` (1/1); gate passed; full pytest
  11441 passed / 36 skipped.

### `check_sources` input planning (done 2026-11-23)

- `gossamer::mcp_dispatch::check_sources_plan` owns mode validation,
  URL-list normalization and canonical deduplication.
- Binding: `rust/tool-check-rs` (3/3); gate passed; full pytest
  11440 passed / 36 skipped.

### Guard orchestration kernels (done 2026-11-23)

- `gossamer::guard::prepare_scope_pairs` and `gossamer::guard::merge_reports`
  now normalize scope text and assemble the additive guard block.
- Binding: `rust/guard-service-rs` (2/2); gate passed; full pytest
  11438 passed / 36 skipped; clippy clean.

### Research/search caller kernels (done 2026-11-23)

- `gossamer::jsonfmt::json_fallback` and `gossamer::service::search::provider_indices`
  now back `_json_fallback`, `_dedup_results` and `_resolve_providers`.
- Binding: `rust/research-kernels-rs` (5/5); gate passed; bench 1.38x.
- Full Python suite: 11436 passed / 36 skipped.

### FetchStats payload (done 2026-11-23)

- `gossamer::stats::fetch_stats_dict` owns the exact `FetchStats.to_dict()`
  payload, including percentile interpolation, Python one-decimal rounding,
  domain ordering and error-class ordering.
- Binding: `rust/stats-rs` (2/2); gate passed; bench 0.57x (correctness port).
- Python `FetchStats.to_dict()` delegates to the Rust seam.

### High-fan-in caller kernels (done 2026-11-23)

- Ported deterministic slices of the six ranked callers:
  `inspect_html_page` request routing, `extract_document` validation,
  `_enforce_delay`/`_init_rate_limit` policy kernels, `batch_inspect_pages`
  planning, and `web_search` routing/cache keys.
- Core: `gossamer::mcp_dispatch`, `gossamer::providers::base`,
  `gossamer::service::search`.
- Binding: `rust/caller-kernels-rs` (14/14); gate passed; bench 0.51x
  (correctness port; only `search_cache_key` is faster at 1.78x).
- Real methods now call the Rust seams; network/browser execution and side
  effects remain in Python.

### MCP tool-body kernels (done 2026-11-23)

- `gossamer::mcp_dispatch` now owns inspection routing, the unsupported
  `tables_as` error payload, and tool-argument default merging from the
  frozen 12-tool schema.
- Binding crate: `rust/tool-bodies-rs` (6/6); gate passed; bench 0.13x
  (correctness port only).
- Still in Python: real tool bodies, HTTP/browser calls and MCP transport.

### Units 4b, 5, 6-8 and provider-policy slice (done 2026-11-23)

- `gossamer::service::search`, `gossamer::service::research`,
  `gossamer::mcp_dispatch`, `gossamer::cli_router` and `gossamer::providers::base`
  now own deterministic orchestration kernels.
- Thin bindings: `rust/search-service-rs` (5/5), `rust/research-service-rs`
  (3/3), `rust/orchestration-rs` (4/4), `rust/adapter-base-rs` (7/7).
- Full Python suite after the provider-policy slice: 11424 passed / 36 skipped.
- Still in Python: HTTP/browser/provider transport, binary document extraction,
  MCP transport, full CLI execution, and the browser lane.

## Current state

- `dev_rustify` after `7b97a2e` plus the provider-policy slice: pure Rust front door lifted, replay 43/43, fallback 0, no fallback path.
- Rust owns: `env`/`settings` config resolution (unit 1), documents payload helpers (unit 2), deterministic FetchService kernels (unit 3), deterministic `_core` kernels, `benchmarks`, `skills`, `audit-cargo`,
  `verify-bundled-assets`, `cache` maintenance, `categories`, document URL rejection,
  search provider aliases/errors, CLI help/error text, MCP schema constant.
- Rust does not yet own: `research`, `download`, `locate-pdf`, `extract`, `check`, `discover`,
  `crawl`, `cite` (stubs), live `search`/`research`/`inspect`/`batch`, MCP transport and tool
  bodies, provider HTTP, document extraction, config resolution.
- Verification: 43 replay cases; Python suite is the behavioral oracle at 11,444 passing tests.

## Open questions

- Minimum supported Rust version and release packaging for the `gossamer` binary.
- Whether `rust/text-links-rs` gains a CLI surface when `documents` lands (ADR required).
