# Semantic Crawl — Implementation Plan (Amendment)

Status: **Approved for implementation** (2026-08-29). Supersedes the
implementation details in `SEMANTIC_CRAWL_PLAN.md`; that document's
features, invariants, non-goals, and gain table remain the contract. This
amendment records the concrete spec produced by reading the v0.4.7 code
(`dev` @ `e795aa5`): exact seams, signatures, formulas, the pin-risk
audit against the existing crawl tests, and the curation rules the
implementation must follow.

Version note: the original plan's "v0.4.7/v0.4.8" release targets have
already shifted to **v0.4.8 (A–E) / v0.4.9 (F)** because 0.4.7 was taken
by the meta-oxide clean-install fix.

---

## 0. What this amendment changes relative to the original plan

| # | Original plan | Amendment | Why |
|---|---------------|-----------|-----|
| Δ1 | "`_crawl_score` is the single scoring seam" (rewritten) | `_crawl_score` **keeps its exact v0.4.6 signature** and gains three optional kwargs: `corpus=None, label_extra=frozenset(), base_terms=None`. `corpus=None` → bit-for-bit legacy behaviour. | 29 existing crawl tests call the crawl and pin scores; an additive seam means the legacy path stays testable and untouched, and the N<3 degenerate rule has an exact regression target. |
| Δ2 | "one small corpus object" (unspecified) | Concrete `_CrawlCorpus` dataclass: `n: int`, `df: dict[str, int]`, `add_page(text)`, `idf(t)`. Module-level, no class coupling. | Deterministic, unit-testable in isolation. |
| Δ3 | Path priors listed under A with no gating | Path priors apply **only when `corpus.n >= 3`** (the non-degenerate regime). For `n < 3` the scorer is exactly v0.4.6 (uniform weights, no prior). | The plan's own degenerate rule says "the scorer *is* v0.4.6 until the crawl has read a few pages" — an ungated prior would break that exactness for any `/docs/`-style URL in a 2-page crawl. |
| Δ4 | Thesaurus "150–300 terms … curated by hand" | Curation **constraint added**: no ultra-generic tokens. Concrete exclusion list (§2.6.3). | The pin audit (§2.9) showed `test_query_echo_derived` and every derived-query test only pass if common fixture words ("platform", "hub", "notes", "company") never enter a cluster. Precision beats recall for a half-weighted expansion. |
| Δ5 | "every existing v0.4.6 crawl test passes unchanged" | **Two amendments required** (details §2.9): `test_depth_decay_allows_deep_overtake` (fixture) and `test_relevance_focuses_the_crawl` (score pins only). All 27 other tests pass unchanged. | Anchor context (A) legitimately re-ranks a fixture whose short root page puts the query words inside the weak candidate's ±50-char window; the expanded query denominator rescales the flat-regime pins. Both test intents are preserved by amending the fixtures/pins, not by weakening the feature. |
| Δ6 | E2 `seed_urls: list[str]` | Confirmed supported: `ToolParam(..., list[str], ...)` already ships in `batch_inspect_pages` (agent_tools.py:649) and `json_schema` emits `array of string` for it. No coercion work needed. | Verified against the registry, not assumed. |
| Δ7 | E1 "one `search_web` call" | Exact seam: `self.search_web(f"site:{host_key} {focus}", max_results=5)`; parse the JSON, take `results` (list of `{title, url, snippet}`); `error` key or JSON failure → fail-open. Tier 2.8 in-memory search cache makes repeat crawls free. | E1's parser must match the real payload shape (`{"results": [...], "guard": {...}, ...}`). |
| Δ8 | Open decisions §11 of the original plan | All five resolved (defaults adopted, logged in §6). | The plan was approved as written ("start with semantic crawl plan"). |
| Δ9 | (added during step 1) "every existing v0.4.6 crawl test passes unchanged" | `test_relevance_focuses_the_crawl` score pins rescale because the expanded query's half-weighted terms join the flat-regime denominator: A `0.63 → 0.467`, A1 `0.417 → 0.302`. Pins widened (`0.4 ≤ A < 0.6`, `0.25 ≤ A1 < 0.5`) and an `+2` echo assertion added; all other assertions unchanged. | Discovered while computing the audit against the on-disk fixtures; see §2.9. |
| Δ10 | (added during step 2) `documents` is a list of URL strings | The record-shape change of feature D (`{url, anchor, score}`) plus the `min_score` floor on documents required amending two fixtures: the PDF anchor in `test_relevance_focuses_the_crawl` ("Annual report" → "Deep learning annual report") and the anchors in `test_document_links_collected_not_fetched` ("Report"/"Spec" → "platform report"/"platform spec") now carry the query vocabulary so the documents clear the floor. Shape assertions read `d["url"]`; `documents_total`/never-fetched assertions kept. | The floor is part of D's contract (§3.3) — an irrelevant document is *reported* (`skipped`, `documents_below_score`), not silently dropped. The fixtures' intent (collection, dedupe, ranking, never-fetched) is preserved. |

