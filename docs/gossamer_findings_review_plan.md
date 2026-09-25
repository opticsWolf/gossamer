# Gossamer findings review and implementation plan

**Review date:** 2026-09-25  
**Reviewed tree:** `dev_rust` at `f042d15` (`0.9.6`)  
**Source report:** [`gossamer_findings.md`](gossamer_findings.md)  
**Scope:** Compare the reported literature-hunt findings with the current implementation; distinguish confirmed defects, partially implemented features, and requests that require a product decision; propose an ordered, testable implementation plan.

No source code was changed during this review. The plan below is not an implementation commitment or version bump.

---

## 1. Executive summary

The main reliability findings are valid. The CLI writes its result directly to the current text stream, and this Windows environment reports `cp1252`; a controlled CLI test with Greek `μ` produced no JSON and returned exit code 1. The arXiv adapter also fails live: a current query received HTTP 406 and the generic retry wrapper issued the failing request three times. Provider exceptions are currently encoded inconsistently: normal responses have a list in `results`, while provider failures have an error dictionary there; the CLI then returns success because the error is data, not an exception.

Several findings need qualification:

- `extract URL --store` already downloads and saves the original bytes together with extracted Markdown. The missing feature is a standalone, opaque-file download command with reliable validation and failure reporting—not all ability to save a fetched document.
- The arXiv live smoke test already exists, but is opt-in and does not assert `Accept`; it should be corrected/strengthened rather than duplicated. Its current search-plus-fetch path uses `delay=0`, which bypasses the adapter's documented three-second interval.
- OpenAlex already sends an email in its `User-Agent` and `Contact-Agent` headers and recognizes `GOSSAMER_OPENALEX_EMAIL`; it does not send the `mailto` parameter described in the report, uses a placeholder fallback email, and does not honor provider retry guidance.
- There is no Semantic Scholar provider in gossamer today. The classification keyword is not provider support; no Semantic Scholar key variable or adapter contract currently exists. **Integration is now confirmed and is included as a build milestone below**; the exact upstream endpoint/auth/field contract must be verified before coding.
- F5's documentation alternative is already satisfied: `skills/gossamer/SKILL.md` includes the Windows venv invocation. A PATH shim is optional.

Recommended order: (1) CLI encoding, HTTP retry behavior, arXiv and OpenAlex request correctness; (2) stable provider-error response and CLI exit status; (3) standalone download and DOI-to-OA resolution; (4) provider-native OpenAlex query controls and the confirmed Semantic Scholar adapter; (5) opt-in cross-provider merging, including Semantic Scholar; (6) mirror/human-needed behavior and collection-workflow documentation.

---

## 2. Findings compared with the code

### B1 — CLI output encoding: confirmed, high priority

**Current code:** `gossamer/cli.py:160-199` dispatches to a toolbox method and calls `print()` on the returned value. It does not normalize stdout/stderr encoding. `tests/test_cli.py` covers parsers, dispatch, and basic errors, but no non-ASCII output.

**Reproduction:** This environment's Python process reported `sys.stdout.encoding == "cp1252"`. With a stub result containing Greek small letter mu (`μ`, U+03BC) and a strict cp1252 stream, `main()` returned 1, stdout was empty, and stderr contained a `charmap` encoding error. (This is Greek `μ`, not the cp1252-encodable micro sign `µ`.)

**Conclusion:** The report is accurate: the user gets no valid JSON. The current CLI catches the encoding exception as a `ValueError`, so this is more precisely a failed command rather than an uncaught process crash.

### B2 — arXiv HTTP 406: confirmed; suspected cause needs isolation

**Current code:** `ArxivAdapter` is in `gossamer/research_providers.py:469-542`. It requests `http://export.arxiv.org/api/query`, sets `_ARXIV_UA = "gossamer/0.5.3 (mailto:researcher@example.org)"`, and sends no explicit Atom `Accept` header (`_get`, lines 497-508). The generic Python retry decorator retries every exception three times with fixed 1s / 2s waits and does not inspect status or `Retry-After` (`gossamer/search_providers.py:112-144`).

**Reproduction:** A live CLI query to arXiv returned 406 on all three attempts. Thus the report's observed failure is current. However, the explanation “httpx default User-Agent” is not exact: this adapter overrides httpx's default UA with a custom but stale value and placeholder contact. Missing/incorrect `Accept`, the placeholder identity, and/or the request scheme are candidates; they need a controlled request comparison before attributing the fix to one header.

