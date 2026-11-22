# Port map: gossamer

4 of 6 units have a Rust port; no original changed since it was recorded.

## Ports

| Unit | Crate | In Rust | Kept in the original | Cases | Last diff | Original |
| --- | --- | --- | --- | ---: | ---: | --- |
| `gossamer/skills/` | `rust/skills-rs` | `skill_names`, `skill_text`, `skill_description`, `default_destinations`, `install` | `iter_skills`, `SkillNotFound`, `InstallResult` | 662 | 662/662 | unchanged |
| `gossamer/text_links.py` | `rust/text-links-rs` | `extract_links` | - | 211 | 211/211 | unchanged |
| `scripts/audit_cargo.py` | `rust/audit-cargo-rs` | `load_registry_packages` | `main` | 216 | 216/216 | unchanged |
| `scripts/verify_bundled_assets.py` | `rust/verify-bundled-assets-rs` | `check` | `main` | 10 | 10/10 | unchanged |

## Whole app

| Glue | Runs |
| --- | --- |
| `rust/app/src/units/audit_cargo.rs` | Rust, handing some cases to the original |
| `rust/app/src/units/skills.rs` | Rust, handing some cases to the original |
| `rust/app/src/units/verify_bundled_assets.rs` | Rust, handing some cases to the original |

## Not ported yet

In the order `analyze` suggests.

| Unit | Lines | Exports | Readiness |
| --- | ---: | ---: | ---: |
| `benchmarks.py` | 321 | 11 | 89 |
| `rust/` | 21 | 0 | 86 |

Regenerate with `rust-anything map --write`. Commit this file: when the original changes upstream, the next map marks every port whose recording it outdated.
