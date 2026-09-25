# Gossamer findings review and implementation plan

**Review date:** 2026-09-25

**Baseline reviewed tree:** `dev_rust` at `f042d15` (`0.9.6`)

**Implementation branch:** `dev_rust`; initial implementation commits are being versioned per fix as requested.

**Source report:** [`gossamer_findings.md`](gossamer_findings.md)

**Scope:** Compare the reported literature-hunt findings with the implementation, distinguish confirmed defects from upstream limits and missing features, and track the ordered implementation with testable acceptance criteria.

The initial review made no source changes. Implementation began after approval; this file is now the living status/plan.

---

## 1. Executive summary

The baseline review reproduced three reliability issues: cp1252 stdout rejected Greek `μ`; arXiv returned HTTP 406; and provider exceptions produced a dict inside `results` while the CLI exited successfully. Implementation has started. **Completed:** UTF-8 CLI output (`0.9.7`), status-aware transient HTTP retries (`0.9.8`), arXiv-specific typed 406/rate-limit reporting (`0.9.9`), a stable provider-error envelope/CLI status (`0.9.10`), configured OpenAlex `mailto` handling without a fabricated default (`0.9.11`), a standalone validated file downloader (`0.9.12`), and DOI-to-OA candidate resolution (`0.9.13`). The arXiv service is still returning an upstream 406 from this network; code identifies it and avoids repeated requests rather than pretending headers solved the edge limit. Remaining work includes the confirmed Semantic Scholar adapter, OpenAlex precision controls, multi-provider merge, and compliant mirror handling.

Several findings need qualification:

- `extract URL --store` still saves original bytes together with extracted Markdown. A distinct `download URL -o PATH` / `download_file` tool is now implemented for opaque files: it streams to an atomic destination, enforces size limits, checks PDF magic when appropriate, and returns classified errors without trying to extract or bypass bot walls.
- The arXiv live smoke test already existed and remains opt-in. It is now a single default-paced `id_list` call and skips only on the typed upstream 406 rate-limit result; offline tests cover header/parser behavior.
- OpenAlex now sends an operator-supplied `GOSSAMER_OPENALEX_EMAIL` as `mailto` and client-identification headers (`0.9.11`); no placeholder contact is fabricated. A free `GOSSAMER_OPENALEX_KEY` remains optional for casual use and raises the daily budget. Generic `Retry-After` and exponential backoff are implemented; current official guidance does not document a response-body `retryAfter` field, so do not invent a parser for one.
- There is no Semantic Scholar provider in gossamer today. The classification keyword is not provider support; no Semantic Scholar key variable or adapter contract currently exists. **Integration is now confirmed and is included as a build milestone below**; the exact upstream endpoint/auth/field contract must be verified before coding.
- F5's documentation alternative is already satisfied: `skills/gossamer/SKILL.md` includes the Windows venv invocation. A PATH shim is optional.

Recommended order: (1) CLI encoding, HTTP retry behavior, arXiv and OpenAlex request correctness; (2) stable provider-error response and CLI exit status; (3) standalone download and DOI-to-OA resolution; (4) provider-native OpenAlex query controls and the confirmed Semantic Scholar adapter; (5) opt-in cross-provider merging, including Semantic Scholar; (6) mirror/human-needed behavior and collection-workflow documentation.

---

## 2. Findings compared with the code

### B1 — CLI output encoding: confirmed and fixed

**Baseline code/evidence:** The original `gossamer/cli.py` called `print()` without configuring stdout/stderr. This environment reported `sys.stdout.encoding == "cp1252"`; a strict cp1252 CLI test with Greek small letter mu (`μ`, U+03BC) returned 1, emitted no JSON, and printed a `charmap` encoding error. (This is Greek `μ`, not the cp1252-encodable micro sign `µ`.)