**Existing coverage:** `tests/test_live_smoke.py:102-110` already has an opt-in search-and-fetch test. It is gated by `GOSSAMER_LIVE`, so it does not run in the ordinary offline suite. It uses `ArxivAdapter(delay=0.0)` and makes two requests, despite the adapter's documented responsible-use interval of one request per three seconds. `tests/test_research_providers.py` checks the current User-Agent but not `Accept`.

### B3 — provider error shape and CLI status: confirmed

**Current code:** `research_categories.search_category()` (`gossamer/research_categories.py:501-520`) catches provider exceptions and assigns `results = {"error": ...}`. Successful provider calls return a list. This creates the union-shaped `results` field reported in the findings. `gossamer/cli.py:194-199` returns zero after printing any normal toolbox result; it only returns 1 when a `ValueError` or `RuntimeError` escapes. Because `search_category()` has already caught the provider exception, the CLI treats a failed provider call as success.

**Existing tests:** `tests/test_research_categories.py` includes an assertion for an error under `out["results"]`; `tests/test_cli.py` documents that tool-level error payloads currently exit zero. Both need to change with the contract.

### OpenAlex anonymous throttling: partially addressed

**Current code:** `OpenAlexAdapter` (`gossamer/research_providers.py:75-149`) reads `GOSSAMER_OPENALEX_EMAIL` and includes an email in `User-Agent` and `Contact-Agent`. `GOSSAMER_OPENALEX_EMAIL` is present in the settings/keystore key list. If unset, the adapter uses the placeholder `research@example.org`. Its request parameters currently include only `search` and `per_page`; it does not include `mailto`. On a response error, it simply calls `raise_for_status()`.

The shared Python retry decorator retries all exceptions with fixed delay/backoff; it does not honor a `Retry-After` header or a provider-specified wait. The findings are therefore partly right: there is already an attempt at polite identification, but it is not the tested `mailto=` path from the hunt and the retry behavior is insufficient. A real project contact address must be selected rather than inventing one.

### Semantic Scholar: integration confirmed; adapter does not exist yet

The `scholarly` category currently lists `openalex`, `crossref`, `arxiv`, and `zenodo` (`gossamer/research_categories.py:159-164`). There is no Semantic Scholar adapter, endpoint implementation, key setting, or live test. The word `semanticscholar` currently appears only as a classification keyword, and the reported 429s were not generated by a gossamer adapter. The user has now confirmed that Semantic Scholar integration is part of the improvement plan. Build it as a distinct opt-in scholarly provider; keep OpenAlex as the category default unless a later decision changes that. Verify the current official API contract before selecting request URLs, auth headers, rate handling, or response mappings.

### F1 — download primitive: partially present; important gap remains

**Existing capability:** `extract URL --store` is available in the CLI and toolbox. `DocumentExtractor.extract_document()` can fetch a URL and store the original bytes plus extracted Markdown (`gossamer/document.py:97-303`). Its URL fetch follows redirects, records final URL/status/content type, and enforces `max_response_bytes` (`_fetch_document_url`, lines 493-537).

**Missing capability:** There is no standalone `download URL -o file` command in `gossamer/cli.py`. The extraction path requires the body to be recognized/parsed as a supported document or text format; it is not an opaque binary saver. The current fetch buffers the bounded body in memory, has no resume facility, and does not expose a download-specific minimum-size or PDF-magic validation contract. Error results are generic document-extraction errors rather than structured download outcomes such as 404, bot wall, timeout, too large, or invalid PDF.

**Conclusion:** The report's practical pain is valid, but “there is no download primitive” should be read as “there is no general-purpose standalone download primitive.” `extract --store` is an existing workaround for accessible, extractable files.

### F2 — DOI-to-OA resolver: confirmed

OpenAlex results re-attach the full provider record to `raw` (`OpenAlexAdapter._search_impl`, lines 128-137), so `best_oa_location` can be available to a caller that inspects raw output. There is no dedicated DOI normalization and OA-candidate resolution command/service, no ranking/fallback chain, and no concise result that identifies the selected URL and its source. This is a separate feature from downloading.

### F3 — provider-native query passthrough: confirmed

OpenAlex search sends only `search` and `per_page`. There is no `filter`, `select`, or equivalent advanced-query surface plumbed through `research_categories`, the toolbox API, or the CLI. Preserving each raw result does not enable caller-controlled filters/projections.

### F4 — cross-provider merge/dedupe: confirmed

