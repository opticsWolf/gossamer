# stitch-web-researcher — Improvement Plan

> Generated: 2026-08-25 · Source: CodeRadar code-graph analysis (31 smell findings),
> manual source audit, git history review.
> Scope: `src/lib.rs`, `stitch_web_researcher/*.py`, `tests/`, docs.
>
> ## ✅ Implementation Status (2026-08-25)
>
> | Item | Status |
> |---|---|
> | P0-#1 delete `_extract_main_content` | ✅ done |
> | P0-#2 batch visited-skip fix | ✅ done (+tests) |
> | P0-#3 README signature fix | ✅ done |
> | P1-#4 semaphore-bounded batch (`max_concurrency`, default 8) | ✅ done (+tests) |
> | P1-#5 cache HTML inspections (namespaced `page:` / `structured:` keys) | ✅ done (+tests) |
> | P1-#6 unify sync/async inspect | ✅ done — *deviation:* no Python-layer retry added; Rust core already retries 3× w/ backoff, doubling would mean 9 attempts |
> | §4.3 Option A binding `extract_main_content_markdown(html) -> (selector_label, markdown)` | ✅ done (+tests); wired into `_fetch_with_browser_oxide` as `metadata["content_selector"]`; re-exported from package root |
> | Bonus: removed dead `extract_links` wrapper in `lib.rs` (found by `cargo check`) | ✅ done |
> | Housekeeping: `.gitignore` scratch artifacts | ✅ done |
> | P2-#7 flatten `_fetch_html` mode matrix | ✅ done — strategy helpers `_static_fetch` / `_browser_fetch`; deep-nesting finding eliminated |
> | P2-#8 dispatch-table `parse_file` | ✅ done — `_parse_pdf` / `_parse_spreadsheet` / `_parse_office_document`; 157 → ~25 LOC |
> | P2-#9 split `DocumentMetadata` | ✅ done — grouped mixins (`FileMeta`, `WebBasicsMeta`, `OpenGraphMeta`, `TwitterMeta`, `StructuredDataMeta`) composed into a deliberately flat `DocumentMetadata`; serialized schema unchanged |
> | P2-#10 `ToolboxConfig` dataclass | ✅ done — legacy kwargs still accepted with `DeprecationWarning`; mixing styles raises `TypeError` (+tests) |
> | P2-#11 extract `http_fetch_html` attempt loop | ✅ done — per-attempt `fetch_attempt(msg, retryable)` helper |
> | P2-#12 browser-provider integration test | ✅ done — `tests/test_browser_integration.py`, marked `slow`, deselected by default via `addopts = "-m 'not slow'"` |
>
> Post-P2: **166 tests passing** (+1 slow, opt-in). CodeRadar: all four original
> High-severity structural findings eliminated. Remaining findings are inherent
> to schema/config classes (data-class on Pydantic DTOs & `ToolboxConfig` —
> field-bags by design) or small-medium leftovers documented above.
>
> Post-implementation: **161 tests passing** (145 → 161). Remaining smell findings
> (34) map entirely to the P2 backlog.

---

## 1. Executive Summary

The project is architecturally sound: clean layering between the Rust core (`_core`)
and the Python orchestration package, no circular imports, honest truncation semantics,
and a well-documented async boundary. However, the analysis surfaced:

| Category | Count | Highlights |
|---|---|---|
| Confirmed bugs | 4 | dead+broken function, cosmetic batch dedup, cache not covering pages, unbounded batch concurrency |
| Documentation bugs | 2 | README signature drift, mislabeled section numbering |
| High-severity smells | 3 | deep nesting in `http_fetch_html`, 157-LOC `parse_file`, 33-field `DocumentMetadata` |
| Medium smells | ~20 | long methods, param lists, duplication between sync/async paths |

Work is organized into four priority bands (P0 → P3). Estimated total effort for
P0–P1 is **~1 day**; P2 is **~2–3 days**; P3 is optional refactoring.

---

## 2. Findings Catalog (with evidence)

### F1 · Dead AND broken function — `_extract_main_content` 🔴

- **Location:** `stitch_web_researcher/agent_tools.py:118`
- **Evidence:** Zero callers across production code, tests, and benchmarks
  (verified by grep + CodeRadar upstream traversal).
