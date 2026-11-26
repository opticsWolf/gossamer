# Remaining Rust port implementation plan

Status: planning document, written 2026-11-25 on `dev_rustify` after `7d6ac4f`.
Source of truth for decisions: `rust/PORT.md`. This document is an execution plan,
not a replacement for `rust/PORT.md`; after each completed unit, update both.

## 1. Executive summary

The pure Rust front door is lifted and the recorded app replay is **57/57**, but
that replay only covers a frozen deterministic surface. CodeRadar still shows a
large amount of live orchestration in Python, and the Rust CLI still returns
`gossamer: error: unsupported command shape` for several documented flags.

The next work should follow the core-first order already agreed in `rust/PORT.md`:

1. deterministic kernel in `rust/core`;
2. thin PyO3 compatibility seam where Python callers still need it;
3. recorded parity cases from existing tests plus explicit error-path inputs;
4. `record → build → diff → bench → gate → enable`;
5. Rust CLI or MCP caller wired to the kernel;
6. `rust-guidelines` review, CodeRadar reindex, full pytest, app replay, commit.

The single highest-value gap is **`ResourceAdapter.search`**: CodeRadar ranks it
with **99 dependents**, the largest non-vendor fan-in in the graph. The next
unit should be live provider dispatch, not more isolated helper kernels.

## 2. Evidence collected with CodeRadar

Store was reindexed before analysis:

```text
modules:     665
classes:    1096
functions:  9770
call edges: 17566
```

The annotation on `gossamer/cli.py::main` remains current and points to
`rust/PORT.md`:

```text
annotation:gossamer/cli.py::main:1
```

### 2.1 Highest fan-in non-vendor entities

| Callers | Entity | Current Rust position |
| ---: | --- | --- |
| 99 | `gossamer/search_providers.py::ResourceAdapter.search` | Policy kernels only; live dispatch still Python |
| 89 | `gossamer/agent_tools.py::WebResearcherToolbox.inspect_html_page` | Static CLI envelope only; transport/cache/browser path still Python |
| 86 | `gossamer/agent_tools.py::WebResearcherToolbox.extract_document` | Local text/XML/errors/store in CLI; binary/paged execution still Python |
| 83 | `gossamer/search_providers.py::ResourceAdapter._enforce_delay` | Deterministic gate ported; clock/sleep/state still Python |
| 48 | `gossamer/search_providers.py::ResourceAdapter._init_rate_limit` | Rate-state parsing/planning ported; object mutation still Python |
| 37 | `gossamer/agent_tools.py::WebResearcherToolbox.batch_inspect_pages` | Static batch envelope only; side effects still Python |
| 29 | `gossamer/agent_tools.py::WebResearcherToolbox.search_web` | DuckDuckGo CLI path only |
| 19 | `gossamer/agent_tools.py::WebResearcherToolbox.inspect_html_structured` | Not wired in Rust CLI |
| 19 | `gossamer/agent_tools.py::WebResearcherToolbox.research` | DuckDuckGo/general envelope only; provider fan-out still Python |
| 17 | `gossamer/agent_tools.py::WebResearcherToolbox.discover_resources` | CLI body owns HTML/sitemap/feed fetch; toolbox path still Python |
| 13 | `gossamer/agent_tools.py::WebResearcherToolbox.check_sources` | CLI body owns status/content probes |

### 2.2 Complexity risks from CodeRadar

These are the places where a monolithic port would be unsafe. Each must be split
into deterministic kernels before moving the caller.

| Complexity | Lines | Entity |
| ---: | ---: | --- |
| 68 | 554 | `gossamer/crawl.py::Crawler.crawl` |
| 61 | 342 | `gossamer/downloader.py::DownloadService.download_file` |
| 31 | 230 | `gossamer/fetch.py::FetchService.batch_inspect_pages_impl` |
| 29 | 233 | `gossamer/document.py::DocumentExtractor.extract_document` |
| 27 | 183 | `gossamer/open_access.py::OpenAccessLocator.locate` |
| 27 | 161 | `gossamer/document.py::DocumentExtractor._try_jats_route` |
| 25 | 212 | `gossamer/fetch.py::FetchService._inspect_html_page_impl` |

