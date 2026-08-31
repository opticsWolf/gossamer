# Changelog

Reconstructed from git history on 2026-08-28 (prior to that, release notes
lived in commit messages only). One line per version bump commit; tier/finding
labels (C/S/M/P/T) reference `docs/CODE_REVIEW_2026-08-27.md`.

## [Unreleased]

- **Citations: APA (7th) now renders via citeproc-py (official CSL engine).**
  `citations.py` gains an optional CSL rendering path: `to_apa` builds
  CSL-JSON (lowercase `id`; string `container-title`, since citeproc-py's
  plain formatter does a bare `str()` on list fields) and renders it with the
  bundled `apa.csl` style through `CitationStylesBibliography`. When
  citeproc-py is not installed, or the style file is unavailable, it falls
  back to the retained pure-Python approximation, so the module and the
  `export_citations` tool stay fully functional offline. MLA has no style
  shipped with citeproc-py-styles, so it always uses the approximation.
  citeproc-py / citeproc-py-styles are an optional `citations` extra (lazy
  import; BibTeX / CSL-JSON formatters stay pure and tests stay green with
  or without it). Adds `TestCiteprocIntegration` (citeproc path + forced-
  unavailable fallback). Full suite green (1162 passed).

- **`agent_tools.py` composition split (god-class reduction), incremental.**
  The 5100-line `WebResearcherToolbox` facade is split into focused
  collaborators; each phase keeps the class body's behaviour identical and
  the full suite green.
  - **Phase 1** (`config.py`, `models.py`, `fetch.py`, `crawl.py`): moved
    module-level helpers out of `agent_tools.py` and re-exported them so the
    public import surface (`mcp_server.py`, `__init__.py`, tests) is
    unchanged.
  - **Phase 2** (`search.py`): moved the search concern (search/failover,
    merge + dedup, result-level cache, provider resolution, `_finish_search`)
    into `SearchService`. The three public search entry points stay on the
    facade as thin delegations; `SearchService` reads shared state through
    `self._tb` (the toolbox), so a caller that reassigns `tb.providers` is
    seen live instead of via a stale captured copy.

- **Unified API-access interface: `ResourceAdapter`** (new ABC in
  `search_providers.py`). Every data source the harness can ask for -- a web
  search engine, a scholarly index, a legal/financial/geo feed -- now shares
  one contract: politeness (per-call gap + jitter), a hard per-window quota
  (raises the new `QuotaExhaustedError`), auth injection, live header-based
  retuning, and a retry/backoff `search()` wrapper. Callers never see a raw
  429; a quota stop is never retried so the harness can fail over.
  - `SearchProvider` is now a narrowing of `ResourceAdapter`
    (`domain="search"`); the five search engines are unchanged from the
    outside (all existing provider tests still pass).
  - New `research_providers.py` module with two concrete domain adapters built
    on the base: `OpenAlexAdapter` (scholarly, polite-email auth) and
    `OpenMeteoAdapter` (geo, 10 000/day quota). More follow the §4 matrix in
    `docs/research_access_layer_plan.md`.
  - `RateLimit` gains `jitter`, `quota`, `quota_window`; new exports
    `RateState` / `QuotaExhaustedError`.
  - **Per-provider politeness + quota defaults wired into every legacy
    search engine** (previously they all fell through to `RateLimit()`
    with `jitter=0.0` / `quota=None`, so search had no jitter and no
    quota enforcement). Each engine now falls back to its own constant
    when constructed without an explicit delay, encoding the engine's
    real limits: DuckDuckGo/Browser-Oxide `1.0 s + 1.0 s` jitter, no
    quota; Google `0.2 s + 0.1 s` jitter, `100/day`; Bing `0.2 s + 0.1 s`
    jitter, no quota; Exa `0.1 s + 0.05 s` jitter, `1000/month`. An
    explicit `delay`/`RateLimit`/`fetch_delay` still wins; the module
    constants are never mutated by construction.
  - **DuckDuckGo politeness unified to a single default (`1.0 s + 1.0 s`
    jitter) across standalone use and the toolbox default.** The toolbox
    now constructs its auto-default provider as
    `DuckDuckGoProvider(delay=config.ddgs_delay, jitter=config.ddgs_jitter)`
    instead of a flat `delay=1.0`; `ToolboxConfig` gains `ddgs_jitter`
    (default `1.0`) alongside `ddgs_delay`, and `mcp_server.py` exposes it
    via `STITCH_DDGS_JITTER` (default `1.0`). `DuckDuckGoProvider` and the
    base `_init_rate_limit` accept an optional `jitter` that only applies
    when a float `delay` is given; the module-level `_DUCKDUCKGO_RATE_LIMIT`
    constant is unchanged by construction. Search politeness stays fast in
    tests: `search_interval=0` skips the gap (and its jitter) entirely.