- **Why it's broken:** it does `from scraper import Selector`. `scraper` is a
  **Rust crate**, not a Python package — it appears only in `Cargo.toml:14`.
  Any invocation raises `ImportError` immediately.
- **Origin:** almost certainly a leftover from an earlier pure-Python prototype of
  the extraction pipeline before the logic moved into Rust.

**Verdict on usefulness:** see §4 — the *functionality* is required, but it already
exists in Rust (`lib.rs::extract_main_content`) and runs on every fetch path. The
Python copy is redundant.

### F2 · `batch_inspect_pages` skip logic is cosmetic 🔴

- **Location:** `agent_tools.py:913–945`
- **Behavior:** the validation loop logs `"Skipping already-visited URL"` and
  `continue`s — but both fetch branches then iterate the **original `urls` list**,
  so "skipped" URLs are fetched anyway. The `visited_urls` dedup guarantee silently
  does not hold in batch mode.
- **Fix:** build a filtered list during validation and pass that to both branches.

### F3 · Cache covers documents but not pages 🔴

- **Location:** `self.cache.get/put` appears only at `agent_tools.py:985,1000`
  (inside `extract_document`).
- **Impact:** `inspect_html_page`, `inspect_html_structured`, and
  `batch_inspect_pages` always hit the network. README advertises TTL caching as a
  core feature; in practice repeat page inspections pay full cost.
- **Fix:** route `_fetch_html` results through `Cache` using the existing canonical
  `_cache_key()`; store `(markdown, links, metadata, method)` tuples (serialize
  metadata + links as JSON alongside markdown).

### F4 · Unbounded concurrency in batch fetch 🟠

- **Location:** `lib.rs::fetch_many_inner` — one `tokio::spawn` per URL, no
  semaphore; no per-domain politeness delay on the static-batch path.
- **Impact:** a 100-URL batch against one domain is a self-inflicted DoS (and rude
  to the target). Single-fetch path rate-limits correctly; batch bypasses it.
- **Fix:** add a `Semaphore` (configurable, default ~8) around spawns; optionally
  group URLs per domain and stagger within groups.

### F5 · README API drift 🟡

- `fit_context_window(system_prompt=..., user_input=..., context_chunks=..., budget=...)`
  example does not match real signature `(pieces, max_tokens, model_name)` — copy-paste
  raises `TypeError`.

### F6 · Sync/async duplication without parity 🟡

- `inspect_html_page_async` duplicates ~20 lines of `inspect_html_page` **minus the
  `@retry` decorator** — async path is strictly less resilient than sync.
- `search_web_async` re-imports `asyncio` locally though it's a module-level import.

### F7 · CodeRadar high-severity smells 🟠

| Smell | Entity | Signal |
|---|---|---|
| deep-nesting (6) | `lib.rs::http_fetch_html` | retry loop; extract attempt logic |
| long-method (157 LOC) | `StructuredOxideParser.parse_file` | 4 near-identical format branches |
| data-class / too-many-fields (33) | `DocumentMetadata` | WMC=0; candidate for sub-model split |
| long-parameter-list (14) | `WebResearcherToolbox.__init__` | config object overdue |

### F8 · Housekeeping ⚪

- Scratch artifacts at repo root: `crash.log`, `step1_links.json`,
  `wiki_topics.json` → `.gitignore`.
- `search_providers.py` section comment says "5." twice (Browser-Oxide provider).
- Exported-but-internally-unused public API (`fit_context_window`,
  `estimate_markdown_tokens`, `get_default_providers`, granular `extract_*`
  metadata fns, `BrowserOxideSearchProvider`) — fine as API surface, but none has
  an integration test exercising the browser provider end-to-end.

---

## 3. Remediation Plan

### P0 — Correctness (do first, ≤ 2 h)

| # | Task | Files | Acceptance criteria |
|---|---|---|---|
| 1 | Delete `_extract_main_content` (F1). Functionality already lives in Rust (see §4). | `agent_tools.py` | grep shows zero references; test suite green |
| 2 | Fix batch visited-skip (F2): filter list once, use filtered list everywhere. | `agent_tools.py` | unit test: pre-visit URL, batch call returns no result entry for it |
| 3 | Fix README `fit_context_window` example (F5). | `README.md` | example runs verbatim in REPL |