`search_category()` invokes one provider per call. There is no explicit multi-provider scholarly fan-out, DOI/arXiv identifier normalization across sources, merge policy, or per-record source attribution. The existing single-provider behavior should remain the default to avoid unexpected quota usage and latency.

### F5 — shell invocation: documentation alternative already present

`skills/gossamer/SKILL.md` gives the Windows venv invocation (`…/.venv/Scripts/python.exe -m gossamer.cli …`). A global PATH shim may be convenient, but it is not required to address the documentation gap described in the finding.

### F6 — bot walls and binary acquisition: confirmed

The document download path uses a static `httpx.Client`; it does not download binary files through a browser session. Browser-backed fetch options are not wired into `extract`'s `_fetch_document_url`. There is also no automated mirror chain or structured “human needed” response listing candidate URLs. The sensible first step is a compliant, provenance-preserving mirror chain and clear failure report—not bypassing a site's bot wall or access controls.

### Smaller workflow items

- `check --mode status` exists in the CLI; `cache --action prune|clear|reset` also exists. The skill does not currently present the end-to-end collection recipe that would make these easy to discover.
- There is no `cite --from-pdf` path. Treat that as a later convenience feature; first get DOI extraction/resolution and file acquisition right.
- Add a concise explanation of cache behavior to the workflow docs if users still cannot tell when a result is cached after using the existing commands.
- Additional documentation drift found during review: `AGENTS.md` still says patent providers are key-gated, while `google-patents` is a keyless number-lookup provider. Update that statement when doing the next documentation synchronization.

---

## 3. Detailed implementation plan

### Milestone A — make CLI output and HTTP failures reliable

**Priority:** P0  
**Scope:** B1, B2, OpenAlex throttling  
**Likely files:** `gossamer/cli.py`, `gossamer/search_providers.py`, `gossamer/research_providers.py`, `gossamer/settings.py` (only if a new contact setting is needed), `tests/test_cli.py`, `tests/test_research_providers.py`, `tests/test_live_smoke.py`.

#### A1. Define the CLI text-output contract

1. Configure CLI stdout and stderr to UTF-8 before printing command output/errors. Handle streams that do not provide `reconfigure()` (pytest capture, embedded callers) without breaking them. Use a safe fallback for malformed surrogates; preserve valid non-ASCII Unicode rather than replacing it.
2. Keep command output as JSON/text exactly once—do not add a second JSON-encoding layer.
3. Add a Windows subprocess regression test with `PYTHONIOENCODING=cp1252` and a Greek `μ` result. Assert that stdout is valid UTF-8, parses as JSON, contains the intended character, and exits zero. Include an error-path test for non-ASCII stderr if provider messages can contain it.

**Acceptance:** The B1 reproduction no longer produces an encoding error or empty stdout under a cp1252-configured process.

#### A2. Fix and pin arXiv request behavior

1. Compare request variants using the same query: current request, correct Atom `Accept`, a non-placeholder contact-bearing User-Agent, and HTTPS only if supported by the documented endpoint. Record the winning request shape; do not guess the endpoint or contact identity.
2. Replace the stale `0.5.3`/placeholder UA with a versioned product UA and a real configurable contact. Reuse a shared contact setting if one is deliberately established; otherwise introduce and document a narrowly named arXiv contact setting.
3. Send `Accept: application/atom+xml` if confirmed by the comparison/provider contract. Keep the parser on the documented Atom response.
4. In offline tests, assert URL, query parameters, User-Agent, Accept, search parsing, and `id_list` fetch behavior. Assert that permanent 4xx errors are not retried.
5. Repair the existing opt-in live smoke test rather than adding a duplicate. Prefer a single stable known-ID `id_list` request per live run; verify the ID before relying on it. If testing both search and fetch, preserve the adapter’s three-second request interval. Keep live tests opt-in, not a required default-suite network dependency.

**Acceptance:** A real, opt-in request returns a parsed arXiv record; a 406 is not retried blindly; the offline header tests remain deterministic.

#### A3. Make Python HTTP retries status-aware

