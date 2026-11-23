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

## Current state

- `dev_rustify` at `57d3ef0`: pure Rust front door lifted, replay 43/43, fallback 0, no fallback path.
- Rust owns: `env`/`settings` config resolution (unit 1), deterministic `_core` kernels, `benchmarks`, `skills`, `audit-cargo`,
  `verify-bundled-assets`, `cache` maintenance, `categories`, document URL rejection,
  search provider aliases/errors, CLI help/error text, MCP schema constant.
- Rust does not yet own: `research`, `download`, `locate-pdf`, `extract`, `check`, `discover`,
  `crawl`, `cite` (stubs), live `search`/`research`/`inspect`/`batch`, MCP transport and tool
  bodies, provider HTTP, document extraction, config resolution.
- Verification: 43 replay cases; Python suite is the behavioral oracle at 11,391 passing tests.

## Open questions

- Minimum supported Rust version and release packaging for the `gossamer` binary.
- Whether `rust/text-links-rs` gains a CLI surface when `documents` lands (ADR required).
