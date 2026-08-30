# Research Data Access Layer — Plan

> Unified, boundary-honouring API access for the ~30 scholarly/legal/financial/geo
> resources below, exposed as a single MCP surface for an LLM harness.

Status: **plan**. Build on branch `main` (or a feature branch off it).
Last verified: **2026-08-31** (live-docs pass over all 30 resources).

---

## 1. Goal

Give the MCP/LLM harness **one stable surface** to reach a long tail of domain
APIs, where the layer **guarantees it never violates each provider's limits,
auth rules, or boundaries**. The harness calls tools; the layer owns the
politics (rate limits, keys, pagination, backoff, normalization).

Two hard requirements:
1. **Common surface** — the harness speaks one vocabulary regardless of source.
2. **Boundary enforcement** — per-provider rate limiting, concurrency caps,
   auth injection, and 429/backoff handling are *enforced by the layer*, not
   left to the model's discretion.

This plugs into the existing `stitch-web-researcher` project (Rust core
`_stitch_web_researcher_core` + Python wrapper + MCP server), reusing its
guard (prompt-injection protection), SSRF guard, cache, and provider abstraction.

---

## 2. Design principles

- **Layered, not monolithic.** Provider adapters sit behind a boundary
  coordinator; the MCP surface sits above normalization. Each layer is swappable.
- **The layer owns all politeness.** The model never sees a raw 429 or a
  `Retry-After`. It sees a clean "quota exhausted, retry later" signal.
- **Server truth > config.** Configured limits are *defaults/safeties*; live
  response headers (`X-RateLimit-Remaining`, `X-Rate-Limit-*`, `Retry-After`)
  win. Limits self-adjust.
- **Secrets never in code.** Keys come from env / a config file, per-provider.
- **Fail soft, stay polite.** On exhaustion or error, return structured data +
  a budget note, not a stack trace.
- **Reuse, don't re-litigate.** Existing guard/SSRF/cache/provider-registry
  patterns carry over.

---

## 3. Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  MCP tool surface (stable, small tool set for the LLM harness)  │
│  research_search / research_fetch / research_doi /              │
│  research_tseries / research_geo / research_multi               │
└───────────────────────────┬───────────────────────────────────┘
                            │ normalized records + budget + provenance
┌───────────────────────────▼───────────────────────────────────┐
│  Normalization layer  (provider JSON → common schema)           │
└───────────────────────────┬───────────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────────┐
│  Boundary coordinator  (the "politeness engine")                │
│   • per-provider token-bucket limiter (rps)                     │
│   • per-provider concurrency semaphore                          │
│   • header-driven dynamic adjustment (X-RateLimit-*)            │
│   • 429 → exponential backoff w/ jitter + Retry-After           │
│   • optional global soft cap + shared key pool                  │
└───────────────────────────┬───────────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────────┐
│  Key manager  (env/config → per-provider auth injection)        │
└───────────────────────────┬───────────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────────┐
│  Provider adapters (one per resource)  ← HTTP client, Rust or   │
│  Python; each knows base URL, auth shape, endpoints, schema.    │
└───────────────────────────────────────────────────────────────┘
   reused: guard (injection), ssrf (URL allow/deny), cache, telemetry
```

### 3.1 Provider interface
Every adapter implements the same contract:

```python
class ResearchProvider(Protocol):
    name: str                      # stable id, e.g. "openalex"
    domain: str                    # "scholarly" | "legal" | "financial" | "geo" | ...
    requires_key: bool
    default_limits: Limits         # rps, concurrency, per-page
    def search(self, query: str, params: dict) -> Iterator[RawRecord]: ...
    def fetch(self, record_id: str, params: dict) -> RawRecord: ...
    def inject_auth(self, req: Request) -> Request: ...   # auth per provider
    def parse_headers(self, resp) -> RateState: ...       # read server limits
