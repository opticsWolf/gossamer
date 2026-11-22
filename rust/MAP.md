# Rust port map

Goal: standalone pure Rust app with PyO3 thin wrappers for Python compatibility.
Branch: `dev_rustify`. Source of branch: `dev_browser` @ `ab2b18f`.
Session started: 2026-11-22.

## Decisions

See [DECISIONS.md](DECISIONS.md). Current ADRs: 001–012. ADR-012 chooses one pure Rust core
crate with thin PyO3 wrappers.

## Units

| Unit | Wave | LOC | Status | Owner | Next action |
|---|---:|---:|---|---|---|
| `gossamer/skills/` | 0 | 140 | done (2026-11-22) | this session | gate passed; wired to `skills`; 662/662 diff, 21/21 replay |
| `scripts/` | 0 | 150 | done (2026-11-22) | this session | two crates gated; wired to `audit-cargo` and `verify-bundled-assets` |
| `gossamer/` | 1 | 17,931 | claimed (2026-11-22) | this session | dynamic-code unblocked; ADR-012 pure-core cutover; split into 39 files |
| `benchmarks.py` | 2 | 321 | open | — | after wave 1 |
| `gossamer/text_links.py` | 1 | 49 | done (2026-11-22) | this session | 211/211; next thin wrapper is `ssrf.py` |

`rust-anything map --write` generates the measured `rust/PORT_MAP.md`; this file is the plan and
the claims. Check them against each other at every checkpoint.

## Checkpoint 0 — wave 0, unit 1 done (2026-11-22)

- `rust/skills-rs`: gate passed (662/662 cases, 268 Rust LOC / 139 Python LOC, 1.9x).
- Bench: overall 1.51x geometric mean; `default_destinations` 8.53x, `install` 1.76x,
  `skill_description` 1.36x, `skill_text` 1.29x; `skill_names` 0.29x (keep original for now).
- `rust/app` front door replay: 21/21 cases passed; fallback rate 85.7% (18/21).
- Still kept in the original: `iter_skills`, `SkillNotFound`, `InstallResult` (see `PORT_MAP.md`).
- Next: claim `scripts/`, then research the dynamic-code blocker for `gossamer/`.

## Checkpoint 0 — wave 0 complete (2026-11-22)

- `rust/audit-cargo-rs`: gate passed, 216/216 cases, 65 Rust LOC / 77 Python LOC (0.8x).
- `rust/verify-bundled-assets-rs`: gate passed, 10/10 cases, 164 Rust LOC / 71 Python LOC (2.3x).
- Two crates were needed because the unit contains two `main` functions.
- `audit-cargo` parses Cargo.lock in Rust; the OSV query still falls back to Python.
- `verify-bundled-assets` checks wheels/sdists in Rust via `check_path`; PyO3 `check` stays faithful
  to the non-Path error contract. Rust unit tests cover the fixtures.
- Added `rust/original_entry.py` shim so replay can reach all three original entry points.
- Review fixes applied: malformed `skills install` args fall back before emitting text;
  `verify-bundled-assets` prevalidates all artifacts before printing; six regression cases added.
- Added a Cargo workspace at `rust/Cargo.toml` with `rust/port-error` for canonical port errors.
  `pyo3/extension-module` is now opt-in for port crates so the app binary can build without it.
- Front door replay: **32/32 cases passed**, fallback rate **68.8%** (22/32).
- Wave 0 is complete. Next: research the dynamic-code blocker for `gossamer/` (ADR-003).

## Checkpoint 1 — wave 1 research done (2026-11-22)

- `rust/research/dynamic-code.md`: the only true dynamic import is the 27-entry adapter factory
  in `research_categories.py`; it maps cleanly to static dispatch. No code generation needed.
- ADR-006 dependency research written for all eight `unknown` dependencies: `browser-oxide`,
  `citeproc`, `ddgs`, `jailguard`, `mcp`, `office-oxide`, `pdf-oxide`, `tiktoken`.
- `rust-anything analyze gossamer/` finds 39 files: 24 ready, 14 caution, 1 blocked. The whole
  package is too large to bridge as one unit; proceed file-by-file, starting with thin `_core`
  wrappers (`text_links.py`, `ssrf.py`, `budget.py`) before stateful/IO-heavy modules.
- Notes: many `gossamer/` modules already delegate to `gossamer._core`; see
  `rust/research/core-wrapper-modules.md`. Reuse those algorithms rather than reimplementing them.
- First file port: `gossamer/text_links.py` -> `rust/text-links-rs`, gate passed, 211/211 cases.
  Not wired into `rust/app` yet: there is no CLI command that calls `extract_links` directly.
- ADR-012 pure core at `rust/core/`: crates.io name `gossamer-web`, internal lib name `gossamer`.
  It now owns `textlinks`, `compat`, `urlparts`, `urls`, `dedupe`, `guard`, `metaextract`, `robots`,
  `sections`, `cacheutils`, `categories`, `miscutils`, `ssrf`, `tokens`, and `budget`. Legacy
  `src/textlinks.rs`, `src/ssrf.rs`, `src/tokens.rs`, `src/budget.rs`, `src/urls.rs`,
  `src/dedupe.rs`, `src/robots.rs`, `src/cacheutils.rs`, `src/categories.rs`, `src/miscutils.rs`,
  `src/guard.rs`, `src/sections.rs`, and `src/metaextract.rs` are thin PyO3 wrappers; `text-links-rs`
  delegates to the core scanner.
- `rust/core/tests/no_pyo3.rs` enforces ADR-012: no PyO3 dependency or PyO3 types in core source.
- Verification: core crate 47 unit tests + 2 guard tests + 29 doctests, `text-links-rs` gate
  211/211, wheel rebuild, full `pytest -q -p no:cacheprovider` 11391 passed / 36 skipped,
  app replay 32/32.

## Research prerequisites

- Done: `rust/research/dynamic-code.md` — blocker for `gossamer/` (ADR-003).
- Done: one ADR-006 research file each for `jailguard`, `browser-oxide`, `citeproc`, `ddgs`, `mcp`,
  `office-oxide`, `pdf-oxide`, `tiktoken`.
- Partial mappings: record gaps for `bs4` → `quick-xml`/`scraper` and `pydantic` → `serde`/`garde`
  (ADR-007).
- First unit risks: `string-offsets`, `generators`, `importlib.resources` behavior, `exceptions`.

## Out of scope for the Rust binary

- pytest, pytest-asyncio, pytest-xdist — test tooling only (ADR-006).
- DonSeTch/Bladebro-derived features not already in `dev_browser` — review docs only.
- Browser bypass/stealth posture; keep the current isolation and wall semantics.
- Full interactive browser driver (`act`, `state`, `run`, `vision`) — not part of gossamer.

## Open questions

- Minimum supported Rust version and packaging targets beyond Windows 11 / Linux x86_64.
- Exact crate choices for each `unknown` runtime dependency (research-derived; round 2).
- Which Python entry points the PyO3 wrappers must preserve after lift.
- Whether `benchmarks.py` becomes a Rust subcommand or stays a standalone binary.

## Checkpoints

Stop after wave 0, wave 1, and wave 2. At each checkpoint update this file, write
`rust/HANDOFF.md`, run `rust-anything map --write`, and report gate, replay, fallback rate, and
remaining risks before continuing.
