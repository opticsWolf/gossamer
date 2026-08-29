# Changelog

Reconstructed from git history on 2026-08-28 (prior to that, release notes
lived in commit messages only). One line per version bump commit; tier/finding
labels (C/S/M/P/T) reference `CODE_REVIEW_2026-08-27.md`.

## [0.4.6] — focused crawl

- New tool: `crawl(root_url, query=None, max_depth=3, max_pages=15,
  same_host=False, min_score=0.05)` — bounded BFS over the link graph with a
  relevance-ranked frontier (`score × 0.7^depth`; query coverage +
  containing-page topic coverage); flat scores degrade to plain BFS; failed
  fetches do not consume the page budget; documents collected, never fetched;
  full pages stay in the page cache for in-full re-reads (registry: 13 tools)

## [0.4.5] — text-level link detection

- `extract_links(text)`: stdlib regex detector for URLs written inside
  document text (`http/https` + `www.` promotion, trailing Latin/CJK
  punctuation stripped, deduped, capped at 50); wired into
  `extract_document` (`links`) and `extract_document_structured`
  (`payload.links`)

## [0.4.4] — bugfix plan release

- All nine `IMPLEMENTATION_BUGFIX_PLAN` items: URL rejections honor the JSON
  error contract; serialized JSON is never string-cut (M11 shrinkers);
  section selection sees Setext headings; batch 4-tuple ABI parity with
  single-page reads; named Rust type aliases; `normalize_url("report.pdf")`
  no longer promoted to a URL; provider visibility (`available_providers`,
  fallback note); `_risk_name` enum/str normalization; `benchmarks.py --guard`
  with a planted-injection corpus and stub/real backend auto-selection
- `.pdb` untracked and excluded from wheels via `.gitignore` (closes P3)
- Test count 734 → 816

## [0.4.3] — research orchestration (Tier 3.13)

- New tool: `research(topic, depth=5, max_tokens=0)` — search, dedupe,
  fan-out through the normal page pipeline, per-source status + provenance,
  global budget with later sources yielding first; synthesis left to the
  calling agent

## [0.4.2] — sitemap-aware discovery (Tier 3.12)

- New tool: `discover_resources(url)` — feed declarations
  (`<link rel=alternate>`) plus a bounded `/sitemap.xml` probe (index hops
  ≤ 3, ≤ 10 fetches, 500 URLs per sitemap, 1000 total); page left unvisited

## [0.4.1] — HTML table extraction (Tier 3.11)

- `inspect_html_structured` gains `tables` (top-level `<table>` grids,
  capped at 20 tables × 500 rows, cells truncated at 1000 chars) via a new
  Rust binding; raw HTML kept on the static fetch path (5-tuple)

## [0.4.0] — more input formats (Tier 3.10)

- `extract_document` supports 11 formats: PDF, DOCX, XLSX, PPTX, TXT, MD,
  CSV, JSON, XML, RSS/Atom feeds (returned as entry lists)

## [0.3.4] — real async path (Tier 2.9)

- `batch_inspect_pages_async` (Rust `batch_research` fan-out); honest
  documentation that async wrappers are thread-pool offloading, not native
  async I/O

## [0.3.3] — search-result caching (Tier 2.8)

- Provider search results cached and merged across provider fallback

## [0.3.2] — transport overrides (Tier 2.7)

- Proxy, custom headers, and cookie support on the static fetch path
  (config + `STITCH_*` env knobs)

## [0.3.1] — observability (Tier 2.6)

- Fetch telemetry in `get_stats` (per-domain counters, cache hit rate,
  timings) plus a Rust log bridge

## [0.3.0] — disk cache eviction (Tier 2.5)

- Disk cache size cap with LRU eviction; new `prune_cache` tool

## [0.2.3] — prompt-injection guard (§7)

- Optional `[guard]` extra (JailGuard): lazy import, fail-open, annotate vs
  redact modes, chunked scanning with cached verdicts, `STITCH_GUARD_*` env
  knobs, `get_stats()["guard"]`

## [0.2.2] — conditional revalidation (Tier 1.4)

- Stale page-cache entries revalidated with ETag/Last-Modified (304) before
  full re-download

## [0.2.1] — provenance (Tier 1.3)

- `fetched_at`, `http_status`, `final_url`, `content_type`, `content_hash`
  in research payloads

