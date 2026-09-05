# Live provider test — one-by-one results

> **Date:** 2026-09-05 · **Method:** each of the 5 search engines and 26 domain adapters
> exercised individually against the real network (no mocks), plus the pre-existing
> `tests/test_live_smoke.py` suite under `GOSSAMER_LIVE=1`.
> **Environment:** no API keys set — key-gated providers could only be tested to their
> no-key behavior. All optional deps installed (`browser_oxide`, `pdf_oxide`,
> `office_oxide`, `mcp`, `jailguard`, `citeproc`).
>
> Bottom line: **the offline suite is green while 11 of 31 live surfaces are broken —
> wrong hostnames, wrong paths, wrong response keys.** This is the failure mode
> predicted in `REVIEW_2026-09-05.md` §E.5. Nothing below was fixed in code; every
> "correct endpoint" was verified with a raw `httpx` call.

## 1. Smoke suite first (`GOSSAMER_LIVE=1`)

```
11 passed, 1 failed — test_fred_fetch: httpx.ConnectError (Errno 11001, getaddrinfo failed)
```

Retried in isolation: same failure in 0.41 s. Raw DNS check then showed the cause —
see §3.1. (The suite's other 11 tests, covering OpenAlex/Crossref/arXiv/PubMed/DOAJ/
OpenLibrary/Open-Meteo/WorldBank/GitHub search+fetch, all pass live.)

## 2. Full matrix (individual probes, `delay=0.0`, 1 s politeness gap)

| # | Surface | Result | Notes |
|---|---|---|---|
| 1 | DuckDuckGo search | ✅ `n=3` | |
| 2 | Google (no key) | ✅ clean `RuntimeError` asking for keys | correct |
| 3 | Bing (no key) | ✅ clean `RuntimeError` | correct |
| 4 | Exa (no key) | ✅ clean `RuntimeError` | correct |
| 5 | BrowserOxide search | ✅ `n=3` | |
| 6 | OpenAlex S+F | ✅ | |
| 7 | Crossref S+F | ✅ | |
| 8 | arXiv S+F | ✅ | |
| 9 | PubMed S+F | ✅ | |
| 10 | DOAJ search | ✅ | |
| 11 | OpenLibrary S+F | ✅ | |
| 12 | Open-Meteo S+F | ✅ | |
| 13 | WorldBank fetch | ✅ (`SP.POP.TOTL`) | search retired by design, returns note — OK |
| 14 | GitHub search | ✅ | keyless, rate-limited |
| 15 | CourtListener search | ✅ (`count=110959`) | fetch broken, see §3.9 |
| 16 | Yahoo **fetch** (chart) | ✅ | search broken, see §3.7 |
| 17 | BioRxiv date-interval | ✅ | free-text broken, see §3.8 |
| 18 | FRED | 🔴 dead hostname | §3.1 |
| 19 | NASA | 🔴 wrong API paths | §3.2 |
| 20 | NVD | 🔴 wrong host/path/params/parse | §3.3 |
| 21 | Zenodo S+F | 🔴 wrong paths | §3.4 |
| 22 | SoftwareHeritage search | 🔴 endpoint doesn't exist | §3.5 |
| 23 | Overpass | 🔴 dead hostname | §3.6 |
| 24 | Yahoo **search** | 🔴 wrong parse keys → always `[]` | §3.7 |
| 25 | BioRxiv free-text | 🔴 silent `[]` | §3.8 |
| 26 | AlphaVantage search | 🔴 wrong parse key (untestable E2E w/o key, certain by inspection) | §3.10 |
| 27 | CourtListener fetch | 🔴 wrong path + auth wall | §3.9 |
| 28 | eCFR | 🔴 wrong BASE + wrong response model | §3.11 |
| 29 | FederalRegister | 🔴 stale host + spurious `api_key=` → 302 | §3.12 |
| 30 | EUR-Lex | 🔴 endpoint doesn't exist | §3.13 |
| 31 | GermanGov | 🔴 dead hostname | §3.14 |
| 32 | Congress (no key) | ✅ 403, `requires_key=True` honest | key-gated, correct |
| 33 | Census (no key) | ✅ "Missing Key" page, `requires_key=True` honest | key-gated, correct |
| 34 | ChemRxiv (no key) | ✅ 403, `requires_key=True` honest | key-gated, correct |