Rust app counterparts with high complexity that should be refactored as they
gain more flags:

| Complexity | Lines | Entity |
| ---: | ---: | --- |
| 50 | 282 | `rust/app/src/units/extract.rs::run` |
| 37 | 260 | `rust/app/src/units/crawl.rs::run` |
| 32 | 125 | `rust/app/src/units/cite.rs::run` |
| 30 | 197 | `rust/app/src/units/download.rs::run` |
| 24 | 146 | `rust/app/src/units/discover.rs::run` |

### 2.3 Additional issues found by `diagnose --unresolved --low-confidence`

Most unresolved references are expected dynamic external calls or generated
driver code. Two groups are genuine port gaps because they enumerate the state
that is still owned by Python:

- `gossamer/document.py::DocumentExtractor._inspect_html_structured_impl`
  calls Python-side `cache`, `visited`, `in-flight`, robots, rate-limit, URL
  preparation, budget fitting, and guard scanning.
- `gossamer/fetch.py::FetchService.batch_inspect_pages_impl`
  calls Python-side `cache`, `visited`, `in-flight`, robots, rate-limit, URL
  preparation, identity keys, and metadata extraction.

These confirm that structured inspect and batch are not finished by writing
more formatting kernels; the caller-side state machine must be ported too.

### 2.4 Dead-code and clone findings

- `dead-code` is dominated by vendored C#/Java/Python tests and is noise for
  this port.
- One relevant finding: `gossamer/research_providers.py::_service_normalize_research`
  is unreachable. It is a generated seam that was never wired. Clean it up only
  after confirming no recorded case depends on it.
- `find-clones` at 0.85 similarity found vendor test duplication, not meaningful
  application duplication. No clone-driven refactor is planned.

### 2.5 Rust/Python surface counts

```text
functions under root src/ (legacy PyO3 wrappers): 206
functions under rust/app/src/units/:                96
functions under rust/core/src/:                    820
provider classes in gossamer/research_providers.py: 40
parser kernels already in rust/core/src/adapters/: ~170
```

The provider port should reuse the ~170 parser kernels in `rust/core/src/adapters`
rather than porting 40 provider classes as independent units.

### 2.6 Current Rust CLI unsupported-shape map

Source scan of `rust/app/src/units/*.rs` found these real contract gaps:

| Command | Unsupported shape |
| --- | --- |
| `inspect` | `--structured` |
| `crawl` | `--search-prior` |
| `download` | `--resume`, `--try-mirrors` |
| `cite` | `--enrich`, `--from-pdf` |
| `research` | any provider other than DuckDuckGo |
| `extract` | binary/PDF/Office formats, paged extraction, `tables-as` other than JSON, URL `--store` |
| `batch` | browser strategy (`--use-smart browser`) |
| `search` | provider live calls beyond DuckDuckGo HTML |

Most other `return unsupported()` calls are malformed-argument guards; they are
acceptable only if they match Python argparse errors. Any supported documented
flag that still returns the generic unsupported message is a port gap.

## 3. Coverage matrix

