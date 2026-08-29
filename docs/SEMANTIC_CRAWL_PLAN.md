# Semantic Crawl — Implementation Plan

Status: **Approved** (2026-08-29). Implementation details (exact seams,
signatures, formulas, test pin audit, curation rules) are superseded by
`docs/semantic_crawl_implementation_plan.md`; this document's features,
invariants, non-goals, and gain table remain the contract.
Applies to: `stitch-web-researcher` v0.4.7 (`dev` @ `e795aa5`)
Target releases: **v0.4.8** (features A–E) and **v0.4.9** (feature F, optional
extra). *Shifted up one from the original v0.4.7/v0.4.8 — v0.4.7 was taken by
the meta-oxide clean-install fix.*

---

## 1. Purpose

v0.4.6 shipped a focused crawl: bounded BFS over the link graph with a
**lexical** frontier (bag-of-words coverage × 0.7^depth). It works, is
deterministic, and inherits the full page pipeline — but it has four known
limits:

| # | Limit | Symptom |
|---|-------|---------|
| G1 | **Paraphrase blindness** | "deep learning" never matches a link labelled "neural nets" |
| G2 | **No site-wide discrimination** | every word weighs the same; "platform"/"guide" drown real signal |
| G3 | **No richness signal** | the 300-char head skim says what a page is *about*, not how much substance it has; the agent must full-read to find out |
| G4 | **Link-graph-blind discovery** | crawl only sees what is linked; search-ranked pages, sitemap pages, and URLs written inside documents are invisible |

This plan closes all four with **six incremental features (A–F)**. Every
feature is additive, fails open to the v0.4.6 behaviour, keeps the toolbox
**LLM-free**, keeps the test suite **deterministic and offline**, and adds
**no new tools** (the registry stays at 13).

## 2. Non-negotiable invariants (build upon / do not break)

1. **Single fetch seam** — every URL is fetched through
   `_inspect_html_page_impl` (cache, robots S4, in-flight S5, rate limits
   M13, SSRF S1, guard §7, provenance, full 4-tuple cache write). No
   feature may introduce a second network path.
2. **M11 budget invariant** — crawl output is built via `_fit_json` +
   `_shrink_crawl`; the payload is always valid JSON, always within
   `max_markdown_chars`.
3. **LLM-free toolbox** — no API keys, no model inference in the core.
   Semantics come from deterministic machinery (A–E) or an *optional,
   fail-open* local model (F). The active LLM stays in the calling agent.
4. **Determinism & offline tests** — new logic must be testable against
   `example.com` paths with the fake `_fetch_html` 4-tuple seam, no
   network, no LLM, no randomness. Optional heavy backends follow the
   guard pattern (§7): lazy import, fail-open, stub backend in tests
   labelled as plumbing-only.
5. **Registry pin** — 13 tools; MCP/LLM pins in `tests/test_mcp_server.py`
   and `tests/test_p8_tool_registry.py` only change if a tool is added
   (this plan adds none).
6. **Version convention** — A–E = v0.4.8 (feature), F = v0.4.9 (new
   extra). Pure-Python: no Rust changes, no maturin rebuild in dev
   (release packaging rebuilds at ship time).

## 3. What we build upon (existing inventory)

| Existing piece (version) | Used by |
|---|---|
| `crawl()` + `_crawl_score` classmethod + topic-word machinery (v0.4.6) | A, B, D — `_crawl_score` is the single scoring seam; the IDF/thesaurus/embedding signals feed it |
| Page pipeline seam `_inspect_html_page_impl` (all tiers) | everything — unchanged |
| Page cache with full 4-tuple pre-budget write (C4/Tier 1.x) | F — page embeddings side-cached by `(url, content_hash)` |
| M11 `_fit_json` / `_shrink_crawl` | C — new payload fields must stay inside the budget |
| `search_web` + provider fallback + Tier 2.8 search cache | E1 — search prior is one cached search call |
| `discover_resources` (Tier 3.12, sitemap/feed probe) | E2 — sitemap URLs become `seed_urls` |
| `extract_links` (v0.4.5) in document extraction | E3 — document-internal URLs re-enter the frontier via `seed_urls` |
| Guard optional-extra pattern (§7): `[guard]` extra, lazy import, fail-open, `STITCH_GUARD_*` env knobs, stub in benchmarks | F — `[embed]` extra mirrors it exactly |
| Test regime: fake `_fetch_html` (4-tuple), example.com-only, config pins (`respect_robots=False`, zero delays) | all new tests |

## 4. Features

### A. BM25 / IDF frontier scoring — closes G2

