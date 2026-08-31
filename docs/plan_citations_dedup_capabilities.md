# Plan — Citations/Export, Dedup + Liveness, Structured-Domain Capabilities, and Go-Project Hybrids

Scope of this plan (four workstreams requested):

1. **Citation / export path** — APA / MLA / BibTeX / CSL output, reconstructed from the existing DOI-backed adapters.
2. **Result dedup + source-liveness checks** — de-duplicate results by DOI/URL/content, and report per-source liveness.
3. **Structured-domain capability interface with per-category fallback** — register the many domain adapters behind a category table with ordered fallback, so the model can ask "scholarly works about X" and get the best adapter with graceful degradation.
4. **Selective adoption of Go-project pieces (diagnostics, audit, opt-in memory)** — implemented **off by default**, because a single-user agent tool pays the cost of every feature on every call.

**Implementation status (updated as each workstream lands):**

- Workstream 1 (citation / export path): **DONE** — `stitch_web_researcher/citations.py`, the `export_citations` MCP tool, `tests/test_citations.py` (+32), version `0.5.3`. Full suite green.
- Workstream 2 (dedup + source-liveness): pending.
- Workstream 3 (structured-domain capability + per-category fallback): pending.
- Workstream 4 (Go-project hybrids, off by default): pending.

Design constraints carried over from the existing code:

- Adapters own **only** request + parse logic (the `ResourceAdapter` base owns politeness, quota, auth injection, live rate-limit retune, retry/backoff). New code must not push request/response logic into the base.
- Everything routes through the **single source of truth** `TOOL_REGISTRY` (`config.py`) → `execute_tool` (`agent_tools.py`) → toolbox method. New tools need a toolbox method **and** a `ToolSpec`.
- Output is always budgeted via `ContentBudget._fit_json` / `_shrink_*` (`budget.py`) so a large export can't blow the token window.
- Never raise out of a tool: return a JSON error dict (`research`/`web_search` already do this).
- Offline-testable: adapters are unit-tested with mocked `httpx.get`.

---

## Workstream 1 — Citation / Export Path

> **IMPLEMENTED** (v0.5.3). Artifacts: `stitch_web_researcher/citations.py`,
> the `export_citations` `ToolSpec` in `config.py::TOOL_REGISTRY`, the
> `WebResearcherToolbox.export_citations` facade method in `agent_tools.py`,
> and `tests/test_citations.py` (32 tests). Suite green.
>
> Small deviation from the draft spec: the MCP tool's `results` param is a
> `list[str]` (the schema has no dict type), so the facade parses each entry
> that is valid JSON object into a dict and passes DOIs/URLs through as-is;
> `format_citations()` itself still accepts dicts, DOIs, and URLs.

### 1.1 What we already have (do not rebuild)

Every scholarly adapter already returns result dicts carrying the fields a citation needs:

| field | Crossref / Arxiv / PubMed / DOAJ | OpenAlex |
|---|---|---|
| `doi` | `w["DOI"]` | `w.get("doi")` |
| `id` | DOI or arXiv id | OpenAlex id |
| `url` | DOI/landing URL | doi or id |
| `title` | `title[0]` | `title` |
| `authors` | ", "-joined `family`/`name` | ", "-joined display names |
| `published` | `date-parts[0][0]` (YYYY-MM-DD) | `publication_date` |
| `raw` | full provider JSON | full provider JSON |
| `citations` | — | `cited_by_count` |

So a citation record can be **reconstructed** from any of these without a new fetch — best effort from the fields above — and **enriched** by a single canonical DOI lookup when a DOI is present.

### 1.2 New module: `stitch_web_researcher/citations.py`

Pure, dependency-free (stdlib `datetime`, `re`, `json` only). No HTTP.

