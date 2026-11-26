# PORT — canonical port decisions and state

Small, canonical copy of the `rust-core-port` decisions for the remaining gossamer work.

Detailed execution plan derived from CodeRadar: `rust/REMAINING_PORT_PLAN.md`.
Detail lives in `rust/DECISIONS.md` (ADRs), `rust/MAP.md` (waves/checkpoints),
`rust/HANDOFF.md` (session state) and `rust/PORT_MAP.md` (generated unit map).
If this file and those disagree, this file wins for *decisions*; the ADRs win for *rationale*.

## Decisions (agreed 2026-11-23)

1. **Crate layout.** Orchestration services are modules inside the existing pure core
   `rust/core` (`gossamer::config`, `gossamer::service::fetch`, `gossamer::service::search`,
   `gossamer::documents`, `gossamer::providers`). `rust/<unit>-rs` crates are reserved for
   standalone binaries only. No new per-service crates.
2. **Python wrappers.** Regenerate thin PyO3 wrappers per unit so Python can call the new Rust
   code incrementally before cutover. Wrappers stay thin: argument marshaling and exception
   mapping only, all logic in `rust/core`. Existing `gossamer._core` extension keeps working
   while units migrate.
3. **Equivalence bar.** Exact match for JSON shape and key order, counts, verdicts, rates,
   exit codes, and stderr text. No normalization except named noise: timestamps, temp paths,
   wall-clock timings. Provider HTTP is recorded as local fixtures and compared on the parsed
   payload.
4. **Checkpoints.** One unit at a time. Stop after each unit and report changed files/functions,
   gate result, diff count and bench numbers, differences/risks, and remaining ranked units.
   One reviewable commit per unit; batching only on explicit request.
5. **Docs + graph.** This file is canonical. CodeRadar is indexed on the repo for fan-in and
   blast-radius ranking, and the entry-point entity carries an annotation pointing here. Use
   `C:/Users/Main/.venv/Scripts/coderadar.exe` (0.16.0) — the `coderadar` on `PATH` is a
   stale 0.8.16 shim and must not be used.
6. **Scope.** CLI (`gossamer`), MCP server (12 tools) and the Python library API all stay in
   scope. Browser lane (`browser_isolation`, `browser_worker`, `browser_oxide`) and PDF/Office
   binary extraction stay in Python until their crates are proven.

## Chosen core units (ordered)

| # | Unit | Python source | LOC | Why |
|---|------|---------------|-----|-----|
| 1 | `config` | `config.py`, `settings.py`, `runtime_config.py` | ~1,280 | Highest fan-in; no network; currently duplicated ad hoc in `units/cache.rs` and `units/inspect.rs` |
| 2 | `documents` schema | `models.py`, `structured_parser.py` | ~1,230 | Payload schema shared by every CLI command and MCP tool |
| 3 | `service::fetch` | `FetchService` in `fetch.py` | ~1,450 | Hot path, largest blast radius, owns retries/cache/robots/budget |
| 4 | `service::search` + `providers` | `search.py`, `search_providers.py` | ~1,360 | 11 provider classes, throttling, key resolution |
| 5 | `providers` (research) | `research_providers.py`, `research_categories.py` | ~4,560 | Largest surface; last |
| 6 | `documents` extraction | `document.py`, `resource_store.py`, `open_access.py`, `citations.py` | ~2,750 | Depends on 1–3 |
| 7 | `MCP` tool bodies | `agent_tools.py`, `mcp_server.py` | ~1,620 | Needs 2–6 in place |
| 8 | CLI routing | `cli.py` | ~420 | Thin layer over the above |

Not in the ordered list: `crawl.py`, `downloader.py`, `discovery.py`, `liveness.py`
(ported after 4, before 7).

## Unit log

### Unit 1 — config resolution (done 2026-11-23)

- Core: `rust/core/src/config.rs` (discovery, precedence, keystore, templates,
  CPython-shaped JSON detail, `ConfigStore` mtime cache).
- Binding crate: `rust/config-rs` — thin delegator, switched on with
  `rust-anything enable` (marked block in `gossamer/env.py` and
  `gossamer/settings.py`, reversible with `disable`, kill switch
  `RUST_ANYTHING_UNIT_CONFIG=original`).
- Recorded from `tests/test_rust_parity_config.py` plus explicit inputs:
  **24/24 cases**, gate passed, bench 0.96x (parity; not a speed port).
- Python suite: 11397 passed / 36 skipped.
- Deviation to remember: `record --from-tests` on `tests/test_settings.py`
  captured calls whose results depend on `monkeypatch`ed env and `tmp_path`,
  so they cannot be replayed out of process. The replayable pins live in
  `tests/test_rust_parity_config.py`; environment precedence is proven by
  `tests/test_settings.py` itself, which now runs against the Rust code.
