# Glossary

Names used across this port. Update as units introduce new domain terms.

| Term | Meaning in the Python original | Rust name / rule |
|---|---|---|
| skill pack | A directory containing `SKILL.md` shipped in `gossamer/skills/` | `SkillPack` / unit `gossamer/skills/` |
| SKILL.md | Frontmatter + instructions that an agent harness loads verbatim | `SkillDocument` |
| front door | The new entry point that owns argv/env and delegates unported work | `rust/app` |
| unit | One independently bridgeable, recordable, gatable piece | A crate under `rust/<unit>/` |
| bridge | Generated crate + binding that calls `port.rs` from the original | `rust-anything bridge` |
| shadow | Drop-in replacement with the original API and a kill switch | `rust-anything shadow` |
| replay | Run recorded app cases against original, front door, or lifted binary | `rust-anything app replay` |
| fallback rate | Share of replay cases still answered by Python | Decreases monotonically |
| gate | Final per-unit check: fmt, clippy, tests, cases, export and error-path coverage | `rust-anything gate <crate>` |
| PyO3 thin wrapper | Compatibility binding with no business logic | Delegates to `<crate>::port` only |
| TOOL_REGISTRY | Single source for MCP tool names, schemas, and LLM definitions | Rust static table in the front door |
| content verdict | Rust-side classification of fetched content (`content`, `shell`, `challenge`, ...) | `ContentVerdict` when that unit is ported |
| browser isolation | Child-process render for `browser_oxide` crash containment | `BrowserIsolation` when that unit is ported |
| cache key | Strategy-scoped identity for fetched documents | Preserve byte-for-byte per ADR-005 |