## 1. Current state (verified seams, v0.4.7)

All line numbers refer to `stitch_web_researcher/agent_tools.py` at `e795aa5`.

| Seam | Location | Contract |
|------|----------|----------|
| `crawl()` | ~4063 | Params `root_url, query, max_depth, max_pages, same_host, min_score`; clamps to hard caps (5 / 50); returns JSON via `_fit_json` + `_shrink_crawl` (M11). |
| `_crawl_host_key(url)` | ~3985 | Lowercase host, leading `www.` stripped. |
| `_crawl_tokens(text)` | ~3993 | `{re.findall(r"[a-z0-9]+", lower) − STOPWORDS}`. **Hyphens split tokens** ("front-end" → `front`, `end`). |
| `_crawl_topic_words(text)` | ~4001 | Top-40 TF content words, sorted `(-count, term)`. |
| `_crawl_score(cls, url, anchor, depth, query_terms, page_terms)` | ~4017 | `0.7·\|label∩Q\|/\|Q\| + 0.3·\|label∩P\|/\|label\|`; `label = tokens(anchor) ∪ tokens(urlpath)`. `depth` unused in body (decay applied by caller). |
| `_shrink_crawl(result, budget)` | ~4044 | Keeps `budget // 500` pages from the head; `pages_omitted` counter. |
| `expand(page, page_url, depth)` | inner fn | Iterates `page["follow_up_links"]` — candidates are `FollowUpCandidate` dicts: **`title` = anchor text** (or `"(untitled)"`), `url`, `type` ∈ {page, document}. Documents routed before scoring, never fetched. Filters: dedupe (fragment-stripped key) → same-host → boilerplate path → asset extension → `min_score` floor → frontier push `(score·0.7^depth, seq, key, depth, anchor)`. Queue cap 200, weakest evicted. |
| `fetch_record(url, depth, score_eff)` | inner fn | `_inspect_html_page_impl(url, None, "", 0, 1)`; classifies `error`/`warning` dicts; record = `{url, depth, score, status, title, markdown[:300], links_total}`; **full `md` (pre-skim) is in hand here** — the corpus hook point. |
| Root fetch | inline | Same impl call; failure/error-dict kills the crawl; root record hard-coded `score: 1.0`; root appended to `pages` **before** `expand(root_page, root, 0)`. |
| Effective query | inline | Explicit `query` → `_crawl_tokens(query)`; else `_crawl_topic_words(root_md + " " + root_title)`; echo `"derived from root page"`. |
| Page text available | — | `page["markdown"]` in `expand()`/`fetch_record()` is the **full delivered markdown** (skim is applied only to the record). Corpus update uses this. |
| Search seam | `search_web()` @ ~1505 | Public method; provider fallback + `search_merge` + Tier 2.8 in-memory cache (TTL, cap 256); returns JSON with `results` (list of `{title, url, snippet}`), `guard`, optional `provider_fallback`, `count`; `{"error": ...}` when all providers fail. |
| Registry | `TOOL_REGISTRY` | 13 tools; `ToolParam` supports `str/int/bool/list[str]` (list already used at :649). `execute_tool` dispatches by method name. |
| Packaging | `pyproject.toml` | `[tool.maturin] python-packages = ["stitch_web_researcher"]` → **the whole package dir ships in the wheel; a JSON file placed inside `stitch_web_researcher/` is packaged with no `include` block** (explicit includes were a live trap per the existing comment). |
| Existing crawl tests | `tests/test_crawl.py` | 29 tests; fakes `tb._fetch_html` (4-tuple `(markdown, links, meta, method)`); queries used: `"deep learning"`, `"notes"`, `"needle deep"`, plus derived (no-query) crawls; score pins: root `1.0`, depth-1 `0.6 ≤ s < 1.0`, depth-2 `0.3 < s < 0.6`; `documents == [PDF]`; echo `"derived from root page"`. |

Constant inventory (class attrs on `WebResearcherToolbox`): `_CRAWL_MAX_DEPTH=5`,
`_CRAWL_MAX_PAGES=50`, `_CRAWL_PAGE_CHARS=300`, `_CRAWL_QUEUE_CAP=200`,
`_CRAWL_DEPTH_DECAY=0.7`, `_CRAWL_QUERY_WEIGHT=0.7`, `_CRAWL_CONTEXT_WEIGHT=0.3`,
`_CRAWL_TOPIC_WORDS=40`, `_CRAWL_MIN_SCORE=0.05`, `_CRAWL_LIST_CAP=30`,
`_CRAWL_SKIP_EXTENSIONS`, `_CRAWL_SKIP_PATH_PREFIXES`, `_CRAWL_STOPWORDS`.

## 2. Step 1 — features A + B (BM25/IDF + thesaurus), v0.4.8

Single commit: `Feature: BM25/IDF frontier scoring with offline thesaurus
expansion (v0.4.8)`.

