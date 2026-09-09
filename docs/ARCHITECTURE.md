# gossamer — architecture (v0.9.1)

How the system fits together, why it is split the way it is, and where
each behavior lives. Companion: [Quick reference](./QUICKREF.md) for
commands, [README](../README.md) for the user manual,
[Changelog](../CHANGELOG.md) for per-version history.

## 1. Principles

1. **One contract, three surfaces.** MCP tools, CLI commands, and
   `execute_tool(name, args)` are generated/driven from a single
   `TOOL_REGISTRY` (`gossamer/config.py`). A tool exists once; adding a
   fourth surface would be mechanical.
2. **Parse in Rust, decide in Python.** Everything deterministic and
   side-effect-free (response parsing, text shaping, scoring math,
   safety scans) lives in `_core` (PyO3). Everything involving I/O,
   secrets, time, or policy (HTTP, keys, rate limits, retries, cache,
   orchestration) stays Python.
3. **JSON strings cross the boundary.** Rust functions take strings
   and return JSON strings. No Python objects cross into Rust and no
   Rust objects leak out. `raw` payloads are re-attached Python-side
   with `json.dumps`, so stored bytes are identical either way.
4. **Parity, not trust.** Every Rust kernel has a differential test
   against its vendored Python oracle plus seeded fuzz. A port is done
   only when the oracle agrees byte-for-byte, including error paths.
5. **Metadata is bonus, never fatal.** Extraction, enrichment, and
   guard verdicts degrade to empty/absent rather than failing the
   call that carries them.

## 2. Surface layer

### MCP server (`gossamer/mcp_server.py`)

Stdio server built on the `mcp` v2 SDK. Tools are registered by
iterating `TOOL_REGISTRY` (P8 — no hand-written per-tool functions),
each wrapper dispatching through `execute_tool`. A lazily-built
toolbox singleton is configured from `GOSSAMER_*` env
(`_config_from_env`). Entry points: `python -m gossamer.mcp_server`,
`gossamer-mcp` (plus the legacy `stitch-web-researcher-mcp` alias).

### CLI (`gossamer/cli.py`)

`argparse` over subcommands 1:1 with the registry
(`search research categories inspect batch extract check discover
crawl cache cite`). Entry points: `gossamer`, `python -m
gossamer.cli`. Prints the same JSON the MCP tools return.

### Skills (`skills/gossamer/SKILL.md`)

Teaches harnesses routing, budgets, auth, and config/cache locations.
Installed to the harness skills dir; the repo copy is the source of
truth.

### Tool registry (`gossamer/config.py`)

`ToolSpec(name, description, method, params)` + `ToolParam`
entries. `spec.kwargs(arguments)` fills registry defaults for omitted
optionals. This module also owns URL canonicalization
(`canonical_url`/`normalize_url`: `query=keep/drop/drop-tracking`),
which defines result-identity everywhere (dedupe, cache keys).

## 3. Toolbox + collaborators (Python)

`WebResearcherToolbox` (`agent_tools.py`) is a facade: thin methods
delegating to collaborator objects, all returning JSON strings.