**Idea.** The crawl already tokenizes every fetched page (topic words).
Turn that into a live site vocabulary: terms *rare* on the site
discriminate, terms on every page don't.

**Mechanics** (all inside the `_crawl_score` classmethod + one small
corpus object, pure Python):

- Corpus = full delivered text of every fetched page so far (incl. root).
  `N` = corpus size, `df(t)` = number of corpus pages containing `t`.
- `idf(t) = ln(1 + (N − df(t) + 0.5) / (df(t) + 0.5))`, clamped ≥ 0.
- **Degenerate-corpus rule:** for `N < 3` (root only / two pages),
  `idf(t) := 1` for all `t` — exactly the v0.4.6 coverage scores. The
  scorer therefore *is* v0.4.6 until the crawl has read a few pages, and
  improves as it goes. Deterministic, no special-casing downstream.
- Scores become IDF-weighted coverage (same 0.7/0.3 weights, so the
  `min_score=0.05` threshold keeps its meaning):

  ```
  query_cov = Σ idf(t) over t ∈ (label ∩ QUERY)   /  Σ idf(t) over t ∈ QUERY
  ctx_cov   = Σ idf(t) over t ∈ (label ∩ TOPICS)  /  Σ idf(t) over t ∈ label
  lexical   = 0.7 × query_cov + 0.3 × ctx_cov
  ```

- Label = anchor tokens ∪ path tokens, **plus anchor context** (G1
  enabler, Tier-A cheap): the ±50 chars of page text around the link are
  tokenized and merged into the label (cap the context contribution:
  label keeps its identity, context adds at most 8 extra tokens).
- **Path priors:** small multiplicative nudges on the final lexical
  score, table-driven constant: `/docs/`, `/guide(s)/`, `/blog/`,
  `/api/`, `/changelog/`, `/reference/` × 1.15; `/pricing`, `/careers`,
  `/contact`, `/about` × 0.85. Explainable, tunable, tested.

**Gain.** "platform" (df = N) gets idf ≈ 0 and stops outranking
"etcd" (df = 1). Site-wide discrimination with zero dependencies.

### B. Offline thesaurus (query expansion) — closes G1 (shallow layer)

**Idea.** A frozen, in-repo synonym map expands the query before scoring.
No network, no model, no LLM — and no non-determinism.

**Mechanics:**

- `stitch_web_researcher/thesaurus.json`: `{"version": 1, "clusters":
  [["deep learning","neural network","neural net","ml",...], ...]}`.
  Seeded with ~150–300 terms across the domains the researcher actually
  touches (ML/AI, web dev, data engineering, security, infra); curated by
  hand, versioned in the file, extended per release if a gap is found.
- Expansion: for each query term, pull in cluster members; the expanded
  set is capped at `2 × |QUERY|` terms (ties broken by term order).
- **Weighting:** expanded terms enter the IDF sums at **half weight**
  (`0.5 × idf(t)`) so direct matches always dominate expansions.
- Expansion is applied once per crawl to the effective query (explicit
  `query` or root-derived); the expanded set is what features A/C/D score
  against, and what the `query` echo in the payload shows
  (`"deep learning +2"` style: base terms + count expanded).
- Interaction with root-derived queries: unchanged — root topic words
  remain the implicit expansion when no query is given; the thesaurus
  expands them too (capped).

**Gain.** "neural nets" query finds the "deep learning" page (one cluster
hit). Cheap, deterministic, testable offline; the *deep* paraphrase layer
is feature F.

### C. Richness signals in the crawl payload — closes G3

**Idea.** The full page text is already in hand at crawl time. Compute
three stats before presentation so the agent can triage *without*
full-reading.

**Payload additions** (per page record):

| Field | Definition | Cost in payload |
|---|---|---|
| `content_chars` | `len(full delivered markdown)` (pre-skim) | ~12 chars |
| `term_hits` | occurrences of (expanded) query terms in the full body, counted on the token stream | ~10 chars |
| `excerpt` | **opt-in** (param `excerpts=True`, default `False`): ~300-char window from the region of highest query-term density (slide 300-char windows at 100-char steps; max density wins, ties → earliest; leading `…` marker when not at head) | +~320 chars/page |

- The existing 300-char head skim stays (`markdown`) — it carries H1/lead
  context; `excerpt` carries substance. Both only coexist when
  `excerpts=True`.
- **Budget decision:** at default `max_pages=15`, the base additions
  (~25 chars/page) keep the v0.4.6 fit; `excerpts=True` is documented as
  "accept a larger payload or lower `max_pages`" — `_shrink_crawl`
  continues to drop pages from the tail under M11, so no overflow path
  changes.
