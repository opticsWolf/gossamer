# Rust port decisions

Source: `rust-anything` whole-app workflow, round 1 accepted 2026-11-22.
These ADRs are the source of truth for `rust/MAP.md`, the front door, and every unit port.
New decisions get a new numbered ADR; changed decisions get a superseding ADR.

## ADR-001: End state is a standalone pure Rust app

Status: accepted (2026-11-22)

Context: `dev_rustify` branches from `dev_browser`. The user wants the app rewritten in Rust, not a
Rust-accelerated Python app. `rust-anything analyze . --whole-app --json` reports a CLI shape with
10 entry points and a blocked 17,931-LOC `gossamer/` core.

Decision: Build `rust/app` as the standalone entry point that eventually owns all CLI/MCP behavior.
The Python tree remains in the repo only as reference and replay oracle until cutover. See ADR-009
for the PyO3 compatibility surface.

Consequences: The final `app lift` must produce a Rust binary/library with zero fallback to Python.
`get random` live sites, browser workers, and Python-specific packaging are not part of the core
contract unless recorded cases require them.

## ADR-002: Cover all entry points; first unit is `gossamer/skills/`

Status: accepted (2026-11-22)

Context: Entry points are `gossamer`, `gossamer-mcp`, `stitch-web-researcher-mcp`, `benchmarks.py`,
`browser_worker.py`, `cli.py`, `keystore.py`, `mcp_server.py`, `audit_cargo.py`, and
`verify_bundled_assets.py`. Wave 0 contains `gossamer/skills/` (140 LOC, 8 exports, readiness 91)
and `scripts/` (150 LOC, 4 exports, readiness 81).

Decision: The front door eventually takes every entry point. Claim `gossamer/skills/` first because
it has the highest readiness and the smallest semantic surface; `scripts/` follows in wave 0.

Consequences: No entry point is silently dropped. `benchmarks.py` stays in wave 2 until the core
units it measures are ported.

## ADR-003: Research-first handling for the blocked core

Status: accepted (2026-11-22)

Context: `gossamer/` is marked `blocked` because of dynamic code at
`gossamer/research_categories.py:287` (computed imports / dynamic execution).

Decision: Before claiming `gossamer/`, run a research spike to locate every dynamic-code path,
identify its callers, and decide whether to restructure it in Rust. Do not leave the unit
permanently outside the port and do not hand-wave the blocker away.

Consequences: `gossamer/` stays blocked in `MAP.md` until `rust/research/dynamic-code.md` exists and
the spike is reviewed. Its port is claimed with the piece that owns the dynamic code.

## ADR-004: Equivalence bar — exact where stable, normalized noise only

Status: accepted (2026-11-22)

Context: CLI/MCP output can contain timestamps, temp paths, and JSON key order differences that are
not semantic. Errors, exit codes, and stable text are part of the contract.

Decision: `app replay` must match stable stdout, stderr, and exit codes exactly. Known noise is
normalized only in recorded cases, never in the Rust port.

Consequences: Every wildcard or ordering relaxation needs a named reason in the case and a note in
this file. A mismatch is a port bug, not a reason to loosen the case.

## ADR-005: Preserve CLI, environment, cache, and MCP schema compatibility

Status: accepted (2026-11-22)

Context: Existing users and agents depend on `GOSSAMER_*` environment variables, `gossamer` CLI
flags, the on-disk cache layout, and the MCP tool schema (`TOOL_REGISTRY` / `SKILL.md` drift tests).

Decision: Treat these as frozen compatibility surfaces. Any intentional break requires a new ADR
that supersedes this one.

Consequences: Recorded cases include current flags, env vars, cache keys, and the 12 MCP tool
schemas. `test_skill_drift.py` remains a contract test until the Rust front door replaces it.

## ADR-006: Test tooling stays out; runtime dependencies get research first

Status: accepted (2026-11-22)