**Implementation status:** Fixed in `0.9.7` / commit `d88e843`. The CLI reconfigures standard output/error as UTF-8 when supported, uses `backslashreplace` for otherwise unencodable surrogate data, and leaves captured/embedded streams without `reconfigure()` untouched. A subprocess regression test sets `PYTHONIOENCODING=cp1252:strict` and asserts the resulting JSON is valid UTF-8 and preserves `μ`.

### B2 — arXiv HTTP 406: confirmed; current evidence points to an upstream edge limit

**Current code:** `ArxivAdapter` is in `gossamer/research_providers.py`. It calls `http://export.arxiv.org/api/query`, which redirects to HTTPS, and now sends an identifiable project/version User-Agent plus `Accept: application/atom+xml`. Earlier code used a stale `gossamer/0.5.3` UA with the fabricated contact `researcher@example.org`. The generic retry policy now retries transport errors and selected transient statuses, but not arbitrary 4xx responses.

**Live evidence:** The adapter returned HTTP 406 on a one-request `id_list` call. A separate one-request probe with a plain `Mozilla/5.0` User-Agent and no explicit Accept also returned 406. The HTTPS response came from Varnish with an empty body and no `Retry-After`; the HTTP endpoint returned a 301 redirect to HTTPS. This weakens the initial theory that a particular User-Agent or `Accept` value is the cause. The official [arXiv API terms](https://info.arxiv.org/help/api/tou.html) require no more than one request every three seconds, one connection at a time, across the caller's machines. A recent [public report of the same 406 behavior](https://github.com/blazickjp/arxiv-mcp-server/issues/277) describes empty Varnish 406s across curl, urllib, and httpx from the same host, clearing only after a period with no traffic. That report is not an arXiv guarantee, but it is consistent with an IP/edge throttle or temporary upstream condition. The original curl success may have occurred outside the failure window; it does not establish that browser impersonation fixes the adapter.

**Implementation conclusion:** Do not spoof a browser or retry 406 repeatedly. Keep the explicit Atom Accept and truthful project UA, map arXiv 406 to an actionable typed rate-limit error (include `Retry-After` if the service supplies one), and let the caller wait rather than extending a possible edge block. This improves failure handling; a successful live response remains dependent on arXiv availability and the source IP's quota state.

**Coverage:** `tests/test_live_smoke.py` now makes one default-paced `id_list` request and skips with a clear explanation only when the provider returns the typed 406 rate-limit result. Offline tests pin headers, parsing, and exactly one attempt for the 406. Ordinary tests remain offline.

### B3 — provider error shape and CLI status: confirmed and fixed

**Baseline code:** `research_categories.search_category()` put provider errors inside `results`, and the CLI returned zero because the exception had already been converted to data.

**Implementation status:** Fixed in `0.9.10` / commit `bd6bd26`. Adapter and engine failures now use `results: []` plus a top-level string `error`; normal guarded-engine metadata is preserved outside the result list. The `research` CLI parses its JSON response and returns 1 when the top-level error is set, while still printing parseable JSON. Tests cover adapter failures, engine error envelopes, guarded success metadata, and CLI success/failure status.

### OpenAlex anonymous throttling: contact and retry behavior improved; quotas remain provider-controlled

**Current code:** `OpenAlexAdapter` reads `GOSSAMER_OPENALEX_EMAIL`; when configured, it sends that address as the `mailto` query parameter and in `User-Agent`/`Contact-Agent`. If unset, no email is fabricated. The optional `GOSSAMER_OPENALEX_KEY` continues to be sent as `api_key`.

**Implementation status:** The generic retry decorator is status-aware (`0.9.8`), honors bounded `Retry-After`, and uses exponential backoff for retryable 429/5xx and transport errors. Configured `mailto`/headers and omission of a default contact are covered in `0.9.11`. Current official OpenAlex docs describe 429 responses, rate-limit headers, and exponential backoff; they do not establish the free-text/body `retryAfter` field described in the findings, so no undocumented parser is added. An API key is the supported way to increase the current daily budget; `mailto` identifies the client but should not be described as authentication or a guaranteed quota bypass.

### Semantic Scholar: integration confirmed; adapter does not exist yet

The `scholarly` category currently lists `openalex`, `crossref`, `arxiv`, and `zenodo` (`gossamer/research_categories.py:159-164`). There is no Semantic Scholar adapter, endpoint implementation, key setting, or live test. The word `semanticscholar` currently appears only as a classification keyword, and the reported 429s were not generated by a gossamer adapter. The user has now confirmed that Semantic Scholar integration is part of the improvement plan. Build it as a distinct opt-in scholarly provider; keep OpenAlex as the category default unless a later decision changes that. Verify the current official API contract before selecting request URLs, auth headers, rate handling, or response mappings.

### F1 — standalone download primitive: implemented; resume is deferred

**Existing capability:** `extract URL --store` continues to save the original bytes plus extracted Markdown. Its parsing-oriented behavior is unchanged.

**Implementation status:** `download URL -o PATH` and the `download_file` Python/MCP tool are implemented in `0.9.12` (`gossamer/downloader.py`). The downloader streams under the configured/per-call byte cap to a temporary file, revalidates every redirect against SSRF and robots policy, then atomically installs the output. Existing destinations are preserved unless `overwrite=true`. PDF output is signature-checked when inferred from `.pdf` or explicitly requested. Errors distinguish URL/robots rejection, HTTP 404/access denial/rate limit, bot wall, too-large/too-small/partial responses, invalid/unexpected content, network/timeout, and local write failures. Success returns final URL, response metadata, size, hash, and path.

**Scope limitation:** Resume (`Range`/`If-Range`) is not implemented yet. The downloader rejects a 206 response unless resume is explicitly designed and tested later. It never attempts a browser or access-control bypass. Tests use a local HTTP server and cover redirects, validation, PDF signature, min/max size, overwrite behavior, 404, bot wall, partial response, robots, and redirect SSRF revalidation.

**Live smoke:** A W3C sample-PDF URL returned `robots_disallowed`; the downloader correctly made no request. This verifies the policy gate, not a successful external transfer. A future live success smoke should use a known robots-allowed source.

### F2 — DOI-to-OA resolver: implemented with OpenAlex first

`locate_pdf` / `gossamer locate-pdf DOI` is implemented in `0.9.13`. It normalizes bare DOI, `doi:` prefix, and DOI resolver URL inputs; uses OpenAlex's documented `works?filter=doi:https://doi.org/<doi>` query; and reads `best_oa_location` plus other OA `locations` from the preserved raw work record. Results rank the best location first, deduplicate URLs, and include PDF or landing-page kind, source, license, version, and work metadata. `not_found`, `closed_access`, and open-access-without-location are distinct from provider/invalid-input errors. The resolver does not download files; use `download_file` separately.

This first pass deliberately uses OpenAlex only. Unpaywall and repository fallbacks remain deferred until their current API/auth/terms are verified. The offline suite pins DOI normalization, request construction, candidate ordering/deduplication, and error outcomes. An opt-in-style manual CLI smoke for `10.1371/journal.pone.0266781` successfully returned the PLoS PDF URL and PMC/DOAJ landing-page candidates with their OA metadata.
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
**Status:** Core CLI/retry/arXiv failure reporting and configured OpenAlex mailto are implemented in `0.9.7`–`0.9.11`; a successful arXiv live response is still upstream-dependent.
**Scope:** B1, B2, OpenAlex throttling  
**Likely files:** `gossamer/cli.py`, `gossamer/search_providers.py`, `gossamer/research_providers.py`, `gossamer/settings.py` (only if a new contact setting is needed), `tests/test_cli.py`, `tests/test_research_providers.py`, `tests/test_live_smoke.py`.

#### A1. Define the CLI text-output contract

1. Configure CLI stdout and stderr to UTF-8 before printing command output/errors. Handle streams that do not provide `reconfigure()` (pytest capture, embedded callers) without breaking them. Use a safe fallback for malformed surrogates; preserve valid non-ASCII Unicode rather than replacing it.
2. Keep command output as JSON/text exactly once—do not add a second JSON-encoding layer.
3. Add a Windows subprocess regression test with `PYTHONIOENCODING=cp1252` and a Greek `μ` result. Assert that stdout is valid UTF-8, parses as JSON, contains the intended character, and exits zero. Include an error-path test for non-ASCII stderr if provider messages can contain it.

**Acceptance:** The B1 reproduction no longer produces an encoding error or empty stdout under a cp1252-configured process.

#### A2. Fix and pin arXiv request behavior

1. Keep a truthful project/version User-Agent and the Atom `Accept`; do not impersonate a browser. The official endpoint redirects HTTP to HTTPS. A 406 may be an IP/edge throttle, not a malformed request, so do not churn through header variants during a failure window.
2. Map arXiv HTTP 406 specifically to `ProviderRateLimitError`, preserving status and parsed `Retry-After` when present. When no hint is supplied, say that it may be temporary edge/IP throttling and immediate retries should be avoided. Keep generic permanent 4xx responses non-retryable.
3. Preserve the official arXiv request interval (one request per three seconds and one connection at a time). Keep this adapter's existing default pacing; do not set `delay=0` in live tests that make multiple calls.
4. In offline tests, assert URL, query parameters, User-Agent, Atom Accept, search parsing, `id_list` fetch behavior, and exactly one request on 406. Test `Retry-After` parsing with mock responses only.
5. Repair the existing opt-in live smoke test rather than adding a duplicate. Make one stable known-ID `id_list` request per live run. Skip only when the typed upstream 406 limit response occurs, with the reason visible; keep all live tests opt-in.

**Acceptance:** Offline tests pin request shape and verify an actionable typed 406 with no immediate retry. The opt-in live test parses a record when arXiv is available and otherwise reports a provider-rate-limit skip; a successful live request is not a release gate while the upstream edge is returning 406.

#### A3. Make Python HTTP retries status-aware

1. **Done (`0.9.8`):** Retry only transport errors and HTTP 408/425/429/5xx; permanent 4xx/application errors fail immediately.
2. **Done (`0.9.8`):** Parse `Retry-After` seconds and HTTP-date forms, enforce a bounded wait, and add jitter without retrying before the requested wait. Python API calls use this policy independently of the Rust page-fetch retry code.
3. **Done (`0.9.11`):** Send configured `GOSSAMER_OPENALEX_EMAIL` as a `mailto` query and contact header; do not fabricate a default. OpenAlex's published [API mailto guidance](https://github.com/ourresearch/openalex-docs/blob/main/how-to-use-the-api/rate-limits-and-authentication.md) supports contact by `mailto` or User-Agent. Current [authentication docs](https://developers.openalex.org/guides/authentication) describe optional free API keys that increase the daily budget; email is identification, not authentication.
4. **Partially done:** Honor documented response headers. Current OpenAlex docs describe 429, rate-limit headers, and exponential backoff, but do not establish the free-text `retryAfter` response-body field seen in the findings; do not add an undocumented parser. If a future official response contract exposes a structured delay, add it with a fixture.
5. **Done for shared retry and OpenAlex contact (`0.9.8`, `0.9.11`):** Mocked tests cover 429 + Retry-After, retry exhaustion, permanent 406/400, and OpenAlex email/parameter construction. Use local servers/mocks rather than real provider throttling.

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

### Milestone C — standalone, validated download operation (implemented)

**Priority:** P1; highest collection-workflow value  
**Status:** Implemented in `0.9.12`; resume support is explicitly deferred.
**Scope:** F1, groundwork for F6  
**Files:** `gossamer/downloader.py`, `gossamer/agent_tools.py`, `gossamer/cli.py`, `gossamer/config.py`, download/CLI/MCP tests, README/QUICKREF/SKILL/AGENTS/ARCHITECTURE.

1. Add a standalone URL-to-file API/command such as `download URL -o PATH`; do not require the document parser to understand the response.
2. Reuse the existing URL validation, robots policy, domain throttling, redirect handling, response cap, and provenance conventions. Revalidate redirect destinations according to the project’s URL-safety policy.
3. Stream to a temporary file in the destination directory and atomically rename only after successful validation. Avoid accumulating large files in memory. Preserve final URL, status, content type, byte count, and output path.
4. Support a configurable minimum byte count. Validate expected formats by signature (e.g. `%PDF-`) when the user requests/infers that format; do not reject arbitrary binary downloads merely because they are not PDFs. Detect HTML/challenge responses masquerading as PDFs and report them as unexpected/bot-wall content rather than saving them as successful PDFs.
5. **Deferred:** Resume is not included in `0.9.12`. The downloader rejects 206 partial responses rather than saving an unrequested partial body. Implement `Range`/`If-Range`, validators, server-ignores-Range behavior, and interrupted-transfer cleanup only as a separately tested follow-up.
6. Return stable error classes for not found, access denied/bot wall, timeout/network error, too large, too small, invalid signature/content type, and local write failure. Include a suggested manual-action path without attempting access-control bypass.
7. **Done:** Keep `extract URL --store` for extraction-plus-storage. Expose `download_file` through the Python toolbox/MCP registry and `download` through the CLI with the shared parameter contract.

**Verification:** The local HTTP-server tests cover PDF bytes, HTML/challenge responses, too-small/oversized/partial responses, redirects, 404, overwrite behavior, robots checks, and SSRF revalidation. They verify no partial final file on failure and preserve provenance. Resume is not an acceptance criterion for this release.

### Milestone D — DOI-to-OA location (implemented)

**Priority:** P1  
**Status:** Implemented in `0.9.13`; OpenAlex is the first/only source in this pass.
**Scope:** F2  
**Files:** `gossamer/open_access.py`, `gossamer/research_providers.py`, toolbox/CLI/tool registry, `tests/test_open_access.py`, README/QUICKREF/SKILL/AGENTS/ARCHITECTURE.

1. **Done:** `locate-pdf DOI` and the `locate_pdf` toolbox/MCP method normalize DOI forms and return candidates without downloading.
2. **Done:** Resolve through OpenAlex's documented DOI filter and preserve `best_oa_location` plus all OA locations with provenance/license/version metadata.
3. **Deferred:** Add Unpaywall/repository fallbacks only after verifying current API/auth/terms; do not scrape publisher pages.
4. **Done for OpenAlex:** Return `not_found`, `closed_access`, `open_access_no_location`, and structured invalid-input/provider errors. Partial-source merging is not needed while the resolver has one source.
5. **Done:** Offline tests cover DOI normalization, request auth/filter, candidate ranking/deduplication, landing-page fallback, empty/closed results, and provider failures. Live checks remain opt-in.

**Acceptance:** Given a DOI, the command reports zero or more inspectable OA candidates with provenance and never claims a file was downloaded.

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
- Per the user’s instruction, every feature/fix commit increments the patch version by `0.0.1` and is committed/pushed. Plan-status edits are bundled with the related implementation commit; they do not receive a separate bump by themselves.

---

## 6. Suggested delivery order

1. **A1 + A2 + A3:** implemented in `0.9.7`–`0.9.11`; arXiv live success remains upstream-dependent.
2. **B:** implemented in `0.9.10` with a stable provider error envelope and nonzero CLI status.
3. **C:** implemented in `0.9.12` with validation and classified outcomes; resume deferred.
4. **D:** completed in `0.9.13` — DOI-to-OA candidate resolution via OpenAlex.
5. **E1 + E2:** next — precise provider-native queries and the confirmed Semantic Scholar adapter; keep OpenAlex as the default.
6. **E3:** explicit cross-provider merge, including Semantic Scholar, only after individual provider behavior is stable.
7. **F:** compliant mirror handling and collection-recipe/docs synchronization.

This order fixes tool-breaking defects before adding the acquisition and precision features that motivated the hunt, while keeping external API work opt-in and policy-compliant.