- For document entries (feature D) `term_hits` is **not** computable
  (documents are not fetched) — documents carry only `score`.

**Gain.** The agent answers "is this page worth a full read?" from
`(content_chars, term_hits, excerpt)` — no speculative full-reads, and
the answer is lexical-grounded rather than title-based.

### D. Ranked documents — closes part of G3 (for PDFs/DOCX)

**Idea.** Today `documents` is an unranked URL list (document links are
routed before scoring runs). Score them with the **same** A+B scorer.

**Mechanics:**

- Document candidates go through the same pipeline as pages, *stop at
  scoring* (never enqueued, never fetched), and record
  `{url, anchor, score}`.
- `documents` returns sorted by score desc; entries below `min_score` are
  dropped from the list but counted in `documents_below_score` (their
  URLs stay visible in `skipped` with reason `"below min score"`).
- `documents_total` keeps its meaning (unique document URLs seen).

**Gain.** The agent reads the *right* PDF first via
`extract_document` — and `extract_document` (v0.4.5) now returns the
URLs written inside it, feeding feature E3.

### E. Discovery seeds — closes G4

Three entry points into the frontier, all bounded, all through the
existing filters (same-host, boilerplate, assets, dedupe).

**E1. Search prior** (param `search_prior: bool = False`, opt-in):

- One `search_web` call: `site:<root host> <effective query>` (when no
  query, the root title). Provider fallback and the Tier 2.8 search
  cache are inherited — repeat crawls are free.
- Top **5** result URLs are normalized, deduped against `visited`,
  same-host filtered, documents routed to `documents`; the rest enter the
  frontier as depth-1 candidates **exempt from the `min_score` floor**
  (the search engine already ranked them) with a rank bonus
  `+ 0.1 / (i+1)` added to their lexical score (i = 0-based rank).
- Search failure (all providers down) is **non-fatal**: logged, crawl
  proceeds link-graph-only (fail-open).

**E2. Sitemap / external seeds** (param `seed_urls: list[str] = []`):

- Caller-supplied URLs (typically the output of `discover_resources`,
  Tier 3.12) enter the frontier at **depth 0** (their children are
  depth 1, within `max_depth` as usual).
- Seeds are scored like any candidate; below-floor seeds are skipped with
  reason `"seed below min score"` — *except* seeds are exempt from the
  floor by default? **No:** seeds respect `min_score` (the caller chose
  them deliberately, but the floor still guards asset-like junk); a seed
  that fails the floor is skipped and reported, never silently dropped.
  (Deliberate-ness is expressed by passing `min_score=0` if the caller
  wants unconditional fetching.)

**E3. Cross-modal loop** (no new mechanism):

- The supported pattern, documented in the README: crawl →
  `extract_document(top PDF)` → its `links` (v0.4.5) → re-crawl or
  `seed_urls` in a follow-up crawl. E2's `seed_urls` is the seam; E3 is
  the loop the agent runs. No crawl-internal document fetching is added
  (that would break the "documents are never fetched by the crawl"
  contract).

**Gain.** The frontier is no longer limited to what happens to be linked:
search-ranked pages, sitemap pages, and document-internal references all
become first-class candidates — while robots/SSRF/rate-limit/cache apply
unchanged because seeds ride the normal pipeline.

### F. Optional local embeddings (`[embed]` extra) — closes G1 (deep layer)

**Idea.** Dense vectors for the residual paraphrase gap (cross-lingual
near-synonyms, no shared term at all). Mirrors the guard optional-extra
pattern end to end.

**Mechanics:**

- **Extra:** `pip install .[embed]` → `onnxruntime` + a MiniLM-class
  ONNX encoder (~80–90 MB). Model resolved via
  `STITCH_EMBED_MODEL` (explicit path) or downloaded-once into the cache
  dir with a pinned model hash; load is lazy (first scored crawl),
  ~1 s one-time.
- **Backend selection** (`_embed_backend()`, exactly the guard pattern):
  real encoder when importable + model available; otherwise
  **fail-open to lexical-only** (v0.4.6 + A–E behaviour). Tests inject a
  deterministic **stub embedder** (hashed bag-of-words into a fixed
  64-dim vector) — plumbing checks only, labelled non-semantic, same
  honesty rule as the guard stub.
- **What gets embedded:** the effective query (1×, cached per crawl);
  each fetched page's first ~1500 chars (1×/page, side-cached in-process
  by `(url, content_hash)` — the on-disk cache 4-tuple is untouched);
  candidate labels at enqueue (anchor + path, ≤ queue cap 200, ~ms each
  on CPU).