(S+F = search and fetch both exercised, via smoke suite or probes.)

## 3. Broken surfaces in detail (with verified fixes)

### 3.1 🔴 FRED — hostname does not exist

- Adapter: `BASE = "https://api.fred.stlouisfed.org/graph/series_data"` (`research_providers.py:690`).
- `nslookup api.fred.stlouisfed.org` → **Non-existent domain**. Every FRED call fails at DNS.
- Verified live: the official API host `api.stlouisfed.org` resolves and answers
  (`400 … api_key is not set` — correct behavior), and the keyless CSV download
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDP&cosd=2023-01-01` returns 200
  with real observations.
- **Fix:** point at the official API (`/fred/series/observations?series_id=…&file_type=json`,
  parse `observations`), or at minimum the `fredgraph.csv` endpoint for the keyless path.
  Note the official JSON nests observations under `observations`, not the adapter's
  `observation`.

### 3.2 🔴 NASA — three wrong paths

- Adapter uses `/neo/ws/near_earth_date` (search) and `/neo/ws/neo/{id}` (fetch);
  fetch parses `near_earth_objects._single`. Live result: `401 Unauthorized`.
- Verified live: `GET /neo/rest/v1/feed?start_date=2024-01-01&end_date=2024-01-01&api_key=DEMO_KEY`
  → **200** with feed data. Single-object endpoint is `/neo/rest/v1/neo/{id}` and returns
  the object directly (no `_single` wrapper).
- **Fix:** `ws/near_earth_date` → `rest/v1/feed`, `ws/neo/` → `rest/v1/neo/`, drop the
  `_single` unwrap. (The 401 was the server rejecting an unknown path, not a key problem.)

### 3.3 🔴 NVD — host, path, params, and parse keys all wrong

- Adapter: `BASE=https://nvd.nist.gov/api/v2`, path `/vulnerabilities/search`,
  params `searchQuery/pageSize/cveId` + `jsonp=jsonCallback`, parses
  `vulnerabilityJsonDocument[].cve.cveMetadata.cvssData`. Live result: `403`.
- Verified live: `GET https://services.nvd.nist.gov/rest/json/cves/2.0?keywordSearch=log4j&resultsPerPage=2`
  → **200**, `{"totalResults":34, "vulnerabilities":[{"cve":…}]}`.
- The real API 2.0 has no `jsonp` concept, no `searchQuery`/`pageSize` params
  (`keywordSearch`/`resultsPerPage`/`cveId`), and no `vulnerabilityJsonDocument` envelope.
  The adapter's docstring describes an API that does not exist.
- **Fix:** rewrite against `https://services.nvd.nist.gov/rest/json/cves/2.0`
  (`keywordSearch`/`cveId`, `resultsPerPage`, parse `vulnerabilities[].cve`;
  CVSS lives under `metrics.cvssMetricV31[]`, not `cveMetadata.cvssData`).

### 3.4 🔴 Zenodo — search and fetch paths both 404

- Adapter: `/api/records/search` and `/api/record/{id}`. Live: both **404**.
- Verified live: `GET https://zenodo.org/api/records?q=quantum&size=2` → **200** with hits.
- **Fix:** `/records/search` → `/records` (`q`, `size`, `page`), `/record/{id}` →
  `/records/{id}`. (Record `url` field should also be the human page
  `https://zenodo.org/records/{id}`, not an `/api/…` URL.)

### 3.5 🔴 Software Heritage — search endpoint doesn't exist

- Adapter: `GET /api/v1/search/?q=`. Live: **404**
  `{"error":"Resource not found","reason":"The resource /api/1/search/…"}`.
- Verified live: origin lookup works
  (`/api/1/origin/https://github.com/python/cpython/get/` → 200). There is no public
  REST full-text code search to call.