| Collaborator | File | Owns |
|---|---|---|
| Fetch | `fetch.py` | static/browser dispatch, page cache reads, robots gate order (S4: robots before rate-limit so disallowed fetches burn no delay), conditional revalidation (ETag/Last-Modified via `Cache.get_stale`; 304 re-freshens, 200 replaces), fetch telemetry choke point |
| Search | `search.py` | provider failover/merge, result-level cache, within-provider dedup |
| Crawl | `crawl.py` | priority-frontier BFS over the link graph (§6) |
| Documents | `document.py` | bytes → text via oxide converters, `pages=` slicing, `store=` persistence, figure extraction |
| Discovery | `discovery.py` | feed declarations + bounded `/sitemap.xml` probe |
| Search engines | `search_providers.py` | `SearchProvider` ABC + DDG/Google/Bing/Exa (DDG HTML parsing lives here — the one engine kept Python) |
| Domain adapters | `research_providers.py` | 35 scholarly/legal/patent/financial/geo adapters on one politeness/quota contract; URL/params/keys/rate/retry in Python, row-building in Rust |
| Routing | `research_categories.py` | keyword classifier (Euro terms folded in, no separate category) + provider factories |
| HTML meta | `meta_extractor.py` | `_core` kernels first, legacy `meta_oxide` package as last-resort fallback, then empty |
| Structured docs | `structured_parser.py` | Pydantic v2 schemas + `StructuredOxideParser` (PDF/office/HTML) |
| Models | `models.py` | result models, provenance dicts, fetch stats, batch records |
| Tokens | `token_budget.py` | thin accounting over the in-core encodings |
| Cache | `cache.py` | two-tier memory-LRU + disk-TTL (§7) |
| Assets | `resource_store.py` | downloaded + embedded file assets for stored content |
| Guard | `guard.py` | optional JailGuard ONNX detector (§9) |
| Citations | `citations.py` | BibTeX/CSL-JSON/APA/MLA reconstruction over ported kernels |
| Budgets | `budget.py` | output-budget enforcement with link-budget reservation |
| Sections/links | `sections.py`, `text_links.py` | markdown shaping + `![]()`/bare-URL detection |
| Net safety | `ssrf.py`, `robots.py`, `liveness.py`, `dedup.py` | SSRF allow-listing, robots compliance, reachability probes, result dedupe |
| Secrets/config | `env.py`, `settings.py`, `keystore.py` | `GOSSAMER_*` (legacy `STITCH_*` honored), keystore, `gossamer.json` (§7) |

## 4. Rust core (`src/`, `_core`)

### Module map

| Module | Kernels |
|---|---|
| `adapters/` | all 35 provider row-builders (`*_parse_search/fetch`): `common` (shared error/type helpers), `finance`, `legal`, `scholar`, `patents`, `misc`, `tests` — `mod.rs` re-exports keep every `crate::adapters::*` path stable |
| `metaextract.rs` | HTML metadata via the `meta_oxide` crate FFI + `sparse()`/normalizers matching `to_py_dict` shapes |
| `xmlatom.rs` | ATOM/XML traversal (arXiv/PubMed), SDMX-ML (Bundesbank/BIS), namespace-URI resolution |
| `ssrf.rs` | IP/DNS allow-list logic mirroring CPython `ipaddress` tables |
| `robots.rs` | robots.txt parser (fetch half stays Python) |
| `guard.rs` | prompt-injection regexes/heuristics, byte-identical scans |
| `cite.rs` | BibTeX/CSL-JSON/APA/MLA formatters |
| `categories.rs` | keyword routing tables |
| `tokens.rs` | registry-first encodings + 6 embedded BPEs (`tiktoken-rs`) |
| `budget.rs` | truncate/fit/shrink with `ensure_ascii` fidelity rules |
| `sections.rs` | ATX+Setext sectioning, bit-identical BM25 |
| `dedupe.rs` | dedupe planning over canonical URLs |
| `textlinks.rs`, `urls.rs` | link-text scan, normalize/canonical/hash |
| `cacheutils.rs` | disk-key (blake2b-128), human sizes, content-type ext, safe names |
| `miscutils.rs` | domain-of, sha256-hex, link classification, page ranges |
| `pycompat.rs` | `py_strip`/`py_repr`/`char_head`/`char_slice`/splitlines — CPython string semantics Rust lacks |
| `lib.rs` | `#[pymodule] _core` registry only (all paths explicit) |
| `fetch.rs` | blocking HTTP transport: shared Tokio runtime (`block_on`), client singleton, overrides, SSRF-net, HTML stripping, retry |
| `bridge.rs` | the 10 fetch `#[pyfunction]` wrappers + logging/tables served through `_core` |

### Boundary rules (normative for new ports)

1. **Signatures take/return strings.** JSON in, JSON string out.
   Native ints/floats only for leaf helpers (`human_size`,
   `parse_page_range`); dicts/lists never cross as objects.
2. **Wrappers own fallibility order.** The first fallible expression
   (JSON parse, `ET.fromstring`, `.encode`/`.split` type gates) runs in
   Python so malformed inputs raise the original exception type from
   the original place.
3. **Error mapping is exact.** Kernel `Err(String)` values carry a
   `Kind: message` prefix decoded to `AttributeError`/`TypeError`/
   `ValueError`/`KeyError`/`IndexError`/`RuntimeError` with identical
   text, including CPython quirks (`KeyError` slice reprs,
   dict-iteration order effects, eager double-pops).