### 2.1 `_CrawlCorpus` (new, module-level in agent_tools.py)

```python
class _CrawlCorpus:
    """Live site vocabulary: document frequency over fetched pages."""
    __slots__ = ("n", "df")
    n: int                      # pages added so far (root counts)
    df: dict[str, int]          # term -> number of corpus pages containing it
```

- `add_page(text)`: `n += 1`; for each token in `_crawl_tokens(text)`:
  `df[t] = df.get(t, 0) + 1`. (Set semantics per page — `df` counts pages,
  not occurrences.)
- `idf(t)`:
  - `n < _CRAWL_IDF_MIN_CORPUS` (3) → `1.0` for every term (Δ3 gating).
  - else `max(0.0, ln(1 + (n − d + 0.5) / (d + 0.5)))` with `d = df.get(t, 0)`.
    A term absent from the corpus (`d = 0`) gets the maximum idf
    `ln(1 + 2n + 1)` — rare/unobserved query terms are hard to match,
    standard BM25 behaviour.
- Feeding rule (Δ-wiring): `corpus.add_page(full_md)` runs **after** each
  successful fetch and **before** that page's `expand()`:
  - root: after the root record is appended, before `expand(root_page, …)`;
  - other pages: in `fetch_record`, after `pages.append(record)`, before
    `expand(page, url, depth)`.
- Consequence: candidates on the root are scored with `n = 1`, on the first
  child with `n = 2` → both uniform (exact v0.4.6); from the second child
  onward the site vocabulary discriminates.

### 2.2 `_crawl_score` v2 — additive signature (Δ1)

```python
@classmethod
def _crawl_score(cls, url, anchor, depth, query_terms, page_terms,
                 corpus=None, label_extra=frozenset(), base_terms=None):
```

- `label = tokens(anchor) ∪ tokens(urlpath) ∪ label_extra`
  (`label_extra` = capped anchor-context tokens, §2.4).
- **Mode table** (single decision point in the body):

| Condition | Weights | Path prior | Result |
|-----------|---------|-----------|--------|
| `corpus is None` | uniform, no expansion distinction | none | **exactly the v0.4.6 formula** (legacy/CI-regression path) |
| `corpus.n < 3` | base query terms weight `1.0`, expanded `0.5`; ctx weight `1.0` | none | v0.4.6 math with expansion only |
| `corpus.n ≥ 3` | base `idf(t)`, expanded `0.5·idf(t)`; ctx `idf(t)` | applied (§2.5) | full A |

- `base_terms=None` → every query term is a base term (full weight); used by
  the legacy path and by crawls where expansion added nothing.

### 2.3 Weighted coverage (the A formula, concretised)

```
w_q(t)  = idf(t)                    if t ∈ base_query_terms
        = 0.5 · idf(t)              otherwise (thesaurus-expanded)
query_cov = Σ_{t ∈ label ∩ Q} w_q(t) / Σ_{t ∈ Q} w_q(t)        (0 if denominator 0)
ctx_cov   = Σ_{t ∈ label ∩ P} idf(t) / Σ_{t ∈ label} idf(t)    (0 if denominator 0)
score     = 0.7 · query_cov + 0.3 · ctx_cov   [· path_prior if n ≥ 3]
```

- Note the **role swap** vs the legacy line: legacy ctx was
  `|label ∩ P| / |label|` — the weighted form keeps the same orientation
  (denominator over `label`). The query term set `Q` is the **expanded**
  set; `P` is still the containing page's `_crawl_topic_words` (full
  delivered markdown + title), computed in `expand()` as today.
- Weights are computed **at scoring time** from the current corpus, so IDF
  sharpens as the crawl proceeds — that is the intended "improves as it
  goes" behaviour.

### 2.4 Anchor context (A, enabler for G1)

Per candidate, in `expand()`:

1. `anchor = cand["title"]`; skip if `anchor == "(untitled)"` or empty.
2. `pos = md.lower().find(anchor.lower())` on the page's full delivered
   markdown (`page["markdown"]`); `pos < 0` → no context (link labels are
   often absent from the rendered markdown; fail-open).
3. Window = `md[max(0, pos−50) : pos+len(anchor)+50]`
   (`_CRAWL_CONTEXT_CHARS = 50`).
4. `context_tokens = _crawl_tokens(window) − (tokens(anchor) ∪ tokens(path))`
   — the label keeps its identity; context only *adds*.
5. Cap: if `len(context_tokens) > 8` (`_CRAWL_CONTEXT_TOKEN_CAP`), keep the
   8 highest-frequency in the window, ties broken alphabetically
   (`sorted` on `(-count, term)` — deterministic).
6. Result passed as `label_extra`; cached per-page per-`anchor` string
   (repeated anchors are common) in a plain dict local to `expand()`.

### 2.5 Path priors (Δ3-gated)