- **Fix:** replace keyword search with origin-URL lookup (the one real primitive), or
  drop `search` and keep `fetch` for SWEET ids/origin URLs. (The `fetch` path's
  `/source/sid/` URL is unverified — confirm before keeping.)

### 3.6 🔴 Overpass — hostname does not exist

- Adapter: `BASE = "https://overpass-api.org/api/interpreter"` (also cited in its
  docstring). `getaddrinfo` → **11001, host unknown**.
- Real hosts: `overpass-api.de` (resolves; my test queries got `406`, likely my
  ad-hoc query encoding), `overpass.kumi.systems` (reached, returned an Overpass
  timeout doc — proving protocol viability from here).
- Two compounding bugs: dead host **and** no input validation — my probe passed the
  plain string `"Berlin"`, which is not Overpass QL, and the adapter shipped it to the
  network unchallenged.
- **Fix:** default host → `https://overpass-api.de/api/interpreter` (POST, `data=` form
  field), validate/reject non-QL input with an example, keep the 60 s timeout.

### 3.7 🔴 Yahoo search — wrong envelope keys, always returns `[]`

- Adapter parses `body.quoteCollection.quotes` and reads `shortName`.
- Verified live against `/v1/finance/search?q=AAPL`: the real envelope is top-level
  **`{"quotes": [...]}`** with **`shortname`** (lowercase n). Result: search silently
  returns `[]` while `fetch` (v8 chart, verified 200) works fine.
- **Fix:** `resp.json().get("quotes", [])`, `q.get("shortname")`.

### 3.8 🔴 BioRxiv free-text — silent `[]` by design-that-isn't

- Adapter: any non-DOI, non-date query falls back to `_lookup(str(max_results))`, i.e.
  `GET /details/biorxiv/5/0/json`. Verified live: the API answers
  `{"messages":[{"status":"Both dates must be in yyyy-mm-dd format"}],"collection":[]}`.
  So `search("crispr")` → `n=0`, no error, no hint. (Date-interval path verified
  working; DOI path untested — my test DOI may not exist.)
- **Fix:** the API has no full-text endpoint — return an actionable error/message
  record for free text ("pass a DOI or YYYY-MM-DD[/YYYY-MM-DD]"), or proxy free text
  through Europe PMC/OpenAlex. Never silent-`[]`.

### 3.9 🔴 CourtListener fetch — wrong path, plus an auth wall

- Adapter fetch: `GET /api/rest/v4/cluster/{id}/` → **404** (HTML). Correct plural
  `/clusters/{id}/` → **401 `Authentication credentials were not provided`**.
  Cluster/opinion *detail* requires auth; only *search* is keyless (verified:
  `count=110959`).
- **Fix:** `/cluster/` → `/clusters/` **and** catch 401 with an actionable message
  (`GOSSAMER_COURTLISTENER_KEY` needed for fetch), since keyless fetch is impossible by
  API design, not by adapter bug.

### 3.10 🔴 AlphaVantage search — wrong result key (breaks the authed path too)

- Adapter parses `body.get("data", [])`. The real `SYMBOL_SEARCH` response nests under
  **`bestMatches`** (`1. symbol`/`2. name`/`3. type`/`4. region`). With a valid key,
  `rows` is still `[]` → falls into the "note" branch and returns a junk record titled
  with the raw query. (No-key probe returned exactly such a record — the failure
  signature of the bug, visible without a key.)
- **Fix:** parse `bestMatches`, map `1. symbol`/`2. name`/`3. type`/`8. currency`.

### 3.11 🔴 eCFR — wrong BASE, wrong response model

- Adapter: `BASE=https://www.govinfo.gov/ecfr/rest/ecfr/json`, parses
  `title_title/part_title/sections{}`. Live: the URL returns **HTML** → `JSONDecodeError`.
  That govinfo path is not a JSON API.
- Verified live: the real API is `https://www.ecfr.gov/api/versioner/v1` —
  `/titles.json` → 200; per-title structure at
  `/structure/{issue_date}/title-{n}.json` → 200 with a `children[]` tree
  (verified for Title 21 @ 2026-08-31). No `sections` dict, no `title_title` keys.