```

### 3.2 Boundary coordinator (the core deliverable)
- **Token bucket per provider** refilled at `1/rps`; `acquire()` blocks/sleeps
  when empty so the provider's rps ceiling is never crossed.
- **Concurrency semaphore per provider** for providers that cap simultaneous
  connections (Crossref `X-Concurrency-Limit`, etc.).
- **Header feedback loop:** after each response, parse `X-RateLimit-Remaining`
  / `X-RateLimit-Reset` / `X-Rate-Limit-*` / `Retry-After` and *retune* that
  provider's bucket and concurrency live. If the server says "you're near the
  limit," the layer backs off before the hard 429.
- **429 handling:** exponential backoff with full jitter; honour `Retry-After`
  when present; cap max attempts; then return a `quota_exhausted` result.
- **Global soft cap (optional):** a shared token bucket across all providers so
  the harness can't collectively abuse the network host / egress.
- **Key pool:** when several keys are configured for a provider (OpenAlex, NCBI),
  the coordinator round-robins to spread each key under its own limit.

### 3.3 Key manager
- Loads keys from env vars (`STITCH_OPENALEX_KEY`, `STITCH_NCBI_KEY`, ...) or a
  config file. **Never** committed.
- Validates presence before a provider runs; fails the *tool call* with a clear
  "provider X needs key Y" message — not a cryptic 401.
- Supports key rotation/pooling for high-limit providers.

### 3.4 Normalization
Provider-specific JSON → one common record schema (see §6) so the harness
and downstream storage don't care which source answered.

### 3.5 Security (reuse existing)
- **SSRF guard:** every provider URL is checked against the existing SSRF
  filter; only declared provider hosts are allowed (block loopback/private).
- **Guard/prompt-injection:** responses from untrusted web sources pass through
  the existing `guard` before anything the model acts on. Research APIs are
  mostly trusted JSON, but any HTML/HTML-with-scripts or user-comment fields
  (e.g. CourtListener comments, Federal Register comments) are treated as
  untrusted data.

---

## 4. Conditions & API matrix — VERIFIED 2026-08-31

**Status key:** ✅ verified against official docs this session · ⚠️ no explicit
published number (use polite default + header retune, verify before prod).

### 4.1 General science & aggregators
| Provider | Base | Auth | Verified limit | Pagination | Status |
|---|---|---|---|---|---|
| OpenAlex | api.openalex.org | none / polite email / free key | >100 rps → 429; key = 10× keyless daily budget; per_query: per_page≤100, sample≤10k, basic paging≤10k (cursor beyond) | cursor paging | ✅ |
| Semantic Scholar | api.semanticscholar.org | none / key | unauth **1000 rps shared** (throttled); key intro **1 rps** | offset/limit | ✅ |
| Crossref | api.crossref.org | none / polite email | per-pool (public/polite) via `X-Rate-Limit-*`; concurrency via `X-Concurrency-Limit`; **reworked 2025-11-05** | offset/limit, per_page≤1000 | ✅ |
| CORE | core.ac.uk | key | **5 single req per 10 s** (0.5 rps), or 1 batch req/10 s | ? | ✅ |
| DOAJ | doaj.org/api | none | **2 rps**, burst up to 5 queued (avg 2/s) | offset/limit | ✅ |
| Open Library | openlibrary.org/api | none | **1 rps** default; **×3 with descriptive UA + email** | offset/limit | ✅ |

### 4.2 Domain-specific science & medical
| Provider | Base | Auth | Verified limit | Pagination | Status |
|---|---|---|---|---|---|
| arXiv | arxiv.org/api | none | **1 req per 3 s**, single connection; no hard cap (responsible use); max 30k results/query in ≤2k slices | start/index | ✅ |
| PubMed / NCBI E-utilities | eutils.ncbi.nlm.nih.gov | none / key | **3 rps no key, 10 rps key**; IP-block if abused | start/retmax | ✅ |
| bioRxiv | api.biorxiv.org | none | **No official limit**; community best-practice 1 rps/call, ≤3 rps total | page (≤100) | ⚠️ |
| medRxiv | api.medrxiv.org | none | **No official limit**; ≤3 rps total across bio+med | page | ⚠️ |
| ChemRxiv | chemrxiv.org (Cambridge Open Engage) | none | **No published limit found** | ? | ⚠️ |
| NASA Open APIs | api.nasa.gov | none / developer key | **DEMO_KEY: 30 req/hr/IP, 50 req/day/IP**; developer key higher | ? | ✅ |

### 4.3 Legal & government regulatory
| Provider | Base | Auth | Verified limit | Pagination | Status |
|---|---|---|---|---|---|
| CourtListener | www.courtlistener.com/api/rest/v4 | none / auth | **2026-05-07: 5 r/min, 50/hr, 125/day rolling** (old 5000/hr removed) | page | ✅ |
| Caselaw Access Project | cap-prod.ecu.edu | — | **API SUNSET 2024-09-05** — data now via CourtListener | — | ❌ remove/redirect |
| Congress.gov | api.data.gov | data.gov key | **5,000 calls/hour** (raised from 1,000 in Mar 2024) | start/count | ✅ |
| eCFR | ecfr.gov/api | none | **No explicit limit published** | page/limit | ⚠️ |
| Federal Register | federalregister.gov/api | none | **5 rps** (XML downloads 1 rps) | first 2,000 only | ✅ |
| EUR-Lex CELLAR | data.europa.eu | none | **No explicit limit** (SPARQL + REST) | ? | ⚠️ |
| Open Legal Data (DE) | de.openlegaldata.io | none | **No explicit limit published** | page | ⚠️ |
| e-Gov Law (JP) | laws.e-gov.go.jp/api/2 | none | **No published limit found** | ? | ⚠️ |

### 4.4 Financial & economic
| Provider | Base | Auth | Verified limit | Pagination | Status |
|---|---|---|---|---|---|
| FRED | fred.stlouisfed.org | none / key | **No published hard limit** (generous); key recommended | ? | ⚠️ |
| World Bank | api.worldbank.org | none | **No published limit** (keyless, generous) | page | ⚠️ |
| Alpha Vantage | alphavantage.co | key | **25 req/day, 5 req/min**; unlimited for verified OSS/edu | ? | ✅ |
| Yahoo Finance | query.finance.yahoo.com | none (unofficial) | **Undocumented, throttled**; legacy YQL 2,000 req/hr/IP | ? | ⚠️ ToS-gray |

### 4.5 Tech docs, code & software
| Provider | Base | Auth | Verified limit | Pagination | Status |
|---|---|---|---|---|---|
| GitHub | api.github.com | none / 60hr / PAT 5000hr | REST **60 r/hr unauth, 5,000 r/hr auth**; 1,000/hr per repo (GITHUB_TOKEN); GraphQL 5,000/hr | page, per_page≤100 | ✅ |
| Software Heritage | archive.softwareheritage.org | none / token | **Anonymous lower, token higher**; client auto-paces to server hints; no hard public number | ? | ⚠️ |
| Zenodo | zenodo.org/api | none / token | **Loose/documented**; OAI-PMH 50/page, resumption token valid **2 min** | page | ⚠️ (limit) |
| NVD (NIST) | nvd.nist.gov/api | none / key | **5 req/30 s unauth, 50 req/30 s with key** | ? | ✅ |

### 4.6 Geolocation, maps & environment
| Provider | Base | Auth | Verified limit | Pagination | Status |
|---|---|---|---|---|---|
| Open-Meteo | open-meteo.com | none | **10,000 calls/day** non-commercial free tier; no per-min limit; burst throttled | ? | ✅ |
| OpenStreetMap / Overpass | overpass-api.org | none | **No explicit number**; small requests prioritized; be polite | ? | ⚠️ |
| US Census Bureau | api.census.gov | key | **Key required; ~5,000 req/day commonly cited** (unconfirmed) | ? | ⚠️ |

---

## 5. MCP tool surface

Keep it **small and stable** — the harness should learn a handful of tools.

| Tool | Purpose | Key params | Returns |
|---|---|---|---|
| `research_search` | search one provider | `provider`, `query`, `params{}` | `records[]`, `provider`, `budget` |
| `research_fetch` | get one record by id/DOI | `provider`, `record_id`, `params{}` | `record`, `budget` |
| `research_doi` | resolve a DOI (Crossref) | `doi` | `record`, `budget` |
| `research_tseries` | macro time series | `provider` (fred|worldbank), `series`/`codes[]`, `start`,`end` | `points[]`, `budget` |
| `research_geo` | geo/climate lookups | `provider` (open-meteo|census|overpass), `lat`,`lon`/`geometry`, `params` | `record`, `budget` |
| `research_multi` | same query across many providers, merge | `providers[]`, `query` | `by_provider{}`, merged `records[]`, `budget` |

### 5.1 Common record schema (normalized)
```json
{
  "source": "openalex",
  "id": "W2022310430|s2:",
  "title": "...",
  "url": "https://...",
  "doi": "...",
  "published": "2019-...",
  "authors": [{"name": "...", "id": "..."}],
  "citations": 12,
  "open_access": true,
  "fields": {"scholarly": {"primary_location": {...}}},
  "raw": {}
}
```
Every tool also returns a `budget` object:
```json
{"provider":"openalex","rps_limit":100,"remaining_today":98765,"reset_seconds":3600,"exhausted":false}
```
When `exhausted==true`, the tool returns an empty `records` array and a clear
message ("quota exhausted; retry after Ns") instead of erroring.

---

## 6. Rate-limit & boundary model (detail)

1. **Default from §4**, clamped to the most conservative known limit per provider.
2. **Live retune:** parse server headers after each response; shrink the bucket
   toward the server's stated remaining/interval.
3. **Backoff:** on `429`/`403`/`418` → sleep `min(base*2^n + jitter, max_backoff)`
   or `Retry-After` if larger; then retry up to `max_attempts`.
4. **Exhaustion signal:** after max attempts, set `budget.exhausted=true` and
   return structured "retry later" — the harness decides when to call again.
5. **Concurrency:** per-provider semaphore; global soft cap optional.
6. **Key pool:** round-robin configured keys to multiply effective quota safely.

---

## 7. Config & secrets

```ini
# settings.ini / env — never committed
[providers.openalex]
key = ${STITCH_OPENALEX_KEY}
polite_email = research@example.org        # polite-pool User-Agent
[providers.ncbi]
key = ${STITCH_NCBI_KEY}
tool = stitch-web-researcher
email = research@example.org
[providers.alpha_vantage]
key = ${STITCH_ALPHAVANTAGE_KEY}
[providers.github]
key = ${STITCH_GITHUB_TOKEN}               # 5000/hr vs 60/hr
[limits]
default_rps = 5.0
global_soft_cap_rps = 20.0
max_backoff_seconds = 60
```
Missing key → tool returns a clean "provider X needs key Y (set STITCH_...)" error.

---

## 8. Error handling & retries (unified)

- `429 / 403 / 418` → backoff + retry (see §6.3).
- `404` → return a structured "not found" record, don't retry.
- `4xx` (bad params) → fast fail with the provider's error message.
- `5xx / network` → short backoff + retry, then soft error.
- Always attach **provenance** (provider, endpoint, request id, timestamp) so
  the harness can audit and debug.

---

## 9. Integration with stitch-web-researcher

- New Python module `stitch_web_researcher/research_providers.py` (adapters +
  coordinator + key manager + normalization) — pure Python is fine; keep heavy
  HTTP in the existing async client.
- Register adapters in the existing **tool registry** pattern
  (`agent_tools.py::TOOL_REGISTRY`) or a new `research_tools` group.
- Wire the six tools into the existing MCP server (`mcp_server.py`).
- Reuse `guard.py` (injection) and `ssrf.py` (URL allowlist) unchanged.
- Reuse `cache.py` for read-through caching of idempotent fetches.
- Version bump in lockstep with any new release (pyproject/Cargo).

---

## 10. Phased implementation

**Phase 0 — Spec lock (done).** Verified all 30 resources against live docs;
frozen §4 matrix.

**Phase 1 — Core layer.** `ResearchProvider` interface, token-bucket + concurrency
limiter, header feedback loop, 429/backoff, common schema, key manager. **No
providers yet** — unit-test the coordinator against a mock server.

**Phase 2 — First wave (robust, low-risk, no/cheap keys).** OpenAlex, Crossref,
arXiv, PubMed, World Bank, FRED, Open-Meteo, DOAJ, Open Library, GitHub(auth).

**Phase 3 — Domain waves.** Legal (CourtListener only — CAP is sunset; Congress.gov,
eCFR, FedRegister, EUR-Lex, DE, JP), science (bioRxiv/medRxiv, ChemRxiv, NASA,
NVD, Software Heritage, Zenodo), financial (Alpha Vantage, Yahoo), geo (Overpass,
Census).

**Phase 4 — MCP surface.** Expose the six tools; normalize; budget reporting.

**Phase 5 — Hardening.** Global soft cap, key pooling, usage dashboard/telemetry,
docs + CHANGELOG, release.

---

## 11. Testing

- **Coordinator:** mock HTTP server that returns `X-RateLimit-*` and `429`s; assert
  the layer never exceeds configured/live limits, backs off correctly, and reports
  exhaustion.
- **Per provider:** recorded fixtures (VCR-style) for schema + normalization;
  **not** live for rate-limited/free-tier providers (esp. Alpha Vantage 25/day).
- **Security:** SSRF allowlist, guard on any HTML/comment fields.
- **MCP:** tool schema + return-shape tests (reuse existing `test_mcp_server.py`).

---

## 12. Risks & open questions

- **Caselaw Access Project is DEAD as a live API** — sunset 2024-09-05; its data
  lives under CourtListener now. Drop the separate CAP adapter; use CourtListener.
- **Alpha Vantage (25/day) and CourtListener (125/day) are brutally low.** Not
  "search" APIs for bulk use; surface as *lookup-only* and cache aggressively.
- **Yahoo Finance is unofficial/undocumented/ToS-gray.** Recommend flagging, not
  hiding; gate behind the global soft cap.
- **Semantic Scholar nuance:** unauth ceiling (1000 rps shared) is high, but the
  *key* introductory rate is 1 rps — default to unauth with a polite header.
- **Crossref/OpenAlex/NVD all changed or are strict** — the header-driven retune
  (§6.2) exists precisely so config doesn't rot.
- **EUR-Lex / JP / DE legal APIs** are structured differently (SPARQL/XML) and
  may need custom adapters — budget extra time.
- **⚠️ Open items without a published number** (bioRxiv, medRxiv, ChemRxiv, eCFR,
  EUR-Lex, Open Legal Data DE, e-Gov JP, FRED, World Bank, Software Heritage,
  Overpass, US Census, Zenodo-limit): verify before prod; until then run them on
  polite defaults behind the header-retuning coordinator.
- **Open question:** `research_multi` fans out to many providers at once →
  multiplied rate-limit pressure. Gate behind the global soft cap. Recommend yes,
  but throttled.
- **Open question:** key storage — env-only (simplest, this plan) vs. encrypted
  store if multi-tenant. Start env-only.

---

## Appendix A — Confirmed vs spec discrepancies (verified 2026-08-31)

| Provider | Your spec | Verified (2026) |
|---|---|---|
| Semantic Scholar | "1 req/sec" | Unauth **1000 rps shared**; key starts at **1 rps** |
| OpenAlex | "Polite Pool via email" | Keyless OK; key = 10× daily budget; **>100 rps → 429**; cursor paging >10k |
| Crossref | "Free open REST" | Pools reworked **2025-11-05**; per-pool headers + concurrency limit |
| CORE | "10 rps with key" | **5 req / 10 s** (0.5 rps) |
| arXiv | "3 rps" | **1 req per 3 s**, single connection |
| NASA | "3000 req/hr unauth" | **DEMO_KEY 30 req/hr, 50 req/day** per IP; developer key higher |
| Congress.gov | "50 req/min" | **5,000 req/hour** (Mar 2024) |
| CourtListener | "Free REST v4" | **2026-05-07: 5 r/min, 50/hr, 125/day** (5000/hr gone) |
| Caselaw Access Project | listed | **API sunset 2024-09-05** — use CourtListener |
| NVD | "30/min unauth, 500/min key" | **5 req/30 s unauth, 50 req/30 s key** |
| Alpha Vantage | "25 requests/day" | ✅ correct, **+ 5 req/min** |
| PubMed | "3 rps no key, 10 with" | ✅ correct |
| Open-Meteo | "no key, free" | ✅ correct, **+ 10,000 calls/day free-tier cap** |
