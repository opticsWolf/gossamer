# Research: `scripts/` unit risks

## 1. Two `main` functions

`analyze` groups `scripts/audit_cargo.py` and `scripts/verify_bundled_assets.py` into one unit, but
`rust-anything bridge` refuses a unit with two `main` definitions. Bridge each file separately:

- `rust/audit-cargo-rs` — `load_registry_packages`
- `rust/verify-bundled-assets-rs` — `check`

`main` stays in Python and is owned by the front door / replay shim.

## 2. Regex look-around

Source: <https://docs.rs/regex/latest/regex/index.html#syntax>

Rust's `regex` crate does not support look-ahead or look-behind. The original
`re.findall(r"\[\[package\]\](.*?)(?=\[\[package\]\]|\Z)", ...)` was replaced with a manual split on
`[[package]]` markers. The `name`/`version`/`source` patterns use supported `(?m)` and character
classes only. This was confirmed by `clippy::invalid_regex` during the port.

## 3. `pathlib.Path` vs JSON across PyO3

Source: <https://docs.python.org/3/library/pathlib.html>

- `Path.suffix` is `".whl"`; `Path.name.endswith(".tar.gz")` is a separate check.
- `Path`-like Python objects do not cross the generated JSON bridge as paths; they arrive as
  JSON values, so the faithful `check(Value)` reproduces the original's `AttributeError` for
  non-path inputs.
- The real archive logic is exposed as `check_path(&Path)` in Rust and covered by Rust unit tests
  against fixtures in `rust/fixtures/`.

Sources: <https://docs.python.org/3/library/zipfile.html>,
<https://docs.python.org/3/library/tarfile.html>

## 4. CLI argument conventions

- `audit_cargo.main(argv)` treats `argv[0]` as the lock path.
- `verify_bundled_assets.main(argv)` treats `argv[0]` as the program name and reads targets from
  `argv[1:]`.

`rust/original_entry.py` preserves both conventions when replay falls back.

## 5. Determinism

Four live-network doc examples were unstable during `app record`; they are excluded. App cases use
`--fresh rust/fixtures` so replay runs in a sandbox with the archive fixtures present.