| Python surface | Rust today | Missing |
| --- | --- | --- |
| CLI help/errors | Frozen and replayed | none known |
| CLI `check` | Full status/content probe | none known |
| CLI `discover` | HTML/sitemap/feed fetch | full toolbox-side resource discovery |
| CLI `cite` | DOI/URL/JSON formatting | `--enrich`, `--from-pdf` |
| CLI `locate-pdf` | OpenAlex live lookup | full OpenAccessLocator flow |
| CLI `download` | Bounded HTTP, validation, errors | mirrors, resume, streaming/multi-candidate loop |
| CLI `extract` | local text/XML/RSS, URL errors, store | binaries, pages, tables-as CSV, URL store |
| CLI `research` | taxonomy + DuckDuckGo category results | provider fan-out |
| CLI `crawl` | bounded link graph | search prior, seeds, robots/cache side effects |
| CLI `search` | DuckDuckGo HTML | provider dispatch/auth/failover |
| CLI `inspect` | static fetch + markdown/links/metadata | structured mode, transport retries, browser/cache |
| CLI `batch` | static batch envelope | browser/cache/visited state |
| CLI `cache` | prune/clear/reset | none known |
| MCP schema/tool bodies | frozen schema + planning/envelope kernels | MCP transport and live tool execution |
| Python library | many deterministic kernels delegated to Rust | live orchestration classes remain |

## 4. Rules that apply to every unit

1. Keep CLI flags, `GOSSAMER_*` env vars, cache layout, and 12 MCP tool schemas
   unchanged unless a new ADR explicitly approves a break.
2. Exact stable outputs: stdout, stderr, exit code, JSON key order, counts,
   verdicts, rates, and error text. Normalize only already-named noise.
3. Pure core stays PyO3-free. `rust/core/tests/no_pyo3.rs` must keep passing.
4. No `unsafe`, no `unwrap`/`expect`/`panic!` on recoverable errors. Use
   canonical error structs and `#[expect(lint, reason = "...")]`, never
   `#[allow]`.
5. Thin PyO3 wrappers may hold clock, sleep, filesystem I/O, or provider state
   only until the caller itself is ported. No business logic in wrappers.
6. Browser fallback stays off until SSRF/request interception exists. Do not
   import stealth/bypass/CAPTCHA-solving/auto-consent behavior from Bladebro.
7. Live network results are not replay-pinned unless served from a deterministic
   local sink. Deterministic validation/error cases are replay-pinned.
8. After each unit: `rust-guidelines` narrow section review with guideline IDs,
   `coderadar reindex`, full pytest, app replay, `rust/PORT.md` update, commit
   and push.
9. Do not stop between units for confirmation unless a new ADR decision, a
   failing gate, a `trend.stalled` bench, or a contract break is discovered.

## 5. Unit-by-unit implementation plan

---

### U1. Live provider dispatch (`ResourceAdapter.search`)

**Status:** first slice complete 2026-11-25.

**Priority:** highest. **CodeRadar evidence:** 99 dependents; `affected` shows
provider, browser, HuggingFace, live smoke, and fallback tests.

**Goal**

Move the generic provider call lifecycle into Rust while reusing the existing
Rust parser kernels. Keep per-provider transport details data-driven.

**Target shape**

- New pure-core module: `rust/core/src/providers/live.rs`.
- New registry module: `rust/core/src/providers/registry.rs`, or extend
  `rust/core/src/providers/base.rs` if registry grows too large.
- Extend `rust/core/src/service/search.rs` with the public orchestration kernel:
  `search_provider_plan`, `search_provider_result`, `search_failure_envelope`.
- Thin wrapper: extend `rust/adapter-base-rs` or add `rust/provider-live-rs`.
- Python seam: `ResourceAdapter.search` keeps only clock, sleep, and mutable
  `RateState` until U1d; all decisions/planning delegate to Rust.

**Data types to introduce in core**

```text
ProviderCallPlan {
  provider, url, method, headers, query_params, body, timeout_seconds,
  max_results, retry, gate, cache_key
}

ProviderCallOutcome {
  status, headers, body, parsed_records, error, retry_decision, rate_state
}

ProviderFailure {
  code, message, provider, attempted_urls, retryable, human_action_needed
}
```

**Steps**

1. Use CodeRadar `callees` on `ResourceAdapter.search` and `_search_impl` to
   freeze the exact Python lifecycle: URL preparation → auth injection →
   `inject_auth` → quota gate → HTTP call → parser → normalization → fallback
   note → cache/result return.
