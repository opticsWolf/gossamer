# Alternative legal & financial providers — research findings

> **Date:** 2026-09-05 · **Method:** researched with the tool itself
> (`web_search` → `inspect_html_page`), every recommended endpoint then verified live
> with raw HTTPS calls. Companion to `LIVE_PROVIDER_TEST_2026-09-05.md` §3, which
> documents what's broken.
>
> Conventions: ✅ verified 200 with real data in this pass · ⚠️ works but with a
> caveat · ❌ tested and unusable · 🔍 not verifiable from here, needs a follow-up.

## 1. Legal — recommended disposition per slot

| Slot (current) | Verdict | Recommendation |
|---|---|---|
| CourtListener search | ✅ works | **Make it the `legal` default.** Search is keyless and verified (`count=110959` for "miranda"). Note it ships its **own MCP server** (per its help pages) — a future option is delegating instead of reimplementing. |
| CourtListener fetch | 🔴 404 path + 401 auth wall | Fix path `/cluster/` → `/clusters/` **and** expect 401 keyless: surface "fetch needs `GOSSAMER_COURTLISTENER_KEY`" instead of crashing. |
| Congress (key-gated) | ✅ honest 403 | Keep as the keyed US-legislation source. |
| eCFR | 🔴 fixable | **Fix in place** — real API verified (see §3). No replacement needed. |
| FederalRegister | 🔴 fixable | **Fix in place** — host swap + drop `api_key` (see §3). |
| EUR-Lex | 🔴 no public REST | **Remove adapter; replace with documented `site:`-scoped `web_search` fallback** (`site:eur-lex.europa.eu`). Machine alternative is CELLAR SPARQL — disproportionate complexity for this toolbox. |
| GermanGov | 🔴 fictional host | **Remove.** No verified keyless German-law JSON API found in this pass (candidate: `recht.bund.de` portal API — 🔍 follow-up). |

### 1.1 New legal sources worth adding

- **GovInfo API ✅** — `GET https://api.govinfo.gov/collections?api_key=DEMO_KEY` → 200
  with collection list (BILLS confirmed in payload). Covers bills, bill status, CFR,
  Federal Register, US Code, hearings — one keyless (DEMO_KEY) adapter could subsume
  parts of the eCFR/FR/Congress surface. Granule endpoints:
  `/collections/{code}/{date}/...`, packages endpoint `/packages/{id}/summary`.
  Priority: **high** — official, broad, keyless-friendly.
- **Caselaw Access Project 🔍** — the natural second case-law source (6.9M+ cases),
  but `GET api.case.law/v1/cases/?search=…` currently **301s to the docs site**, and
  the docs are JS-rendered (unreadable to the static fetch; stealth render also empty).
  Status unverifiable from here. **Do not add until verified**; CourtListener covers
  the need meanwhile.
- **Congress.gov bulk + GovInfo Bill Status** — already reachable via the GovInfo
  adapter above; no separate adapter needed.

## 2. Financial — recommended disposition per slot

| Slot (current) | Verdict | Recommendation |
|---|---|---|
| FRED | 🔴 fixable | **Fix in place**: official `api.stlouisfed.org/fred/series/observations` (key) + keyless `fredgraph.csv` fallback (verified 200 with real GDP rows). |
| Yahoo **fetch** | ✅ works | Keep. |
| Yahoo **search** | 🔴 2-line parse fix | `quoteCollection.quotes` → top-level `quotes`, `shortName` → `shortname` (verified against live payload). |
| AlphaVantage search | 🔴 parse fix | `body["data"]` → `body["bestMatches"]`, fields `1. symbol`/`2. name`/`3. type` (certain by API contract; E2E needs a key). |
| WorldBank fetch | ✅ works | Keep. |
| Stooq (candidate) | ❌ | **Do not add.** The documented CSV endpoint returns Stooq's "page does not exist" page and the daily endpoint serves a JS bot-wall from here. Possibly retired or restricted; re-check from deployment network before reconsidering. |

### 2.1 New financial sources worth adding (all ✅ verified below, all keyless)

