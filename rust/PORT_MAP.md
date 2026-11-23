# Port map: gossamer

5 of 6 units have a Rust port; no original changed since it was recorded.

## Ports

| Unit | Crate | In Rust | Kept in the original | Cases | Last diff | Original |
| --- | --- | --- | --- | ---: | ---: | --- |
| `benchmarks.py` | `rust/benchmarks-rs` | `arrow`, `bench_rust_fetch`, `bench_meta_oxide`, `bench_token_budget`, `bench_batch_fetch`, `bench_structured_parser`, `load_guard_corpus`, `bench_guard`, `bench_guard_corpus` | `timed`, `main` | 4 | 4/4 | unchanged |
| `gossamer/skills/` | `rust/skills-rs` | `skill_names`, `skill_text`, `skill_description`, `default_destinations`, `install` | `iter_skills`, `SkillNotFound`, `InstallResult` | 662 | 662/662 | unchanged |
| `gossamer/text_links.py` | `rust/text-links-rs` | `extract_links` | - | 211 | 211/211 | unchanged |
| `scripts/audit_cargo.py` | `rust/audit-cargo-rs` | `load_registry_packages` | `main` | 216 | 216/216 | unchanged |
| `scripts/verify_bundled_assets.py` | `rust/verify-bundled-assets-rs` | `check` | `main` | 10 | 10/10 | unchanged |

## Whole app

| Glue | Runs |
| --- | --- |
| `rust/app/src/units/audit_cargo.rs` | Rust, handing some cases to the original |
| `rust/app/src/units/benchmarks.rs` | Rust, handing some cases to the original |
| `rust/app/src/units/skills.rs` | Rust, handing some cases to the original |
| `rust/app/src/units/verify_bundled_assets.rs` | Rust, handing some cases to the original |

## Not ported yet

In the order `analyze` suggests.

| Unit | Lines | Exports | Readiness |
| --- | ---: | ---: | ---: |
| `rust/` | 24 | 0 | 84 |

Regenerate with `rust-anything map --write`. Commit this file: when the original changes upstream, the next map marks every port whose recording it outdated.
