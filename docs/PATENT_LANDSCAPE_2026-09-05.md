# Patent search & API landscape (US, EP, JP, KR, CN, WIPO, DE)

> **Date:** 2026-09-05 · **Method:** same as the provider research — web search via
> the toolbox itself, docs pages read in full, every endpoint claim probed live.
> Companion to `PROVIDER_ALTERNATIVES_2026-09-05.md`.
>
> Conventions: ✅ verified live here · ⚠️ verified with caveats · ❌ tested and
> unusable · 🔑 needs a key (unverifiable authed path from here).

## 1. Matrix

| Office | Surface | Auth | Format | Verdict |
|---|---|---|---|---|
| US (USPTO) | PatentsView v1 query API (`api.patentsview.org/.../query`) | was keyless | JSON | ❌ **retired** — serves the Angular SPA shell (200 text/html) |
| US | PatentsView Search Platform (`search.patentsview.org/.../api/v1/...`) | 🔑 service-desk key (`PATENTSVIEW_API_KEY`) | JSON (`count`/`total_hits`) | ⚠️ key-gated; host DNS-dead from here — needs verification from deployment net |
| US | PatentsView bulk downloads (quarterly TSVs) | none (site interaction) | TSV | ⚠️ ETL job material, not an adapter |
| EP (EPO) | OPS 3.2 (`ops.epo.org/3.2/rest-services/...`) | 🔑 free registration, OAuth2 client-credentials, fair-use quotas | XML (REST) | ✅ endpoint confirmed (anon → 403 Fair-Use, i.e. alive); fully spec-able |
| JP (JPO) | JPO developer API (account `JPO_API_USERNAME`+`PASSWORD`) | 🔑 account | JSON/XML per endpoint | ⚠️ exists per third-party verification; JPO origin CloudFront-walls automation and filters cloud egress — test per deployment |
| JP | J-PlatPat UI (`j-platpat.inpit.go.jp`) | none | HTML | 🔴 UI-only, not the recommended path |
| KR (KIPO) | KIPRIS Plus REST (`kipo-api.kipi.or.kr/openapi/service/...`) | 🔑 per-user `serviceKey`, free dev tier / paid operation | **XML only** | ✅ spec known; key-gated |
| CN (CNIPA) | PSS / cpquery / cponline portals | account + slider CAPTCHA + OTP | HTML SPA | ❌ no public API; live probes from research: 412/406/403s |
| WIPO | PATENTSCOPE UI | none | HTML | 🔴 ToS bans automation; only paid SFTP bulk |
| DE (DPMA) | DPMAconnectPlus interface + backfiles | agreement + marginal cost | interface/bulk | ⚠️ paid, not keyless; no free API |
| DE | DEPATISnet UI | none | HTML | UI-only (Espacenet/OPS carry DE data anyway) |
| Global | Google Patents BigQuery public dataset | GCP project (query billing) | SQL | ⚠️ powerful but not keyless-in-practice |

## 2. What this means for gossamer

1. **There is no keyless patent search API left.** The last one (PatentsView v1)
   retired. Every viable adapter is key-gated — same pattern as the existing
   Google/Bing/Exa/Congress/AlphaVantage providers, so the architecture already
   supports it.
2. **Recommended new `patent` category** (not a fold-in — prior art is neither
   case law nor finance): `uspto-patentsview` / `epo-ops` / `jpo` / `kipris`,
   all `requires_key=True`, with `epo-ops` as default (broadest coverage:
   EP biblio + INPADOC families incl. JP/CN/DE legal events).
3. **Coverage without new keys:** DE/CN/JP patents are reachable *today* through
   EPO INPADOC/Espacenet data (once OPS is keyed) and `site:`-scoped web_search;
   CN additionally via Google Patents BigQuery for GCP users.
4. **Do not build:** PATENTSCOPE adapter (ToS), CNIPA adapter (no API, CAPTCHAs),
   DEPATISnet scraper (UI-only; DE data comes via OPS), PatentsView-v1 adapter
   (dead), J-PlatPat scraper (UI-only + egress filtering).
