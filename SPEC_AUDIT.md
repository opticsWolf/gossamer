# v4 Spec Compliance Audit

> Generated: 2026-06-15  
> Re-verified: 2026-08-27 (post C1–C7, S1–S7, P1–P8; tool count + metadata-path claims corrected)  
> Status: Feature-complete with minor gaps

---

## 1. Rust Core (`_core`)

| Requirement | Status | Notes |
|-------------|--------|-------|
| PyO3 bindings | ✅ | pyo3 0.27, `py.detach()` for thread crossing |
| Async HTTP via reqwest | ✅ | brotli decompression, browser impersonation, 30s timeout |
| Shared Tokio runtime | ✅ | `OnceLock<Runtime>` singleton, zero cold-start penalty |
| HTML parsing via scraper | ✅ | main content extraction heuristics |
| Markdown conversion via html2md | ✅ | `fetch_and_extract` returns `(markdown, links)` |
| Concurrent batch fetch | ✅ | `batch_research` with `tokio::spawn` |
| Exponential backoff retry | ✅ | 3 attempts, 500ms base delay |
| `fetch_and_extract` Python binding | ✅ | exported from `_core` |
| `batch_research` Python binding | ✅ | exported from `_core` |
| LTO + strip in release | ✅ | `Cargo.toml` `[profile.release]` |

**Verdict**: ✅ Complete

---

## 2. Python Orchestration (`agent_tools.py`)

| Requirement | Status | Notes |
|-------------|--------|-------|
| `WebResearcherToolbox` class | ✅ | Full toolbox with all methods |
| TTL file caching | ✅ | `.cache` + `.meta` files, configurable TTL |
| Per-domain rate limiting | ✅ | `_domain_last_seen` dict, configurable delay |
| User-Agent rotation | ✅ | 3 Chrome UAs, round-robin |
| Visited URL deduplication | ✅ | `visited_urls` set |
| URL validation | ✅ | scheme + host check |
| Retry decorator | ✅ | exponential backoff, configurable |
| Smart/fallback routing | ✅ | `use_smart` flag, browser_oxide → reqwest fallback |
| Async variants | ✅ | `search_web_async`, `inspect_html_page_async`, `batch_inspect_pages_async` (thread-pool wrappers) |
| Tool surface (P8) | ✅ | One `TOOL_REGISTRY` drives every surface — `get_llm_definitions()`, the MCP tools, and the `execute_tool(name, arguments)` dispatcher — 10 tools: search_web, inspect_html_page, batch_inspect_pages, extract_document, extract_document_structured, inspect_html_structured, clear_cache, prune_cache, reset_visited, get_stats |
| Token-aware truncation | ✅ | two-pass: tokens first, then char cap |
| meta-oxide integration | ✅ | `_compact_metadata()` in inspect output |

**Verdict**: ✅ Complete

---

## 3. Multi-Provider Search (`search_providers.py`)

| Requirement | Status | Notes |
|-------------|--------|-------|
| `SearchProvider` ABC | ✅ | abstract `search()` method |
| `DuckDuckGoProvider` | ✅ | ddgs package, no API key needed |
| `GoogleProvider` | ✅ | httpx-based, env vars for keys |
| `BingProvider` | ✅ | httpx-based, env var for key |
| `ExaProvider` | ✅ | optional exa-py |
| `get_default_providers()` | ✅ | auto-detects available providers |
| `resolve_provider_name()` | ✅ | case-insensitive name → provider |
| Fallback chaining | ✅ | tries named provider, then remaining |
| LLM tool `provider` enum | ✅ | `["duckduckgo", "google", "bing", "exa"]` |
| Result-level search cache (Tier 2.8) | ✅ | Successful results cached in-memory (bounded, TTL) keyed by normalized `(query, max_results, provider, mode)`; errors never cached; cleared by `clear_cache` |
| Within-provider dedup (Tier 2.8) | ✅ | Duplicate normalized URLs collapsed within the first successful provider's result list |
| Cross-provider merge (Tier 2.8) | ✅ | Opt-in `search_merge` / `STITCH_SEARCH_MERGE`: queries every provider and merges + dedups up to `max_results` |

**Verdict**: ✅ Complete

---

## 4. Structured Parsing (`structured_parser.py`)

| Requirement | Status | Notes |
|-------------|--------|-------|
| Pydantic v2 schemas | ✅ | `>=2.7.0` required |
| `DocumentMetadata` | ✅ | expanded with 20+ HTML metadata fields |
| `ExtractedTable` | ✅ | validated rows/headers |
| `ExtractedPage` | ✅ | page number + content |
| `ParsedDocumentPayload` | ✅ | full document with metadata, pages, tables |
| `StructuredOxideParser` | ✅ | PDF, DOCX, XLSX, PPTX support |
| Document format coverage (M16 + Tier 3.10) | ✅ | `classify_link` advertises pdf/OOXML + txt/md/csv/json/xml/rss/atom; text-like Content-Types cover extension-less URLs (feeds become entry lists) |
| XMP datetime parsing | ✅ | ISO 8601 variants |
| `extract_document_structured` in toolbox | ✅ | URL → temp file → parse → cleanup |