2. Add deterministic kernels first:
   - `provider_call_plan(...)`: merges base URL, query params, auth headers,
     API key source, timeout, and max results; returns exact ordered maps.
   - `provider_response_classify(status, headers, body_sample)`: uses existing
     `retryable_status`, `rate_state_from_headers`, and bot-wall markers.
   - `provider_failure_envelope(...)`: canonical error shape including
     provider name via `py_repr`, attempted URL list, and fallback notes.
3. Record parity from `tests/test_providers.py`,
   `tests/test_fix_provider_fallback.py`, `tests/test_browser_provider.py`,
   `tests/test_huggingface.py`, and `tests/test_live_smoke.py`; add explicit
   `--inputs` cases for 401/403/406/429/5xx, malformed JSON, empty body,
   challenge body, and missing-key errors.
4. Wire `ResourceAdapter.search` to delegate planning/classification/failure,
   keeping `httpx` calls in Python for the first slice.
5. Add a Rust app `search_provider` internal unit used by both CLI `search`
   and CLI `research`, with provider selection from
   `service::search::resolve_order` and registry validation from
   `service::research::provider_factory_path`.
6. Replace CLI `research` unsupported branch for non-DDG providers once at
   least one non-DDG provider has a recorded case.
7. Follow `coderadar affected` on `ResourceAdapter.search` before each caller
   edit; browser provider tests and provider fallback tests are required gates.

**Completed slice**

- `provider_request_plan` is implemented and gated 39/39; every adapter now
  supplies declarative `AUTH_POLICY` data and no Python `inject_auth` mutation
  bodies remain.
- Bench is 0.08x: this is a correctness kernel, not a speed port.
- Retry verdicts now flow through Rust as well; gate extended to 39/39 with
  quota, permanent status, transient status, transport, final attempt,
  Retry-After cap, and jitter-cap parity cases.
- CLI `research --provider crossref` and `--provider openalex` are live Rust
  provider dispatches; manual live parity verified (parsed JSON equal; only
  provider response key ordering varied), not replay-pinned.
- `py_dumps` float formatting now matches Python repr for scientific notation.

**Exit criteria**

- Gate 100%; live smoke tests unchanged; CLI `search --provider <known>` and
  `research --provider <known>` no longer return unsupported for supported
  providers.
- Auth/key injection, rate-state mutation, and failover are either Rust-owned
  or explicitly documented as the next slice.

**Risks**

- Provider-specific `inject_auth` behavior differs across 40 classes.
- Live results are nondeterministic; pin only decision kernels and errors.
- Do not change provider aliases or unknown-provider envelopes.

---

### U2. Live fetch transport and structured inspect

**Priority:** second. **Evidence:** `inspect_html_page` 89 dependents;
`inspect_html_structured` 19; `_dispatch_fetch` 7; diagnose shows unresolved
cache/visited/in-flight/robots/rate calls in structured impl.

**Goal**

Extend Rust from static one-shot fetch to the full deterministic fetch state
machine, then wire structured inspect.

**Target shape**

- Extend `rust/core/src/service/fetch.rs` with:
  `fetch_attempt_plan`, `fetch_retry_decision`, `fetch_cache_decision`,
  `structured_inspect_plan`.
- New `rust/core/src/service/fetch_transport.rs` for HTTP status/body
  classification and bounded response handling.
- Extend `rust/app/src/units/inspect.rs` to support `--structured`.
- Keep browser process calls behind the existing isolation boundary and off by
  default.

**Steps**

1. Port `_fetch_html_dispatch` bookkeeping into a deterministic stats kernel:
   latency bucket, bytes, domain, error class. Existing `FetchStats` stays the
   public payload.
2. Add retry and status kernels using `providers::base::retryable_status` and
   `retry_decision`; pin exact retry counts and delays.
3. Add cache-decision kernel covering strategy compatibility, stale entry
   acceptance, and page-cache key construction; do not move filesystem I/O yet.