- **Fix:** rewrite against the versioner API (titles → structure tree → node text).
  Note: ecfr.gov serves a bot-wall on HTML pages but the API endpoints answer 200.

### 3.12 🔴 Federal Register — stale host + spurious empty `api_key` → 302 on every call

- Adapter: `BASE=https://api.federalregister.gov/v1`, always sends `api_key=` (empty
  without a key). Live: **302 Moved Temporarily** on search *and* fetch.
- Verified live: `GET https://www.federalregister.gov/api/v1/documents.json?…` (no key
  at all) → **200**. The FR API is keyless; the `api.` host is retired in favor of `www.`.
- **Fix:** host → `www.federalregister.gov`, `requires_key=False`, stop sending
  `api_key` (an empty one appears to trigger the redirect), follow redirects
  defensively. (`fetch` document-number path shape is otherwise right.)

### 3.13 🔴 EUR-Lex — endpoint doesn't exist

- Adapter: `GET https://eur-lex.europa.eu/search/api/v3/search?…` → **404** (HTML).
  There is no public v3 JSON search REST on eur-lex.europa.eu. Both search and
  `fetch` (same endpoint, `q=docid:…`) can never work.
- Real machine access is the Publications Office CELLAR (SPARQL / direct resource
  resolution — a direct resource probe 404'd only because my test id was stale, the
  service itself is real) or the EUR-Lex search UI (JS, no stable REST).
- **Fix:** re-scope to CELLAR-backed lookup (CELEX → CELLAR URI) or remove the adapter
  from the `legal` category until a real transport exists. Do not keep a 404-ing
  adapter in the default routing table.

### 3.14 🔴 GermanGov — hostname does not exist

- Adapter: `BASE=https://api.de.gov.de/v1`, path `/aktenseiten?suchbegriff=`.
  `getaddrinfo` → **11001**. There is no such public API host.
- **Fix:** remove or re-scope (the current Bundesgesetzblatt portal is
  `www.recht.bund.de`, whose API surface needs separate investigation — left as
  follow-up work, not asserted here).

## 4. Key-gated providers (no keys in this environment)

| Surface | No-key behavior | Assessment |
|---|---|---|
| Google/Bing/Exa search | Clean `RuntimeError` naming the missing env vars | ✅ correct, untestable further here |
| Congress | 403 from api.data.gov | ✅ `requires_key=True` honest |
| Census | 302 → "Missing Key" HTML page | ✅ `requires_key=True` honest; minor: adapter appends empty `key=` and unknown `limit=` params |
| ChemRxiv | 403 | ✅ `requires_key=True` honest |
| AlphaVantage | Returns junk note-record (see §3.10) | 🔴 bug visible even without a key |

## 5. What this means for the review report

- `REVIEW_2026-09-05.md` §E.5 is confirmed empirically: the green suite coexists with
  11 broken live surfaces because only 10 adapters have live tests and the keyless
  wave-2 adapters (the ones most likely to be wrong — they were written against docs
  rather than responses) have none.
- Recommended minimum: extend `tests/test_live_smoke.py` to every `requires_key=False`
  adapter (search-only where fetch needs auth: CourtListener, Census-n/a), assert
  `n>0` on fixed reference queries, run nightly with `GOSSAMER_LIVE=1`. The fix recipes
  in §3 are ordered by user impact: FRED/NASA/NVD/Zenodo/eCFR/FR first (high-value
  data sources), EUR-Lex/GermanGov/SWH second (need re-scoping, not just URL swaps).
- The `research_by_category` routing table (`research_categories.py`) currently routes
  `legal` → CourtListener/eCFR/FederalRegister/EUR-Lex/GermanGov: **3 of its 5 legal
  providers are broken**, so category-routed legal research fails most of the time.
  Same for `financial` → AlphaVantage/Yahoo (search broken on both; only Yahoo fetch
  works). Consider routing `legal` default → CourtListener (working) and failing loud
  on the rest until fixed.

*Probe script used: `.tmp_live_probe.py` (deleted after the run). Raw `httpx`
verification calls are quoted inline above and are re-runnable as-is.*
