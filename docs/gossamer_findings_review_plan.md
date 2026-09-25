# Gossamer findings review and implementation plan

**Review date:** 2026-09-25

**Baseline reviewed tree:** `dev_rust` at `f042d15` (`0.9.6`)

**Implementation branch:** `dev_rust`; initial implementation commits are being versioned per fix as requested.

**Source report:** [`gossamer_findings.md`](gossamer_findings.md)

**Scope:** Compare the reported literature-hunt findings with the implementation, distinguish confirmed defects from upstream limits and missing features, and track the ordered implementation with testable acceptance criteria.

The initial review made no source changes. Implementation began after approval; this file is now the living status/plan.

---

## 1. Executive summary

The baseline review reproduced three reliability issues: cp1252 stdout rejected Greek `μ`; arXiv returned HTTP 406; and provider exceptions produced a dict inside `results` while the CLI exited successfully. Implementation has started. **Completed:** UTF-8 CLI output (`0.9.7`), status-aware transient HTTP retries (`0.9.8`), arXiv-specific typed 406/rate-limit reporting (`0.9.9`), a stable provider-error envelope/CLI status (`0.9.10`), configured OpenAlex `mailto` handling without a fabricated default (`0.9.11`), a standalone validated file downloader (`0.9.12`), DOI-to-OA candidate resolution (`0.9.13`), OpenAlex native `filter`/`select` support (`0.9.14`), the opt-in Semantic Scholar adapter (`0.9.15`), explicit scholarly DOI/arXiv merge (`0.9.16`), and compliant caller-supplied mirrors (`0.9.17`). The arXiv service is still returning an upstream 406 from this network; code identifies it and avoids repeated requests rather than pretending headers solved the edge limit. Remaining work is collection workflow polish, the stale patent-routing instruction, and deferred OpenAlex fielded search/resume.

Several findings need qualification:

- `extract URL --store` still saves original bytes together with extracted Markdown. A distinct `download URL -o PATH` / `download_file` tool is now implemented for opaque files: it streams to an atomic destination, enforces size limits, checks PDF magic when appropriate, and returns classified errors without trying to extract or bypass bot walls.
- The arXiv live smoke test already existed and remains opt-in. It is now a single default-paced `id_list` call and skips only on the typed upstream 406 rate-limit result; offline tests cover header/parser behavior.
- OpenAlex now sends an operator-supplied `GOSSAMER_OPENALEX_EMAIL` as `mailto` and client-identification headers (`0.9.11`); no placeholder contact is fabricated. A free `GOSSAMER_OPENALEX_KEY` remains optional for casual use and raises the daily budget. Generic `Retry-After` and exponential backoff are implemented; current official guidance does not document a response-body `retryAfter` field, so do not invent a parser for one.
- Semantic Scholar integration is implemented in `0.9.15` as an opt-in provider; OpenAlex remains the scholarly default. `GOSSAMER_SEMANTICSCHOLAR_API_KEY` is optional but recommended to avoid the shared anonymous rate-limit pool; keyless 429 errors name the variable and do not retry the shared pool.
- Cross-provider research is explicitly opt-in in `0.9.16`; it searches sequentially, merges only on DOI/arXiv identifiers, and retains per-provider raw records and conflicts.
- F5's documentation alternative is already satisfied: `skills/gossamer/SKILL.md` includes the Windows venv invocation. A PATH shim is optional.

Implementation order completed: (1) CLI/HTTP/arXiv/OpenAlex reliability; (2) stable provider-error contract; (3) validated download; (4) DOI-to-OA lookup; (5) OpenAlex filter/select; (6) Semantic Scholar; (7) identifier-based scholarly merge; (8) policy-checked caller-supplied mirrors and collection workflow docs. Remaining work is lower-priority cache observability, `cite --from-pdf`, optional PATH shim, and deferred OpenAlex fielded-search/download resume.

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

### Semantic Scholar: integration confirmed and implemented

`SemanticScholarAdapter` is implemented in `0.9.15` as an opt-in scholarly provider; OpenAlex remains the default. It uses the official Academic Graph `/paper/search` and `/paper/{paper_id}` endpoints, requests explicit fields, pages at no more than 100 results/request and caps relevance search at 1,000 results, and normalizes paper IDs, DOI, title, authors, abstract, year/date, venue, citation count, and OA-PDF metadata. Rust kernels build the common rows and Python reattaches each provider's raw record.

