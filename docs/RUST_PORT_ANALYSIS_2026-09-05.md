# Rust Port Analysis — gossamer (2026-09-05, `dev_rust` kickoff)

Question: how much of the ~17.1k-line Python package can move into Rust
(`gossamer-core`) leaving a thin PyO3 layer?

Short answer: **~80% (~13–14k lines).** The codebase is already shaped for
it — JSON-string service boundaries, a shared tokio runtime + warm reqwest
client in `_core`, and the heaviest extraction work already delegated to
Rust-backed crates (`pdf_oxide`, `office_oxide`, `meta-oxide`,
`browser-oxide`). What must stay Python is the harness surface: MCP server
(Python `mcp` SDK), the toolbox facade, and the CLI.

## 1. What Rust already owns (no work)

`src/lib.rs` (~1.5k lines) already provides, over a global tokio runtime
with connection reuse and test HTTP overrides:

- HTTP substrate: `fetch_and_extract`, `fetch_html_full`,
  `fetch_html_conditional`, `fetch_and_extract_linked`, `batch_research`
  (reqwest + gzip/brotli/deflate).
- HTML pipeline: `extract_main_content_markdown` (html2md),
  `extract_links_from_html`, `extract_tables_from_html` (scraper).
- Plumbing: `configure_http`, `init_rust_logging`.

Sibling Rust-backed packages (already consumed from Python, become direct
crate deps in a full port): `pdf_oxide` + `office_oxide` (binary documents
via `structured_parser`), `meta-oxide` (metadata, optional),
`browser-oxide` (rendered fetch, optional). **Assumption to verify:** all
four must be usable as Rust crates (crates.io or vendored git deps), not
just as the Python wheels we consume today.

## 2. Module-by-module verdict

### Phase 1 — pure logic, portable in days (no I/O, stdlib-only)

| Module | Lines | Notes |
|---|---|---|
| `ssrf.py` | 134 | IP/DNS allow-list logic → `std::net` (already used in lib.rs) |
| `dedup.py` | 149 | URL canonicalization + hashing; pure |
| `budget.py` | 162 | Token/byte budgets; pure |
| `sections.py` | 232 | Markdown sectioning; pure |
| `text_links.py` | ~? | Link-text detection; pure |
| `robots.py` | 302 | Parser is pure; only the fetch half needs reqwest |
| `guard.py` | 606 | Prompt-injection regexes/heuristics; pure, must stay byte-identical |
| `citations.py` | 576 | Citation formatting (bibtex/CSL/APA/MLA); pure |
| `research_categories.py` | 515 | Keyword tables + routing; pure (adapter factories stay Python or become a Rust registry) |
| `models.py` | 396 | pydantic → `serde` structs + PyO3 getters |
| `token_budget.py` | 276 | Needs `tiktoken-rs` (o200k_base ranks); **verify count parity** against tiktoken first |
| `structured_parser.py` | 882 | Page ranges, table shaping; thin over oxide crates |

Differential-test strategy: property/fixture tests comparing Python vs Rust
outputs byte-for-byte (guard scans and token counts must match exactly;
markdown shaping should too).

### Phase 2 — I/O with a uniform shape (the long tail, mechanical)

| Module | Lines | Notes |
|---|---|---|
| `research_providers.py` | 3673 (~35 adapters) | Each adapter is URL-build → GET → JSON/XML parse → normalize. Uniform enough for a shared `Adapter` trait + `serde_json`/`quick-xml`; pilot with one keyless (Open-Meteo) + one keyed (EPO) against mocked HTTP, then batch the rest. Secrets via a Rust resolver (env + keystore file — easy). |
| `search_providers.py` | 830 | `ResourceAdapter` base (retry/backoff, rate-limit) ports directly; `ddgs` engine needs reimplementation (DDG HTML parsing via reqwest+scraper) — moderate, or keep this one engine Python. |
| `search.py` / `discovery.py` | 419 / 251 | Orchestration over fetch+parse; portable. |
| `crawl.py` | 981 | Frontier, BM25 scoring, `thesaurus.json` (embed with `include_str!`); portable, keep scoring formula identical. |
| `fetch.py` | 1336 | Policy layer (provenance, slicing, revalidation, page cache) over the fetchers `_core` already has. Port or keep as the thin layer — either is defensible. |
| `document.py` | 1030 | Orchestration over pdf/office oxides + stdlib feed parsing; portable. |
| `liveness.py` | 153 | HTTP probes → reqwest; portable. |

### Phase 3 — stateful (port with compat constraints)

| Module | Lines | Notes |
|---|---|---|
| `cache.py` | 458 | File/SQLite layout must stay readable by both sides during transition → version the cache dir, or port atomically with a migration. |
| `resource_store.py` | 323 | Same constraint as cache. |
| `env.py` / `settings.py` / `keystore.py` | ~450 total | Trivially portable (env + JSON files), but also fine to keep in Python — the Rust side only needs a secret/option resolver. Recommend: port the *resolver*, keep file *discovery* where it is until Phase 4. |

### Stays Python (the thin layer, ~2k lines)

| Module | Lines | Why |
|---|---|---|
| `mcp_server.py` | 311 | Python `mcp` SDK. Rust `rmcp` exists but re-doing registration/schemas buys nothing. |
| `agent_tools.py` | 1254 | Becomes the facade: config builders + thin wrappers returning the same JSON strings. |
| `cli.py` | 180 | argparse is fine; a `clap` binary is an optional end-state (Python-free `gossamer`), not a requirement. |
| `__init__.py` | 157 | Re-exports. |

## 3. Target architecture

- `gossamer-core` (Rust lib): HTTP client, SSRF/robots, cache, fetch
  pipeline, extract pipeline (HTML/MD/tables/links/metadata/documents),
  crawl frontier + scoring, all domain adapters, budgets, guard, citations,
  categories, secret/option resolver.
- PyO3 layer: config passthrough + service facades. **Keep the existing
  JSON-string boundary** — it is what makes the layer thin and is already
  the harness contract (MCP and CLI both speak JSON).
- Python: MCP server, toolbox facade, CLI, exports. No logic except
  harness adaptation.

## 4. Ordering (risk-ordered)

1. **Phase 1** pure modules with differential tests (days, zero behavior risk).
2. **Adapter trait + 2 pilot adapters** with mocked-HTTP parity tests.
3. **Adapter batches** (mechanical; biggest line count, lowest risk per unit).
4. **Orchestration** (fetch/document/crawl/search) on top of the ported pieces.
5. **Stateful** (cache/store with migration) + resolver.
6. **Optional end-state**: `rmcp` server / `clap` binary; Python becomes a shim.

## 5. Risks

- **Parity, not performance, is the risk**: retry/backoff timing, rate-limit
  windows, robots edge cases, BM25/threshold constants. Mitigation: the
  existing mocked-shape tests + live smoke suite become the parity oracle;
  record live fixtures where feasible.
- **`tiktoken-rs` count parity** must be verified before porting budgets.
- **Oxide crates as Rust deps** (see §1 assumption) — confirm before Phase 2.
- **PyPI story is orthogonal**: the port does not fix publishing (the
  `meta-oxide` git dep does that); but a mostly-Rust core makes future
  `gossamer` wheels *more* shippable, not less.
- Keep `abi3` + the shared runtime; every new blocking call goes through
  `shared_runtime().block_on`, never a fresh runtime (deadlock risk).
