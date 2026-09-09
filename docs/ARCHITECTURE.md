# gossamer — architecture (v0.9.0)

How the system fits together, why it is split the way it is, and where
each behavior lives. Companion: `docs/QUICKREF.md` for the
command-level reference, `README.md` for the user manual.

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
| `adapters.rs` | all 35 provider row-builders (`*_parse_search/fetch`) + shared error/type helpers |
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
| `lib.rs` | PyO3 surface + shared Tokio runtime (`block_on`) |

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
- **Live smoke**: opt-in real-request drift detection, key-optional.
- **Hermetic default**: no network, SSRF guard on; full run
  `pytest -q -n auto --ignore=tests/test_live_smoke.py`.

## 11. Build & release

`maturin develop --release` builds `_core` in place
(`abi3`, LTO + stripped release profile). Versions move together
(`pyproject.toml`, `Cargo.toml` + lockfile) 0.0.1 per milestone with
a `CHANGELOG.md` entry; every bump is committed and pushed. The
`meta_oxide` crate arrives via git rev (fork) — PyPI-clean because no
Python direct reference remains. Docs rule: `docs/` keeps this file
plus `QUICKREF.md`; working notes live in gitignored `planning/`.
