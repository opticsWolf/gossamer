# Research: `gossamer/` modules that already delegate to `_core`

Date: 2026-11-22.

## Finding

15 modules under `gossamer/` call `gossamer._core` directly:

```text
budget.py, cache.py, config.py, dedup.py, guard.py, meta_extractor.py, models.py,
research_categories.py, research_providers.py, resource_store.py, robots.py, ssrf.py,
structured_parser.py, text_links.py, token_budget.py
```

Examples:

- `text_links.py:47` — `extract_links` delegates to `_rust.text_links_scan`.
- `ssrf.py:58` — `validate_public_url` delegates to `_rust.ssrf_check_url` and maps `ValueError`.
- `budget.py:40,63,81,100` — truncation and JSON fitting already live in `src/budget.rs`.
- `robots.py`, `resource_store.py`, `dedup.py`, `cache.py` — hashing, robots matching, safe path
  names, and canonical URL logic are already in the Rust core.
- `research_providers.py` — many provider parsers are thin wrappers over `_core` functions.

## Decision (ADR-012)

Do **not** reimplement these algorithms in migration crates. A single pure Rust core crate will own
the logic and will work without Python. PyO3 bindings become thin wrappers over that crate.

The legacy root crate cannot simply gain `rlib`: 141 `#[pyfunction]`s and PyO3-typed fields such as
`BibliographicRecord.extra: Py<PyAny>` would keep the core Python-dependent. The cutover therefore
moves modules one at a time into a new pure crate, updates the legacy binding to delegate to it,
and runs the existing parity suite before deleting the old copy.

For early bridge units, the Python wrapper's validation/error-mapping behavior is still the
contract to record. `text_links.py` is the first example: its only Python-side rules are
non-string/empty input and `max_links <= 0`, while the scanner itself is already in Rust.