- `BibliographicRecord` — a normalised dataclass: `title`, `authors: list[str]` (each `{"family","given"}` when available, else a single string), `year`, `month`, `day`, `doi`, `url`, `venue`/`journal`, `publisher`, `container_title`, `abstract`, `extra: dict` (raw provider payload), `id`.
- `record_from_result(result: dict) -> BibliographicRecord` — maps the adapter result-dict keys onto the record. Handles the Crossref `author: [{family, given}]` vs OpenAlex `authorships[].author.display_name` shape via a small per-source normaliser. Missing fields stay `None`/`[]`; this never raises.
- `enrich_with_doi(record, adapter=None) -> BibliographicRecord` — when `record.doi` is set, fetch the Crossref (or OpenAlex) full record for that DOI and fill in missing `venue`/`abstract`/`authors`. The *adapter is injectable* so tests can pass a fake; production passes a `CrossrefAdapter` (keyless, polite UA). This is the one place new network I/O is added for citations.

### 1.3 Formatters (pure functions, `records -> str`)

- `to_bibtex(records)` — `@article{key, ...}` / `@inproceedings` heuristic (detect from `venue`/`event` hints, else default `@article`); citation key = `firstauthoryear` lowercased.
- `to_csl_json(records)` — the canonical machine interchange form (items with `id`→`DOI`, `title`, `author`, `container-title`, `issued`, `URL`, `type: article-journal`). CSL-JSON is the *hub*: APA/MLA can be derived from it, and it's what citeproc libraries consume.
- `to_apa(records)` and `to_mla(records)` — style templates. These are **approximations** (no full CSL-STYLE processor); document that limitation in the docstring and in the tool description. APA 7th and MLA 9th basic shapes: author `Last, F. M.`, year in parens, title in quotes/title-case, venue italic.

### 1.4 Toolbox tool + registry

- Method `export_citations(self, results, style="bibtex", enrich=False, dedupe=True) -> str`
  - `results`: list of result dicts (as returned by `search_web`/`research`/`search_by_category`), OR a list of DOIs/URLs.
  - `style ∈ {bibtex, csl-json, apa, mla}` (enum param).
  - `enrich`: when true, do the DOI lookup enrichment (one extra call per unique DOI, rate-limited by the base).
  - `dedupe`: collapse records that share a DOI or a normalised URL before formatting.
  - Returns the formatted string, passed through `ContentBudget._fit_json` so a big export is shrink-safe.
- `ToolSpec` entry `export_citations` in `config.py::TOOL_REGISTRY`, dispatched to the method above.
- Also add a `format_citation` convenience if useful (single record), but `export_citations` with a one-item list is enough for v1.

### 1.5 Tests

`tests/test_citations.py` — offline:
- `record_from_result` on a Crossref-shaped and an OpenAlex-shaped dict (assert normalised fields).
- Each formatter round-trips a known record to an expected string.
- `enrich_with_doi` with a **fake adapter** (returns a canned Crossref payload) fills in the abstract/venue.
- Dedup by DOI collapses two dicts with the same DOI to one BibTeX entry.

---

## Workstream 2 — Result Dedup + Source-Liveness

### 2.1 Current state (reuse, don't re-implement)

- `research()` in `agent_tools.py` **already** dedupes candidate URLs via a `seen` set on `normalize_url(...)` and validates each against the SSRF guard before fanning out. So per-source liveness (status ok/error) already exists there.
- `web_search()` in `SearchService` already dedupes + optionally cross-merges results.
- What is **missing**: a *shared* dedup utility usable by any tool (not just `research`), dedup by **DOI** (not just URL — two URLs can be the same work), and a **liveness probe** that runs on a result list regardless of which tool produced it.

### 2.2 New shared helpers: `stitch_web_researcher/dedup.py`

- `dedupe(results, by=("doi","url","hash"))` — returns `(kept, dropped)` where `dropped` is a list of `{index, reason, match}`. Matching order: DOI first (strongest identity), then normalised URL, then content hash of `snippet`/`title` (weak signal, last resort). Pure, offline.
- `content_hash(text)` — SHA-256 helper (reuse `_sha256_hex` from `models.py`).

### 2.3 Liveness check: `stitch_web_researcher/liveness.py`

- `check_liveness(url, timeout=10.0) -> dict` — does a HEAD (or lightweight GET) through the **existing** fetch/SSRF/robots pipeline (do **not** build a new requester). Returns `{url, status, alive: bool, http_status, error?}`. Reuse `validate_public_url` (ssrf.py) + the toolbox's `_rate_limit_domain` politeness so we don't hammer hosts.
- Because a full fetch is expensive, provide a **status-only** mode (HEAD / minimal GET) as the default; a full-content check is opt-in.