- **`ExaProvider` no longer depends on the `exa-py` SDK.** It now calls
  Exa's REST API directly with `httpx` (`POST
  https://api.exa.ai/v1/search`, `Authorization: Bearer`), so Exa works
  out of the box whenever `EXA_API_KEY` is set -- no optional package to
  install. The public contract is unchanged (same `search_type` /
  `search(query, max_results)` shape, same `title`/`url`/`snippet`
  output). The `_exa_available` guard is removed; `get_default_providers`
  now enables Exa purely on `EXA_API_KEY`.
- **Exa provider surface expanded** to mirror the `exa-py` SDK search
  options against the REST API. `ExaProvider.search()` now accepts SDK-style
  keyword arguments forwarded to `POST /v1/search`: `type` (all Exa modes --
  `auto`/`instant`/`fast`/`deep-lite`/`deep`/`deep-reasoning`),
  `contents` (`highlights`/`text`/`summary`/`extras`/`livecrawl`/`maxAgeHours`),
  `include_domains` / `exclude_domains`, `start_published_date` /
  `end_published_date`, `category` (`company`/`publication`/`people`/...),
  `moderation`, `system_prompt`, `output_schema` (structured output),
  `additional_queries` (deep-search variants), `text_filters`,
  `result_filters`. snake_case SDK names translate to the REST camelCase
  fields; an explicit `num_results` overrides `max_results`. Results always
  carry `title`/`url`/`snippet` (toolbox-compatible) plus richer fields
  (`text`, `summary`, `publishedDate`, `author`, `linkingDomains`,
  structured `content`) when the API returns them. The base interface
  (`_search_impl`, `search(query, max_results)`) is unchanged. Tests:
  `tests/test_exa_provider_features.py` (+18).
- **Fetch-path politeness: configurable jitter + crawl-aware throttling.**
  The per-domain fetch gap is now `fetch_interval + uniform(0, fetch_jitter)`
  where `fetch_jitter` is a new `ToolboxConfig.fetch_jitter` (default `1.0`,
  preserving the historical 0.5-1.5 s gap; set `0` to disable). The old
  hardcoded `random.uniform(0.0, 1.0)` is gone. Throttling is now
  crawl-aware: `crawl()` forwards its root host key as `politeness_root` to
  the fetch path, so only same-domain (intra-site) pages are spaced out --
  cross-domain links (each visited at most once) skip politeness entirely,
  keeping the crawl fast without hammering external hosts. Single/batch
  fetches pass `politeness_root=None` and keep the historical per-domain
  behaviour (a first visit to any unseen domain takes no gap). Wired through
  the MCP server as `STITCH_FETCH_JITTER` (default `1.0`). Tests:
  `tests/test_s7_fetch_politeness.py` (+8).