1. Replace the current “retry every exception” behavior with explicit retry eligibility: transient connection/timeouts and appropriate 408/429/5xx responses; permanent 4xx responses should fail immediately.
2. Parse `Retry-After` seconds and HTTP-date forms, clamp waits to a documented maximum, and add jitter without retrying sooner than the server asks. Reuse tested semantics where appropriate, but do not assume the Rust page-fetch retry implementation handles Python API calls.
3. For OpenAlex, send `mailto` from `GOSSAMER_OPENALEX_EMAIL` alongside contact headers, provided the parameter is confirmed against the API contract. Decide on a valid package contact before adding any default; never ship a fabricated email as a polite-pool identity.
4. Handle structured provider retry guidance when present. Prefer documented response fields/headers; only parse a free-text wait message if tests pin the real observed format. Keep API-key/quota errors and permanent failures out of retry loops.
5. Add unit tests using mocked `httpx` responses for 429 + Retry-After, retry exhaustion, permanent 406/400, and OpenAlex email/parameter construction. Use a local test server for timing behavior rather than real provider throttling.

**Risk:** `retry()` is shared across many provider implementations. Changing its semantics can change request counts and error timing widely. Run the full adapter/offline suite and add explicit tests for quota fail-fast and retryable/non-retryable status classes.

### Milestone B — stabilize provider error responses

**Priority:** P0  
**Scope:** B3  
**Likely files:** `gossamer/research_categories.py`, `gossamer/cli.py`, `tests/test_research_categories.py`, `tests/test_cli.py`, API/tool docs.

1. Make `results` consistently a list. On provider failure return `results: []` and put the message in a top-level `error` field. Preserve `query`, `category`, `provider`, and `available_providers` so clients can still recover.
2. Keep unknown-provider/category errors on the same top-level convention; their current `results: []` shape is already closer to the desired contract.
3. Make the CLI recognize a top-level hard provider error and return nonzero after printing the JSON error payload. Do not rely on an exception that the toolbox deliberately catches for MCP/tool callers.
4. Update existing tests that assert `out["results"]["error"]` or expect tool-level failures to exit zero. Add assertions that every success and failure has a list-valued `results`, that failure details are top-level, and that CLI stdout stays parseable while status is nonzero.

**Compatibility:** This changes the location of provider errors in the JSON response. Document it in the changelog and skill/API notes; do not silently keep a union type for backwards compatibility.

### Milestone C — add a standalone, validated download operation

**Priority:** P1; highest collection-workflow value  
**Scope:** F1, groundwork for F6  
**Likely files:** `gossamer/document.py` or a focused download module, `gossamer/agent_tools.py`, `gossamer/cli.py`, tool registry/MCP wiring if parity is desired, `tests/` download-specific module, README/QUICKREF/SKILL.

1. Add a standalone URL-to-file API/command such as `download URL -o PATH`; do not require the document parser to understand the response.
2. Reuse the existing URL validation, robots policy, domain throttling, redirect handling, response cap, and provenance conventions. Revalidate redirect destinations according to the project’s URL-safety policy.
3. Stream to a temporary file in the destination directory and atomically rename only after successful validation. Avoid accumulating large files in memory. Preserve final URL, status, content type, byte count, and output path.
4. Support a configurable minimum byte count. Validate expected formats by signature (e.g. `%PDF-`) when the user requests/infers that format; do not reject arbitrary binary downloads merely because they are not PDFs. Detect HTML/challenge responses masquerading as PDFs and report them as unexpected/bot-wall content rather than saving them as successful PDFs.
5. Add resumable downloads only with correct HTTP Range/If-Range handling. Test 206 continuation, servers that ignore Range and return 200, validators/ETags, interrupted transfers, and cleanup of partial files. If robust resume support expands scope, ship the safe atomic non-resume path first and track resume as a follow-up rather than pretending it works.
6. Return stable error classes for not found, access denied/bot wall, timeout/network error, too large, too small, invalid signature/content type, and local write failure. Include a suggested manual-action path without attempting access-control bypass.
7. Keep `extract URL --store` as the extraction-plus-storage operation; clarify the distinction in help/docs. Decide whether `download` should be an MCP tool as well as CLI/API so the documented CLI/MCP parity remains intentional.

**Acceptance:** A local test server can serve a PDF, HTML challenge, truncated body, oversized body, redirect, 404, and resumable response. Tests verify bytes on disk, no partial final output on failure, correct provenance/error class, and URL-safety behavior.

### Milestone D — implement DOI-to-OA location

**Priority:** P1  
**Scope:** F2  
**Likely files:** a focused resolver module, `gossamer/research_providers.py` (OpenAlex reuse), toolbox/CLI/tool registration, `tests/` resolver module, docs.