- `lookup_key` is intentionally not an exported case: its variadic signature
  is not representable in the recorded-case format. It stays in core and is
  exercised through `getenv` and the Python suite.

## Unit log

### Unit 3 — service::fetch kernels (done 2026-11-23)

- Core: `gossamer::service::fetch` owns strategy resolution/compatibility,
  cached-entry strategy checks, the M14 text heuristic, budgeted markdown
  slicing, metadata compaction and follow-up formatting.
- Binding crate: `rust/fetch-service-rs`, enabled over the module-level seams
  in `gossamer/fetch.py` and the `FetchService` methods; kill switch
  `RUST_ANYTHING_UNIT_FETCH_SERVICE=original`.
- Recorded from `tests/test_rust_parity_fetch_service.py`: **25/25 cases**,
  gate passed, bench 0.62x (the profitable kernels are `looks_like_text`
  2.89x and `format_follow_ups` 2.00x).
- Explicitly still in Python: `_dispatch_fetch`, `_inspect_html_page_impl`,
  `batch_inspect_pages_impl`, browser escalation, page-cache I/O and toolbox
  state. This unit is the deterministic decision layer those callers use,
  not a full transport port.

### Unit 4a — FetchService dispatch decision (done 2026-11-23)

- Core: `gossamer::service::fetch::dispatch_decision` chooses static/browser
  outcomes from already-computed results, keeping I/O in Python.
- Binding: `rust/fetch-service-rs` export `_service_dispatch_decision`, enabled
  with the same kill switch as unit 3.
- Recorded: 31/31 cases, gate passed, CodeRadar reindexed after review.
- Remaining in Python: actual HTTP dispatch, browser process calls, cache I/O
  and `batch_inspect_pages_impl`.

### U1 provider request-planning kernel (done 2026-11-25)

- Added `gossamer::providers::live::provider_request_plan`, a declarative
  auth/header/query policy evaluator used by `ResourceAdapter.inject_auth`.
- Every `ResourceAdapter` subclass now supplies declarative `AUTH_POLICY`
  data; all 18 Python `inject_auth` mutation bodies were removed and flow
  through the Rust request-planning kernel.
- `retry_decision` now caps jitter at 0.25 s to match the Python decorator;
  `_service_retry_plan` is enabled from Rust for every `ResourceAdapter.search`
  retry, including status/transport/quota/cap verdicts.
- Extended `rust/adapter-base-rs` with `_service_provider_request_plan`; gate
  **27/27 cases match**, bench 0.08x for request planning (correctness kernel;
  kept despite slow crossing because the caller is not batched yet).
- Rust-guidelines review: `M-PANIC-IS-STOP` and `M-UNSAFE` satisfied by
  returning `Result` with canonical `TypeError` messages and no unsafe code.
- CLI `research --provider crossref`, `--provider openalex`,
  `--provider zenodo`, `--provider huggingface`, and
  `--provider huggingface-papers` now run live Rust HTTP calls and use the
  matching `adapters::scholar` parsers plus Python-style `raw` serialization.
  Verified against the Python oracle on live scholarly queries; live results
  are not replay-pinned.
- Added `discovery::fetch_text_with_user_agent` and core `USER_AGENT` for APIs
  that reject generic clients (needed by Zenodo); documented fallible API.
- `jsonfmt::py_dumps` now formats floats with Python `repr` exponent spelling,
  fixing scientific-notation differences in embedded `raw` payloads.
- Full pytest 11479 passed / 36 skipped; app replay 57/57.

### Download size-cap error parity (done 2026-11-25)

- `gossamer::downloader::download_bytes` now reports declared body size, final
  URL, and status in a structured too-large marker instead of an opaque string.
- CLI `download --max-bytes` now emits the exact Python `too_large` envelope.
- Verified live against the Python oracle; app replay 57/57, full pytest
  11468 passed / 36 skipped.

### Download error envelope parity (done 2026-11-25)

- CLI `download` now emits the full canonical error object from the Rust
  classifier: `http_status`, `url`, and `retry_after` for HTTP failures, plus
  byte/status/url fields for validation failures.
- Verified against the Python oracle for `not_found` and `too_small` live
  cases; app replay 57/57, full pytest 11468 passed / 36 skipped.

### Structured-format rejection kernel (done 2026-11-25)

- Added `gossamer::document::structured_format_error`, computing the canonical
  unsupported-format message (including URL-path suffixes) and the exact stderr
  log line used by the CLI.
- `gossamer extract --structured` now reproduces the Python envelope for flat
  formats. Core unit tests 2/2; app replay 57/57, full pytest 11468 passed /
  36 skipped.

### CLI `extract` stored local text (done 2026-11-25)