4. Add `structured_inspect_plan` that maps a fetched HTML result plus token
   budget into the exact structured payload shape: markdown, links, tables,
   navigation fields, guard fields, budget status.
5. Wire CLI `inspect --structured` through the new plan and remove its
   unsupported branch.
6. Only then consider browser escalation. The first browser slice must keep
   `GOSSAMER_BROWSER_FALLBACK` default off and preserve process isolation.

**Exit criteria**

- `inspect --structured` parity on local fixture and live static URL.
- Retry/cache cases recorded and gated.
- Browser fallback remains disabled by default; no SSRF bypass behavior added.

**Risks**

- Cache and visited state are shared with toolbox methods; partial ports can
  change cache-hit flags.
- Structured payloads include token-budget shrinking, which must remain exact.

---

### U3. Full document extraction execution

**Priority:** third. **Evidence:** `extract_document` 86 dependents, complexity
29; CLI `extract` is the most complex Rust unit (50).

**Goal**

Move the document execution router into Rust while keeping binary decoders
behind narrow adapter interfaces.

**Target shape**

- Extend `rust/core/src/document.rs` with `document_route`, `document_result`,
  `document_error`, `jats_route_decision`.
- New `rust/core/src/document_execution.rs` for local/remote source dispatch
  planning.
- Keep PDF/Office byte decoding behind a trait or explicit kernel boundary;
  do not bundle or auto-download external binaries.

**Steps**

1. Record parity for `_extract_from_bytes`, `_fetch_document_url`,
   `_finish_document`, `_try_jats_route`, `_finish_jats_result`.
2. Add a Rust route kernel that decides: local vs URL, suffix acceptance,
   structured vs flat, JATS vs OA vs direct fetch, store vs pages.
3. Wire CLI `extract` to the route kernel and remove its inline suffix/pages
   validation duplication.
4. Add binary-format errors first (exact unsupported messages), then wire
   decoders that already exist as Rust kernels (`meta_oxide`, `office_oxide`
   research ADRs).
5. Add paged PDF/Excel extraction only after page-range planning is recorded.
6. Add `tables-as csv` and URL `--store` as separate slices.

**Exit criteria**

- CLI `extract` no longer returns unsupported for any documented input shape
  that the Python oracle handles.
- Binary decoder absence produces exact Python error, not a generic failure.

**Risks**

- `DocumentExtractor._inspect_html_structured_impl` shares state with U2; do
  not duplicate its cache/visited logic.
- JATS route has 27 complexity and multiple provenance fields.

---

### U4. Batch inspection side effects

**Priority:** fourth. **Evidence:** `batch_inspect_pages` 37 dependents;
diagnose shows unresolved cache/visited/in-flight/robots/rate calls.

**Goal**

Move batch orchestration state into Rust after U2 provides fetch kernels.

**Target shape**

- Extend `rust/core/src/service/fetch.rs` with `batch_plan`,
  `batch_entry_finalize`, `batch_state_decision`.
- Extend `rust/app/src/units/batch.rs` to own cache/visited/in-flight state for
  the CLI path.

**Steps**

1. Record `_service_normalize_batch_results` is already done; extend with
   `_service_batch_plan` covering dedupe, URL preparation, robots checks, and
   rate gaps.
2. Replace Python `batch_inspect_pages_impl` side effects with Rust seams, one
   state family at a time: visited → cache → in-flight → robots → rate.
3. Add browser strategy only after U2 browser support exists.
4. Pin exact rejection records for invalid/SSRF URLs; already present in CLI
   but must remain byte-identical.

**Exit criteria**

- Batch cache-hit and visited flags match Python for repeated URLs.
- Browser strategy unsupported branch removed or explicitly documented as
  blocked by U2.

---

### U5. Download advanced modes

**Priority:** fifth. **Evidence:** `DownloadService.download_file` complexity
61 / 342 lines; CLI unsupported for `--resume` and `--try-mirrors`.