## [0.2.0] — chunked reads (Tier 1.2b)

- `inspect_html_page` paging via `offset` / `max_chunks` (resumable reads of
  over-budget pages)

## [0.1.35] — page-range reads (Tier 1.2a)

- `extract_document` page ranges (PDF pages / XLSX sheets)

## [0.1.34] — relevant sections (Tier 1.1)

- Over-budget pages deliver query-relevant sections instead of
  head-first truncation

## [0.1.33] — M16

- `extract_document` tool description advertises only the formats it can
  deliver

## [0.1.32] — M15

- Retry 429/503 and honor `Retry-After`

## [0.1.31] — M14

- Text-gating decision uses head, middle, and tail samples

## [0.1.30] — M13

- Caller `RateLimit` is copied, never aliased and mutated

## [0.1.29] — M12

- Relative hrefs in delivered markdown are absolutized against the page URL

## [0.1.28] — M11

- Payload budgeting no longer re-tokenizes on every pass; shrink-then-
  serialize instead of cutting serialized JSON

## [0.1.27] — M10

- Batch failures explicit with a tagged `BatchEntry`

## [0.1.26] — M9

- One shared HTTP client across fetches (connection pool reuse)

## [0.1.25] — M7

- Per-process state bounded (LRUs with caps) so long-lived MCP servers do
  not grow without limit

## [0.1.24] — M6

- `asyncio.get_running_loop` replaces deprecated `get_event_loop`

## [0.1.23] — M5

- gpt-4o mapped to the correct tiktoken encoding

## [0.1.22] — M4

- `truncate_to_tokens` fallback cut clamped (never past the last token
  boundary)

## [0.1.21] — M3

- `@retry` on `search_web` was dead code; retry moved into provider search

## [0.1.20] — M2

- Provider aliases actually select providers

## [0.1.19] — M1

- Local file paths are never promoted to URLs

## [0.1.18] — P8

- Unified tool registry + `execute_tool` dispatcher (single source of truth
  for all surfaces)

## [0.1.17] — P7

- Shipped typing surface: `py.typed` + `_core.pyi`

## [0.1.16] — P2

- `pdf_oxide` / `office_oxide` imports made lazy (`documents` extra)

## [0.1.15] — P1

- Real runtime dependencies declared; optional extras split (`mcp`,
  `documents`, `guard`-style)

## [0.1.14] — S4

- robots.txt compliance (Disallow/Allow + Crawl-delay + opt-out)

## [0.1.13] — S7

- MD5 cache-key hashing replaced with blake2b

## [0.1.12] — S6

- `clear_cache` scoped to cache-owned files (no more `rmtree` of the
  configured dir)

## [0.1.11] — S5

- Cache and toolbox made thread-safe (locking, atomic writes, in-flight URL
  guard)

## [0.1.10] — S3

- Response size cap + content-type gate in the Rust core

## [0.1.9] — S2

- Hidden HTML (`display:none`, comments, scripts) stripped before markdown
  conversion

## [0.1.8] — S1

- SSRF guard for LLM-supplied URLs (DNS resolution of every returned
  address, private/internal ranges, `.local`-style suffixes)

## [0.1.7] — C2

- Static fetch path extracts real metadata (`fetch_html_full` keeps the raw
  HTML so meta-oxide runs without a second fetch)

## [0.1.6] — C7

- CI made green and deterministic (clippy type aliases, ruff pin, OS-split
  install steps)

## [0.1.5] — C6

- `batch_inspect_pages` shares the page cache with single-page inspection

## [0.1.4] — C5

- `parse_html` populates `ParsedDocumentPayload.links` (deduped, titled,
  typed, capped)

## [0.1.3] — C4

- Document-cache hits re-apply the token/char budget on read

## [0.1.2] — C3

- URLs marked visited only after a successful fetch; cache served on repeat
  visits; `reset_visited` exposed; clear resets the visited set

## [0.1.1] — C1

- Links quota reserved in the output budget so content-rich pages keep
  follow-up links (`delivered_links`)

## [0.1.0] — initial release

- Hybrid Rust/Python web researcher: Rust async fetch core (PyO3), page
  cache with TTL, multi-provider search (DDG/Google/Bing/Exa), markdown
  extraction with token-aware budgeting, MCP server