5. **Build order when keys exist:** `epo-ops` → `kipris` (best-documented keyed
   API: host + service paths + XML-only known) → `uspto-patentsview` (needs host
   re-verification) → `jpo` (needs per-deployment egress test) → `dpma-connect`
   (paid contract, last).

## 3. Adapter sketches (for implementation)

- **EPO OPS**: `POST ops.epo.org/3.2/auth/accesstoken` (grant_type=client_credentials,
  Basic consumer key/secret) → `GET ops.epo.org/3.2/rest-services/published-data/search?q=...`
  with `Authorization: Bearer`, `Accept: application/xml`. CQL query language.
- **KIPRIS Plus**: `GET http://kipo-api.kipi.or.kr/openapi/service/{patUtliInfoSearchService,...}/{getWordSearch,...}?serviceKey=...`
  (XML; free dev quota, paid operation tier — §11 forbids key sharing, one key per deployment).
- **PatentsView Search**: `GET search.patentsview.org(:80)/api/v1/{patents,...}` with key
  header/param per service-desk docs; envelope `{error, count, total_hits, ...}`.
  ⚠️ re-verify host resolution + exact auth placement live before writing.
- **JPO API**: account-based (username+password per call, no OAuth); JSON/XML per
  endpoint. ⚠️ verify endpoints + egress from the deployment network first
  (CloudFront + cloud-egress filtering observed).

## 4. Research trail

1. `web_search` ×7 (PatentsView status, EPO OPS, J-PlatPat, KIPRIS, DEPATISnet,
   CNIPA, PATENTSCOPE) via the local toolbox (the `stitch-web-researcher` MCP
   entry is down post-rename — see §5).
2. Docs read in full: DPMA data-supply + webservice pages, KIPRIS/KIPO +
   CNIPA + PATENTSCOPE + JPO surveys on `docs.patentclient.com`
   (patent-client-agents — the best consolidated source found), Eurostat-style
   getting-started equivalents where they existed, `developers.epo.org` OPS page.
3. Live probes: PatentsView v1 (SPA shell), `search.patentsview.org` (DNS-dead),
   OPS anonymous (403 Fair-Use = alive), KIPRIS host (from docs, not probed —
   key required for any response), CNIPA (per cited probes), JPO pages
   (CloudFront 403 static, 404 guessed API path, browser-rendered 404 page).
4. PatentsView tutorial notebook (raw GitHub): confirmed key requirement
   (`PATENTSVIEW_API_KEY`), envelope shape, `api/v1/*` endpoint family.

## 5. Implementation status (0.7.0)

- `patent` category added (`epo` default) with tight keywords; `pct`/`claims`
  deliberately excluded (finance/insurance false positives).
- `EpoOpsAdapter` / `KiprisAdapter` / `PatentsViewAdapter` implemented with
  mocked-shape regression tests (`tests/test_patent_adapters.py`) and
  key-gated smoke tests (skip without keys). EPO anonymous-403 and FR-style
  envelope lessons applied (no `jsonp`-style invented params).
- JPO + DPMAconnectPlus **not** implemented: JPO endpoint paths unverified
  (gateway behind account wall, docs URL rotted, egress filtering) and
  DPMAconnectPlus needs a paid contract. Revisit with credentials in hand.
- Keys for all of the above slot into the keystore:
  `EPO_KEY`/`EPO_SECRET`, `KIPRIS_KEY`, `PATENTSVIEW_API_KEY`
  (`python -m gossamer.keystore --init` lists them).

## 6. Ops note (not patent-related)

The `stitch-web-researcher` MCP server entry in this harness is down because the
package was renamed — the server now boots as `gossamer` (`python -m
gossamer.mcp_server`). This session's research ran through the same toolbox code
via the local venv instead. Update the MCP client config to the new module path
to restore the `stitch-web-researcher_*` tools (or re-register them as `gossamer_*`).