**Goal**

Complete the download caller using existing core kernels.

**Target shape**

- Extend `rust/core/src/downloader.rs` with `resume_plan`, `resume_finish`,
  `mirror_attempt_plan`, `mirror_failure_plan`.
- Reuse `candidate_list`, `finalize_candidates`, `http_error`,
  `validate_finished_file`, `download_bytes`.
- Extend `rust/app/src/units/download.rs` to parse and execute both flags.

**Steps**

1. Parse `--try-mirrors URL [URL ...]` greedily until the next known flag;
   preserve argparse's "expected at least one argument" error text.
2. For each candidate, call the existing single-download Rust path and collect
   attempt envelopes in exact Python order.
3. Terminal errors (`invalid_argument`, `file_exists`) must stop the loop;
   other errors proceed; final envelope comes from `finalize_candidates`.
4. Add resume state kernel: existing partial bytes, Range/If-Range headers,
   status 200 vs 206 handling, append/restart decision, cleanup paths.
5. Record from `tests/test_downloader.py`, including its local HTTP server
   routes for resume and range behavior.
6. Keep live redirect/UA differences out of replay; pin local-server cases.

**Exit criteria**

- `download --try-mirrors` and `download --resume` no longer return unsupported.
- Gate covers success, mirror fallback, terminal error, all-failed, and
  human-action-needed shapes.

---

### U6. MCP transport and live tool bodies

**Priority:** sixth. **Evidence:** 12 frozen tools; `mcp_dispatch` has only
planning/envelope kernels; `gossamer/mcp_server.py::build_server` still owns
server wiring.

**Goal**

Own MCP transport and live tool execution without changing the frozen schema.

**Target shape**

- New pure-core module `rust/core/src/mcp_transport.rs` for request parsing,
  tool dispatch, and response envelopes.
- Extend `rust/core/src/mcp_dispatch/mod.rs` to call the U1–U5 kernels for each
  of the 12 tools.
- Rust app gains an MCP subcommand or server mode only if the existing CLI
  contract already exposes it; otherwise use a separate binary target and keep
  the front door unchanged.

**Steps**

1. Freeze and replay all 12 tool request/response envelopes from
   `rust/fixtures/mcp_schema.json` and `tests/test_p8_tool_registry.py`.
2. Implement generic argument coercion by reusing `ensure_str_list`,
   `tool_arguments`, and `tool_envelope`.
3. Wire each tool body to the corresponding Rust caller: `web_search` → U1,
   `inspect_html_page` → U2, `batch_inspect_pages` → U4, `download_file` → U5,
   `extract_document` → U3, `discover_resources` → CLI discover kernels,
   `crawl` → U7, `manage_cache` → existing cache unit, `research_by_category`
   → U8, `export_citations` → citation kernels, `check_sources` → check unit.
4. Add transport only after body parity; protocol framing is separate from
   business logic.
5. Do not add, remove, rename, or redesign any tool. Schema changes require a
   new ADR.

**Exit criteria**

- All 12 tool bodies execute through Rust with frozen schema and exact envelope
  shape.
- MCP transport replay covers initialize/list-tools/call-tool/error shapes.

---

### U7. Full crawl loop

**Priority:** seventh. **Evidence:** `Crawler.crawl` complexity 68; CLI crawl
unsupported for `--search-prior`; seed URLs ignored.

**Goal**

Move the real crawl state machine into Rust after U2/U4 provide fetch and
visited/cache state.

**Target shape**

- New `rust/core/src/service/crawl_loop.rs`.
- Extend `rust/app/src/units/crawl.rs` to use it.

**Steps**

1. Split `Crawler.crawl` into kernels: root validation, frontier insert,
   frontier scoring, fetch batch, document routing, error recording, and final
   envelope.
2. Reuse all existing crawl kernels in `rust/core/src/service/crawl.rs`.
3. Add search-prior kernel using U1 results.
4. Add seed URL normalization and dedupe using existing URL kernels.
5. Add robots/cache side effects after U2/U4 state is Rust-owned.
6. Pin deterministic local-server crawl cases; do not replay live web crawls.