- **Blending** in `_crawl_score`'s caller (one place):

  ```
  semantic = 0.7 × cos(QUERY, label) + 0.3 × cos(QUERY, source_page)
  final    = 0.5 × lexical + 0.5 × semantic      (real encoder)
  final    = lexical                               (stub / absent)
  ```

  Depth decay, min_score, and the whole frontier machinery are
  unchanged — they consume `final`.
- **Config/env:** `ToolboxConfig.embed_mode: "off" | "auto"` (default
  `"auto"` = use if available), env knobs `STITCH_EMBED_ENABLED`,
  `STITCH_EMBED_MODEL`, `STITCH_EMBED_DIM`; `get_stats()["embed"]`
  mirrors `get_stats()["guard"]` (backend, model hash, embeds, timings).
- **Determinism:** fixed weights, CPU, no randomness → reproducible
  scores for a fixed corpus; the model hash in stats makes runs
  comparable. The stub keeps the CI suite deterministic and network-free.

**Gain.** "transformers" matches "architecture for LLM pretraining" even
with zero shared terms — the last paraphrase layer — without an API key,
without non-determinism, and without changing behaviour for anyone who
doesn't install the extra.

## 5. How the pieces fit together

```
 effective query ──B──▶ expanded query set (≤2×, half-weighted)
        │                    │
        │                    ▼
        │            A: idf() from corpus of fetched pages
        │                    │
        ▼                    ▼
   E1 search prior ──┐  _crawl_score(label, depth)
   E2 seed_urls ─────┤     = 0.7×query_cov + 0.3×ctx_cov   (IDF-weighted,
   root links ───────┘       × path prior                          + anchor context)
        │                          │  F: + 0.5/0.5 blend with cosine signals
        ▼                          ▼
   frontier (cap 200, −min_score, decay 0.7^depth, ties = BFS order)
        │ pop best-first
        ▼
   _inspect_html_page_impl  (cache → robots → in-flight → rate limit →
                             fetch/static+browser fallback → extract →
                             guard → FULL 4-tuple cache write)
        │
        ├─▶ corpus update: tokens, df, topic words, (F) page embedding
        ├─▶ C: content_chars, term_hits, (opt) density excerpt
        ├─▶ D: document links scored + ranked, never fetched
        └─▶ next page … until max_pages / max_depth / frontier empty
        │
        ▼
   M11 _fit_json → crawl payload {pages(+stats), documents(ranked),
   documents_below_score, skipped(reasons), counters, stop}
        │
        ▼
   AGENT (the only LLM in the loop): reads skims/stats/excerpts →
   inspect_html_page(url) for full re-reads (cache hits) →
   extract_document(top PDF) → its links → E2 seed_urls → next crawl
```

**Decision table — who answers which question:**

| Question | Answered by |
|---|---|
| Which unfetched link next? | frontier: A+B (+F) score × 0.7^depth |
| Where do candidates come from? | root links + E1 search + E2 seeds (+ E3 loop) |
| Which PDF first? | D (same scorer, ranked `documents`) |
| Which fetched page to read in full? | C (`content_chars`, `term_hits`, `excerpt`) + skim + title |
| What's inside the PDF? | `extract_document` (v0.4.5 `links` → back into E2) |
| Budget / politeness / safety? | unchanged pipeline + M11 (invariant 1, 2) |

## 6. What we gain (before → after)

| Gap | v0.4.6 | v0.4.8 (A–E) | v0.4.9 (+F) |
|---|---|---|---|
| Paraphrase links (G1) | missed | found when a thesaurus cluster shares a term | found on dense-vector similarity, no shared term required |
| Common-word noise (G2) | uniform weights | IDF over the live site corpus | unchanged (A is final) |
| Agent triage cost (G3) | full-read to judge | stats + density excerpt judge it; full-read only the winners | same, with more accurate page selection |
| PDF ordering (G3) | unranked | ranked by the same scorer | semantic-aware |
| Discovery (G4) | link graph only | + search-ranked pages, sitemap seeds, document-internal URLs | same |
| Behaviour floor | — | everything fails open to exactly v0.4.6 scoring | embed absent → A–E behaviour |

Net: the crawl stops being "polite BFS with a thesaurus-shaped shadow"
and becomes a **relevance engine with discovery inputs**, while the page
pipeline, budget, and determinism invariants are untouched.

## 7. Explicit non-goals

- No LLM anywhere in the toolbox (no API calls, no inference in core).
- No per-candidate LLM or per-candidate embedding calls beyond label+page.
- No per-depth page quotas (global `max_pages` stays the budget model).
- No fetching of documents inside the crawl (contract: documents are
  collected, never fetched).