4. **Truthiness and slicing are Python's.** `is_truthy`, negative
   index clipping, char (never byte) slicing, strip-sets — all in
   `pycompat`, never ad hoc.
5. **Bytes stay out.** Magic-byte sniffing, file I/O, wall-clock
   time, randomness, and network stay Python. Known crossings that
   cannot work: lone surrogates (invalid UTF-8), NaN payloads,
   non-JSON-native ids (arrive `str()`-rendered).

## 5. Provider system

Each adapter implements `search(query, max_results)` and
`fetch(record_id)` against `ResourceAdapter`: politeness delay +
jitter per domain, retry with backoff, SSRF-checked URLs, and a
`requires_key` contract — keyed adapters fail fast naming the exact
`GOSSAMER_*` variable instead of burning retries. Fail-fast credential
checks run before any network. Categories (`research_categories.py`)
map free text to a provider list, keyless-first; `patent` is
`epo` → `kipris` → `patentsview`. Retired fictional endpoints stay
deleted (no guessing); endpoint drift is caught by the opt-in live
smoke suite (`GOSSAMER_LIVE=1 pytest tests/test_live_smoke.py`).

## 6. Crawl scoring

Bounded BFS with a priority frontier popping `score × 0.7^depth`
(ties → discovery order, degrading to plain BFS on flat scores).
`score = 0.7 × cover(label, query) + 0.3 × cover(label, page_topic)`;
the label is anchor text + path tokens + ±50-char surrounding content
words (≤8 tokens); page topics come from full delivered text. Term
weights are BM25 idfs over the live corpus (flat until 3 pages read);
the offline thesaurus (`thesaurus.json`, ~230 terms) expands the
query at half weight (≤2× base). Doc-ish paths ×1.15, transactional
×0.85. Budgets: `max_pages` (15/50), `max_depth` (3/5); boilerplate
and asset URLs skip free; below-`min_score` skips are reported, never
silent. Document links are collected (`{url, anchor, score}`), never
fetched. Every page stays in cache for later full re-reads.

## 7. State: cache, assets, secrets

- **Cache** (`Cache`): tier 1 memory LRU (`cache_memory_entries`,
  default 100) with per-entry timestamps; tier 2 disk (`<blake2b>.cache`
  + `<blake2b>.meta`) with TTL (`cache_ttl_seconds`, default 3600),
  mtime-touch LRU, optional byte cap (`cache_max_bytes`, 0 =
  unlimited) with oldest-first eviction; `get_stale` reads ignoring
  TTL for revalidation; `prune` sweeps expired + enforces the cap +
  drops crash-leftover `*.tmp`; `clear` only removes cache-owned
  suffixes (never the directory — it is user-configurable and
  LLM-invocable). Disk writes are temp-file + `os.replace` (atomic on
  POSIX and Windows). Thread-safe (`_lock` + `_disk_lock`).
- **Assets** (`ResourceStore`): `extract` downloads `![]()` refs;
  `extract_embedded` writes caller-supplied bytes (PDF figures) and
  appends `## Figures`. Filenames are slugged (`_safe_name`, ≤80
  chars, `asset` fallback).
- **Secrets/config**: env `GOSSAMER_*` > legacy `STITCH_*` > keystore
  (`$GOSSAMER_KEYSTORE` > `gossamer.json:keystore` >
  `~/.gossamer/keys.json`, 0600) > `gossamer.json` `"keys"` >
  default. Config files: explicit > `$GOSSAMER_CONFIG` >
  `./gossamer.json` > `~/.gossamer/config.json`. `--check` never
  prints secrets.

## 8. Budgets and provenance

Every payload passes `budget.py`: `max_markdown_chars` (8000) and
`max_tokens` (0 = unlimited) truncation, with `link_budget_ratio`
(0.25) reserved so follow-up links survive content pressure; later
sources yield first in multi-source responses. Provenance
(`fetched_at`, `http_status`, `final_url`, `content_type`, validators
for revalidation) rides alongside, never inside the LLM payload.
`get_stats()` exposes fetch telemetry (p50/p95/p99 over a bounded
1024-sample window) plus cache and guard counters.

## 9. Guard (optional, off by default)