```python
_CRAWL_PATH_PRIOR_GROUPS = (
    (("/docs/", "/guide/", "/guides/", "/blog/", "/api/",
      "/changelog/", "/reference/"), 1.15),
    (("/pricing", "/careers", "/contact", "/about"), 0.85),
)
```

- Match on lowercased `urlparse(url).path` with `startswith`; **first group
  with any match wins** (a URL is never both boosted and damped); no match
  → `1.0`.
- Applied as a final multiplier on the lexical score, **only in the
  `n ≥ 3` regime** (Δ3). Table-driven, unit-tested as a table.

### 2.6 Thesaurus (B)

#### 2.6.1 File

`stitch_web_researcher/thesaurus.json`:

```json
{"version": 1, "clusters": [["deep", "learning", "neural", ...], ...]}
```

- ~35 clusters / ~200 terms (decision D3), domains: ML/AI, web dev,
  data engineering, security, infra/cloud, search/retrieval, databases,
  observability, testing.
- **Terms are bare lowercase tokens** — whatever `_crawl_tokens` can
  produce. Hyphenated phrases are *not* terms (`"front-end"` → the tokens
  `front` and `end`, and `end` is excluded anyway, §2.6.3). A cluster is a
  list of such tokens; cross-cluster membership is allowed.
- Packaged for free: `python-packages = ["stitch_web_researcher"]`
  ships the whole directory (verified §1).

#### 2.6.2 Loader

```python
@functools.lru_cache(maxsize=1)
def _load_thesaurus() -> tuple:
    """(version, (frozenset, ...)) or (0, ()) — fail-open."""
```

- Path: `Path(__file__).resolve().parent / "thesaurus.json"`.
- Any exception (missing file, bad JSON, wrong shape) → `logger.warning`
  once and `(0, ())`; expansion is silently disabled, crawl otherwise
  unaffected. Tests use `_load_thesaurus.cache_clear()` around monkeypatches.

#### 2.6.3 Curation constraint (Δ4 — the precision rule)

**Excluded from clusters** (ultra-generic tokens that would fire on
fixture or incidental text and pollute half-weighted scores):

`platform, note, notes, company, guide, guides, page, pages, hub, data,
cloud, api, apis, search, test, testing, index, end, front, about,
contact, contact, us, team, here, stuff, details`

Plus the general rule: any token that appears in ≥ 30 % of a random
English webpage is ineligible. Rationale: expansion is half-weighted
exactly so that wrong expansions cost little — the cost is a *larger query
denominator*, i.e. diluted scores. Generic terms make that cost systematic.

### 2.7 `_crawl_expand_query` (B, deterministic)

```python
@classmethod
def _crawl_expand_query(cls, base_terms, clusters=None):
    """Returns (expanded_set, added_count)."""
```

- `clusters=None` → `_load_thesaurus()`; empty → return base unchanged.
- Deterministic iteration: base terms in `sorted()` order; for each, clusters
  in **file order**; members in **cluster order**; skip terms already seen
  (base included).
- Cap: added terms ≤ `len(base_terms)` → total `|expanded| ≤ 2·|base|`
  (plan §B). `base_terms` empty → no-op.
- Weights are **not** stored here — scoring re-derives
  `0.5·idf` vs `1.0·idf` from the `base_terms` set (§2.3). This classmethod
  only produces the deterministic term set and the count.

### 2.8 `crawl()` wiring (order of operations)

1. Params clamped (unchanged).
2. `corpus = _CrawlCorpus()` created before the root fetch.
3. Root fetched (unchanged). Root record appended (unchanged, `score 1.0`).
4. `corpus.add_page(root_md)` — **before** query resolution and before
   `expand(root_page, …)`.
5. Base query terms resolved exactly as today (explicit → `_crawl_tokens`;
   else derived `_crawl_topic_words(root_md + " " + root_title)`).
6. `query_terms, added = self._crawl_expand_query(base_terms)`;
   `query_echo = echo + f" +{added}"` when `added > 0`
   (explicit: `"deep learning +2"`; derived: `"derived from root page +2"`).
7. `expand()` gains: per-page context cache; per-candidate `label_extra`;
   call `_crawl_score(url, anchor, depth+1, query_terms, page_terms,
   corpus=corpus, label_extra=…, base_terms=base_terms)`.
8. `fetch_record()`: on success, `corpus.add_page(md)` before
   `expand(page, url, depth)`.
9. Everything else (frontier, decay, dedupe, filters, M11) untouched.

### 2.9 Pin-risk audit (per existing test, v0.4.7)

Thesaurus assumed curated per §2.6.3. `k` = terms expanded per crawl
(= 2 for base size 2, = 4 for base size 4, subject to cluster content).