- `gossamer extract --store` now writes the original bytes and extracted
  markdown for local text-like files and reports the platform-native stored
  paths plus resource counts in the exact Python envelope shape.
- Verified against the Python oracle on a `.txt` fixture; app replay 57/57,
  full pytest 11468 passed / 36 skipped.

### Page-range rejection kernel (done 2026-11-25)

- Added `gossamer::document::page_selection_error`, computing the canonical
  actionable error for page-range reads on flat formats, including URL-path
  suffix handling and query/fragment stripping.
- `gossamer extract` now emits the exact Python envelope for `--pages` on text
  files and for `--pages --store`; paged formats remain outside this slice.
- Core unit tests 2/2; exact Python parity verified; app replay 57/57,
  full pytest 11468 passed / 36 skipped.

### XML/RSS feed extraction kernel (done 2026-11-25)

- Added `gossamer::document::xml_feed_text`, rendering RSS/Atom/RDF entries via
  the pure-core `xmlatom` parser and returning `None` when the document has no
  entries so callers fall back to raw text.
- `gossamer extract` now uses it for local and remote `.xml`/`.rss`/`.atom`
  sources. Verified exactly against the Python oracle on an RSS fixture;
  core unit tests 2/2, app replay 57/57, full pytest 11468 passed / 36 skipped.

### CLI `extract` unsupported URL formats (done 2026-11-25)

- Remote extraction now rejects URL suffixes the Python extractor does not
  support with the exact `Unsupported document format: .<ext>` envelope before
  fetching. Text-like URL suffixes keep the Rust download path.
- Verified exactly against the Python oracle for `https://example.com`;
  app replay 57/57, full pytest 11468 passed / 36 skipped.

### CLI `extract` URL error envelope (done 2026-11-25)

- `gossamer extract` now reports HTTP error pages for remote sources with the
  exact Python `Document extraction failed: Client error ...` envelope. Text-like
  successful remote bodies are decoded; HTML parsing, binary extraction and
  robots/cache side effects remain outside this slice.
- Verified exactly against the Python oracle for `https://example.com/robots.txt`;
  app replay 57/57, full pytest 11468 passed / 36 skipped.

### CLI `crawl` link-graph body (done 2026-11-25)

- `gossamer crawl` now fetches the root, builds a scored frontier from Rust
  crawl kernels, fetches bounded children, and emits the full crawl envelope.
  Search-prior, seed URLs, document scoring and cache/robots side effects
  remain outside this slice.
- Verified against the Python oracle on `https://example.com --query example
  --max-pages 2 --max-depth 1` (exact stable output); app replay 57/57,
  full pytest 11468 passed / 36 skipped.

### CLI `research` general body (done 2026-11-25)

- `gossamer research` now classifies the query via `gossamer::categories::classify_query`,
  resolves explicit/inferred category and provider, calls the Rust DuckDuckGo result
  parser for engine categories, and emits the exact public envelope. Adapter-backed
  categories still fall back in the front door.
- Verified against the Python oracle on a general query; app replay 57/57,
  full pytest 11468 passed / 36 skipped.

### CLI `extract` local text body (done 2026-11-25)

- `gossamer extract` now emits the full extraction envelope for local
  text-like files (`.txt`, `.md`, `.csv`, `.json`) in Rust. Binary, structured,
  stored, ranged and JATS paths remain outside this slice.
- Verified against the Python oracle on text and JSON fixtures; app replay
  57/57, full pytest 11468 passed / 36 skipped.

### Provider call gate transition (done 2026-11-25)

- `ResourceAdapter._enforce_delay` now delegates quota rollover, quota verdict,
  politeness wait and post-call counter mutation to `gossamer::providers::base::provider_call_gate`.
  Clock, sleep and provider state mutation stay on the Python binding side.
- Binding: extended `rust/adapter-base-rs` to 13/13 cases; gate passed. Bench 0.63x
  (parity/transition kernel, not a speed port); 1 of 4 functions at least 1.1x.
- Guidelines: return `Result` instead of panicking (`M-PANIC-IS-STOP`); no unsafe
  (`M-UNSAFE`); `#[expect(clippy::too_many_arguments, reason = ...)]` mirrors the
  Python inputs exactly rather than broadening the API (`M-PANIC-ON-BUG` avoided).
- Full pytest 11468 passed / 36 skipped; app replay 57/57.

### CLI `download` live body (done 2026-11-23)

- `gossamer download` now streams bounded bytes, validates HTTP/PDF/size
  conditions, writes the destination, and emits the download envelope in
  Rust. Mirrors/resume remain outside this slice. Replay stays 57/57; live
  redirect/UA-dependent payloads are not replay-pinned.
- Full pytest 11467 passed / 36 skipped.