### 2.4 Wiring

- Add `dedupe` as a parameter to `web_search(..., dedupe=True, dedupe_by=("doi","url"))` and surface the `dropped` count in its JSON.
- Add a toolbox method `check_sources(self, urls, mode="status") -> str` (new `ToolSpec`) that runs `check_liveness` on each URL in parallel (bounded) and returns per-URL status. Reuses the existing fetch pipeline → no new HTTP surface.
- `research()` already reports per-source `status: ok|error`; extend its payload to also emit a `dropped_dupes` count and a `liveness` summary.

### 2.5 Tests

`tests/test_dedup_liveness.py` — offline:
- `dedupe` collapses same-DOI / same-URL / duplicate-snippet records and reports reasons.
- `check_liveness` with `httpx.get` mocked returns the expected `{alive, http_status}` envelope and rejects non-public URLs via the SSRF guard.

---

## Workstream 3 — Structured-Domain Capability Interface + Per-Category Fallback

### 3.1 Current state

`research_categories.py` already has the exact skeleton to extend:

- `CATEGORIES` — ordered list of `Category(name, description, keywords, provider, kind)`. `general` (no keywords) is the implicit fallback.
- `_ADAPTER_FACTORIES` — `provider → dotted path` map, instantiated lazily by `_make_adapter`.
- `classify(query)` — first keyword hit wins; `search_category` instantiates the adapter and calls `.search()`, catching all exceptions into a result dict.

Currently only `scholarly→openalex` and `geo→open-meteo` are wired; the other 24 adapters (legal, financial, more scholarly) exist but are unreachable through the category surface.

### 3.1 Capability interface

Treat each category as a *capability* with an ordered **fallback chain** rather than a single provider:

```
Category(name, description, keywords,
         providers=[("openalex",), ("crossref",), ("arxiv",)],   # tried in order
         fallback_engine="duckduckgo", kind="adapter")
```

- `providers` is a list of tuples; each tuple is tried, and the **first** that returns ≥1 result wins. If all raise/fail empty, fall through to the next, finally to `fallback_engine`.
- `search_category` already loops adapters defensively; extend it to walk the chain. A category with an empty providers list and only a `fallback_engine` is a pure "engine" category (the current `general` behaviour).

### 3.2 New categories to register

| capability | keywords (seed) | providers (fallback order) |
|---|---|---|
| `scholarly` | (existing, expanded: add "arxiv", "doi", "peer-reviewed") | openalex → crossref → arxiv |
| `legal` | "case law", "statute", "regulation", "code of federal regulations", "congress bill", "eur-lex", "court", "legislation" | courtlistener → ecfr → federalregister → eurlex → germangov |
| `financial` | "stock", "quote", "finance", "market", "exchange rate", "index" | alphavantage → yahoofinance |
| `geo` | (existing) | open-meteo |

- Register the new providers in `_ADAPTER_FACTORIES` (they already exist in `research_providers.py`).
- Add display names in `_PROVIDER_DISPLAY`.
- Keep `classify` keyword-first; new categories slot in before `general`.

### 3.3 Per-category fallback semantics

- **Result-level fallback**: walk `providers`; a provider that returns 0 results (or errors) is skipped, next tried. This is *not* merging — it's "best source first, next-best if the best has nothing."
- **Budget-aware**: cap the number of fallback attempts per category (e.g. 3) to avoid N+1 calls when every adapter is down; the last failure becomes the result's `error`.
- **Optics**: the returned payload names the winning provider and any skipped ones (`{"provider": ..., "tried": [...], "skipped": [...]}`) so the model knows why it got what it did.

### 3.4 Tests

`tests/test_category_fallback.py` — offline:
- `classify` routes "latest arXiv paper on RL" → `scholarly`; "Section 2 of the CFR" → `legal`; "AAPL quote" → `financial`.
- `_make_adapter` instantiates each newly-registered provider.
- A walk with a stubbed adapter chain: first adapter returns [], second returns results → winner is the second; all fail → falls to engine.

---