1. Add `locate-pdf DOI` (or a Python/MCP equivalent) that normalizes DOI forms (`doi:`, DOI URL, bare DOI) and returns candidate locations without downloading automatically.
2. Query OpenAlex through the existing adapter/API path and use the preserved `best_oa_location`/OA locations. Return candidate URL, source/host, location type, license/access metadata if available, and the identifier used to resolve it.
3. Add Unpaywall or repository fallbacks only after checking their current API contract, email/auth requirements, and reuse terms. Make every candidate’s provenance explicit; do not scrape publisher search pages as a fallback.
4. Provide stable not-found, closed-access, provider-failure, and malformed-DOI results. If providers partially fail, preserve any candidates already found and expose per-provider errors.
5. Add mocked tests for DOI normalization, candidate ranking, unavailable/missing OA locations, and partial provider failures. Keep live provider checks opt-in.

**Acceptance:** Given a DOI, the command reports zero or more inspectable OA candidates with provenance; it does not claim a file has been downloaded.

### Milestone E — precise OpenAlex queries and optional scholarly aggregation

**Priority:** P2  
**Scope:** F3 and F4  
**Likely files:** `gossamer/research_providers.py`, `gossamer/research_categories.py`, `gossamer/agent_tools.py`, `gossamer/cli.py`, tool/MCP parameter definitions, `tests/test_research_providers.py`, `tests/test_research_categories.py`, new scholarly merge tests.

#### E1. OpenAlex native query controls (F3)

- Add typed `filter` and `select` parameters (and fielded search only if its mapping is clear) to the OpenAlex adapter and plumb them through the research facade/CLI.
- Reject these options when a different provider is selected; never silently drop a caller’s filter.
- Keep the ordinary query behavior unchanged and preserve provider raw records.
- Test exact request parameters, URL encoding, max-result limits, invalid/unsupported option handling, and CLI/MCP parity.

Prefer typed provider options over arbitrary raw URLs/query strings. Any raw-query escape hatch needs an explicit security, endpoint, and auth boundary.

#### E2. Semantic Scholar provider integration (confirmed)

**Status:** Approved for implementation; not yet present in the code.  
**Likely files:** `gossamer/research_providers.py`, `gossamer/research_categories.py`, `gossamer/settings.py`, `gossamer/_core.pyi`, `src/adapters/scholar.rs`, `src/lib.rs`, `src/adapters/tests.rs`, `tests/test_research_providers.py`, `tests/test_research_categories.py`, `tests/test_live_smoke.py`, provider/docs tables.

1. Before coding, use the project’s documented research workflow to verify Semantic Scholar’s current official API: supported search and paper lookup endpoints, authentication/key header, rate limits, pagination, available fields, abstract representation, error/429 responses, and acceptable-use constraints. Do not copy endpoint or response assumptions from the hand-rolled calls described in the findings.
2. Add a `SemanticScholarAdapter(ResourceAdapter)` with provider name `semanticscholar`, scholarly domain, explicit configured key support where the verified API permits it, and fail-fast missing-key behavior if the chosen API operation requires a key. Use the canonical project setting `GOSSAMER_SEMANTICSCHOLAR_API_KEY` unless verification or existing naming conventions establish a better single name; register it in `settings.py`/keystore and document the exact variable. Do not make the adapter silently fall back to another provider.
3. Implement documented search and paper fetch/lookup operations, rate limiting and response-specific errors. Bound page size/result counts, handle 401/403/429 distinctly, honor provider retry guidance through Milestone A’s retry policy, and retain the raw source record for fields the normalized schema does not yet expose.
4. Normalize only fields present in the verified response contract into gossamer’s common record shape (stable paper ID, title, authors, year/date, DOI, URL, abstract/snippet, citation count and OA-PDF metadata if provided). Parse nested/special abstract formats only according to official examples and fixtures; do not guess.
5. Add the provider to the scholarly provider list and adapter factory/name maps, but place it after existing providers so `openalex` remains the default. This is an explicit opt-in addition; it must not make ordinary category research fan out to Semantic Scholar.
6. Add deterministic mocked tests for request URL/params/auth, search/fetch row shape, empty results, malformed/error bodies, missing key, rate-limit response, and raw-record passthrough. Add one `GOSSAMER_LIVE=1` smoke test, skipped without the required key; keep it outside the default offline test path and keep request count within the documented limit.
7. Update README, QUICKREF, SKILL, category output expectations, key/keystore examples, adapter counts, architecture notes, and changelog after implementation. Recount the test badge from the actual full test run. A docs-only plan update does not change the package version; release/version policy is decided when the code is implemented.

**Acceptance:** `research <query> --provider semanticscholar` makes a documented authenticated request when needed and returns normalized records; missing credentials name the exact setting; 429 behavior is bounded and tested; OpenAlex remains the default; the provider is covered by offline tests and an opt-in live smoke.