### CLI `batch` live body (done 2026-11-23)

- `gossamer batch` now fetches valid URLs through Rust and emits the same
  inspection-result shape per entry, while keeping exact rejection records
  for invalid/SSRF URLs. Browser strategies are not rendered here.
- App replay 57/57; full pytest 11467 passed / 36 skipped.

### CLI `inspect` live body (done 2026-11-23)

- `gossamer inspect` now fetches HTML, extracts markdown/links/metadata,
  applies chunked reads, and emits the full `InspectionResult` envelope in
  Rust. Cache/revalidation/structured/store modes remain outside this slice.
- Verified against the Python oracle on `https://example.com` (only cache hit
  state and timestamp differ by design); replay remains 57/57.
- Full pytest 11467 passed / 36 skipped.

### CLI `search` live DuckDuckGo body (done 2026-11-23)

- `gossamer search --search-only --provider duckduckgo` now fetches and parses
  DuckDuckGo HTML in Rust, with redirect unwrapping, title/snippet extraction,
  and URL dedupe. Empty-query and unknown-provider envelopes remain exact;
  live result sets are network-dependent and are not replay-pinned.
- App replay 57/57; full pytest 11467 passed / 36 skipped.

### CLI `search` empty-query body (done 2026-11-23)

- `gossamer search --search-only` now emits the canonical all-providers-failed
  envelope for an empty query instead of falling back. Real provider calls
  remain outside this slice. Added one deterministic replay case.
- App replay 57/57; full pytest 11467 passed / 36 skipped.

### CLI `crawl` validation body (done 2026-11-23)

- `gossamer crawl` now validates its root URL with exact crawl-prefixed error
  envelopes and SSRF stderr logging. The crawl loop remains outside this
  slice. Added two deterministic replay cases.
- App replay 56/56; full pytest 11467 passed / 36 skipped.

### CLI `research` validation body (done 2026-11-23)

- `gossamer research` now emits the taxonomy for an empty query and exact
  unknown-category envelopes in Rust. Live provider research remains outside
  this slice. Added two deterministic replay cases.
- App replay 54/54; full pytest 11467 passed / 36 skipped.

### CLI `extract` validation body (done 2026-11-23)

- `gossamer extract` now rejects incompatible image/store/structured flag
  combinations with exact JSON envelopes in Rust. Real extraction remains
  outside this slice. Added two deterministic replay cases.
- App replay 52/52; full pytest 11467 passed / 36 skipped.

### CLI `download` validation body (done 2026-11-23)

- `gossamer download` now validates output paths, URL normalization, and SSRF
  policy in Rust with exact error envelopes and stderr logging. Actual
  streaming downloads remain outside this slice. Added two replay cases.
- App replay 50/50; full pytest 11467 passed / 36 skipped.

### CLI `locate-pdf` command body (done 2026-11-23)

- `gossamer locate-pdf` now normalizes DOIs, queries OpenAlex, maps OA
  locations, and emits the public envelope in Rust. Unpaywall fallback and
  provider-specific error translation remain outside this slice. Added a
  deterministic invalid-DOI replay case.
- App replay 48/48; full pytest 11467 passed / 36 skipped.

### CLI `cite` command body (done 2026-11-23)

- `gossamer cite` now formats DOI/URL/JSON inputs with Rust citation kernels,
  including style validation and DOI/URL dedupe. PDF/enrich modes still fall
  back. Added two deterministic replay cases.
- App replay 47/47; full pytest 11467 passed / 36 skipped.

### CLI `discover` command body (done 2026-11-23)

- `gossamer discover` now fetches HTML/sitemaps, extracts feed alternates,
  follows bounded sitemap indexes, and emits the public envelope entirely in
  Rust. Added a deterministic blocked-URL replay case.
- App replay 45/45; full pytest 11467 passed / 36 skipped.

### CLI `check` command body (done 2026-11-23)

- `gossamer check` now probes URLs in the front door via Rust SSRF validation,
  `probe_liveness`, and the shared liveness envelope; no Python fallback.
- Added deterministic replay case `check-blocked`.
- App replay 44/44; full pytest 11467 passed / 36 skipped.

### Research provider registry lookup (done 2026-11-23)

- The closed 27-provider factory table and unknown-provider error text now
  live in `gossamer::service::research`; adapter instantiation stays
  Python-side. Restored the `max_results=0` PyO3 default after `refresh`.
- Binding: extended `rust/research-service-rs` to 11/11 cases; gate passed.
- Full pytest: 11467 passed / 36 skipped; app replay 43/43.

### JATS route planning (done 2026-11-23)

- Spreadsheet-suffix gating and JATS cache-key suffixing now live in
  `gossamer::document`; filesystem/network checks stay binding-side.