### P1 — Robustness (~half day)

| # | Task | Files | Acceptance criteria |
|---|---|---|---|
| 4 | Semaphore-bound batch concurrency (F4): add `max_concurrency` param (default 8) threaded from toolbox → `batch_research`. | `lib.rs`, `agent_tools.py`, tests | 50-URL same-domain batch never opens >8 simultaneous connections (assert via mock server counter) |
| 5 | Cache HTML inspection results (F3): key = canonical `_cache_key`, value = JSON `{md, links, meta, method}`; honor TTL; `clear_cache` already works unchanged. | `agent_tools.py`, tests | second `inspect_html_page(same_url)` hits memory tier (`cache_hit=True` in stats); TTL expiry forces refetch |
| 6 | De-duplicate sync/async inspect (F6): single implementation, async wrapper via `run_in_executor`; restore `@retry` parity. | `agent_tools.py` | async path retries 3× on transient failure (mock-based test) |

### P2 — Structure & maintainability (~2–3 days)

| # | Task | Notes |
|---|---|---|
| 7 | Flatten `_fetch_html` mode matrix | ✅ done. `_dispatch_fetch` now resolves `(fetch_mode, use_smart)` via `_resolve_fetch_strategy` into one of four strategies (`static-only`, `browser-only`, `browser-first`, `auto`) — no more nested mode branch. `use_smart` is the `FetchMode` tri-state (`auto`/`browser`/`static`) |
| 8 | Dispatch table in `parse_file` | `{suffix: handler}` map; each handler ~20 lines; removes 157-LOC method and 3 duplicate Office branches |
| 9 | Split `DocumentMetadata` | sub-models: `FileMeta`, `OpenGraphMeta`, `TwitterMeta`, `StructuredDataMeta`; compose into `DocumentMetadata` while keeping flat serialization via `model_dump()` merge so output schema stays stable |
| 10 | Config object for toolbox | `ToolboxConfig` dataclass absorbing the 14 `__init__` params; keep old kwargs as deprecated passthrough for one minor version |
| 11 | Extract `http_fetch_html` attempt loop | pull per-attempt logic into helper; guard clauses instead of nesting |
| 12 | Integration test for browser provider | mark `@pytest.mark.slow`+skip-if-not-installed; exercises `BrowserOxideSearchProvider.search` against DDG HTML endpoint |

### P3 — Optional

- Return richer error objects instead of stringly `{"error": ...}` dicts (keep
  strings for LLM consumption, add structured `code` field).
- Consider `tracing` subscriber hookup so Rust-side logs surface in Python logging.
- Benchmarks: extend `benchmarks.py` with cache-hit-rate scenario post-P1-#5.

---

## 4. Deep Dive: `_extract_main_content` — required? useful? what to do?

### 4.1 Is the *functionality* required?

**Yes — and it is already running.** In `src/lib.rs`:

```
extract_main_content (lib.rs:41)
        ▲
process_html_anchored (lib.rs:140)   ← every content path flows through here
        ▲
fetch_and_extract · fetch_and_extract_linked · batch_research · process_rendered_html
```

Every markdown payload delivered to the LLM has already been narrowed to the best
of `article > main > [role=main] > .content > #content > body`. Without it, nav
bars/footers would dominate token budgets. So the heuristic is load-bearing.

### 4.2 Is the Python function useful?

**No.** It is a stale duplicate:

- zero callers (grep + graph traversal confirm);
- cannot even import its dependency (`scraper` is Rust-only);
- its behavior is fully superseded by the Rust path above;
- keeping it invites drift — someone may "fix" the Python copy believing it's live.

