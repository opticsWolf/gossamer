# Rust port — complete (2026-09-08)

Branch `dev_rust`, version **0.8.21**. Full suite: **10970 passed / 2 skipped**
Python, **65 passed** Rust lib. Every version bump committed and pushed.

## What ported (M1–M21)

| Milestone | Module | Notes |
|---|---|---|
| M1 (0.8.1) | `urls.rs`, `textlinks.rs` | `normalize_url`/`canonical_url`/`content_hash`, link scan |
| M2 (0.8.2) | `dedupe.rs` | `dedupe_plan` |
| M3 (0.8.3) | `sections.rs` | ATX+Setext, bit-identical BM25 |
| M4 (0.8.4) | `guard.rs`, `pycompat.rs` | chunk/redact/wrap, `py_strip`/`py_repr`/slicing |
| M5 (0.8.5) | `categories.rs` | `classify_query` |
| M6 (0.8.6) | `tokens.rs` | registry-first encoding, 6 embedded BPE |
| M7 (0.8.7) | `cite.rs` | BibTeX/CSL/APA/MLA, exact error paths |
| M8 (0.8.8) | `ssrf.rs` | CPython `ipaddress` tables, zones, NFKC |
| M9 (0.8.9) | `robots.rs` | longest-match-wins groups |
| M10 (0.8.10) | `budget.rs` | truncate/fit/shrink payloads |
| M11–M19 (0.8.11–0.8.19) | `adapters.rs`, `xmlatom.rs` | all **35** response parsers; `quick-xml` 0.42 |
| M20 (0.8.20) | `cacheutils.rs` | `disk_key` (blake2b-128), `human_size` (`nan` casing), content-type ext, `safe_name` |
| M21 (0.8.21) | `miscutils.rs` | `domain_of`, `sha256_hex`, `classify_link`, `parse_page_range` |

Shape throughout: JSON-string boundary, `shared_runtime().block_on`,
`abi3`; HTTP/keys/rate/retry stay Python, row-building in Rust,
`raw` re-attached byte-identical via `json.dumps`. Differential tests
with vendored oracles + seeded fuzz + hostile wrapper tests.

## What stays Python — by design (scope split)

- **Harness**: `mcp_server.py`, `agent_tools.py` (facade), `cli.py`,
  `__init__.py` — MCP/CLI speak JSON already.
- **Stateful objects**: `Cache` / `ResourceStore` (locks, file I/O,
  wall-clock TTL, byte payloads). Only layout helpers ported so the
  on-disk format agrees whichever side wrote it.
- **Orchestration / I/O**: `fetch/search/discovery/crawl/document/
  search_providers/liveness` — HTTP, backoff, frontier, oxide calls.
- **Data + time**: pydantic models, oxide parser, `_utc_now_iso`
  provenance, `_parse_xmp_datetime` (needs `chrono` for little gain),
  `_absolutize_markdown_links` (`urljoin` parity risk outweighs value),
  `env/settings/keystore` (Rust needs no secret resolver while HTTP
  stays Python-side).
- **Oracles**: retired Python row-builders kept where tests import them
  (e.g. `_parse_arxiv_entry`).

## Known boundaries (documented, not bugs)

- Lone surrogates cannot cross PyO3 (Python strips, Rust raises
  `UnicodeEncodeError`).
- Non-JSON-native `record_id`s arrive `str()`-rendered; NaN payloads
  cannot cross the JSON boundary.
- `parse_page_range` above `i128` range reports "not a page number"
  (page numbers never reach 39 digits).
- PyPI publish orthogonal (blocked by `meta-oxide @ git+...`).