- Binding: extended `rust/documents-rs` to 15/15 cases; gate passed.
- Full pytest: 11466 passed / 36 skipped; app replay 43/43.

### Document filename sanitization (done 2026-11-23)

- `_sanitize_filename` now delegates platform-independent basename collapsing
  to `gossamer::document`; file I/O stays Python-side. Restored the generated
  Python candidate/tuple adapters after refreshing `documents-rs`.
- Binding: extended `rust/documents-rs` to 15/15 cases; gate passed.
- Full pytest: 11466 passed / 36 skipped; app replay 43/43.

### Agent URL helper planning (done 2026-11-23)

- Recoverable URL error records and crawl host keys now live in
  `gossamer::agent`; validation and locking stay binding-side.
- Binding: extended `rust/tool-bodies-rs` to 3/3 cases; gate passed.
- Full pytest: 11466 passed / 36 skipped; app replay 43/43.

### Citation enrichment planning (done 2026-11-23)

- `enrich_with_doi` now delegates venue/abstract hit selection to
  `gossamer::cite::enrichment_plan`; adapter calls stay Python-side.
- Binding: extended `rust/citation-dispatch-rs` to 17/17 cases; gate passed.
- Full pytest: 11465 passed / 36 skipped; app replay 43/43.

### In-text DOI detection (done 2026-11-23)

- `find_doi_in_text` now delegates DOI scanning and trailing-punctuation
  trimming to `gossamer::cite`.
- Binding: extended `rust/citation-dispatch-rs` to 15/15 cases; gate passed.
- Full pytest: 11465 passed / 36 skipped; app replay 43/43.

### Open-access candidate mapping (done 2026-11-23)

- OpenAlex and Unpaywall location mapping plus Unpaywall dedup/work metadata
  now live in `gossamer::openaccess`; provider HTTP calls stay Python-side.
- Binding: extended `rust/open-access-rs` to 24/24 cases; gate passed.
- Full pytest: 11465 passed / 36 skipped; app replay 43/43.

### Download finish validation (done 2026-11-23)

- `_validate_finished_file` now delegates size/PDF/HTML/bot-wall validation to
  `gossamer::downloader::validate_finished_file`; file streaming stays
  Python-side. A type-error case was recorded explicitly for gate coverage.
- Binding: extended `rust/downloader-rs` to 14/14 cases; gate passed.
- Full pytest: 11464 passed / 36 skipped; app replay 43/43.

### Download HTTP error classification (done 2026-11-23)

- `_http_error` now delegates status/code mapping and bounded bot-marker
  detection to `gossamer::downloader::http_error`; response streaming stays
  Python-side.
- Binding: extended `rust/downloader-rs` to 10/10 cases; gate passed.
- Full pytest: 11464 passed / 36 skipped; app replay 43/43.

### Download candidate planning (done 2026-11-23)

- `_download_candidates` now delegates input validation, candidate ordering,
  terminal-result detection and final error payload assembly to
  `gossamer::downloader`; actual downloads stay Python-side.
- Binding: `rust/downloader-rs` (7/7); gate passed. Restored the PyO3 default
  for `full_results` after recording.
- Full pytest: 11464 passed / 36 skipped; app replay 43/43.

### Embedded figure planning (done 2026-11-23)

- `ResourceStore.extract_embedded` now delegates filename selection, figure
  section rendering and manifest assembly to
  `gossamer::resourcestore::embedded_figures`; byte writes remain Python-side.
- Binding: extended `rust/resource-store-rs` to 18/18 cases; gate passed.
- Full pytest: 11463 passed / 36 skipped; app replay 43/43.

### Resource image rewriting (done 2026-11-23)

- `ResourceStore.extract` now delegates image-reference extraction and
  rewrite planning to `gossamer::resourcestore::{image_refs, rewrite_image_refs}`;
  downloading, SSRF validation and file writes remain Python-side.
- Binding: extended `rust/resource-store-rs` to 15/15 cases; gate passed.
- Full pytest: 11463 passed / 36 skipped; app replay 43/43.

### Resource magic detection (done 2026-11-23)

- `_detect_ext_from_magic` now delegates byte-prefix classification to
  `gossamer::resourcestore::detect_ext_from_magic`; the PyO3 wrapper decodes
  rust-anything's `$bytes` marker.
- Binding: `rust/resource-store-rs` (5/5); gate passed.
- Full pytest: 11463 passed / 36 skipped; app replay 43/43.

### Sitemap parsing kernel (done 2026-11-23)

- `_probe_sitemaps` now delegates XML parsing, namespace-safe `loc`
  extraction, URL absolutization and per-sitemap caps to
  `gossamer::sitemap::parse_sitemap`; fetching/validation/throttling stay
  in the toolbox.
