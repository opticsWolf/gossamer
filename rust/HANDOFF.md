# Handoff — 2026-11-22

## Where the port stands

- Branch: `dev_rustify` in `D:\User\Documents\Rust\gossamer`.
- **Wave 0 is complete.** All three bridgeable entry points are ported, gated, and wired.
- **Wave 1 is in progress.** `gossamer/` is claimed; research is done and the first file port is gated.

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

`rust-anything map --write` reports 3 of 6 whole-app units ported. `gossamer/text_links.py` is a
file-level sub-unit of wave 1 and is not counted there yet. The extra `rust/` unit is the replay
shim `rust/original_entry.py`, not something to port.

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

- Nothing broken at the wave-0 checkpoint or the current wave-1 checkpoint.
- ADR-012 core code was reviewed against `/skill:rust-guidelines` (correctness, docs, FFI-adjacent
  error mapping, libraries/UX, libraries/resilience, project, AI). Fixes applied: `SsrfError` is a
  canonical struct with backtrace (`M-ERRORS-CANONICAL-STRUCTS`); interpreter version is an explicit
  parameter instead of a static (`M-AVOID-STATICS`); const-parse panics include values
  (`M-PANIC-MESSAGE`); public functions have `# Errors`/`# Examples` (`M-CANONICAL-DOCS`).
- The Rust clone venv was installed without optional extras. A full `pytest` run reports 32 failures
  and 2 collection errors from missing `documents` / `mcp` extras, unrelated to the port. Targeted
  parity suites and the wheel rebuild pass; install `gossamer-web[documents,mcp]` before relying on
  the full suite in this clone.
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

Wave 1 continues file-by-file through `gossamer/`. The dynamic-code blocker is resolved in
`rust/research/dynamic-code.md`, all ADR-006 dependency research is done, and ADR-012 fixes the
end state: one pure Rust core crate with thin PyO3 wrappers.

The pure core is established at `rust/core/` (`gossamer-web` published, `gossamer` as internal lib
name). It now owns `textlinks`, `compat`, `urlparts`, `urls`, `dedupe`, `ssrf`, `tokens`, and
`budget`. Legacy `src/textlinks.rs`, `src/ssrf.rs`, `src/tokens.rs`, `src/budget.rs`, `src/urls.rs`,
and `src/dedupe.rs` are thin wrappers; `text-links-rs` delegates to the core scanner.
`rust/core/tests/no_pyo3.rs` enforces ADR-012. The wheel rebuild and all Rust parity suites pass
9882 passed / 2 skipped combined. Next move another thin `_core` wrapper, for example `robots.rs`.

`text-links-rs` is not wired into `rust/app`: no CLI command calls `extract_links` directly. Add a
front-door command only under a new ADR, or wait until its caller (`document.py` / extraction
paths) is ported.

## Checkpoint rule

Stop after each wave. Wave 0 is done and wave 1 is in progress with user go-ahead.

## Skill to load next

`rust-anything` (whole-app mode). Read `MAP.md` first, then this file.