**Exit criteria**

- CLI crawl supports documented flags and matches Python on local fixture.
- No unsupported branch for supported crawl flags.

---

### U8. Research provider fan-out

**Priority:** eighth. **Evidence:** `WebResearcherToolbox.research` 19 dependents
/ 156 lines; `search_category` complexity 21; 40 provider classes.

**Goal**

Replace the DuckDuckGo-only CLI research path with full provider fan-out built
on U1 and existing parser kernels.

**Steps**

1. Move category/provider selection into `rust/core/src/service/research.rs`.
2. Add provider fan-out planner: provider order, per-provider max results,
   dedupe, merge, and error aggregation.
3. Wire `research --provider` and `--providers` to U1 dispatch.
4. Reuse `research_merge::merge_scholarly_records`, `normalize_records`,
   `provider_order`, and `research_plan`.
5. Add recorded cases for category taxonomy, unknown provider, empty results,
   partial provider failure, and all-provider failure.
6. Keep live provider responses out of replay unless served locally.

**Exit criteria**

- CLI `research` no longer returns unsupported for known providers.
- Error envelopes and provider notes match Python exactly.

---

### U9. Resource store, open access, and citations integration

**Priority:** ninth. **Evidence:**

- `OpenAccessLocator.locate` complexity 27 / 183 lines; CLI locate-pdf already
  matches OpenAlex happy path and not-found, but not full locator flow.
- `resource_store.py::extract` and `extract_embedded` already delegate planning
  to Rust but still own download/store side effects.
- `citations.py::enrich_with_doi` has planning in Rust but adapter calls remain
  Python.

**Goal**

Connect the already-ported deterministic kernels into full Python library paths
before final wrapper retirement.

**Steps**

1. Port `OpenAccessLocator.locate` router; reuse `oa_candidate`,
   `unpaywall_candidates`, `normalize_doi`, and JATS route kernels.
2. Port `ResourceStore._store_url` and `_write_bytes` side effects using the
   U5 download path.
3. Wire citation enrichment adapter calls to U1 provider dispatch.
4. Keep binary extraction and external binary discovery behind explicit errors
   until U3 is complete.

**Exit criteria**

- No Python library call path still recomputes a kernel already owned by Rust.
- Wrappers only forward arguments and map errors.

---

### U10. Final cutover and wrapper retirement

**Priority:** last.

**Goal**

Switch packaging/launcher to `rust/app`, retire PyO3 wrappers, and remove the
Python runtime dependency from production.

**Steps**

1. Complete `rust/cutover.sh` dry run on Windows.
2. Add Linux x86_64 CI for `cargo build`, `cargo test --workspace`, clippy, and
   app replay.
3. Decide MSRV and record it in `rust/DECISIONS.md`.
4. Decide whether `text-links-rs` gains a CLI command; if yes, write a new ADR.
5. Remove root `src/` wrappers and the `rust/<unit>-rs` harness crates only
   after all Python parity tests are no longer part of production.
6. Keep Python tests as the oracle until the final release validation passes.

**Exit criteria**

- Production launcher runs `rust/app` only.
- No fallback path, no Python import, no PyO3 extension in the shipped binary.
- Full release validation: help, replay, schema, cache layout, env contract.

## 6. Recommended execution order

```text
U1 live provider dispatch
  → U2 fetch transport + structured inspect
    → U3 document execution
      → U4 batch side effects
        → U5 download advanced modes
          → U6 MCP transport/tool bodies
            → U7 full crawl loop
              → U8 research fan-out
                → U9 resource/OA/citations integration
                  → U10 final cutover
```

Rationale: U1 is highest fan-in and unblocks CLI search/research and MCP.
U2 unblocks structured inspect and batch. U3 depends on U2 for fetched HTML.
U6 should wait until tool bodies have Rust callers. U7 waits on visited/cache
state. Cutover waits until no documented Python feature is missing.