- Binding: extended `rust/discovery-rs` to 21/21 cases; gate passed.
- Full pytest: 11462 passed / 36 skipped; app replay 43/43.

### Liveness HTTP probe (done 2026-11-23)

- Default `check_liveness` probing now runs through
  `gossamer::service::liveness::probe_liveness` (reqwest HEAD/GET with
  redirects and timeout). Injectable `request_fn`, SSRF validation and
  throttling remain Python-side; verdict mapping stays in Rust.
- Binding: extended `rust/liveness-rs` to 5/5 cases; gate passed. Verified
  live `https://example.com` returns HTTP 200 through the Rust probe.
- Full pytest: 11461 passed / 36 skipped; app replay 43/43.

### Crawl excerpt and query expansion (done 2026-11-23)

- `_crawl_excerpt` and `_crawl_expand_query` now delegate to
  `gossamer::service::crawl`; thesaurus clusters stay caller-supplied and
  tuple markers are preserved through the thin wrapper.
- Binding: extended `rust/crawl-kernels-rs` to 20/20 cases; gate passed.
- Full pytest: 11461 passed / 36 skipped; app replay 43/43.

### Crawl context kernels (done 2026-11-23)

- `_crawl_anchor_context`, `_crawl_path_prior` and `_crawl_term_hits` now
  delegate to `gossamer::service::crawl`; stopword tables and ranking are
  shared with the other crawl kernels.
- Binding: extended `rust/crawl-kernels-rs` to 12/12 cases; gate passed.
- Full pytest: 11460 passed / 36 skipped; app replay 43/43.

### Crawl document routing (done 2026-11-23)

- `_crawl_is_document` now delegates URL-path extension checks to
  `gossamer::service::crawl::crawl_is_document`; query/fragment stripping
  was pinned by replay.
- Binding: extended `rust/crawl-kernels-rs` to 8/8 cases; gate passed.
- Full pytest: 11459 passed / 36 skipped; app replay 43/43.

### Crawl token kernels (done 2026-11-23)

- `_crawl_tokens` and `_crawl_topic_words` now delegate tokenization,
  stopword filtering, TF ranking and deterministic tie-breaking to
  `gossamer::service::crawl`.
- Binding: extended `rust/crawl-kernels-rs` to 5/5 cases; gate passed.
- Full pytest: 11458 passed / 36 skipped; app replay 43/43.

### Crawl payload shrinking (done 2026-11-23)

- `_shrink_crawl` now delegates page-list capping and pretty serialization to
  `gossamer::service::crawl::shrink_crawl`.
- Binding: extended `rust/crawl-kernels-rs` to 2/2 cases; gate passed.
- Full pytest: 11457 passed / 36 skipped; app replay 43/43.

### XML local-name kernel (done 2026-11-23)

- `gossamer::xmlatom::xml_local_name` now backs `_local_name` namespace
  stripping used by scholarly adapters.
- Binding: extended `rust/research-service-rs` to 9/9 cases; gate passed.
- Full pytest: 11456 passed / 36 skipped; app replay 43/43.

### Citation dispatch kernels (done 2026-11-23)

- Citation style resolution and bare DOI/URL input shaping now delegate to
  `gossamer::cite::{citation_style, citation_input}`; formatters and
  enrichment stay on their existing Rust/Python paths.
- Binding: `rust/citation-dispatch-rs` (15/15); gate passed. The generator
  serialized a fully-qualified `Result` as JSON, so the thin wrapper was
  corrected to propagate the error directly (M-FFI-TRANSLATES).
- Full pytest: 11455 passed / 36 skipped; app replay 43/43.

### Metadata merge kernel (done 2026-11-23)

- `merge_into_document_metadata` now delegates field copies, title precedence,
  image list handling and whole-section passthroughs to
  `gossamer::metaextract::merge_into_document_metadata`.
- Binding: `rust/meta-merge-rs` (8/8); gate passed.
- Full pytest: 11454 passed / 36 skipped; app replay 43/43.

### Scholarly record merge (done 2026-11-23)

- `merge_scholarly_records` now delegates DOI/arXiv key extraction, grouping
  and conflict detection to `gossamer::research_merge`. No Python object
  reconstruction is needed; callers receive the same dict list.
- Binding: `rust/research-merge-rs` (6/6); gate passed.
- Full pytest: 11453 passed / 36 skipped; app replay 43/43.

### Batch entry normalization (done 2026-11-23)

- `_normalize_batch_results` now delegates success/failure tagging to
  `gossamer::documents::normalize_batch_results`; the Python side rebuilds
  frozen `BatchEntry` records and tuples.
- Binding: extended `rust/documents-rs` to 13/13 cases; gate passed.
- Restored custom `$tuple`/`FollowUpCandidate` Python wrappers after
  `refresh` regenerated the module (M-FFI-TRANSLATES).