1. **Frankfurter v2 — top FX pick.** `GET https://api.frankfurter.dev/v2/rates?base=USD&quotes=EUR,GBP`
   → 200 `[{"date":"2026-09-05","base":"USD","quote":"EUR","rate":0.86006},…]`.
   No key, no quotas (politeness rate-limit only), 201 currencies from 84 central banks
   back to 1948, time series (`?from=&to=`), CSV/NDJSON output, `group=month` downsampling,
   self-hostable, ships `llms.txt` **and its own MCP server**. (Note: v1 host
   `api.frankfurter.app` is dead — use `api.frankfurter.dev/v2`. Docs verified via the
   tool's own `inspect_html_page`.)
2. **ECB eurofxref daily XML — simplest FX pick.** `GET
   https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml` → 200, ~30 currencies
   vs EUR, updates ~16:00 CET. One XML parse, zero auth, official source. Pair with
   `eurofxref-hist-90d.xml` for history.
3. **ECB SDMX Data Portal — full-macro pick.** `GET
   https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?lastNObservations=2`
   → 200. Covers EXR, interest rates, CPI, balance of payments. SDMX-XML is verbose —
   wrap one dataset at a time (start with EXR).
4. **CoinGecko demo — crypto pick.** `GET
   https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd` → 200
   `{"bitcoin":{"usd":79634}}`. Keyless with shared rate limits (document politeness:
   ~5–15/min safe); `/coins/markets` gives caps/volumes. Covers the crypto gap in the
   current `financial` category (whose keywords already include "crypto"/"cryptocurrency"
   but no provider serves it).
5. **Key-gated bench (no verification possible here, strong free tiers per curated
   sources):** TwelveData (800 calls/day), Finnhub (60/min), Tiingo, Financial Modeling
   Prep (250/day). Add only on demand with a user-supplied key — same pattern as the
   existing Google/Bing/Exa providers.

## 3. Verified endpoint reference (copy-paste for implementation)

```
# eCFR (fix EcfrAdapter BASE + model)
GET https://www.ecfr.gov/api/versioner/v1/titles.json
  -> {"titles":[{"number":21,"name":"Food and Drugs","latest_issue_date":"2026-08-31",…}]}
GET https://www.ecfr.gov/api/versioner/v1/structure/{issue_date}/title-{n}.json
  -> {"identifier":"21","label":"Title 21…","children":[{…}]}   # tree, no `sections` dict

# Federal Register (fix host + drop api_key, requires_key=False)
GET https://www.federalregister.gov/api/v1/documents.json?per_page=2&conditions[term]=immigration
  -> {"description":"Documents matching 'immigration'","count":10000,…}

# FRED keyless fallback (fix FredAdapter)
GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDP&cosd=2023-01-01
  -> observation_date,GDP \n 2023-01-01,27216.445 …

# GovInfo (new adapter; DEMO_KEY works, free personal key raises limits)
GET https://api.govinfo.gov/collections?offset=0&pageSize=2&api_key=DEMO_KEY
  -> {"collections":[{"collectionCode":"BILLS",…},{"collectionCode":"ANNUALREP",…}]}

# Frankfurter v2 (new adapter)
GET https://api.frankfurter.dev/v2/rates?base=USD&quotes=EUR,GBP
GET https://api.frankfurter.dev/v2/rates?from=2024-01-01&to=2024-01-31&base=USD
GET https://api.frankfurter.dev/v2/rates.csv?base=USD          # CSV variant

# ECB eurofxref (new adapter, or fold into Frankfurter adapter w/ providers=ECB)
GET https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
GET https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml

# CoinGecko (new adapter)
GET https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd
GET https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=bitcoin&price_change_percentage=24h
```

## 4. Suggested category routing after the fixes

- `legal` → CourtListener (default, working) · eCFR (fixed) · FederalRegister (fixed) ·
  GovInfo (**new**, bills/code/hearings) · Congress (keyed). **Remove** EUR-Lex, GermanGov.
- `financial` → Yahoo (fixed search + working fetch) · FRED (fixed) · WorldBank (keep) ·
  Frankfurter (**new**, FX) · CoinGecko (**new**, crypto) · AlphaVantage (fixed parse, keyed).
- `geo` → Open-Meteo + ECB macro via Frankfurter/ECB (optional cross-listing).

## 5. Part 2 — Eurozone financial sources & EU/German case law (same method, 2026-09-05)

### 6.1 Financial, Eurozone focus — all ✅ verified, all keyless

1. **Deutsche Bundesbank SDMX — top DE/Eurozone pick.**
   `GET https://api.statistiken.bundesbank.de/rest/data/BBEX3/D.USD.EUR.BB.AC.000?startPeriod=2024-01-02&endPeriod=2024-01-05`
   → 200 SDMX-ML with parseable observations (verified: 2024-01-02 → **1.0956**, correct
   historical ECB reference rate). No key. Key series families: `BBEX3` (ECB euro reference
   rates, daily, `D.{CUR}.EUR.BB.AC.000`), money-market/capital-market rates, German
   macro series. Serves SDMX-ML only (JSON `Accept` → 406, verified) — parse generic
   `<Obs>` with namespace-agnostic ElementTree (`tag.rsplit('}',1)[-1]`). Path shape
   matters: `/rest/data/{FLOW}/{KEY}` works; `/rest/dataflow…` and `/rest/metadata…`
   404 (non-standard service, confirmed via the `sdmx`-python BBK notes). Docs URL from
   third-party indexes is stale (bundesbank.de help page 404s) — the endpoint above *is*
   the documentation.
2. **Eurostat dissemination API — top EU-macro pick.**
   `GET https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nama_10_gdp?format=JSON&lang=en&freq=A&unit=CP_MEUR&na_item=B1GQ&geo=DE&time=2023`
   → 200 JSON-stat, parsed to **Germany 2023 GDP = 4,254,930.0 €M** (verified). No key.
   Dataset codes are stable and documented in the Data Browser (`nama_10_gdp` GDP,
   `prc_hicp_midx` HICP, `une_rt_a` unemployment, `gov_10dd_edpt1` government debt).
   Filter syntax `&{dim}={code}` (`geo=DE`, `time=2023`); JSON-stat cube needs a small
   unpacker (dimensions → index → `value` map), one helper covers all datasets.
   Docs verified via the tool's own inspect of Eurostat's getting-started guide.
3. **BIS SDMX — central-bank research pick.**
   `GET https://stats.bis.org/api/v1/dataflow/BIS/all/1.0` → 200 (flows include
   `WS_CBPOL` policy rates, `WS_CBS_PUB` banking stats, `BIS_EER` effective XR,
   `BIS_PROP_PRICES` property prices). Data query verified:
   `GET https://stats.bis.org/api/v1/data/WS_CBPOL/all/all?startPeriod=2024-01&endPeriod=2024-03`
   → 200 structure-specific data. No key, SDMX-ML only (v1; `format=jsondata` → 406).
   Docs: `https://stats.bis.org/api-doc/v2/` (JS app — endpoint patterns above verified
   directly instead). Watch ToS (data.bis.org terms of permitted use).
4. **ECB SDMX Data Portal** (from Part 1, re-confirmed scope): EXR/MIR/HICP/Yield-curves
   via `https://data-api.ecb.europa.eu/service/data/{FLOW}/{KEY}?...` (verified 200).
   Prefer it over hosting ECB series via Bundesbank duplicates — same primary source.
5. **IMF — deferred.** `data.imf.org`/`api.imf.org` portal was in explicitly-flagged beta
   ("data is not final, should not be used for actual work" per Jan-2025 banner in the
   `sdmx`-python source notes). Global, not Eurozone. Revisit in a later pass, not now.

Not recommended: Stooq re-confirmed ❌ (CSV endpoint serves "page does not exist",
backup endpoint JS bot-wall — independent of network, dead as a machine source).

### 6.2 Legal, EU/German case-law focus

1. **HUDOC (ECtHR) — top EU case-law pick ✅.** The query API is unofficial but stable
   and actively used by third-party extractors (verified against `echr-extractor` source):
   `GET https://hudoc.echr.coe.int/app/query/results?query=<clauses>&select=itemid,docname,appno,kpdate&sort=itemid+Ascending&start=0&length=5`
   with base filter
   `contentsitename:ECHR AND (NOT (doctype=PR OR doctype=HFCOMOLD OR doctype=HECOMOLD)) AND (languageisocode="ENG")`
   → 200, **`resultcount: 95849`**, real records (`CASE OF ARRIGONI v. ITALY`, `22526/93`).
   Free-text goes in as an extra `AND` clause. Full text per `itemid` via the HUDOC
   document endpoint (same family — mark full-text fetch as implementation-time
   verification). No key. This is the ECtHR equivalent of what CourtListener is for the US.
2. **EUR-Lex CELEX full text ✅ (correction to Part 1).** Part 1's 404 was my invented
   CELEX, not a broken endpoint: `GET
   https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:62012CJ0131` (Google Spain,
   C-131/12) → **200**. So CJEU texts *are* machine-retrievable by CELEX — the gap is
   *search* (no REST), not retrieval. Adapter shape: HUDOC/ECLI search → CELEX →
   `legal-content` fetch. Keep the Part-1 verdict on the v3 search endpoint (fictional).
3. **InfoCuria (CJEU search) ⚠️ JS-gated.** `curia.europa.eu/juris/liste.jsf?language=EN&num=C-415/21`
   → 200 but Angular shell only ("RPEX"). No REST; static fetch can't use it. Fallback:
   browser-rendered fetch, or skip — HUDOC + CELEX covers retrieval, web_search covers discovery.
4. **ECLI search engine (e-Justice) ⚠️ form-only.** `e-justice.europa.eu/ecli/` → 200 HTML
   form, no REST endpoint found. Useful as a human link target in payloads, not as an adapter.
5. **openJur (600k+ German decisions) ⚠️ fetch-only, no search adapter.** `openjur.de/robots.txt`
   explicitly disallows `/suche/` for `*` (the tool itself refused the search URL —
   robots compliance working as designed). Decision pages (`/u/…`) are *allowed*, so:
   no keyword-search adapter (would violate robots), but `inspect_html_page` on direct
   openJur URLs is legitimate — surface them as `url` values from web_search discovery.
   (Whether openJur offers a licensed API/feed is a 🔍 follow-up with the operator, not
   something to scrape around.)
6. **recht.bund.de (BGBl portal) 🔍.** Portal live (200) but no API surface identified in
   this pass; Bundesgesetzblatt PDFs remain reachable via GovInfo-style bulk patterns.
   Needs a dedicated follow-up, not an adapter now.
7. **rechtsprechung-im-internet.de (BMJ, BVerfG/BGH/BVerwG/BAG/BSG/BFH decisions) 🔍.**
   Not probed in this pass; static-HTML, stable-URL pattern makes it a strong candidate
   for the same fetch-only treatment as openJur decisions. Verify robots + URL scheme next.

### 6.3 Suggested routing additions

- `financial` += Bundesbank (DE/Eurozone rates+macro) · Eurostat (EU macro) · BIS (policy
  rates/banking) · Frankfurter/ECB (FX, Part 1) · CoinGecko (crypto, Part 1). Yahoo/FRED/
  WorldBank stay; AlphaVantage stays keyed. Classification keywords to add: `bundesbank`,
  `ecb`, `eurostat`, `hicp`, `bundesbank`, `leitzins`, `eonia/estr`, `euribor`, `bip/gdp`
  (DE), `staatsverschuldung`, `arbeitslosenquote`.
- `legal` += HUDOC (ECtHR case law, default for Strasbourg queries) · CELEX-lookup
  (CJEU retrieval by CELEX). ECLI/InfoCuria as link targets, not adapters.
  Classification keywords to add: `echr`, `hudoc`, `egmr`, `menschenrechte`, `strasbourg`,
  `eugh`, `cjeu`, `celex`, `bverfg`, `bgh`, `rechtsprechung`, `urteil`, `az\b` (Aktenzeichen).
- New category candidate: `eu-econ` vs folding into `financial`? Recommend folding —
  Eurostat/Bundesbank/BIS/ECB are all macro/market data; one category with good
  provider descriptions beats a new top-level split.

### 6.4 Verified endpoint reference, Part 2

```
# Bundesbank (new adapter; SDMX-ML XML, namespace-agnostic parse)
GET https://api.statistiken.bundesbank.de/rest/data/BBEX3/D.USD.EUR.BB.AC.000?startPeriod=2024-01-02&endPeriod=2024-01-05
  -> <Obs><ObsDimension value="2024-01-02"/><ObsValue value="1.0956"/>…

# Eurostat (new adapter; JSON-stat)
GET https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nama_10_gdp?format=JSON&lang=en&freq=A&unit=CP_MEUR&na_item=B1GQ&geo=DE&time=2023
  -> {"version":"2.0","class":"dataset",…"value":{"0":4254930.0}}
# HICP: prc_hicp_midx · unemployment: une_rt_a · gov debt: gov_10dd_edpt1

# BIS (new adapter; SDMX-ML XML)
GET https://stats.bis.org/api/v1/dataflow/BIS/all/1.0                      # flow catalog
GET https://stats.bis.org/api/v1/data/WS_CBPOL/all/all?startPeriod=2024-01&endPeriod=2024-03

# HUDOC ECtHR (new adapter)
GET https://hudoc.echr.coe.int/app/query/results?query=contentsitename:ECHR%20AND%20(NOT%20(doctype%3DPR%20OR%20doctype%3DHFCOMOLD%20OR%20doctype%3DHECOMOLD))%20AND%20(languageisocode%3D%22ENG%22)&select=itemid,docname,appno,kpdate&sort=itemid%20Ascending&start=0&length=5
  -> {"resultcount":95849,"results":[{"columns":{"docname":"CASE OF …",…}}]}

# EUR-Lex CELEX retrieval (no search; pair with HUDOC/site: discovery)
GET https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:62012CJ0131  # 200
```

## 6. Part 3 — recht.bund.de access, openJur & InfoCuria access (2026-09-05)

### 7.1 recht.bund.de (BGBl) — full systematic access ✅, no API key, robots-clean

The portal documents its own machine-access scheme (`/de/service/webservice/webservice.html`, fetched and read in this pass):

- **ELI permalink scheme:** `https://www.recht.bund.de/eli/bund/BGBl-1/{YYYY}/{Nr}/`
  (Teil II: `BGBl-2/`; pre-2024 underscore form `BGBl_1/` still resolves). Numbers are
  sequential per year starting at 1 (suffixes like `1a` possible) → **new-issue
  polling = GET the next ELI URL, evaluate the response code** (explicitly documented).
- **Main text PDF:** `…/{Nr}/regelungstext.pdf?__blob=publicationFile`;
  attachments `anlage1.pdf`, `anlage2.pdf`, …; **whole issue ZIP:** `…/{Nr}/?view=zipdownload`.
- Verified live: ELI page → 200; PDF URL → 302 → 302 → **200 `application/pdf`, 279 KB**
  (redirects! the toolbox `extract_document` follows them — BGBl PDFs work through the
  existing document pipeline today, then yield follow-up links via text-link detection).
- **Change detection:** RSS feeds for BGBl I/II + newsletter (links on the webservice page).
- **robots.txt:** only `/SiteGlobals/` + `/de|en/serviceseiten/` disallowed — ELI URLs ✅ allowed.
- Adapter sketch: `search("BGBl-1/2024/…")` builds ELI URLs directly (citation-addressed,
  like the eCFR adapter's *intent*); `fetch` = ELI page or PDF/ZIP by suffix. No key, no
  rate documentation — stay polite (sequential-number polling is cheap by design).

### 7.2 openJur — search is forbidden, decisions are fetchable, API lives next door

- `openjur.de/robots.txt` **disallows `/suche/` for `*`** (the toolbox correctly refused
  the search URL in this pass). A keyword-search adapter against openJur would violate
  robots — **do not build one**.
- Decision pages (`/u/…`) are *not* disallowed → direct-URL fetch is legitimate;
  discovery via `web_search` (openJur pages are indexed).
- **The real answer is Open Legal Data (OLDP), verified ✅:** `https://de.openlegaldata.io`
  offers a documented REST API (`/pages/api/` read in this pass: cases search/list,
  law books/norms search, courts/cities/states endpoints, OpenAPI docs at `/api/docs/`,
  SDKs for Python/PHP/JS/Java, bulk dumps at `static.openlegaldata.io/dumps/de/`,
  HuggingFace mirrors, **and its own MCP server**). Live-verified:
  `GET /api/cases/?search=Miete&page_size=2` → 200, **`count: 424746`**, records with
  `id/slug/court{name,city,state,jurisdiction}/file_number/date/ecli/source_url`;
  `/api/laws/?search=144` → 200. Robots explicitly *invite* API/dump use
  ("do not crawl us … download everything via our data dumps or API").
- Note: old host `api.openlegaldata.io` is DNS-dead — use `de.openlegaldata.io/api/`.
- Adapter sketch: `OldpAdapter(BASE=https://de.openlegaldata.io/api)`,
  `search` → `/cases/search/?text=&page_size=` (**not** `?search=` — the list endpoint
  silently ignores it and returns the unfiltered total; verified), `fetch` →
  `/cases/{id}/`, laws via `/laws/` + `/laws/search/`. Search supports
  `start_date/end_date/court/court_jurisdiction/cited_law_book/cited_law_section/
  decision_type/return_text` filters and returns `snippets`. Covers German federal +
  state + EuGH decisions and statutes in one adapter. Priority: **highest of the legal adds**.
  (Full comparison openJur vs OLDP: §8.)

### 7.3 InfoCuria — allowed but JS-only; use the side doors

- `infocuria.curia.europa.eu/robots.txt` → `Allow: /` + sitemap ✅, but the app is an
  Angular SPA ("RPEX"): static fetch returns the shell; the sitemap lists only app
  tabs, not affair pages. Keyword search is not machine-accessible → **no search adapter**.
- Access paths that *do* work, in order of preference:
  1. **CELEX full text** (verified §6.2.2): `legal-content/…?uri=CELEX:{n}` → 200.
     Discovery via `web_search` (InfoCuria/EUR-Lex pages indexed) or HUDOC cross-refs.
  2. **Browser render** through the toolbox's own `use_smart="browser"` path for known
     affair URLs (robots allow; implementation-time verification needed).
  3. Third-party scrapers (e.g. the CURIA Apify actor found in research) — external
     dependency + key, last resort only.
- Same verdict for `rechtsprechung-im-internet.de` (BMJ portal), but stronger:
  robots is **`Disallow: /` for `*`** (sole exceptions: DG_JUSTICE_CRAWLER + ECLI sitemap).
  ❌ Not accessible to the toolbox at all — do not fetch; cite via `web_search` snippets
  or the courts' own sites (BVerfG/BGH decision databases) instead.

## 7. Appendix — openJur vs Open Legal Data: is completeness comparable?

Short answer: **no — different sizes, different strengths, unknowable overlap.**
Figures verified live 2026-09-05 unless noted.

| Dimension | openJur | Open Legal Data (OLDP) |
|---|---|---|
| Decisions | ~600.000+ (homepage claim); 610.000 (Feb 2023, Wikipedia) | **424.746** (`/api/cases/` total, live) |
| Statutes | 130+ Gesetze (2014 figure, stale); DBIS: federal/state/EU norms | **176.915** norms, 9.664 law books (`/api/laws/`, `/api/law_books/`, live) |
| Courts | Federal + OLG + state courts (direct court contributions) | **1.119** (`/api/courts/`, live); BVerfG, BGH, EuGH, BayVGH, Kammergericht, 12 OVGs individually verified |
| Full-text search API | ❌ none (`/suche/` robots-forbidden) | ✅ `/cases/search/?text=` + 12 filters, snippets, `return_text` |
| Bulk access | ❌ | ✅ dumps + HuggingFace + SDKs + MCP server |
| Update rhythm | Courts contribute regularly (per Wikipedia, 2023) | "Daily new documents" claim holds — newest record 2026-08-26 (live) |
| Machine-access friction | CAPTCHA wall appearing (even `/i/ueber.html`, verified); search disallowed | Robots explicitly invite API/dump use |
| Legal risk | ⚠️ sued 2023 (FAZ), VG Hamburg ruling Apr 2025 — availability overhang | MIT-licensed platform, open-data principles |

Notes and caveats:

- **Do not read 424k vs 600k as "OLDP covers 70% of openJur".** Neither side publishes
  its source list; there is no statement that OLDP ingests openJur (or vice versa).
  Both collect from courts directly. Overlap is unknowable from public sources — treat
  them as partially overlapping corpora, not subset/superset.
- **openJur's count is fresher than it looks but weaker than it sounds:** the 610k figure
  is Feb 2023; the homepage still claims "mehr als 600.000" with no date. Given the
  2023–2025 lawsuits, growth may have stalled — while OLDP demonstrably ingests to the
  current month.
- **For the toolbox the comparison is lopsided regardless of counts:** openJur's extra
  ~180k decisions are unreachable to machine clients (no API, search disallowed,
  CAPTCHA spreading), while every OLDP record is one GET away with structured metadata
  (ECLI, file number, court tree, citations). Completeness you cannot query is not
  completeness you can use.
- **Statutes side:** OLDP's 176k norms across federal/state/EU books vs openJur's
  aging "130+ Gesetze" figure — OLDP is the safer bet here too, with BGBl ELI
  (§7.1) as the authoritative primary source underneath both.
- Practical consequence for adapter priority (§7.2 stands, strengthened): build the
  OLDP adapter first; keep openJur as fetch-only URL targets discovered via web_search;
  never build an openJur search adapter.

## 8. Research trail (reproducible with the tool)

1. `web_search("free case law API court opinions Caselaw Access Project CourtListener alternatives", search_only=true)` → LoC guides, CourtListener MCP note, CAP mentions.
2. `web_search("free stock market data API no api key required Stooq Frankfurter daily quotes", search_only=true)` → `rahul-ai-studio/awesome-finance-apis` shortlist (Stooq/Frankfurter/Finnhub/TwelveData/Tiingo/FMP).
3. `web_search("govinfo API collections bills …", search_only=true)` → GovInfo developer hub, DEMO_KEY pattern.
4. `web_search("free exchange rates API no key ECB …", search_only=true)` → ECB Data Portal SDMX docs, Apify ECB actors (unneeded — direct endpoints verified instead).
5. `inspect_html_page(awesome-finance-apis, query=…)` → per-provider free-tier/limitation table (§2.1.5 sources).
6. `inspect_html_page(frankfurter.app/docs/)` → v2 API discovery (`api.frankfurter.dev`, no key, llms.txt + MCP) — the search index still pointed at the dead v1 host, found only by reading the docs page.
7. Negative results that saved implementation time (Part 1 + Part 2):
8. Part-2 trail: `web_search` Bundesbank-SDMX / Eurostat-REST / HUDOC-API / openJur-API
   (DE+EN) → sdmx-python sources (BBK/BIS/IMF registry), Eurostat getting-started guide
   (`inspect_html_page` → exact URL grammar + filter syntax), echr-extractor repo
   (`inspect_html_page` → `buildQueryUrl`/`HUDOC_BASE_URL` pattern, then raw
   `src/hudoc/query.ts` for the exact filter grammar), Bundesbank landing page (JS-only —
   endpoint reconstructed from sdmx-python `bbk.py` + live probing instead), BIS
   `api-doc/v2` (JS shell — flow catalog + data pattern verified directly),
   openJur `/robots.txt` (`/suche/` disallowed → no search adapter, fetch-only),
   CELEX retest with a real case (Google Spain C-131/12 → 200; the Part-1 404 was an
   invented CELEX). `case.law/docs/api` = 404, `case.law/docs/` = JS-only (CAP unverifiable); Stooq CSV = removed + bot-wall; `api.frankfurter.app/v1` = 404 (superseded by v2).