**Verdict**: ✅ Complete

---

## 5. Token Budgeting (`token_budget.py`)

| Requirement | Status | Notes |
|-------------|--------|-------|
| `count_tokens()` | ✅ | tiktoken-based, model-specific |
| `truncate_to_tokens()` | ✅ | precise token count, custom ellipsis |
| `fit_context_window()` | ✅ | system + user + context chunks |
| `resolve_encoding()` | ✅ | model → encoding name mapping |
| `estimate_markdown_tokens()` | ✅ | ~4 chars per token heuristic |
| GPT-4 / GPT-3.5 support | ✅ | cl100k_base |
| Claude 3 support | ✅ | cl100k_base |
| Graceful fallback | ✅ | `len(text)//4` if tiktoken unavailable |
| Integration in toolbox | ✅ | `max_tokens`, `model_name` params, `_truncate()` |

**Verdict**: ✅ Complete

---

## 6. HTML Metadata (`meta_extractor.py`)

| Requirement | Status | Notes |
|-------------|--------|-------|
| meta-oxide wrapper | ✅ | lazy import, graceful fallback |
| `extract_all()` | ✅ | 13 metadata formats |
| `extract_meta()` | ✅ | standard HTML meta |
| `extract_opengraph()` | ✅ | Open Graph |
| `extract_twitter()` | ✅ | Twitter Cards |
| `extract_jsonld()` | ✅ | JSON-LD / Schema.org |
| `merge_into_document_metadata()` | ✅ | merges into base metadata dict |
| Wired into fetch pipeline | ✅ | both the static and browser fetch paths return metadata (C2) |
| Compact metadata for LLM | ✅ | `_compact_metadata()` in toolbox |
| `DocumentMetadata` expanded | ✅ | 20+ new fields for HTML metadata |

**Verdict**: ✅ Complete

---

## 7. Smart Fetch (browser_oxide)

| Requirement | Status | Notes |
|-------------|--------|-------|
| browser_oxide integration | ✅ | Python layer, not Rust |
| Headless JS rendering | ✅ | `Page.navigate()` with challenge detection |
| Fallback to static | ✅ | reqwest fallback on any error |
| `fetch_method` in output | ✅ | "smart" or "static" |
| HTML metadata extraction | ✅ | meta-oxide on rendered HTML |

**Verdict**: ✅ Complete

---

## 8. Package & Distribution

| Requirement | Status | Notes |
|-------------|--------|-------|
| `pyproject.toml` complete | ✅ | name, version, authors, classifiers, URLs |
| `Cargo.toml` correct | ✅ | lib name `_core`, LTO + strip |
| `requirements.txt` | ✅ | all deps listed with versions |
| `README.md` | ✅ | architecture, features, usage examples |
| Wheel builds cleanly | ✅ | `maturin build --release` → clean wheel |
| Wheel contents clean | ✅ | no `__pycache__`, no `.pdb` |
| ABI3 compatible | ✅ | `cp38-abi3` wheel |
| 114 tests passing | ✅ | across 3 test files |

**Verdict**: ✅ Complete

---

## 9. Gaps & Missing Features

| Gap | Priority | Description |
|-----|----------|-------------|
| **In-memory cache** | ~~Medium~~ | ✅ Resolved | Two-tier Cache (LRU + disk TTL) in `cache.py`. Integrated into `WebResearcherToolbox`. |
| **CI/CD pipeline** | ~~Medium~~ | ✅ Resolved | GitHub Actions: `ci.yml` (test matrix + lint) + `release.yml` (wheel build + PyPI publish). |
| **`meta-oxide` packaging** | Low | Upstream has packaging bug; requires manual build workaround. Documented but not ideal for end users. |
| **`browser_oxide` installation** | Low | Not on PyPI; requires git install. Should be noted in README prerequisites. |
| **Performance benchmarks in CI** | Low | `benchmarks.py` exists but not automated. |
| **`office_oxide` from git** | Low | Not on PyPI; requires git+https install. Should be noted. |
| **Type hints / stubs** | Low | No `.pyi` stub files. Type hints exist in docstrings but not as proper annotations on all functions. |
| **Error types** | Low | No custom exception hierarchy; uses generic `Exception` / `ValueError` / `RuntimeError`. |

---

## 10. HTML Structured Parsing (Task 5A)