- Full pytest: 11452 passed / 36 skipped; app replay 43/43.

### DOI normalization (done 2026-11-23)

- `gossamer::openaccess::normalize_doi` owns bare/`doi:`/resolver DOI shape
  validation and percent decoding; open-access HTTP lookup remains Python.
- Binding: `rust/open-access-rs` (15/15); gate passed. Immutable regex cache
  documented performance-only; panic message includes the regex source.
- Full pytest: 11451 passed / 36 skipped; app replay 43/43.

### Crawl frontier scoring (done 2026-11-23)

- `Crawler._crawl_score` now delegates legacy and semantic score formulas to
  `gossamer::service::crawl::crawl_score`. Tokenization, IDF lookup and URL
  path priors stay in the caller because they involve corpus state.
- Binding: `rust/crawl-kernels-rs` (3/3); gate passed.
- Full pytest: 11450 passed / 36 skipped; app replay 43/43.

### Provider rate-state header parsing (done 2026-11-23)

- `_rate_state_from_headers` now delegates to
  `gossamer::providers::base::rate_state_from_headers`; the returned payload
  is rebuilt into the existing `RateState` object so callers can still mutate
  `rps`/`exhausted`.
- Binding: extended `rust/research-service-rs` to 6/6 cases; gate passed.
- Restored the `max_results=0` PyO3 default after `refresh`.
- Full pytest: 11449 passed / 36 skipped; app replay 43/43.

### Per-domain throttle gap (done 2026-11-23)

- `_rate_limit_domain` now delegates the throttle gap/sleep decision to
  `gossamer::providers::base::throttle_gap`; lock, timestamps, robots lookup
  and sleeping remain in Python.
- Binding: extended `rust/caller-kernels-rs` to 18/18 cases; gate passed.
- Restored a Python-side `TypeError` guard for `web_search_route` so the
  recorded error path matches the generated PyO3 conversion (M-FFI-TRANSLATES).
- Full pytest: 11448 passed / 36 skipped; app replay 43/43.

### Liveness probe verdicts (done 2026-11-23)

- `gossamer::service::liveness::liveness_result` maps a completed probe
  outcome into the public `ok`/`unreachable`/`error` status dict.
- Binding: `rust/liveness-rs` (5/5); gate passed. Network probing, SSRF
  validation and throttling remain Python-side.
- Full pytest: 11447 passed / 36 skipped; app replay 43/43.

### Tool argument list coercion (done 2026-11-23)

- `ensure_str_list` now delegates to `gossamer::config::ensure_str_list`,
  including Python-shaped `TypeError` messages and tuple-marker handling.
- Binding: extended `rust/config-rs` to 30/30 cases; gate passed.
- Restored custom `FileNotFoundError`/`OSError` mappings after `refresh`
  regenerated the PyO3 error helper (M-FFI-TRANSLATES).
- Full pytest: 11445 passed / 36 skipped; app replay 43/43.

### Quota window rollover (done 2026-11-23)

- `gossamer::providers::base::quota_period` now labels UTC day/month quota
  windows for `_reset_quota_if_rolled`; time sampling stays in Python.
- Binding: extended `rust/adapter-base-rs` to 17/17 cases; gate passed.
- Full pytest: 11444 passed / 36 skipped; app replay 43/43.

### Retry decision policy (done 2026-11-23)

- `gossamer::providers::base::retry_decision` now owns the retry/raise/cap
  decision for the `@retry` decorator in `gossamer/search_providers.py`.
- Binding: extended `rust/adapter-base-rs` to 14/14 cases; gate passed.
- Full pytest: 11443 passed / 36 skipped; app replay 43/43.

### Research request planning (done 2026-11-23)

- `gossamer::service::research::research_plan` owns topic validation,
  depth clamping and plan-result budget clamping for `research()`.
- Binding: `rust/research-plan-rs` (3/3); gate passed; full pytest
  11442 passed / 36 skipped.

### Full two-tier cache (done 2026-11-23)

- `gossamer::cache::Cache` now backs `gossamer.cache.Cache` through a thin
  `PyCache` adapter. Python keeps a compatibility view for legacy `_memory`
  inspections; all hit/miss/LRU/disk/TTL/eviction behavior lives in Rust.
- Full pytest: 11441 passed / 36 skipped; app replay 43/43.
- Root `cargo clippy --all-targets` still has pre-existing stale adapter test
  imports; `cargo check`, workspace tests and workspace clippy are clean.

### Resource discovery (done 2026-11-23)

- `gossamer::discovery::find_feed_links` owns feed-alternate extraction and
  URL absolutization for `discover_resources`.
- Binding: `rust/discovery-rs` (1/1); gate passed; full pytest
  11441 passed / 36 skipped.