- **Citation reconstruction and export (Plan workstream 1, new `citations.py`).**
  Reconstructs bibliographic records from the result dicts the scholarly
  adapters already return (`doi`, `id`, `title`, `authors`, `published`,
  `raw`) with **no extra network calls**, and renders them as BibTeX,
  CSL-JSON, APA or MLA. The adapters support two shapes -- the unified form
  (`authors` a ", "-joined string, `published` a date string) and Crossref's
  native form (`authors` a list of `{family, given}` dicts, `published` a
  `{date-parts}` dict) -- and `record_from_result()` normalises both.
  `enrich_with_doi()` can optionally make one canonical DOI lookup per
  unique DOI to fill a missing venue/abstract; the adapter is injectable so
  tests stay offline. `format_citations()` accepts adapter result dicts, bare
  DOIs, or URLs, and dedupes by DOI then URL. The new `export_citations`
  MCP tool (`config.py` registry + `agent_tools.py` facade method) takes a
  `list[str]` of DOIs/URLs/JSON-serialized dicts and returns the formatted
  citations as text (a JSON error dict on empty / bad style, never a raise).
  APA/MLA are documented approximations, not a full CSL-STYLE processor.
  Tests: `tests/test_citations.py` (+32).

## [0.5.0]

- **`research_by_category`: category-aware, provider-specific search tool.**
  A thin overlay on the generic `web_search` toolbox. Given a free-form
  query it classifies the query into a domain *category* and triggers the
  provider best suited to it, so the harness can reach domain sources
  (scholarly / geo) without knowing their names up front.
  - Categories are declared as data in the new `research_categories.py`
    (`scholarly` -> `openalex`, `geo` -> `open-meteo`, `general` ->
    `duckduckgo`); classification is keyword based with word boundaries so
    `late breaking news` does not match the bare token `lat`. The
    `general` category is the implicit fallback.
  - The routing table is the single source of truth: the tool's LLM-facing
    `description` is auto-generated from it (`describe_categories()`), so
    adding a category updates the description with no drift. A domain
    category routes to its adapter called directly (kept out of the default
    search provider list so `web_search`'s behaviour is untouched); the
    engine fallback goes through the normal caching/deduped search path.
  - `research_categories()` introspection helper returns the taxonomy as
    JSON. It is a callable facade method but is deliberately **not**
    registered in `TOOL_REGISTRY`, so it is not part of the MCP surface.
  - Version bump `0.4.13 -> 0.5.0` (Python + Rust core in sync).

## [0.4.13]

- **Phase 6 (`discovery.py`, `budget.py`): extract the final two cohesive
  clusters of the `agent_tools.py` composition split.** This is the last
  composition phase -- the four extracted modules from Phase 1 plus these
  two leave the `WebResearcherToolbox` facade as a thin composition root
  (public entry points, tool dispatch, and `research()` orchestration stay
  on the facade). Each collaborator reads shared state through
  `self._tb` (the toolbox), mirroring `SearchService` / `FetchService` /
  `DocumentExtractor` / `Crawler`.
  - **`discovery.py` -> `ResourceDiscovery`**: moves the site-resource
    discovery cluster (`discover_resources` full implementation,
    `_find_feed_links`, `_probe_sitemaps`, and the `_DISCOVER_*` /
    `_FEED_*` / `_LINK_*` constants) out of the facade. The facade keeps one
    thin `discover_resources` delegation, so the tool registry and public
    surface are unchanged. URL-prep / robots / rate-limit / visited-state
    reads go to `self._tb._prepare_url`, `self._tb._robots_disallows`,
    `self._tb._claim_in_flight`, `self._tb._release_in_flight`,
    `self._tb._rate_limit_domain`, `self._tb._validate_url`, and the fetch
    reads to `self._tb._fetch._fetch_html_with_html` /
    `self._tb._fetch._static_fetch`.
  - **`budget.py` -> `ContentBudget`**: moves the output-content budget
    enforcement (`_truncate`, `_content_budget`, `_shrink_parsed_payload`,
    `_shrink_research`, `_json_fits`, `_fit_json`) out of the facade; it
    reads configuration through `self._tb` (`model_name`, `link_budget_ratio`,
    `max_markdown_chars`, `max_tokens`). Every external caller is
    retargeted: `fetch.py` / `document.py` / `crawl.py` call
    `self._tb._budget.*`, and the facade's own `research()` calls
    `self._budget._fit_json` / `self._budget._shrink_research`. The moved
    clusters gain the imports they lost with the move
    (`json`, `copy`, `re`, `count_tokens`, `truncate_to_tokens`,
    `ParsedDocumentPayload`, `_JSON_FIT_FLOOR`, `urljoin`, `urlparse`).
    Tests that patched moved seams (`tb._FEED_TYPE_PREFIXES`) now read
    `tb._discovery._FEED_TYPE_PREFIXES`.

## [0.4.12]

- **Phase 5 (`crawl.py`): extract the focused-crawl orchestration into a
  `Crawler` collaborator.** Moves the best-first crawl cluster — the
  `_CRAWL_*` scoring constants, `_crawl_tokens`, `_crawl_topic_words`,
  `_crawl_is_document`, `_crawl_anchor_context`, `_crawl_path_prior`,
  `_crawl_term_hits`, `_crawl_excerpt`, `_crawl_expand_query`, `_crawl_score`,
  `_shrink_crawl`, and `crawl()` — out of the `WebResearcherToolbox` facade
  into `crawl.py` (which already held the `_CrawlCorpus` /
  `_load_thesaurus` helpers). The facade keeps one thin delegation (`crawl`)
  so the public import surface and tool dispatch are unchanged. `Crawler`
  reads all shared toolbox state through `self._tb` (the toolbox), mirroring
  `SearchService` / `FetchService` / `DocumentExtractor`: FetchService reads
  go to `self._tb._fetch.*`, and the 6 facade-state reads (`_validate_url`,
  `_fit_json`, `max_tokens`, `max_markdown_chars`, `search_web`,
  `_inspect_html_page_impl`) are retargeted to `self._tb.*`. `_crawl_host_key`
  is a small static helper shared by the kept facade method
  `_rate_limit_domain`, so it stays on the facade and the 4 `crawl()` calls
  are retargeted to `self._tb._crawl_host_key`. The moved cluster gains the
  imports it lost with the move (`re`, `os`, `normalize_url`,
  `DOCUMENT_EXTENSIONS`, `SsrfBlockedError`). Tests that patched moved seams
  (`tb._crawl_score`, `T._crawl_excerpt`, ...) now call `Crawler.*`.

## [0.4.11]

- **Phase 4 (`document.py`): extract the document-extraction + structured-
  inspection concern into a `DocumentExtractor` collaborator.** Moves the
  `_finish_document`, `extract_document`, `_fetch_document_url`,
  `_download_and_extract`, `_extract_document_pages`, `_parse_document_pages`,
  `_extract_local`, `_extract_from_bytes`, `_extract_json_text`,
  `_extract_xml_feed`, `extract_document_structured`,
  `_extract_document_structured_impl`, `_extract_html_tables`,
  `inspect_html_structured`, and `_inspect_html_structured_impl` cluster out of
  the `WebResearcherToolbox` facade into `document.py`. The facade keeps three
  thin delegations (`extract_document`, `extract_document_structured`,
  `inspect_html_structured`) so the public import surface and tool dispatch are
  unchanged. `DocumentExtractor` reads all shared toolbox state through
  `self._tb` (the toolbox), mirroring `SearchService` / `FetchService`: a
  caller that reassigns toolbox attributes is seen live instead of via a stale
  captured copy. The one internal caller of a moved method (`inspect_html_page`
  -> `_inspect_html_structured_impl`) is retargeted to
  `self._doc._inspect_html_structured_impl`; `fetch.py`'s
  `self._tb._extract_html_metadata` stays on the facade. Tests that patched
  moved seams (`tb._extract_local`, `tb._fetch_document_url`,
  `tb._extract_from_bytes`, `agent_tools.extract_tables_from_html`) now patch
  `tb._doc.*` / `document.*`.

## [0.4.10]

- **Per-phase versioning of the `agent_tools.py` composition split.** Each
  composition phase now bumps the patch version by 0.0.1 (Phase 3 = 0.4.10),
  so every extracted collaborator has its own released version. Phase 3
  (`fetch.py`): moves the fetch/inspect concern (fetch strategies, page-level
  two-tier cache, `inspect_html_page` / `batch_inspect_pages`, result
  scanning) into a `FetchService` collaborator, leaving thin facade
  delegations.

## [0.4.9]

- **`use_smart` is now an explicit tri-state render strategy** (was `bool`/
  `None`). New values: `"auto"` (default, follows `fetch_mode` -- static
  first, stealth `browser_oxide` on failure/non-text), `"browser"` (headless
  browser first, static on failure), `"static"` (static-only). The per-call
  default is now `"auto"` (previously `False`), so the `auto` -> stealth
  fallback is active by default through every entry point, including the
  `execute_tool` / MCP path that previously forced static-only. Backed by
  `FetchMode` plus `_coerce_fetch_mode` / `_resolve_fetch_strategy`, which
  collapse `(fetch_mode, use_smart)` into one of four strategies
  (`static-only`, `browser-only`, `browser-first`, `auto`). Passing a bool
  now raises `ValueError` instead of being misread.

- **Crawl now reports `fetch_method` on every page record** (root and
  discovered pages), so a crawler can see which pages needed the stealth
  browser. Previously only `inspect_html_page` / `research` surfaced it.

- **Batch `batch_inspect_pages` `fetch_mode="auto"` now falls back to the
  stealth browser** (was static-only). A page the static Rust engine can't
  render (empty / non-text / JS-rendered body) is re-fetched through the
  Python `browser_oxide` path, exactly like single-page `inspect_html_page`
  and crawl, and the entry's `fetch_method` reports `"stealth-fallback"`
  (or `"static"` for pages the static engine served). The whole batch stays
  static-only for `fetch_mode="static"`, and the page cache is overwritten
  with the method that actually served each page.

- **Tool surface reduced 13 → 7 via clean folds (P8)**. The single
  `TOOL_REGISTRY` now exposes: `web_search` (folds the old `search_web`
  pure-search and `research` orchestration into one tool with a `search_only`
  flag -- `false`/default keeps the old research behavior), `inspect_html_page`
  (new `structured=True` returns the old `inspect_html_structured` payload),
  `batch_inspect_pages`, `extract_document` (new `structured=True` returns the
  old `extract_document_structured` payload), `discover_resources`, `crawl`,
  and `manage_cache` (folds the old `clear_cache` / `prune_cache` /
  `reset_visited` cache tools into one `action`-dispatched tool). The old
  `search_web`, `research`, `extract_document_structured`,
  `inspect_html_structured`, `clear_cache`, `prune_cache`, `reset_visited`
  methods are retained as thin backing methods so existing callers and tests
  keep working; `get_stats` remains a toolbox method but is no longer exposed
  as an MCP/LLM tool.

- **Indirect prompt-injection hardening of delivered content (Pattern 3 + 4).**
  When the optional guard is enabled, every delivered scope is (a) wrapped in
  an explicit `<untrusted-web-content source="url">` marker carrying a
  directive that the enclosed text is data, not instructions (Pattern 3), and
  (b) normalized before the detector scans it: every Unicode `C*` category
  character (zero-width spaces U+200B/U+200C/U+200D, zero-width joiner, BOM,
  bidirectional controls U+202A..U+202E, other `Cf`) except `\n\r\t` is
  stripped, and NFKC normalization resolves compatibility glyphs (fullwidth,
  ligatures, circled). The detector therefore scores exactly what the model
  reads, and redaction offsets stay aligned. NFKC does **not** merge true
  homoglyphs (Cyrillic `а` vs Latin `a`) -- those are ordinary letters, not
  compatibility chars -- and are left to the detector / an allowlist. The
  transform is opt-in: with the guard off (the default) output is
  byte-identical. `guard.normalize_untrusted_text()` is exported for reuse.

## [0.4.8] — semantic crawl: BM25/IDF frontier scoring, thesaurus, richness, ranked documents, discovery seeds

- **BM25/IDF scoring (feature A)**: the crawl frontier's relevance score
  now weights terms by inverse document frequency over the pages fetched
  so far (`_CrawlCorpus`, fed after every successful fetch and before that
  page's links are scored). While fewer than 3 pages have been read all
  weights are flat, so the scorer starts exactly like v0.4.6 and sharpens
  as the crawl reads the site.
- **Anchor context (feature A)**: words within ±50 chars of a link's
  anchor text in the containing page's rendered markdown join the
  candidate's label (capped at 8 tokens, highest-frequency first, cached
  per page per anchor; fail-open when the anchor is not in the body).
- **URL path priors (feature A)**: documentation-ish paths
  (`/docs/`, `/blog/`, `/api/`, …) score ×1.15 and transactional ones
  (`/pricing`, `/careers`, …) ×0.85, applied only in the non-degenerate
  (≥ 3 pages) regime. Table-driven in `_CRAWL_PATH_PRIOR_GROUPS`.
- **Offline thesaurus (feature B)**: `thesaurus.json` (31 clusters,
  ~230 curated topic terms, no ultra-generic tokens) expands the query
  with half-weighted synonyms, capped at 2× the base size, deterministic
  iteration. The query echo reports additions ("deep learning +2").
  Loader fails open to expansion-off on any problem.
- **Richness payload (feature C)**: every page record now carries
  `content_chars` (full delivered size, pre-skim) and `term_hits`
  (query-term occurrences in the full body). Opt-in `excerpts=True`
  adds a keyword-densest 300-char `excerpt` per page (window 300,
  step 100; densest window wins, ties earliest, zero density omitted,
  ellipses mark a window not touching the head or tail).
- **Ranked documents (feature D)**: `documents` is now a rank-ordered
  list of `{url, anchor, score}` records — scored at first sighting
  with the live corpus (depth 0, no decay), floored by `min_score`
  (below-floor entries counted in `documents_below_score` and
  reported in `skipped`); still never fetched.
- **Search prior (feature E1)**: `search_prior=True` (opt-in) runs one
  site-scoped web search (`site:<host> <focus>`, top 5) before the crawl
  and feeds the results into the frontier at depth 1 with a rank bonus
  (+0.1/(rank+1), ties by rank). Results are exempt from `min_score`
  (the engine already ranked them), document results route to
  `documents` like page links, and a failed search is fail-open
  (warning logged, the crawl continues link-graph only). The payload
  echoes `search_prior` and, when on, `search_results` (eligible
  count).
- **Seed URLs (feature E2)**: `seed_urls` (list, default empty) are
  caller/agent-supplied starting URLs — normalised against the root,
  SSRF-checked in full (S1), pushed at depth 0 (their children are
  depth 1), and subject to `min_score`; a below-floor seed is skipped
  with reason `"seed below min score"`, a blocked one as
  `"ssrf blocked"`. Seed fetch failures are normal non-fatal `errors`
  entries that do not consume budget.
- **Cross-modal loop (feature E3)**: the intended agent pattern (no
  mechanism added): crawl → top `documents` → `extract_document` → its
  `links` (v0.4.5 text link detection) → next crawl with `seed_urls`.
- New tests: 16 for A+B, 7 for C+D, 14 for E1–E3 in
  `tests/test_crawl_semantic.py`; legacy no-corpus `_crawl_score` calls
  return bit-for-bit v0.4.6 results.

## [0.4.7] — clean-install fix for the meta-oxide dependency

- `meta-oxide` now resolves to the packaging-fixed fork
  (`git+https://github.com/opticsWolf/meta_oxide.git@81bdb53`, v0.1.2).
  The PyPI releases ship a broken sdist (missing python source), so a
  clean `pip install` of this package failed before this fix. Verified in
  a fresh venv: install + full suite green (860 passed, 1 skipped, 7
  deselected). Note: the PEP 508 direct reference means this project
  cannot be uploaded to PyPI until meta-oxide 0.1.2 is published and the
  dependency is switched back to `meta-oxide>=0.1.2`.
- `browser-oxide` moved to the optional `[browser]` extra: PyPI ships
  macOS/Windows wheels only, so it could never install on Linux; the code
  degrades to the static fetch path when it is absent.

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

- All nine `docs/IMPLEMENTATION_BUGFIX_PLAN.md` items: URL rejections honor the JSON
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