- No changes to `research()`, `search_web`, or the page pipeline itself.
- No new registry tools (13 stays 13); no new Rust code; no new required
  dependencies (F is an opt-in extra).

## 8. Release & commit plan

| Step | Version | Content | Commits (one concern each) |
|---|---|---|---|
| 1 | 0.4.8 | A (BM25/IDF + anchor context + path priors) + B (thesaurus.json + expansion) | `Feature: BM25/IDF frontier scoring with offline thesaurus expansion (v0.4.8)` |
| 2 | 0.4.8 | C (richness payload) + D (ranked documents) | `Feature: crawl richness stats and ranked document list` |
| 3 | 0.4.8 | E1 + E2 + E3 docs (`search_prior`, `seed_urls`) | `Feature: discovery seeds for the crawl frontier (search prior, seed_urls)` |
| 4 | 0.4.9 | F (`[embed]` extra, backend, blend, stats, stub, docs) | `Feature: optional local embeddings for crawl relevance (v0.4.9)` |
| — | — | README (new "Semantic Crawl" section + crawl param table), SPEC_AUDIT rows, badge (final test count), version bumps, push | folded into each step |

Gates per step: full pytest (expect growth 859 → ~920+ by the end of
0.4.8), ruff package clean, no Rust changes (no clippy/maturin needed in
dev; release packaging rebuilds at ship time).

## 9. Test plan (all offline, example.com, fake `_fetch_html`)

- **A:** rare-term beats common-term on a 4-page fixture; N<3 degeneracy
  equals v0.4.6 scores exactly (pinned regression); anchor-context link
  outranks bare-anchor twin; path-prior multipliers applied (table
  test); IDF improves across a multi-page crawl (score shift asserted).
- **B:** cluster expansion finds the paraphrase page; cap at 2×|Q|;
  half-weight ordering (direct match always above expansion-only match);
  thesaurus file version pin; root-derived query also expanded.
- **C:** `content_chars`/`term_hits` match fixture ground truth;
  excerpt = densest window (constructed fixture with a mid-page keyword
  block); `excerpts=False` default keeps the v0.4.6 payload shape;
  budget fit at 15 pages with stats on (M11 still holds).
- **D:** documents sorted desc; below-floor counted in
  `documents_below_score` and listed in `skipped`; documents never
  fetched (fetch-call assertion).
- **E1:** search prior URLs enter frontier, rank bonus ordering,
  min_score exemption, non-fatal search failure (providers stubbed
  down), search cache hit on repeat crawl;
  **E2:** seed_urls dedupe/robots/same-host respected, below-floor seed
  skipped with reason, depth bookkeeping (seed = depth 0);
  **E3:** README loop pinned by an integration-style test (crawl →
  extract fixture doc → seed_urls round-trip).
- **F:** stub determinism (same corpus → same scores); fail-open with
  package absent (score == A–E score exactly); blend math unit-tested;
  page-embedding side-cache hit on re-crawl; `get_stats()["embed"]`
  shape; model-hash reporting.
- **Regression:** every existing v0.4.6 crawl test passes unchanged
  except where a payload field was intentionally added (pin updates are
  deliberate and called out in the commit).

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Payload budget overflow from new fields | base stats ~25 chars/page (fits); excerpt opt-in; `_shrink_crawl` tail-drop unchanged (M11 is a hard floor) |
| IDF noise on tiny corpora | N<3 → uniform (exact v0.4.6 scores); no other special cases |
| Thesaurus staleness / wrong clusters | versioned file, capped expansion, half-weighting keeps direct matches dominant; clusters are additive, not replacements |
| Search prior returns off-topic results | host-scoped `site:` query, top-5 cap, same-host filter, fail-open on provider failure |
| Embedding model size / download | opt-in extra, lazy load, pinned hash, env override path; absent → fail-open; CI never downloads (stub) |
| Non-determinism creeping in | stub + fixed weights; stats report model hash; no randomness in A–E by construction |
| Scope creep into `research()` | non-goal §7; research keeps its search-then-fetch shape |

## 11. Open decisions — **resolved 2026-08-29** (see the decision log in
`docs/semantic_crawl_implementation_plan.md` §6; all defaults adopted):
1. `search_prior` default **False**; 2. head skim + opt-in excerpt **coexist**;
3. **~200 terms** now, grown per-release; 4. **MiniLM-class English** first,
multilingual later via model swap; 5. blend weights **fixed constants**.

---

*Builds on: v0.4.5 text-level link detection, v0.4.6 focused crawl,
Tier 2.8 search cache, Tier 3.12 discovery, §7 guard extra pattern,
M11 budget invariant, S1/S4/S5 page-pipeline guards.*