`GuardConfig(enabled, mode, scopes, threshold, max_chunks)`: the
JailGuard ONNX detector scans untrusted scopes in overlapping
~256-token windows (no seam-hiding), verdicts cached by
`sha256(chunk)`. Modes: `annotate` (default — attach verdict +
`<untrusted-web-content>` wrapper), `redact` (placeholder spans),
`block` (withhold content, never cached). Fails open when the model
is absent (`risk: None`). No import, no latency when disabled.

## 10. Testing

- **Parity suites** (`tests/test_rust_parity_*.py`): oracle vs Rust,
  edge matrices, seeded fuzz, hostile inputs, plus Rust `#[test]`
  shape tests. The suite is the port contract.
- **Behavioral suites**: one file per surface, auto-grouped into
  `area_*` markers by filename (`tests/conftest.py`).
- **Source pins** (`test_m9_http_pool.py`, `test_m15_retry_after.py`):
  assert on the Rust transport source itself (client singleton,
  retry-after branch) — they read `src/fetch.rs` since the `lib.rs`
  split (registry / transport / bridge).
- **Live smoke**: opt-in real-request drift detection, key-optional.
- **Hermetic default**: no network, SSRF guard on; full run
  `pytest -q -n auto --ignore=tests/test_live_smoke.py`.

## 11. Document pipeline

`extract_document` accepts a URL or local path. URLs are SSRF-checked,
robots-gated, rate-limited, and downloaded under `max_response_bytes`
(Content-Length early-reject + streaming chunk cap); locals are read
under the same cap. Bytes route by suffix (`classify_link` must stay
in sync): PDF via `pdf_oxide`, office via `office_oxide`, plain text
(CSV/TXT/MD, pretty-printed JSON), XML/RSS/Atom (feeds become entry
lists, other XML falls back to raw text), extension-less URLs via
Content-Type sniffing. Tables render as markdown tables by default on
both converters — no flag exists or is needed.

- `pages="10-20"` selects PDF pages / XLSX sheets through the
  structured parser (per-page blocks); cached under a range-specific
  key; cannot combine with `store=`.
- `structured=True` returns a validated `ParsedDocumentPayload`
  (`DocumentMetadata`, `ExtractedPage[]`, flattened `tables`).
- `store=True` writes `<stem><ext>` (verbatim bytes) + `<stem>.md`
  (full untruncated markdown) under `store_dir` (default
  `stored_documents/`), with `stored.resources` reporting downloaded
  refs; `store=True, include_images=True` (PDF only) additionally
  saves raster figures as `<stem>.files/page{i}_{j}.png` (capped at
  200, vector-only figures skipped) with a `## Figures` section.
- Document text runs link detection (bare `www.` promoted, Latin/CJK
  trailing punctuation stripped, deduped, capped at 50) into `links`,
  so reports yield follow-up targets despite no hyperlink annotations.

## 12. Async & threading model

Every blocking toolbox method has an `*_async` twin
(`search_web_async`, `inspect_html_page_async`,
`batch_inspect_pages_async`). "Async" means **thread pool**: each
wrapper offloads the shared blocking implementation to Python's
default executor (`loop.run_in_executor(None, …)`) so the event loop
stays responsive. The underlying network I/O is synchronous — there
is no native async I/O in the fetch/search layer (the Tokio runtime
in `_core` serves the Rust fetch primitives the Python layer drives
synchronously). Shared mutable state is lock-guarded (`Cache` tiers,
`FetchStats`, per-domain rate state, in-flight/visited sets) because
the MCP SDK dispatches tools on worker threads. Use the async twins
inside `asyncio` apps; call sync methods otherwise.

## 13. Observability

`get_stats()` reports a `fetches` section from the single
dispatch choke point: totals, error counts, bytes, p50/p95/p99/max
latency over a bounded 1024-sample sliding window (`fetch_stats_window`),
per-domain and per-error-class breakdowns, plus cache and guard
counters. Rust-side HTTP logging is off (zero cost) until
`GOSSAMER_RUST_LOG=error|warn|info|debug` bridges Rust `log` events
into Python `logging` (one-time init record confirms liveness).

## 14. HTTP transport overrides

For authenticated/proxied sources, the static fetch path bakes
process-level overrides into the lazily-built shared client at first
use (last non-empty value wins — singleton, not per-request):
`http_proxy` / `user_agent` / `custom_headers` / `cookies` (all with
`GOSSAMER_*` env spellings). Invalid values log and are ignored, never
fatal. Robots, politeness delay, and per-host concurrency apply on top.