Context: `pytest`, `pytest-asyncio`, and `pytest-xdist` are declared but are test tooling, not
runtime dependencies. Twelve runtime dependencies have `fit: unknown` in the whole-app report.

Decision: Do not port pytest tooling into `rust/app`. For every runtime dependency with `fit:
unknown` or `none`, write `rust/research/<dependency>.md` from primary sources before claiming the
unit that uses it.

Consequences: Research files are prerequisites, not optional reading. Tooling-only dependencies are
listed in `MAP.md` as out of scope for the Rust binary.

## ADR-007: Use partial crate mappings and record the gaps

Status: accepted (2026-11-22)

Context: `bs4` maps only partially to `quick-xml`/`scraper`; `pydantic` maps only partially to
`serde`/`garde`, and validation error text will differ.

Decision: Use `quick-xml`/`scraper` and `serde`/`garde` for the units that need them, and add
recorded cases for every behavior the crates do not cover.

Consequences: Validation messages and HTML edge cases are characterized explicitly. A recorded gap
is a contract; if the Rust behavior differs, the port is wrong until the case passes or a new ADR
approves the difference.

## ADR-008: Windows first, wave checkpoints, gradual cutover

Status: accepted (2026-11-22)

Context: Host is Windows 11; no WSL or Docker is available. The plan has waves 0, 1, and 2. The
Python original can stay beside the Rust app during migration.

Decision: Target Windows 11 first, then Linux x86_64 in CI. Stop for user go-ahead after each wave.
Keep the Python original and Rust kill switches in place until replay is 100% and fallback is 0%.

Consequences: `RUST_ANYTHING_UNIT_<NAME>=original` must keep working through the waves. Cutover is
an explicit user decision, with `rust/cutover.sh` for steps only the user can perform.

## ADR-009: PyO3 bindings remain as thin compatibility wrappers

Status: accepted (2026-11-22)

Context: The user wants a pure Rust app but also wants Python bindings via PyO3 as thin wrappers.
The stock `rust-anything` lift removes bindings; this port deliberately deviates from that default.

Decision: Unit crates may expose PyO3 wrappers after the lift, provided they contain no business
logic and only delegate to `port.rs`. The standalone Rust `rust/app` remains the primary entry point.

Consequences: The lift checklist must preserve the PyO3 wrapper crates and test them after lift.
Wrappers are a compatibility surface for existing Python callers, not a second implementation.

## ADR-010: One Cargo workspace, with PyO3 extension mode opt-in

Status: accepted (2026-11-22)

Context: The four migration crates initially duplicated metadata, lints, and dependency versions.
They also enabled `pyo3/extension-module` unconditionally, which is correct for a Python-loaded
`cdylib` but risky for the standalone Rust binary, especially on Linux.

Decision: Keep one workspace at `rust/Cargo.toml` with shared package metadata, dependencies, and
lints, and `default-members = ["app"]`. Each port crate has a `default` feature that enables
`pyo3/extension-module`; `rust/app` depends on the port crates with `default-features = false`.
Build artifacts stay under `rust/app/target` via `rust/.cargo/config.toml`.

Consequences: `cargo build` from `rust/` builds the front door without `extension-module`, while
`rust-anything gate` still builds Python extensions with it enabled. Avoid `cargo build --workspace`
for the app because feature unification can re-enable `extension-module`.

## ADR-011: Port logic returns canonical errors, not Python-formatted strings

Status: accepted (2026-11-22)

Context: Generated `port.rs` code initially returned `Result<_, String>` with embedded Python
exception names. That made the core depend on the FFI vocabulary and made errors harder to inspect
from Rust glue.

Decision: Shared crate `rust/port-error` provides `ErrorKind` and `PortError`. Port functions return
`PortError`; PyO3 wrappers map the kind to a Python exception and the front door uses
`PortError::message()` for user-facing errors.