| Test | Verdict | Notes |
|------|---------|-------|
| `test_root_fetch_failure_kills_crawl`, `…error_dict`, `…invalid_root_urls` | **unchanged** | Fail before scoring. |
| `test_root_only_when_max_depth_zero` | **unchanged** | `expand` returns at `depth ≥ max_depth`; query `"deep learning"`-style text is only in a max_depth=0 fixture — no scoring runs. |
| `test_relevance_focuses_the_crawl` (`query="deep learning"`) | **AMENDED (Δ9)** | Pins updated for the expanded denominator (see below). |
| `test_flat_scores_degrade_to_plain_bfs` (`query="notes"`) | **unchanged** | `notes` excluded from clusters (§2.6.3) → no expansion; all scores `1.0` (context adds only `{notes}`) → BFS order. |
| `test_depth_decay_allows_deep_overtake` | **AMENDED (Δ5)** | WEAK anchor `platform` → `company`; stop `frontier exhausted` (see below). |
| `test_explicit_query_beats_derived` (`query="needle deep"`) | **unchanged** (verified) | `k=2` via the ML cluster (`deep`). N: `0.7·2/3 = 0.467`, ctx 0 → `0.467`. X: anchor `platform` context = whole short root md `{platform, stuff, here}` → query_cov 0, ctx `1.0` → `0.3`. `0.467 > 0.3` → `[ROOT, N]`, X not fetched ✓. |
| `test_query_echo_derived` | **unchanged** | Root `# Hub\n\nplatform.\n` → derived `{hub, platform}` → both excluded tokens → `added = 0` → echo stays `"derived from root page"`. This test is the *guard* for the §2.6.3 rule. |
| `test_min_score_zero_follows_unscored`, `…clamped`, `…bad_value` | **unchanged** | No expansion (derived `{platform, hub}`); score-0 candidate enqueued at `min_score=0` as before. |
| Budget/cap tests (`max_pages`, hard caps, bad params, skim, small budget) | **unchanged** | Derived queries from `"platform …"` roots only → no expansion; ordering unaffected. |
| Host/filter tests (`same_host`, boilerplate, assets, documents, dedupe) | **unchanged** | Filters run before scoring or on the floor; scores only matter where asserted (none do, except ordering in `same_host` tests where all candidates tie). |
| Cache tests, `execute_tool_dispatch`, `registry_shape` | **unchanged** | No payload shape change in step 1; registry unchanged. |

#### The fixture amendments (Δ5 + Δ9, exact)

`test_depth_decay_allows_deep_overtake` breaks as written: on the short
root page, the weak candidate's anchor `"platform"` has a ±50 window that
**is the whole page** — so anchor context gives it full query coverage and
it overtakes the depth-2 page the test exists to prove. Fix (fixture
only, test intent preserved): change the WEAK anchor `"platform"` →
`"company"` (not present in the page body → no context → score 0 →
skipped at the min-score floor). The stop reason changes to
`"frontier exhausted"` and `count == 3` (ROOT, MID, DEEP; WEAK never
fetched) — the depth-decay overtake the test exists to prove, now with a
genuinely weak candidate.

`test_relevance_focuses_the_crawl` breaks as written only in its score
pins: with `k = 2` the query denominator gains the half-weighted
expansions, so A scores `0.7·(1/3) = 0.467` (was 0.63) and A1
`0.302` (was 0.417). Pins widened to `0.4 ≤ A < 0.6` and
`0.25 ≤ A1 < 0.5`; an echo assertion `query.endswith(" +2")` was added.
The test's core assertions (A before A1, B/A2 skipped, PDF collected)
are unchanged.

**Verification note:** the audit originally written in this section was
computed from a stale read of the test file. The on-disk fixtures
(`example.com` URLs, module-level helpers, derived query in the
depth-decay test) were re-read before implementation, and all 29 existing
crawl tests plus 16 new semantic tests pass after the two amendments.

### 2.10 New tests — `tests/test_crawl_semantic.py` (step 1, ~12)

As implemented: 16 tests (the list below, with the path-prior case split
into table + corpus-gating, the fail-open test, an exact legacy-path
regression, and a derived-echo guard added).

A (5):
1. Rare term beats common term: 4-page corpus (term R in 1 page, C in all)
   → `idf(R) > idf(C)`; `_crawl_score` with `corpus`: R-labelled candidate
   outscores C-labelled candidate at equal structural overlap.
2. N<3 degeneracy: `corpus` with `n=1` and `n=2` → score equals the legacy
   formula recomputed by hand (uniform weights, no prior) — exact float
   pin (Δ3 regression).
3. Anchor context: two candidates, identical anchors; one page's markdown
   surrounds the anchor with query words → strictly higher score. Plus the
   cap: a window with > 8 distinct extra tokens contributes exactly 8
   (highest-TF, alpha tie-break).
4. Path priors table: `/docs/`, `/guides/`, `/blog/`, `/api/`,
   `/changelog/`, `/reference/` → ×1.15; `/pricing`, `/careers`,
   `/contact`, `/about` → ×0.85; `/x/y` → ×1.0 — and prior is **absent**
   at `n < 3` (unit on the gated path).
5. IDF sharpens across a crawl: same candidate re-scored after
   `corpus.add_page(common_page)` → its common-term contribution drops
   (asserted on the score delta).