## Workstream 4 — Go-Project Hybrids (OFF BY DEFAULT)

The Go project (zohabin) ships a diagnostics endpoint, an audit log, and opt-in session memory. All three are **disproportionate for a single-user agent tool** and add per-call cost/complexity. Adopt them strictly behind opt-in switches that default to off, mirroring how `GuardConfig`/`§7` and `FetchStats` (Tier 2.6) are already gated in stitch.

### 4.1 Diagnostics (low cost — mostly already exists)

- `FetchStats` (Tier 2.6, `models.py`) already tracks latency percentiles, bytes, per-domain counts, error classes. `get_stats` already exposes it.
- **Addition**: a small `diagnostics()` tool returning a compact health snapshot (library/version, adapter count, configured providers, cache hit-rate, active domains). This is a read of in-memory state — **no extra I/O**, so it's cheap enough to leave effectively always-usable, but keep it out of the default LLM description unless requested.

### 4.2 Audit log (opt-in, off by default)

- New `AuditConfig` (`config.py`) mirroring `GuardConfig`: `enabled: bool = False`, `path: Optional[str] = None` (append-only JSONL), `mask_secrets: bool = True`.
- When disabled (default): zero overhead — a single `if self._audit.enabled:` guard at the top of `execute_tool`.
- When enabled: append `{ts, tool, args_redacted, duration_ms, status}` per call. Secrets (API keys) are masked; never log raw result bodies > a small cap.
- **Off by default rationale** (document it): a per-call audit file is a privacy/IO surface a single-user tool doesn't need unless the operator opts in.

### 4.3 Opt-in memory / sessions (opt-in, off by default)

- New `MemoryConfig`: `enabled: bool = False`, `store_dir: Optional[str] = None`, `max_entries: int = 200`.
- When enabled, the toolbox keeps a bounded, FIFO-evicted history of `(query, tool, summary)` keyed per "session" (default: one process, or a caller-supplied `session_id`). Exposed via `memory_*` methods only when enabled.
- **Off by default rationale**: persistent memory is a privacy and determinism concern; an agent tool that starts fresh each call is simpler and safer. Provide it only when a caller explicitly passes a session id / enables config.

### 4.4 Anti-patterns to AVOID (learned from the other projects)

These are explicitly called out so adoption doesn't import their flaws:

1. **Fake/stub tools** (MEOK `deep_research`/`autonomous_research` return hardcoded `sources: ["arxiv.org",...]` with no real fetch) — never do this. Every advertised tool must perform real work or not exist.
2. **Fail-open auth** (MEOK `check_access` logs the user in on any error and metering via an external `proofof.ai` verify) — auth must fail *closed*; don't outsource auth/metering to a third party.
3. **Blocklist SSRF** (MEOK blocks only a small URL prefix list) — stitch already uses a proper `ssrf.py` allow/validate model; keep it, never replace with a blocklist.
4. **Per-call telemetry POST** (web-research-assistant `tracking.py` fires an analytics POST on every call, with a `reasoning`-required dark parameter) — no unsolicited network egress; diagnostics must be local-only.
5. **Upsell-nudge output** — don't inject marketing/upsell text into tool results.

### 4.5 Tests

`tests/test_optin_features.py` —
- With config disabled (default), `execute_tool` has no audit side effects and no memory writes; `diagnostics()` still works.
- With audit enabled + a temp path, a call appends one redacted JSONL line (secrets masked).
- With memory enabled + session_id, history is stored and evicted past `max_entries`.

---

## Ordering / risk

Recommended sequence (each independently testable, all low-risk):

1. **Workstream 1 (citations)** — pure module, no I/O except optional enrichment; lowest risk, high value.
2. **Workstream 2 (dedup/liveness)** — reuses existing pipelines; the `check_sources` tool adds one thin network path.
3. **Workstream 3 (capability/fallback)** — mostly data (register providers + fallback chains); extend `search_category`.
4. **Workstream 4 (hybrids)** — all opt-in, default-off; additive config + guarded hooks.

Each workstream adds its own `tests/test_*.py` and runs green against the existing suite (target: `1127 passed` baseline maintained). No changes to the `ResourceAdapter` base contract; no new third-party dependencies.
