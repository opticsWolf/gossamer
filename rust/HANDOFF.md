# Handoff — 2026-11-22

## Where the port stands

- Branch: `dev_rustify` in `D:\User\Documents\Rust\gossamer`.
- **Wave 0 is complete.** All three bridgeable entry points are ported, gated, and wired.
- **Wave 1 is complete.** All deterministic `_core` kernels live in the pure core; Python-facing Rust modules are thin adapters.

| Unit / crate | Gate | Diff | Notes |
|---|---|---|---|
| `gossamer/skills/` → `rust/skills-rs` | passed | 662/662 | 268 Rust LOC / 139 Python (1.9x) |
| `scripts/audit_cargo.py` → `rust/audit-cargo-rs` | passed | 216/216 | 65 Rust LOC / 77 Python (0.8x) |
| `scripts/verify_bundled_assets.py` → `rust/verify-bundled-assets-rs` | passed | 10/10 | 164 Rust LOC / 71 Python (2.3x) |
| `gossamer/text_links.py` → `rust/text-links-rs` | passed | 211/211 | 109 Rust LOC / 46 Python (2.4x); not front-door wired |

Front door replay:

```text
32/32 cases passed
Fallback rate: 68.8% (22/32 cases answered by the original)
```

`rust-anything map --write` reports 4 of 6 whole-app units ported; the remaining two are
`gossamer/skills/` plus scripts already counted as separate crates, and `benchmarks.py` (wave 2).
The extra `rust/` unit is the replay shim `rust/original_entry.py`, not something to port.

## Files that matter now

- Plan/claims: [MAP.md](MAP.md)
- Decisions: [DECISIONS.md](DECISIONS.md) — ADRs 001–012 (ADR-012: pure core + thin PyO3 wrappers)
- Workspace: [Cargo.toml](Cargo.toml) and [.cargo/config.toml](.cargo/config.toml)
- Measured state: [PORT_MAP.md](PORT_MAP.md)
- Units: [skills-rs](skills-rs), [audit-cargo-rs](audit-cargo-rs),
  [verify-bundled-assets-rs](verify-bundled-assets-rs), [text-links-rs](text-links-rs)
- Shared errors: [port-error](port-error) — `ErrorKind` / `PortError`
- Pure core: [core](core) — crates.io name `gossamer-web`, internal lib `gossamer`, no PyO3
- Front door: [app](app) — glue in `src/units/`
- App cases: [app-cases](app-cases) — 32 deterministic CLI/script cases, including six
  review-fix regressions for malformed `skills install` args and mixed supported/unsupported
  verify targets
- Fixtures: [fixtures](fixtures) — locks and wheel/sdist artifacts used by record/replay
- Research: [skills-unit-risks.md](research/skills-unit-risks.md),
  [scripts-unit-risks.md](research/scripts-unit-risks.md),
  [dynamic-code.md](research/dynamic-code.md),
  [core-wrapper-modules.md](research/core-wrapper-modules.md), and one ADR-006 file per
  `unknown` runtime dependency under `research/`

## What is in progress / broken

- Nothing broken at the wave-0 or wave-1 checkpoints.
- ADR-012 core code was reviewed against `/skill:rust-guidelines` (correctness, docs, FFI-adjacent
  error mapping, libraries/UX, libraries/resilience, project, AI). Fixes applied: `SsrfError` is a
  canonical struct with backtrace (`M-ERRORS-CANONICAL-STRUCTS`); interpreter version is an explicit
  parameter instead of a static (`M-AVOID-STATICS`); const-parse panics include values
  (`M-PANIC-MESSAGE`); public functions have `# Errors`/`# Examples` (`M-CANONICAL-DOCS`).
  A second pass over `urls`/`dedupe`/`robots` added examples to `parse_robots`, `match_rules`, and
  `content_hash` (`M-CANONICAL-DOCS`), and made percent-escape panics include the offending bytes
  (`M-PANIC-MESSAGE`).
- The Rust clone venv now has the `documents` and `mcp` extras installed. Full
  `pytest -q -p no:cacheprovider` passes: 11391 passed / 36 skipped.
- `audit-cargo` parses Cargo.lock in Rust; the OSV HTTP query still falls back to Python. This is
  intentional for wave 0.
- PyO3 `check` remains faithful to the original non-Path error contract. Real archive checking is
  `check_path(&Path)` in Rust, covered by Rust unit tests against `rust/fixtures/`.
- `SkillNotFound`, `InstallResult`, and `iter_skills` are still kept in the original
  (`PORT_MAP.md`). `SkillNotFound` is exposed by the patched PyO3 wrapper, but the generator may
  overwrite that patch.
- Each generated `src/lib.rs` has a manual `port_message` mapping from `ErrorKind` to Python
  exceptions, plus `SkillNotFound` in `skills-rs`. Reapply all of them after `rust-anything refresh`.
- Build from `rust/` with plain `cargo build` (ADR-010). Avoid `cargo build --workspace` for the
  app because feature unification can re-enable `pyo3/extension-module`.
- `rust-anything app wire` rewrites the wired dependency in `rust/app/Cargo.toml` back to a plain
  path dependency. After wiring, restore `verify-bundled-assets-rs.workspace = true` (and check the
  other two port dependencies) so `default-features = false` stays in effect.
- `record --from-tests` instrumentation breaks `importlib.resources.files()` for the skills unit
  ("orphan path"); tests pass normally. Generated cases are the proof.
- `record` requires the local venv on `PATH` as `python3`; a `python3.exe` copy exists in `.venv`.
- App cases exclude four unstable live-network doc examples and use `--fresh rust/fixtures`.
- `install` unit cases create small ignored directories in the repo root. Keep them for repeatable
  `diff`, or re-run `record` after cleaning.

## Next unit

Wave 1 is complete. The dynamic-code blocker is resolved in `rust/research/dynamic-code.md`,
all ADR-006 dependency research is done, and ADR-012 fixed the end state: one pure Rust core crate
with thin PyO3 wrappers.

The pure core is established at `rust/core/` (`gossamer-web` published, `gossamer` as internal lib
name). It owns every deterministic kernel, including
`adapters::{common,patents,finance,misc,legal,scholar}`, `textlinks`, `compat`, `urlparts`, `urls`,
`dedupe`, `cite`, `fetch`, `guard`, `jats`, `jats_mml`, `metaextract`, `robots`, `sections`, `tables`,
`cacheutils`, `categories`, `miscutils`, `ssrf`, `tokens`, `xmlatom`, and `budget`. All root `src/*.rs`
modules are thin binding, logging, or class-shape adapters. `rust/core/tests/no_pyo3.rs` enforces
ADR-012. Source-level M9/M15 tests now read the core fetch path. The wheel rebuild and full Python
suite pass 11391 passed / 36 skipped. Framework-level `/skill:rust-guidelines` checks were run at
each adapter checkpoint; canonical errors, explicit runtime-version parameters, documented immutable
caches, and value-bearing panic messages are in place.

Next checkpoint: wave 2 (`benchmarks.py`), then the Python orchestration modules (`cache.py`,
`document.py`, `search.py`, `mcp_server.py`, `cli.py`) under a new ADR if CLI/env/MCP contracts
would change.

`text-links-rs` is not wired into `rust/app`: no CLI command calls `extract_links` directly. Add a
front-door command only under a new ADR, or wait until its caller (`document.py` / extraction
paths) is ported.

## Checkpoint rule

Stop after each wave. Waves 0 and 1 are done; wave 2 awaits user go-ahead.

## Skill to load next

`rust-anything` (whole-app mode). Read `MAP.md` first, then this file.