B (5):
6. Paraphrase: crawl fixture, `query="neural nets"` (base `{neural, nets}`),
   pages linked as `"deep learning guide"` vs `"about us"` → the
   deep-learning page is fetched, the about page `below min score` (cluster
   supplies `deep`, `learning` at half weight).
7. Cap: 2-term query against a 10-member cluster → `|expanded| ≤ 4`,
   `added_count == 2`, deterministic order (cluster file order).
8. Half-weight ordering: candidate matching only base terms outscores
   candidate matching only expanded terms (n ≥ 3 regime, equal idfs).
9. Thesaurus file pin: loads with `version == 1`, ≥ 30 clusters, all terms
   are bare lowercase alnum tokens, **no excluded generic token** (§2.6.3
   list asserted absent), clusters non-empty.
10. Derived-query expansion: root md `"neural nets overview"` (no explicit
    query) → echo `"derived from root page +N"` with `N > 0`, and a
    `"deep learning guide"` link is enqueued/fetched.

Regression: full `tests/test_crawl.py` green (with the §2.9 fixture
amendment), `tests/test_mcp_server.py` + `tests/test_p8_tool_registry.py`
unchanged (no registry change in step 1).

### 2.11 Docs, version, commit (step 1)

- `README.md`: new `### Semantic Crawl (v0.4.8)` section under the Focused
  Crawl section: IDF over the live corpus (with the N<3 degenerate rule),
  anchor context, path prior table, thesaurus expansion (half-weight,
  cap), and the note that behaviour is byte-identical to v0.4.6 until the
  crawl has read 3 pages.
- `SPEC_AUDIT.md`: row `BM25/IDF frontier scoring + offline thesaurus — v0.4.8`.
- `CHANGELOG.md`: `[0.4.8]` section (unreleased → released in the step-3
  commit; step 1 opens it).
- Version: `0.4.7 → 0.4.8` in `pyproject.toml`, `Cargo.toml`, `Cargo.lock`
  (pure-Python change: **no maturin rebuild in dev**).
- `SEMANTIC_CRAWL_PLAN.md` status line → `Approved 2026-08-29;
  implementation tracked in docs/semantic_crawl_implementation_plan.md`.
- README badge: **not** updated in step 1 (final count lands in step 3).
- Commit: `Feature: BM25/IDF frontier scoring with offline thesaurus
  expansion (v0.4.8)`.

## 3. Step 2 — features C + D (richness stats, ranked documents), v0.4.8

Commit: `Feature: crawl richness stats and ranked document list`.

### 3.1 C — payload fields (per fetched page, including root)

| Field | Definition | Computed from |
|-------|-----------|---------------|
| `content_chars` | `len(full delivered markdown)` pre-skim | `md` in `fetch_record` / root |
| `term_hits` | count of (expanded) query-term **occurrences** in the full body, counted on the token stream (`re.findall` over full md, count hits of the expanded set) | full md + `query_terms` |
| `excerpt` | opt-in only, §3.2 | full md + `query_terms` |

- Root record gets the same fields (its `score` stays `1.0`).
- `term_hits` counts occurrences (not unique terms) — a page that repeats
  the topic 40 times signals substance.
- Documents (D) carry only `score` — `term_hits` needs the body and
  documents are never fetched (contract).
- No `max_pages`/budget interaction: fields are ~25 chars/page;
  `_shrink_crawl` tail-drop is the unchanged overflow path (M11 intact).

### 3.2 C — excerpt algorithm (opt-in)

- New param `excerpts: bool = False` (ToolParam default False; payload
  shape at the default is **identical** to v0.4.7).
- Window: 300 chars, step 100 chars, over the full md (no window → no
  excerpt). Density = `term_hits` within the window (token stream of the
  window). Max density wins; **ties → earliest window**; zero density →
  `excerpt` omitted for that page (an empty excerpt is noise).
- Leading `…` when the window does not start at 0; trailing `…` when it
  does not end at the tail.
- Cost: ~320 chars/page — documented as "raises the payload; pair with a
  lower `max_pages`" (M11 still the hard floor).

### 3.3 D — ranked documents

- Document candidates (routed before the page filters, as today) now also
  run through `_crawl_score` at the point of first sighting, with
  `anchor = cand["title"]`, the **current** corpus (whatever has been
  fetched by then), `depth = 0` (no decay — they are reference material,
  not crawl targets).
- Record: `{url, anchor, score}` (rounded to 3 like pages).
- `documents` returns **sorted by score desc** (ties: first-sighting
  order — deterministic).
- Floor: entries with `score < min_score` are dropped from `documents` but
  counted in `documents_below_score` **and** recorded in `skipped` with
  reason `"below min score"` (same reason string as pages — one bucket,
  traceable). `documents_total` keeps its meaning (unique document URLs
  seen).
- Never enqueued, never fetched (contract, asserted in tests).

### 3.4 New tests (~10)