`GOSSAMER_SEMANTICSCHOLAR_API_KEY` is optional; when set it is sent in the documented `x-api-key` header. The official [API tutorial](https://www.semanticscholar.org/product/api/tutorial) says keys get an individual one-request-per-second rate while keyless requests share a pool. The adapter paces at least one second per request and converts keyless 429 responses to an actionable rate-limit error naming the exact key variable rather than retrying the shared pool. With a key, the common bounded retry policy honors `Retry-After`. The live smoke test is opt-in and requires a key to avoid burdening the shared anonymous pool.

### F1 — standalone download primitive: implemented, with opt-in resume

**Existing capability:** `extract URL --store` continues to save the original bytes plus extracted Markdown. Its parsing-oriented behavior is unchanged.

**Implementation status:** `download URL -o PATH` and the `download_file` Python/MCP tool are implemented in `0.9.12` (`gossamer/downloader.py`). The downloader streams under the configured/per-call byte cap to a temporary file, revalidates every redirect against SSRF and robots policy, then atomically installs the output. Existing destinations are preserved unless `overwrite=true`. PDF output is signature-checked when inferred from `.pdf` or explicitly requested. Errors distinguish URL/robots rejection, HTTP 404/access denial/rate limit, bot wall, too-large/too-small/partial responses, invalid/unexpected content, network/timeout, and local write failures. Success returns final URL, response metadata, size, hash, and path.

**Resume (`0.9.20`):** opt-in `resume=true` / `--resume` continues a partial destination with `Range`/`If-Range` and a validator sidecar. A 206 with matching Content-Range appends; a 200 restarts atomically; 416/mismatches preserve the partial file. Without resume, 206 is still rejected as `partial_response`. It never attempts a browser or access-control bypass. Tests use a local HTTP server and cover redirects, validation, PDF signature, min/max size, overwrite behavior, 404, bot wall, partial response, robots, redirect SSRF revalidation, and resume append/restart/unsatisfiable/validator paths.

**Live smoke:** A W3C sample-PDF URL returned `robots_disallowed`; the downloader correctly made no request. This verifies the policy gate, not a successful external transfer. A future live success smoke should use a known robots-allowed source.

### F2 — DOI-to-OA resolver: implemented with OpenAlex first, Unpaywall fallback

`locate_pdf` / `gossamer locate-pdf DOI` is implemented in `0.9.13`. It normalizes bare DOI, `doi:` prefix, and DOI resolver URL inputs; uses OpenAlex's documented `works?filter=doi:https://doi.org/<doi>` query; and reads `best_oa_location` plus other OA `locations` from the preserved raw work record. Results rank the best location first, deduplicate URLs, and include PDF or landing-page kind, source, license, version, and work metadata. `not_found`, `closed_access`, and open-access-without-location are distinct from provider/invalid-input errors. The resolver does not download files; use `download_file` separately.

OpenAlex remains the first source. Unpaywall v2 fallback is implemented in `0.9.21` after verifying the live endpoint/auth contract (real contact required; example.com yields HTTP 422). It runs only when `GOSSAMER_UNPAYWALL_EMAIL` is configured and OpenAlex yields no usable candidate or fails; without an email no Unpaywall request is made. The offline suite pins DOI normalization, request construction, candidate ordering/deduplication, merged provenance, and error outcomes. An opt-in-style manual CLI smoke for `10.1371/journal.pone.0266781` successfully returned the PLoS PDF URL and PMC/DOAJ landing-page candidates with their OA metadata.
### F3 — OpenAlex native filter/select/fielded search: implemented

Implemented in `0.9.14`: `research_by_category` and `gossamer research` accept provider-native `filter` and `select`; they are passed to OpenAlex only and rejected for other providers rather than silently ignored. OpenAlex's current documented `per_page` ceiling is 100, so requests are capped accordingly. Offline tests assert exact request parameters, per-page cap, category/tool/CLI plumbing, and unsupported-provider errors. Fielded title/author search is implemented in `0.9.19` after live verification (`title.search` and `raw_author_name.search` return 200; `authorships.author.search` returns 400 with the valid-field list). No arbitrary URL/query-string escape hatch was added.

### F4 — cross-provider merge/dedupe: implemented as explicit opt-in

Implemented in `0.9.16`. `research_by_category` accepts an explicit `providers=[...]` list; `gossamer research --providers ...` exposes the same mode. Calls are sequential, restricted to scholarly providers, mutually exclusive with `provider=`, and never run by default. Records merge only on canonical DOI or arXiv ID (matching arXiv versions through the versionless key); titles are not fuzzy-merged. Each merged result retains the first requested provider's canonical record, all source records, source names, and conflicting field values. Unkeyed records remain separate. Partial failures preserve successful results and expose per-provider errors plus a top-level error.
### F5 — shell invocation: documentation alternative already present

`skills/gossamer/SKILL.md` gives the Windows venv invocation (`…/.venv/Scripts/python.exe -m gossamer.cli …`). A global PATH shim may be convenient, but it is not required to address the documentation gap described in the finding.

### F6 — bot walls and binary acquisition: compliant caller-supplied fallback implemented

`download_file` now accepts caller-supplied `fallback_urls`/`--try-mirrors` and tries them sequentially, recording each URL and outcome plus the successful source. Every candidate is independently checked against SSRF, robots policy, and rate limits. It does not discover/scrape mirrors automatically, use browser sessions for binaries, or bypass access controls. If all candidates fail with bot walls/access denials, the result is `human_action_needed` with attempted URLs; otherwise it is `all_sources_failed`. Implemented in `0.9.17` with local tests for fallback success, challenge receipts, ordered attempts, and malformed candidate lists.

### Smaller workflow items

- **Done:** `check --mode status` and `cache --action prune|clear|reset` are documented in the Quickref and the skill now presents the end-to-end collection recipe. Cache-hit observability can still be improved as a lower-priority UX follow-up.
- There is no `cite --from-pdf` path. Treat that as a later convenience feature; first get DOI extraction/resolution and file acquisition right.
- Add a concise explanation of cache behavior to the workflow docs if users still cannot tell when a result is cached after using the existing commands.
- **Done (`0.9.18`):** Correct `AGENTS.md` patent routing: EPO/KIPRIS/PatentsView/Lens are key-gated; Google Patents is keyless number lookup only, not free-text search.

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
**Status:** Implemented in `0.9.12`; opt-in resume added in `0.9.20`.
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

**Status:** Implemented in `0.9.14`. `filter` and `select` are typed through the adapter, category facade, toolbox/MCP tool, and CLI. They are rejected unless OpenAlex is the selected provider. Requests cap `per_page` at the current API maximum of 100; blank controls fail locally. Tests pin request parameters, forwarding, and unsupported-provider behavior.

Fielded `title:`/`author:` search is deferred until the provider's current grammar is verified. Prefer typed options over arbitrary URLs/query strings; no raw-query escape hatch was added.

#### E2. Semantic Scholar provider integration (implemented)

**Status:** Implemented in `0.9.15`; opt-in and keyless-capable, with OpenAlex retained as the default.
**Likely files:** `gossamer/research_providers.py`, `gossamer/research_categories.py`, `gossamer/settings.py`, `gossamer/_core.pyi`, `src/adapters/scholar.rs`, `src/lib.rs`, `src/adapters/tests.rs`, `tests/test_research_providers.py`, `tests/test_research_categories.py`, `tests/test_live_smoke.py`, provider/docs tables.

1. **Verified before implementation:** the official Graph API uses `/paper/search` and `/paper/{paper_id}`, `query`/`fields`/`limit`/`offset`, a 100-item page limit and a 1,000-result relevance cap. `/paper/{paper_id}` accepts `DOI:` identifiers. The API tutorial documents the optional `x-api-key` header and individual 1 request/second allowance; unauthenticated callers share a pool.
2. **Done:** `SemanticScholarAdapter(ResourceAdapter)` uses provider id `semanticscholar` in the scholarly domain. `GOSSAMER_SEMANTICSCHOLAR_API_KEY` is optional, registered in `settings.py`/keystore, and sent only as `x-api-key`. The adapter never silently falls back to another provider.
3. **Done:** Implement search pagination (≤100 per request, ≤1,000 relevance results), paper lookup including DOI identifiers, at-least-one-second pacing, distinct 401/403/429 handling, bounded keyed retries, keyless 429 fail-fast with the exact key setting, and raw-source retention.
4. **Done:** Rust kernels normalize paper ID, title, authors, year/date, DOI, URL, abstract/snippet, citation count, venue, and OA-PDF metadata. Python reattaches the unmodified provider record under `raw`.
5. **Done:** Add Semantic Scholar last in the scholarly provider list, factory, and display-name map. OpenAlex remains the default; category research does not fan out.
6. **Done:** Mocked tests cover URL/params/header, pagination, search/fetch rows, environment key, 429 classification, and raw records. The opt-in live smoke is skipped without an API key and uses one paced request.
7. **Done:** Update README, QUICKREF, SKILL, scholarly taxonomy, key/keystore template, adapter counts, architecture, and changelog. Test badge is recalculated from the full suite.

**Acceptance:** Met by offline tests: `research <query> --provider semanticscholar` returns normalized records; optional auth uses the exact documented header/setting; keyless 429s name the exact setting and fail fast; OpenAlex remains the default; the live smoke is opt-in and key-gated.

#### E3. Opt-in multi-provider search and merge (F4; implemented)

**Status:** Implemented in `0.9.16`.

- `providers=[...]` / `--providers` explicitly opts into sequential scholarly search; no default fan-out. The list is validated as scholarly and is mutually exclusive with `provider=`, `filter`, and `select`.
- Normalize strong identifiers: DOI URLs and `doi:` forms map to one canonical DOI key; arXiv versions share a versionless match key while each exact version remains in its source record.
- Merge only on DOI/arXiv keys, never on title similarity. Records without a strong key stay separate.
- Each merged object contains the canonical first-provider record, all source records/provider names, and conflicting field values. Partial provider results are retained; failed providers appear in `provider_errors` and cause a top-level error.
- Tests cover duplicate DOI across OpenAlex/Crossref/arXiv/Semantic Scholar, versioned arXiv IDs, unkeyed records, conflicts, partial errors, invalid provider combinations, and deterministic ordering.

### Milestone F — compliant mirror handling and workflow/documentation

**Priority:** P2/P3  
**Status:** Caller-supplied mirror fallback (`0.9.17`), collection recipe, and patent-routing docs (`0.9.18`) are implemented; cache observability and convenience features remain deferred.
**Scope:** F6, F5 follow-up, small workflow items  
**Likely files:** `gossamer/downloader.py`, document/download orchestration, docs/README/QUICKREF/SKILL, `AGENTS.md`.

1. **Done (`0.9.17`):** `--try-mirrors` accepts caller-supplied, known OA/repository candidates (including `locate_pdf` results), tries them sequentially, and records each URL/outcome plus the selected source.
2. **Done (`0.9.17`):** Every candidate is independently checked against SSRF, robots, and rate limits. Exhausted bot-wall/access-denied chains return `human_action_needed` with attempts. No browser automation or access-control bypass is used.
3. **Done (`0.9.18`):** Update the skill/README/Quickref with **search → check → locate → download → extract/store → cite**, current commands, `max_pages`/budget guidance, and cache maintenance.
4. **Done:** Document the distinction: `download URL -o PATH` saves an opaque file; `extract URL --store` also parses and stores supported documents. Keep that separation clear in future workflow docs.
5. Defer `cite --from-pdf` until download and metadata extraction are stable; then add PDF metadata/DOI detection and tests rather than guessing citations from arbitrary text.
6. **Done (`0.9.18`):** Fix the stale `AGENTS.md` patent statement and preserve the distinction that `google-patents` is lookup-only, not free-text search.
7. Do not add a PATH shim unless users still need it after the skill’s existing venv command is made prominent. Treat cache-hit visibility as a usability follow-up, not a blocker for download correctness.

---

## 4. Semantic Scholar integration status

**Integrated in `0.9.15`.** The adapter is opt-in, OpenAlex remains the category default, the API key is optional, and keyless 429s return an actionable message instead of hammering the shared pool. The live smoke requires an API key and remains behind `GOSSAMER_LIVE=1`.

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
3. **C:** implemented in `0.9.12` with validation and classified outcomes; opt-in resume added in `0.9.20`.
4. **D:** completed in `0.9.13` — DOI-to-OA candidate resolution via OpenAlex.
5. **E1:** completed in `0.9.14` — OpenAlex-native filter/select.
6. **E2:** completed in `0.9.15` — opt-in Semantic Scholar adapter; OpenAlex remains the default.
7. **E3:** completed in `0.9.16` — explicit identifier-based scholarly merge.
8. **F6:** completed in `0.9.17` — caller-supplied policy-checked mirror attempts.
9. **Docs (`0.9.18`):** collection recipe and patent routing wording completed. Lower-priority follow-ups: cache-hit observability, `cite --from-pdf`, optional PATH shim, download resume, and OpenAlex fielded search.

This order fixes tool-breaking defects before adding the acquisition and precision features that motivated the hunt, while keeping external API work opt-in and policy-compliant.