Consequences: The 888 recorded port cases and the whole-app replay must stay green after this
refactor. A `rust-anything refresh` that regenerates the PyO3 wrappers must reapply the
`port_message` mapping, just as it must reapply `SkillNotFound`.

## ADR-012: One pure Rust core crate; PyO3 bindings are thin wrappers

Status: accepted (2026-11-22)

Context: 15 `gossamer/` modules already delegate to `gossamer._core`, but that crate is currently
a `cdylib` with 141 `#[pyfunction]`s and PyO3-typed fields embedded in its data structures. The
standalone Rust app needs those algorithms without linking Python. The user has decided the end
state: **one Rust crate owns all logic and works independently of Python bindings**; PyO3 exists
only as thin adapters.

Decision: Establish a pure Rust library crate as the single authoritative core (path `rust/core/`,
crates.io name `gossamer-web` — verified available at 2026-11-22 — internal lib name `gossamer`).
It must not depend on `pyo3`, `Python`,
`Bound`, `PyResult`, or Python-specific exception types. A separate Python binding crate (tentative
path `python-bindings/`, lib name `_core`) will contain only argument/result marshaling and
`#[pymodule]` registration, and will depend on the pure core. `rust/app` will depend on the pure
core directly. The existing root `Cargo.toml`/`src/` tree is the legacy binding implementation and
is retired only after parity.

Consequences:
- No business logic may be added to the binding crate.
- A source-level test (`rust/core/tests/no_pyo3.rs`) enforces that the core manifest and source
  contain no PyO3 dependency or PyO3 types.
- Public fallible core APIs return canonical error structs with a backtrace and helper methods
  (`M-ERRORS-CANONICAL-STRUCTS`), not `String` (`SsrfError` is the first example).
- Version-dependent behavior is passed as an explicit parameter, not stored in a static
  (`M-AVOID-STATICS`). The PyO3 wrapper supplies the interpreter minor version at call time.
- Panic messages for provably-invalid internal constants include the offending value
  (`M-PANIC-MESSAGE`), and public functions carry canonical rustdoc sections (`M-CANONICAL-DOCS`).
- PyO3-typed fields (for example `BibliographicRecord.extra: Py<PyAny>`) must be replaced with
  portable Rust types in the core; conversion happens in the binding crate.
- This is a staged cutover: move a module to the pure core, keep its binding wrapper delegating to
  it, run the existing parity suite, then remove the old copy. Do not rewrite all 141 wrappers at once.
- Until the cutover finishes, the legacy root crate remains the Python wheel source and maturin
  `manifest-path` stays unchanged.

Supersedes the open choice in ADR-010's context regarding whether to add `rlib` to the legacy crate:
we choose a new pure core plus thin binding crate, not an `rlib` added to the PyO3-infected crate.

## ADR-013: Orchestration cutover with contract-preserving app lift

Status: accepted (2026-11-23)

Context: Waves 0–2 put all deterministic kernels in the pure core, ported the benchmark harness,
and wired four units into `rust/app`. The front door still sends 26/39 replay cases to Python.
The remaining code is Python orchestration: cache policy, document assembly, provider dispatch,
search sequencing, MCP tool wiring, and CLI parsing. Those modules own user-visible contracts:
CLI flags and exit codes, `GOSSAMER_*` environment variables, cache layout, and MCP tool schemas.
A direct rewrite would risk silent contract drift.

Decision: Cut over orchestration in this order, one unit at a time:

1. `gossamer/cache.py`
2. `gossamer/document.py`
3. `gossamer/research_categories.py` and `gossamer/research_providers.py`
4. `gossamer/search.py`
5. `gossamer/mcp_server.py`
6. `gossamer/cli.py`

Each unit is recorded/replayed before it is wired, stays behind a
`RUST_ANYTHING_UNIT_<NAME>=original` kill switch, and is considered done only when its own gate,
touched Python tests, the full Python suite, app replay, and lint checks pass. Network, browser,
clock, and randomness stay outside stable cases unless supplied by fixtures, a local HTTP sink, or
named normalizers. The Python package remains the replay oracle until fallback reaches 0%.