- `content_chars` / `term_hits` match fixture ground truth (constructed md
  with known term counts; root and child records both checked).
- Excerpt = densest window: fixture with a mid-page keyword block →
  excerpt starts inside the block; `…` markers correct at head/tail/both;
  tie → earliest; zero density → field absent.
- `excerpts=False` default: payload JSON equals the v0.4.7 field set
  (key-set assertion per page record).
- Budget fit at `max_pages=15` with stats on: `_shrink_crawl` still yields
  valid JSON within the budget (M11 regression).
- Documents sorted desc; equal scores keep first-sighting order;
  below-floor → `documents_below_score` count + `skipped` reason;
  `documents_total` counts all seen; **documents never fetched**
  (`tb._fetch_html.state["calls"]` contains no document URL).

### 3.5 Docs/commit (step 2)

README: param table gains `excerpts`; `documents` entry shape documented.
SPEC_AUDIT row. CHANGELOG bullets. Badge still deferred.

## 4. Step 3 — features E1 + E2 + E3 (discovery seeds), v0.4.8

Commit: `Feature: discovery seeds for the crawl frontier (search prior,
seed_urls)`.

### 4.1 E1 — `search_prior: bool = False` (opt-in, Δ7)

- One call: `self.search_web(f"site:{self._crawl_host_key(root)} {focus}",
  max_results=5)` where `focus` = the caller's query string when given,
  else the root title (or the derived-echo text when that is all there is).
- Parse JSON: take `results` (list of `{title, url, snippet}`); on `error`
  key, JSON decode failure, or missing key → **fail-open**: `logger.warning`,
  crawl proceeds link-graph-only. Search cost is bounded: Tier 2.8 in-memory
  cache means repeat crawls of the same root/query are free.
- For each of the top-5 results (rank `i`, 0-based): `normalize_url` →
  dedupe against `visited` → **document type → routed to `documents`**
  (same scorer path as D) → same-host filter (respecting `same_host`) →
  boilerplate/asset filters → push at **depth 1** with
  `effective = (score + 0.1/(i+1)) · 0.7^1`, **exempt from the
  `min_score` floor** (the engine already ranked them; the floor still
  guards everything else).
- Seeds are added to the frontier **after** the root's `expand()`, in rank
  order (discovery-order tie-breaks stay deterministic).
- Payload: `search_prior: true/false` echo + `search_results: n` (int,
  how many were eligible) when the param is on.

### 4.2 E2 — `seed_urls: list[str] = []` (Δ6 confirmed supported)

- New ToolParam `list[str]` (precedent: `batch_inspect_pages` `urls`,
  agent_tools.py:649).
- Each seed: `normalize_url` (base = root) → SSRF `_validate_url` (seeds
  are caller-supplied URLs — S1 applies in full) → dedupe against
  `visited` (a seed equal to the root is a no-op) → document type →
  `documents` → same-host/boilerplate/asset filters → scored like any
  candidate (current corpus, depth 0 for scoring, pushed at **depth 0** so
  their children are depth 1 within `max_depth`).
- Seeds **respect `min_score`** (plan decision: deliberate-ness is
  expressed via `min_score=0`); a below-floor seed is `skipped` with
  reason `"seed below min score"` — never silent.
- Seeds are fetched through the normal pipeline (cache/robots/in-flight/
  rate-limit unchanged); a seed fetch failure is a normal `errors` entry,
  non-fatal, does not consume `max_pages` (failed fetches never do).

### 4.3 E3 — cross-modal loop (docs + one round-trip test)