## 7. Per-unit working checklist

For every unit above:

- [ ] CodeRadar `affected` reviewed before touching callers.
- [ ] Gate command recorded: `rust-anything record <crate> --from-tests -- <runner>`.
- [ ] Explicit invalid-input cases added with `record --inputs` when the gate
      reports unreached error paths.
- [ ] `rust-anything build` succeeds.
- [ ] `rust-anything diff` reaches N/N.
- [ ] `rust-anything bench` run; sub-1.0x accepted only as correctness port and
      recorded in `rust/PORT.md`.
- [ ] `rust-anything gate` passes with warnings as errors.
- [ ] `rust-anything enable` applied; kill switch documented.
- [ ] `rust-guidelines` narrow section review, citing guideline IDs.
- [ ] `coderadar reindex` after code changes.
- [ ] Full pytest green.
- [ ] `rust-anything app replay` at 57/57 or higher.
- [ ] `rust/PORT.md` and this plan updated.
- [ ] `git diff --check`, commit, push.

## 8. Verification command set

Run from `D:/User/Documents/Rust/gossamer`:

```bash
# Rust verification
cd rust
cargo fmt --all
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cd ..

# Python oracle
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider

# App replay
rust-anything app replay . --build "rust/app/target/debug/gossamer.exe"

# Graph refresh
C:/Users/Main/.venv/Scripts/coderadar.exe reindex
C:/Users/Main/.venv/Scripts/coderadar.exe annotations-on gossamer/cli.py::main

# Final hygiene
git diff --check
```

## 9. ADR triggers

A new ADR is required if any of these changes:

- CLI flag or subcommand surface changes.
- Any `GOSSAMER_*` env var name/default changes.
- MCP tool names, descriptions, enums, or required fields change.
- Cache directory layout or key format changes.
- Browser fallback becomes default-on before SSRF/request interception exists.
- `text-links-rs` gains a new CLI command.

## 10. Known non-goals for this plan

- No DonSeTch code, text, tests, docs, assets, or marker tables copied.
- No Bladebro dependency, bundling, auto-download, or stealth/bypass posture.
- No CAPTCHA solving, auto-consent clicking, real-profile browser lanes, or
  headful-by-default browser behavior.
- No live provider responses pinned into replay without a deterministic sink.
- No schema/CLI contract break without an ADR.

## 11. Appendix: CodeRadar commands used

```bash
C:/Users/Main/.venv/Scripts/coderadar.exe reindex
C:/Users/Main/.venv/Scripts/coderadar.exe annotations-on gossamer/cli.py::main
C:/Users/Main/.venv/Scripts/coderadar.exe callees gossamer/cli.py::main
C:/Users/Main/.venv/Scripts/coderadar.exe affected gossamer/search_providers.py::ResourceAdapter.search --max-depth 2
C:/Users/Main/.venv/Scripts/coderadar.exe affected gossamer/agent_tools.py::WebResearcherToolbox.inspect_html_structured --max-depth 2
C:/Users/Main/.venv/Scripts/coderadar.exe diagnose --unresolved --low-confidence
C:/Users/Main/.venv/Scripts/coderadar.exe dead-code --min-confidence 0.7 --max-findings 30
C:/Users/Main/.venv/Scripts/coderadar.exe find-clones --min-lines 12 --min-similarity 0.85 --max-groups 20
C:/Users/Main/.venv/Scripts/coderadar.exe query 'functions select id, name, caller_count, line_count where caller_count >= 8 and not file_path starts_with "tests" and not file_path starts_with "vendor" order by caller_count desc limit 40' --format json
C:/Users/Main/.venv/Scripts/coderadar.exe query 'functions select id, name, complexity, line_count where file_path starts_with "gossamer/" and complexity >= 20 order by complexity desc limit 30' --format json
```