#### E3. Opt-in multi-provider search and merge (F4)

- Add an explicit provider list/merge mode; do not fan out by default. Make quotas, latency, and per-provider errors visible.
- Normalize strong identifiers: canonical DOI (including DOI URLs and `doi:` prefixes) and arXiv ID. For arXiv, retain the original versioned ID while optionally using the versionless ID as a match key.
- Merge only on a strong normalized identifier. Do not merge papers on title similarity alone in the first version.
- Return a canonical presentation record plus all source records/source names and any conflicting field values. Keep partial provider results when one provider fails and report that provider’s error separately.
- Test duplicate DOI across OpenAlex/Crossref/arXiv/Semantic Scholar, versioned arXiv IDs, no-identifier records, conflict preservation, provider failure, and deterministic ordering.

### Milestone F — compliant mirror handling and workflow/documentation

**Priority:** P2/P3  
**Scope:** F6, F5 follow-up, small workflow items  
**Likely files:** document/download orchestration, docs/README/QUICKREF/SKILL, `AGENTS.md`.

1. Add an optional `--try-mirrors`/candidate-chain mode only from known, sourced OA/repository URLs (for example, locations returned by the resolver or supplied by the caller). Record each attempted URL and response outcome.
2. If an endpoint returns a bot wall or access denial, stop that source and report “human action needed” with the exact URL and cause. Do not use browser automation to evade access controls or disregard site policy. Browser-based binary download should remain a separate, explicit future design.
3. Update the skill with a collection recipe: **search → check → locate → download → extract/store → cite**. Include current commands, max-pages/budget guidance, and the role of `cache --action`.
4. Note that `extract URL --store` works for extractable sources before the new download command is available. Keep the standalone download and extract distinction clear.
5. Defer `cite --from-pdf` until download and metadata extraction are stable; then add PDF metadata/DOI detection and tests rather than guessing citations from arbitrary text.
6. Fix the stale `AGENTS.md` patent statement: current patent providers are not all key-gated because `google-patents` is a keyless publication-number lookup. Keep the distinction that it is lookup-only, not free-text search.
7. Do not add a PATH shim unless users still need it after the skill’s existing venv command is made prominent. Treat cache-hit visibility as a usability follow-up, not a blocker for download correctness.

---

## 4. Semantic Scholar integration status

**Decision: confirmed for integration.** This is now a planned implementation item (Milestone E2), not a defer/build decision gate. The adapter is absent today, so the reported 429 is not a gossamer regression; integration must first verify the official API contract and then add the provider, key handling, retry behavior, tests, taxonomy, and documentation described in E2. The work is independent of the P0 reliability fixes and should not block them.

---

## 5. Verification and release approach

- Keep unit/integration tests offline by default. Use mocked HTTP and local test servers for headers, retry timing, JSON contracts, file validation, resume, and merge behavior.
- Keep provider live tests behind the existing `GOSSAMER_LIVE=1` gate. Use one request per live smoke where possible and honor provider-specific delays.
- Run focused tests per milestone, then the complete pytest suite, `ruff check gossamer/`, Rust tests/clippy if Rust code is touched, and the project’s CLI/MCP parity checks.
- Run a manual collection/provider smoke only after the relevant fixes: non-ASCII CLI output; arXiv one-request lookup; OpenAlex throttle simulation with a local/mocked response; one keyed Semantic Scholar live query when credentials are available; DOI location; download of a known accessible PDF; blocked-source classification.
- This review document is documentation-only. Keep package version at **0.9.6** for this file; any later implementation/release version decision belongs to that implementation task.

---

## 6. Suggested delivery order

1. **A1 + A2 + A3:** encoding, arXiv request and retry correction, OpenAlex contact/throttle behavior.
2. **B:** stable provider error envelope and nonzero CLI exit for hard provider failures.
3. **C:** standalone download with validation and clear outcomes.
4. **D:** DOI-to-OA candidate resolution.
5. **E1 + E2:** precise provider-native queries and the confirmed Semantic Scholar adapter; keep OpenAlex as the default.
6. **E3:** explicit cross-provider merge, including Semantic Scholar, only after individual provider behavior is stable.
7. **F:** compliant mirror handling and collection-recipe/docs synchronization.

This order fixes tool-breaking defects before adding the acquisition and precision features that motivated the hunt, while keeping external API work opt-in and policy-compliant.