| Requirement | Status | Notes |
|-------------|--------|-------|
| `parse_html()` on `StructuredOxideParser` | ✅ | Standardizes web-fetching output with document parsing |
| `inspect_html_structured()` on toolbox | ✅ | New LLM function tool with `use_smart` flag |
| HTML table extraction (Tier 3.11) | ✅ | Rust `extract_tables_from_html` parses top-level `<table>` grids (colspan/rowspan expanded, `<th>` headers, caption names, 20-table/500-row caps) and attaches them to the payload and its page; browser path and the M8 page seam are untouched |
| Sitemap-aware discovery (Tier 3.12) | ✅ | `discover_resources(url)`: feed `<link rel=alternate>` scan (RSS/Atom/Feed-JSON only) plus bounded `/sitemap.xml` probe (index hops ≤ 3, ≤ 10 sitemap fetches, 500 URLs per sitemap, 1000 total, ordered dedupe); best-effort degradation on missing/malformed sitemaps; page left unvisited |
| Research orchestration (Tier 3.13) | ✅ | `research(topic, depth=5, max_tokens=0)`: searches the topic (≤ depth*2 candidates, cap 20), normalizes/dedupes/SSRF-validates result URLs (≤ depth, cap 10), fetches each through the normal page pipeline (cache/robots/rate-limit/provenance), and returns per-source status + content + provenance; failures isolated per source; repeated runs served from the page cache; global budget enforced, synthesis left to the calling agent |
| Text-level link detection (v0.4.5) | ✅ | `extract_links(text)`: stdlib regex detector for `http://`/`https://` URLs plus `www.` promotion; strips trailing Latin and CJK punctuation; dedupes and caps at 50; wired into `extract_document` (`links`, full pre-truncation content) and `extract_document_structured` (`payload.links` as `{title, url, type}`) so documents yield follow-up targets without hyperlink annotations |
| Focused crawl (v0.4.6) | ✅ | `crawl(root_url, query=None, max_depth=3, max_pages=15, same_host=False, min_score=0.05)`: BFS over the link graph with a relevance-ranked frontier (`0.7 × query coverage + 0.3 × containing-page topic coverage`, effective `score × 0.7^depth`; ties by discovery order so flat scores degrade to plain BFS); page budget counts successful fetches only; boilerplate paths and static assets skipped; document links collected, never fetched; full pages stay in the page cache for in-full `inspect_html_page` re-reads; global budget enforced |
| HTML metadata merged into `DocumentMetadata` | ✅ | OG, Twitter, JSON-LD, Dublin Core, rel links |
| URL slug derivation for `file_name` | ✅ | Handles paths and root URLs |
| Token-aware truncation | ✅ | Respects `max_tokens` budget |

**Verdict**: ✅ Complete

---

## 11. Two-Tier Caching (Task 5B)

| Requirement | Status | Notes |
|-------------|--------|-------|
| In-memory LRU cache | ✅ | `OrderedDict`-based with configurable max entries |
| File-based TTL cache | ✅ | JSON serialization with timestamp-based expiry |
| Two-tier read (memory → disk → miss) | ✅ | Disk hits promote to memory |
| Cross-instance persistence | ✅ | Disk cache shared across Cache instances |
| Cache stats (hits/misses/hit_rate/disk_size) | ✅ | Exposed via `cache.stats()` and `get_cache_stats()` |
| Integrated into `WebResearcherToolbox` | ✅ | Replaces inline cache; wired into `extract_document` |

**Verdict**: ✅ Complete

---

## 12. CI/CD Pipeline (Task 5C)

| Requirement | Status | Notes |
|-------------|--------|-------|
| Test matrix (OS × Python versions) | ✅ | ubuntu/windows × 3.9–3.13 |
| Rust clippy lint | ✅ | `cargo clippy -- -D warnings` |
| Python lint (ruff) | ✅ | `ruff check stitch_web_researcher/` |
| Wheel building (multi-platform) | ✅ | ubuntu/windows/macos on tag push |
| sdist building | ✅ | Source distribution via `maturin sdist` |
| PyPI publish | ✅ | `pypa/gh-action-pypi-publish` with token secret |
| LICENSE file | ✅ | MIT/Apache-2.0 dual license |

**Verdict**: ✅ Complete

---

## Summary

| Area | Status |
|------|--------|
| Rust Core | ✅ Complete |
| Python Orchestration | ✅ Complete |
| Multi-Provider Search | ✅ Complete |
| Structured Parsing | ✅ Complete |
| Token Budgeting | ✅ Complete |
| HTML Metadata | ✅ Complete |
| Smart Fetch | ✅ Complete |
| Package & Distribution | ✅ Complete |
| HTML Structured Parsing | ✅ Complete |
| Two-Tier Caching | ✅ Complete |
| CI/CD Pipeline | ✅ Complete |
| **Overall** | **✅ Feature-Complete** |

**145 tests passing** · **Clean wheel** · **All v4 spec requirements met**

The remaining gaps (meta-oxide packaging, browser_oxide install, office_oxide from git, benchmarks in CI, type stubs, error types) are all low-priority polish items, not spec violations. The project is ready for distribution.