## 15. Search result caching & merge

Successful searches cache in-memory (bounded, TTL =
`cache_ttl_seconds`) keyed by normalized
`(query, max_results, provider, merge-mode)`; errors never cache and
`clear_cache` wipes it. Within-provider URL dedup is default
(canonical form: scheme case, default ports, fragments, trailing
slash). `search_merge=True` (or `GOSSAMER_SEARCH_MERGE=1`) queries
every provider in priority order and merges + dedupes up to
`max_results` instead of strict first-success failover.

## 16. HTML tables & metadata

`inspect_html_structured` runs raw static HTML through the in-core
`extract_tables_from_html`: top-level tables only, `<th>`-first-row
headers, colspan/rowspan expanded to rectangular grids, markup-free
cells (whitespace collapsed, ≤1000 chars), caption-or-`table-N`
names, ≤20 tables / ≤500 rows per page. Best-effort (failure →
`tables: []`); the browser path exposes no DOM so its tables are
empty; the plain page path is untouched. Page metadata comes from
`meta_extractor`: `extract_all` returns meta/opengraph/twitter/
jsonld/microdata/microformats/dublin_core/rdfa/rel_links/oembed/
manifest sections, merged into `DocumentMetadata` (base values win;
raw sections pass through verbatim).

## 17. Discovery & research orchestration

`discover_resources` fetches once: `<link rel="alternate">` feed
declarations (RSS/Atom/Feed-JSON only, `hreflang` ignored, hrefs
absolutized) plus a bounded `/sitemap.xml` probe (indexes to 3 hops,
≤10 fetches, 500 URLs/sitemap, 1000 total, ordered dedupe,
`truncated` on cap hit). Metadata-level only — the page stays
unvisited. `research(topic, depth, …)` chains plan → dedupe → fan-out:
up to `depth*2` candidates (cap 20), normalized/deduped/SSRF-validated
to ≤ `depth` pages (cap 10), each through the normal pipeline (cache
--; repeated runs are cheap), returning per-source
`url/title/snippet/status` + payload-or-error under the global
budget (later sources yield first). Synthesis stays the agent's job.

## 18. Semantic discovery & link detection

Beyond the §6 frontier: term weights sharpen as the corpus grows
(flat until 3 pages), anchor labels gain ±50-char surrounding content
(≤8 tokens), doc-ish URL paths score ×1.15 vs ×0.85 transactional,
and `thesaurus.json` (~230 terms, 31 clusters, no generic tokens)
expands the query at half weight (≤2× base, deterministic order,
fail-open load). The query echo reports expansions (`"deep learning
+2"`). Document link detection (also §11) covers the Oxide blind
spot on hyperlink annotations.

## 19. Dependencies

| Layer | Package | Role |
|---|---|---|
| Rust | `pyo3 0.27` (abi3) | bindings |
| | `reqwest 0.12` + `tokio` | static fetch primitives |
| | `scraper`, `html2md` | HTML parse → markdown |
| | `serde/serde_json`, `quick-xml` | records, feeds |
| | `tiktoken-rs` | in-core encodings |
| | `meta_oxide` (git fork, no default features) | metadata, in-core |
| | `regex`, `blake2`, misc | scans, hashes, shims |
| Python | `httpx`, `pydantic>=2.7`, `tiktoken`, `ddgs` | providers, schemas, search |
| Oxide | `pdf_oxide`, `office_oxide` (`[documents]`) | document converters |
| Opt | `mcp` (`[mcp]`), `browser-oxide` (`[browser]`), `jailguard` (`[guard]`) | server, JS rendering, guard model |

All MIT/Apache-2.0. No Python direct (git) references remain — the
wheel is PyPI-clean.

## 20. Build & release

`maturin develop --release` builds `_core` in place
(`abi3`, LTO + stripped release profile). Versions move together
(`pyproject.toml`, `Cargo.toml` + lockfile) 0.0.1 per milestone with
a `CHANGELOG.md` entry; every bump is committed and pushed. The
`meta_oxide` crate arrives via git rev (fork) — PyPI-clean because no
Python direct reference remains. Docs rule: `docs/` keeps this file
plus `QUICKREF.md`; working notes live in gitignored `planning/`.