**Action: delete (P0-#1).** If main-content selection ever needs Python-side
customization, the right move is §4.3, not resurrecting this copy.

### 4.3 Making `scraper` usable from Python — our own bindings

There is no official Python package for the `scraper` crate, and we don't need one:
the project already owns a PyO3/maturin bridge (`_core`). Exposing scraper is a
~30-line change with the build pipeline we already have.

**Option A (recommended) — expose main-content extraction with visibility**

Currently the chosen container is invisible to callers. A binding that reports
*which* selector matched enables tuning and debugging:

```rust
// src/lib.rs — new binding

/// Run main-content heuristics on caller-supplied HTML.
/// Returns (matched_selector_or_"body", markdown_of_that_region).
#[pyfunction]
#[pyo3(signature = (html))]
fn extract_main_content_markdown(
    py: Python<'_>,
    html: String,
) -> PyResult<(String, String)> {
    py.detach(|| {
        let document = Html::parse_document(&html);

        let selectors = [
            ("article",      Selector::parse("article").unwrap()),
            ("main",         Selector::parse("main").unwrap()),
            ("role=main",    Selector::parse("[role='main']").unwrap()),
            (".content",     Selector::parse(".content").unwrap()),
            ("#content",     Selector::parse("#content").unwrap()),
        ];
        let mut chosen = ("body".to_string(), None);
        for (name, sel) in &selectors {
            if let Some(el) = document.select(sel).next() {
                chosen = (name.to_string(), Some(el.html()));
                break;
            }
        }
        let (_, html_frag) = match chosen.1 {
            Some(frag) => chosen,
            None => (
                "body".to_string(),
                document
                    .select(&Selector::parse("body").unwrap())
                    .next()
                    .map(|b| b.html())
                    .unwrap_or_else(|| document.html()),
            ),
        };

        Ok((chosen.0, parse_html(&html_frag)))
    })
}
```

Register in `#[pymodule]`: `m.add_function(wrap_pyfunction!(extract_main_content_markdown, m)?)?;`

Then in `__init__.py` re-export and use it in `_fetch_with_browser_oxide` for a
debug field, e.g. `"content_selector": "article"` in inspection metadata.

**Option B — general CSS-selector query API**

For power users / future tools (e.g., an LLM tool `query_html(url, css)`):

```rust
#[pyfunction]
#[pyo3(signature = (html, selector, limit = 50))]
fn select_html(
    py: Python<'_>,
    html: String,
    selector: String,
    limit: usize,
) -> PyResult<Vec<String>> {
    py.detach(|| {
        let document = Html::parse_document(&html);
        // NOTE: user-supplied selector — must handle parse errors, no unwrap().
        let sel = Selector::parse(&selector).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid selector: {e:?}"))
        })?;
        Ok(document
            .select(&sel)
            .take(limit)
            .map(|el| el.html())
            .collect())
    })
}
```

⚠️ Design note: the existing Rust code uses `.unwrap()` on *static* selectors —
fine. Any binding accepting **user-supplied** selectors must map
`Selector::parse` errors to `PyValueError` (as above), never panic across FFI.

**Option C — considered and rejected**

- Wrap `scraper` in a separate standalone PyPI package: unnecessary — we ship
  `_core` anyway; extra packaging burden with no consumer.
- Pure-Python alternatives (`trafilatura`, `readability-lxml`): slower, adds
  heavyweight deps, and diverges behavior from the Rust path that all fetches
  already use.

**Effort estimate:** Option A ≈ 45 min including rebuild (`maturin develop --release`),
re-export, test. Option B adds ~30 min + tests.

---

## 5. Verification Strategy

1. After each P0/P1 change: `pytest -x -q` (145 tests currently green per README).
2. After Rust changes: `maturin develop --release && pytest tests/test_crawler.py -k runtime`.
3. Re-run `coderadar` smell scan after P2 items 7–11 — target: zero High findings,
   `deep-nesting ≥ 5` eliminated, `parse_file` under 60 LOC.
4. Blast-radius check before touching `WebResearcherToolbox.__init__`: 10 dependents
   (all tests) — update fixtures in the same commit.

---

## 6. Suggested Commit Sequence

```
1. chore: remove dead _extract_main_content (superseded by Rust core)
2. fix(batch): actually skip visited URLs in batch_inspect_pages
3. docs(readme): correct fit_context_window signature
4. feat(core): bound batch concurrency with semaphore (default 8)
5. feat(cache): cache HTML inspections through two-tier Cache
6. refactor(toolbox): unify sync/async inspect, restore retry parity
7. feat(core): expose extract_main_content_markdown binding w/ selector visibility
8. refactor(parser): dispatch-table parse_file; split DocumentMetadata
9. refactor(toolbox): ToolboxConfig dataclass
10. chore: ignore scratch artifacts
```