### `check_sources` input planning (done 2026-11-23)

- `gossamer::mcp_dispatch::check_sources_plan` owns mode validation,
  URL-list normalization and canonical deduplication.
- Binding: `rust/tool-check-rs` (3/3); gate passed; full pytest
  11440 passed / 36 skipped.

### Guard orchestration kernels (done 2026-11-23)

- `gossamer::guard::prepare_scope_pairs` and `gossamer::guard::merge_reports`
  now normalize scope text and assemble the additive guard block.
- Binding: `rust/guard-service-rs` (2/2); gate passed; full pytest
  11438 passed / 36 skipped; clippy clean.

### Research/search caller kernels (done 2026-11-23)

- `gossamer::jsonfmt::json_fallback` and `gossamer::service::search::provider_indices`
  now back `_json_fallback`, `_dedup_results` and `_resolve_providers`.
- Binding: `rust/research-kernels-rs` (5/5); gate passed; bench 1.38x.
- Full Python suite: 11436 passed / 36 skipped.

### FetchStats payload (done 2026-11-23)

- `gossamer::stats::fetch_stats_dict` owns the exact `FetchStats.to_dict()`
  payload, including percentile interpolation, Python one-decimal rounding,
  domain ordering and error-class ordering.
- Binding: `rust/stats-rs` (2/2); gate passed; bench 0.57x (correctness port).
- Python `FetchStats.to_dict()` delegates to the Rust seam.

### High-fan-in caller kernels (done 2026-11-23)

- Ported deterministic slices of the six ranked callers:
  `inspect_html_page` request routing, `extract_document` validation,
  `_enforce_delay`/`_init_rate_limit` policy kernels, `batch_inspect_pages`
  planning, and `web_search` routing/cache keys.
- Core: `gossamer::mcp_dispatch`, `gossamer::providers::base`,
  `gossamer::service::search`.
- Binding: `rust/caller-kernels-rs` (14/14); gate passed; bench 0.51x
  (correctness port; only `search_cache_key` is faster at 1.78x).
- Real methods now call the Rust seams; network/browser execution and side
  effects remain in Python.

### MCP tool-body kernels (done 2026-11-23)

- `gossamer::mcp_dispatch` now owns inspection routing, the unsupported
  `tables_as` error payload, and tool-argument default merging from the
  frozen 12-tool schema.
- Binding crate: `rust/tool-bodies-rs` (6/6); gate passed; bench 0.13x
  (correctness port only).
- Still in Python: real tool bodies, HTTP/browser calls and MCP transport.

### Units 4b, 5, 6-8 and provider-policy slice (done 2026-11-23)

- `gossamer::service::search`, `gossamer::service::research`,
  `gossamer::mcp_dispatch`, `gossamer::cli_router` and `gossamer::providers::base`
  now own deterministic orchestration kernels.
- Thin bindings: `rust/search-service-rs` (5/5), `rust/research-service-rs`
  (3/3), `rust/orchestration-rs` (4/4), `rust/adapter-base-rs` (7/7).
- Full Python suite after the provider-policy slice: 11424 passed / 36 skipped.
- Still in Python: HTTP/browser/provider transport, binary document extraction,
  MCP transport, full CLI execution, and the browser lane.

## Current state

- `dev_rustify` after U1: pure Rust front door lifted, replay 57/57, fallback 0, no fallback path.
- Rust owns: config resolution, documents payload helpers, deterministic FetchService kernels,
  deterministic `_core` kernels, `benchmarks`, `skills`, `audit-cargo`,
  `verify-bundled-assets`, the full two-tier `Cache`, `categories`, document URL rejection,
  search provider aliases/errors, CLI help/error text, MCP schema/tool-body envelopes,
  crawl/scoring/excerpt kernels, provider policy/rate-state kernels, research planning and
  merge kernels, download candidate/error/finish validation, OA candidate mapping, citation
  dispatch/enrichment/DOI scanning, and resource-store image/figure planning.
- Rust now owns substantial live CLI bodies: `check`, `discover`, `cite`, `locate-pdf`,
  download validation, extract validation, research validation, crawl validation, DuckDuckGo
  search, and static `inspect`/`batch`.
- Rust does not yet own: browser transport and fallbacks, binary PDF/Office extraction,
  full download filesystem side effects, MCP transport, remaining live tool bodies, the full
  crawl loop, research provider fan-out, and the final PyO3-wrapper retirement/cutover.
- Verification: 57 replay cases; Python suite is the behavioral oracle at 11,479 passing tests.

## Open questions

- Minimum supported Rust version and release packaging for the `gossamer` binary.
- Whether `rust/text-links-rs` gains a CLI surface when `documents` lands (ADR required).