- README pattern (no mechanism): crawl → `extract_document(top PDF)` → its
  `links` (v0.4.5 text link detection) → next crawl with `seed_urls` (or a
  fresh crawl rooted at the document's host).
- Integration-style test: crawl fixture with one PDF → assert PDF is
  ranked in `documents` → feed a hand-made "extracted" link list (the PDF's
  internal URLs) into `seed_urls` on a second crawl of the same toolbox
  → assert those URLs are fetched at depth 0 and their children at depth 1.
  (The PDF itself is not fetched by the crawl — the loop is the agent's.)

### 4.4 New tests (~10)

- E1: prior URLs enter the frontier and are fetched in rank order when
  their scores are flat; rank bonus breaks ties (`+0.1/1 > +0.1/2`);
  floor-exempt (score 0 seed with `min_score=0.05` is still fetched);
  search failure (`search_web` returns `{"error": ...}`) → crawl proceeds,
  no `errors` entry from the crawl's perspective, link-graph result intact;
  repeat crawl reuses the Tier 2.8 search cache (provider call count 1).
- E2: seed dedupe (root URL as seed → no double fetch); below-floor seed
  → `skipped` reason `"seed below min score"`; seed children at depth 1
  within `max_depth`; `same_host=True` + external seed → skipped
  `"external host"`; seed fetch failure → `errors`, budget untouched.
- E3: the round-trip test above.

### 4.5 Docs/commit/badge (step 3 — closes v0.4.8)

- README: `search_prior` + `seed_urls` params, the E3 loop pattern, and
  the crawl param table completed.
- SPEC_AUDIT rows (E1, E2). CHANGELOG closed for `[0.4.8]`.
- **Badge updated to the final test count** (expect ~890–930; exact number
  from the gate run), `tests-only`-free.
- Commit as stated; then push (no tag, no release — standing instruction).

## 5. Step 4 — feature F (`[embed]` extra), v0.4.9

Carried over from the original plan §4.F unchanged, with the guard-pattern
checklist made explicit:

1. Extra: `[project.optional-dependencies] embed = ["onnxruntime>=1.17"]`;
   model ~80–90 MB MiniLM-class, resolved via `STITCH_EMBED_MODEL` (path)
   else downloaded-once into the cache dir with a pinned SHA-256; lazy
   load on first scored crawl.
2. `_embed_backend()` mirrors `_guard_backend()`: real encoder when
   importable + model available; else **fail-open to lexical-only**
   (A–E behaviour — exact, not approximate).
3. Deterministic **stub embedder** for tests: hashed bag-of-words into a
   fixed 64-dim vector; plumbing-only, labelled non-semantic (same honesty
   rule as the guard stub — no semantic claims in output).
4. Embedding targets: effective query (1×/crawl), each fetched page's
   first ~1500 chars (1×/page, in-process side cache keyed
   `(url, content_hash)`; the on-disk 4-tuple is untouched), candidate
   labels at enqueue.
5. Blend (single site, in `crawl`'s frontier push — not inside
   `_crawl_score`): `final = 0.5·lexical + 0.5·semantic` where
   `semantic = 0.7·cos(Q, label) + 0.3·cos(Q, source_page)`;
   stub/absent → `final = lexical`.
6. Knobs: `ToolboxConfig.embed_mode: "off"|"auto"` (default `"auto"`),
   env `STITCH_EMBED_ENABLED` / `STITCH_EMBED_MODEL` / `STITCH_EMBED_DIM`;
   `get_stats()["embed"]` mirrors `get_stats()["guard"]` (backend, model
   hash, count, p50 latency).
7. Tests: stub determinism; fail-open equality (`absent → A–E score
   exactly`); blend math; side-cache hit on re-crawl; stats shape; model
   hash reporting.
8. v0.4.9 commit: `Feature: optional local embeddings for crawl relevance
   (v0.4.9)`; README `[embed]` extra row; badge final.

## 6. Decisions log (original plan §11 — resolved 2026-08-29)

| # | Decision | Resolution |
|---|----------|-----------|
| D1 | `search_prior` default | **`False`** (opt-in). README recommends it for topical crawls. Keeps the default crawl cheap and pure; one cached search call is the only cost when enabled. |
| D2 | Excerpt vs head skim | **Coexist**: head skim (`markdown`, 300 chars) always; `excerpt` only when `excerpts=True`. Skim carries lead/H1 context, excerpt carries substance. |
| D3 | Thesaurus size | **~200 terms now** (≈35 clusters), grown per-release. Precision rule §2.6.3 bounds the damage of staleness (half-weight + cap). |
| D4 | Embedding model | **MiniLM-class, English, ~80 MB first.** Multilingual is a later model swap behind `STITCH_EMBED_MODEL` — the hash-pinned design makes the swap a config change. (Step 4 only.) |
| D5 | Blend weights | **Fixed constants** (`0.5/0.5`, `0.7/0.3`). No knob — deterministic first, tunable later if real-crawl data shows a need. (Step 4 only.) |

## 7. Gates (every step)

- Full pytest (local venv with guard: expect `859 passed, 2 skipped, 7
  deselected` baseline growth; clean-venv/CI parity `860 passed, 1
  skipped` — CI has no guard extra).
- `ruff@0.16.4 check stitch_web_researcher/` clean (package scope only;
  the 8 pre-existing test-dir findings stay out of scope).
- No Rust changes → no clippy/maturin in dev (release packaging rebuilds at
  ship time).
- `git status` clean before push; **no tags, no releases** (standing
  instruction).
- Commit discipline: one concern per commit, single-quoted messages, no
  backticks/apostrophes.

## 8. Open items (to confirm *during* implementation, not blocking)

1. `search_web` `focus` text when the root has no title and no query:
   use the derived-echo string minus the `+N` suffix (keep the `site:`
   query non-empty — a bare `site:host` query is still valid but weak;
   decide from observed provider behaviour in the E1 test stubs).
2. E1 rank bonus magnitude (`0.1/(i+1)`): confirm it cannot lift a
   depth-1 seed above a depth-0 seed's decayed score in a pathological
   flat-score crawl (it can only reorder within depth 1 — verify with one
   unit assertion).
3. Thesaurus cluster content: final cluster list is an implementation
   artefact of §2.6 rules; the file pin test (2.10/9) guards its shape,
   not its exact membership.
4. Badge exact number after step 3 (7.5 above).