Standalone-lift rule: `rust-anything app lift` is run only after replay is 100% and fallback is 0%.
The lift must preserve the pure-core-only business logic (ADR-012), the PyO3 compatibility wrappers
(ADR-009), the single workspace (ADR-010), and canonical port errors (ADR-011). Any CLI, env,
cache-layout, or MCP-schema break requires a new ADR before implementation.

Consequences:
- The remaining Python modules become compatibility orchestrators before they are retired.
- `cache.py` and `document.py` are first because they reuse already-ported kernels and have stable
  filesystem/HTML fixtures.
- `mcp_server.py` and `cli.py` are last because they own the external contracts.
- Benchmark timing normalizers must not hide result-shape differences.
- Fallback rate is reported after every unit; no attempt to force 0% by deleting or weakening cases.

## ADR-014: pure-core PDF extraction uses `pdf_oxide`

- Date: 2026-11-27.
- `rust/core` depends on `pdf_oxide 0.3.78` with default features disabled.
- The Python oracle uses the same upstream engine via PyO3; parity is compared
  on extracted Markdown, not on implementation internals.
- No OCR, rendering, or bundled binaries are enabled. Binary extraction stays
  behind the explicit CLI/kernel boundary and reports canonical errors.

## ADR-015: pure-core Office extraction uses `office_oxide`

- Date: 2026-11-27.
- `rust/core` depends on `office_oxide 0.1.17` with default features disabled.
- Extraction is format-extension driven and uses `Document::from_reader`;
  parity is compared on Markdown output against the Python oracle.
- No editing, rendering, or external binaries are enabled.

## ADR-016: minimum supported Rust version is 1.97

- Date: 2026-11-27.
- The `rust/app` package declares `rust-version = "1.97"` and the Linux CI
  job builds/tests/clippy-checks the workspace on stable. Rust 1.97 is the
  contractual floor: contributions may not raise it without a new ADR.
- The legacy root PyO3 wrapper crate remains outside the production launcher
  contract and is retained only as the Python replay oracle until cutover.

## ADR-017: installed commands launch the Rust app

- Date: 2026-11-27.
- `gossamer`, `gossamer-mcp`, and the legacy MCP alias start the lifted Rust
  binaries through `gossamer.launcher`. The shell/CMD shims under `bin/` are
  the repository-local equivalent.
- `python -m gossamer.cli` and the Python test suite remain the replay oracle;
  they are no longer the production launcher contract.
- `GOSSAMER_RUST_BIN` overrides discovery for custom packaging. The launcher
  preserves the child's stdout, stderr, and exit status.
- PyO3 harness crates and root wrappers stay in the tree as compatibility
  adapters only. Deleting them is deferred until the oracle is no longer
  needed; it is not part of this cutover.

## ADR-018: no separate text-links CLI

- Date: 2026-11-27.
- `text-links-rs` remains a pure-core kernel plus PyO3 compatibility adapter.
  It does not get a new gossamer CLI command or MCP tool.
- Adding that command would change the frozen CLI/MCP schema and therefore
  requires a separate ADR before implementation.

## ADR-019: browser fallback stays default-off in Rust

- Date: 2026-11-27.
- `GOSSAMER_BROWSER_FALLBACK` accepts `off|auto|chrome|bladebro` and defaults
  to `off`. No backend is bundled, downloaded, or enabled by the shipped app.
- `browser_policy::browser_decision` only escalates a `shell`/no-usable-text
  verdict. `challenge`, `auth`, `paywall`, `blocked`, and `soft_404` are terminal
  walls and never escalate.
- A real Chrome/CDP or optional Bladebro subprocess lane requires SSRF-safe
  request interception first; adding it changes the launcher contract and needs
  a follow-up ADR.
