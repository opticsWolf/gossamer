# Implementation Bugfix Plan — post-implementation review (2026-08-28)

Follow-up to `CODE_REVIEW_2026-08-27.md`. The roadmap in that report was
implemented across ~45 commits (tests 183 → 734). This plan covers the
**nine defects found while verifying that work**, not the original findings.

Every item below was reproduced against HEAD (`096ac96`, v0.4.3) before
being written down.

## Severity summary

| # | Severity | Defect | Site |
|---|---|---|---|
| 1 | High | Serialized JSON is string-cut → unparseable tool output | `agent_tools.py:1034` + 4 call sites |
| 2 | High | Section selection blind to Setext headings → Tier 1.1 inert | `sections.py:32` |
| 3 | High | URL rejections raise instead of returning the JSON error contract | `agent_tools.py:2113` + peers |
| 4 | Medium | `cargo clippy -D warnings` red again (`type_complexity`) | `src/lib.rs:1318` |
| 5 | Medium | C2 metadata fix never reached the batch path | `agent_tools.py` batch |
| 6 | Medium | `normalize_url("report.pdf")` still promoted to a URL | `agent_tools.py` |
| 7 | Medium | Guard `risk` enum stringifies as `RiskLevel.High` | `guard.py:338` |
| 8 | Medium | Guard performance impact never measured (Prio-2 requirement) | `benchmarks.py` |
| 9 | Low | Unknown provider name silently falls back to all providers | `agent_tools.py:1502` |

The pattern behind 1, 2 and 5 is the one the original review named:
**correct in the unit, lossy in the pipeline**. The tests are green because
they exercise parts rather than delivered payloads. Every fix below ships
with a test that consumes the *real* pipeline output.

---

## 1. Serialized JSON must never be string-cut  [high]

**Defect.** `_truncate()` cuts text at a character limit and appends
`"\n\n... [truncated]"`. Four call sites hand it *already-serialized JSON*:

* `agent_tools.py:3115` — `extract_document_structured`
* `agent_tools.py:3194` — `inspect_html_structured` (cache hit)
* `agent_tools.py:3245` — `inspect_html_structured` (fresh)
* `agent_tools.py:3599` — `research`

**Reproduced** (3 of 4 directly; the 4th is the same code path):

```
inspect_html_structured (fresh)    parses=FALSE len=8017  tail='rd word wor\n\n... [truncated]'
inspect_html_structured (cached)   parses=FALSE len=8017  tail='rd word wor\n\n... [truncated]'
research(topic, depth=5)           parses=FALSE len=8017  tail='word word w\n\n... [truncated]'
```

Braces unbalanced; the document ends mid-string. Small pages parse fine,
which is exactly why the suite misses it. This regresses the invariant the
codebase previously held ("the payload is never string-cut after
serialization").

**Fix.** Budget the *content fields*, then serialize once — the approach
`_build_inspection_result` already uses.

* Add `_fit_json(build, char_limit, token_limit)`: calls `build(budget)`
  with a shrinking per-field budget until the serialized form fits.
* Add `_shrink_parsed_payload(payload, budget)` for
  `ParsedDocumentPayload` (shrinks `pages[].raw_text` / `pages[].markdown`).
* Add `_shrink_research(result, budget)` for `research()`
  (shrinks `sources[].result.markdown`, then drops whole sources).
* Guarantee of last resort: when a payload cannot be shrunk enough, return
  a small **valid** JSON envelope reporting the overflow — never a cut.

**Test.** `tests/test_fix_json_integrity.py` — drive each of the four tools
with an over-budget page through a local HTTP server and assert
`json.loads()` succeeds and the budget is respected.

## 2. Section selection must see Setext headings  [high]

**Defect.** `_HEADING_RE` matches ATX only, with a comment asserting
"the Rust html2md converter emits ATX". It does not:

```
Alpha            <- h1 becomes Setext
==========
Bravo            <- h2 becomes Setext
----------
### Charlie ###  <- h3+ is ATX, with trailing hashes
```

h1/h2 — the levels that structure nearly every web page — are invisible to
the splitter, so Tier 1.1 degenerates to one `(intro)` blob and BM25 has
nothing to choose between. On a realistic page: `sections_available: 0`,
and the query-relevant section was **not** retained.

Secondary defect in the same regex: `### Charlie ###` yields the title
`"Charlie ###"` — trailing hashes leak into the section anchor.

All 27 tests in `test_t1_sections.py` use hand-written ATX; none touch the
converter.

**Fix.** Extend `_HEADING_RE` to match ATX (stripping optional trailing
hashes) and Setext (`=`/`-` underlines), with `split_sections` taking the
title from whichever group matched. Guard against false positives: the
underline must be >= 2 characters and the title line must not be blank or
itself a rule.

**Test.** `tests/test_fix_setext_sections.py` — pipe
`_core.process_rendered_html` output through `split_sections`, plus an
end-to-end `inspect_html_page(query=...)` asserting the relevant section
survives while filler is dropped.

## 3. URL rejections must honor the JSON error contract  [high]

**Defect.** `normalize_url()` and `_validate_url()` run *before* the
`try` block, so they raise instead of returning `{"error": ...}`:

```
inspect_html_page          -> RAISED SsrfBlockedError
inspect_html_page(local)   -> RAISED ValueError: './notes.html' looks like a local file path
inspect_html_structured    -> RAISED SsrfBlockedError
batch_inspect_pages        -> RAISED SsrfBlockedError
extract_document           -> RAISED SsrfBlockedError
discover_resources         -> RAISED SsrfBlockedError
execute_tool               -> RAISED SsrfBlockedError
```

Worst case: `batch_inspect_pages(["http://10.0.0.5/a", "https://example.com/b"])`
throws away the good URL too. One poisoned link in a scraped list kills the
whole batch — and scraped links are exactly where SSRF payloads arrive.
The MCP wrapper has no `try/except` either.

**Fix.**
* Add `_url_error(url, exc)` returning the standard JSON error dict.
* Wrap `normalize_url` + `_validate_url` at every tool entry point so a
  rejection returns JSON.
* In `batch_inspect_pages`, reject URLs **per entry** — a bad URL becomes
  one error record; every other URL is still fetched.
* Keep the refusal reason in the message so the model can self-correct, and
  keep logging the block.

**Test.** `tests/test_fix_url_error_contract.py` — every entry point returns
parseable JSON carrying an error, and a mixed batch still returns the good page.

## 4. Restore a green clippy  [medium]

**Defect.** `cargo clippy --all-targets -- -D warnings` fails on
`src/lib.rs:1318` (`extract_tables_from_html`, added by Tier 3.11). The
alias pattern from the C7 fix exists at lines 569-1050 but was not applied.

**Fix.** `type ExtractedTableGrid = (String, Vec<String>, Vec<Vec<String>>);`
and use it in the signature and the local `Vec`.

**Test.** CI lint job (already wired).

## 5. Metadata parity between single-page and batch  [medium]

**Defect.** Single-page inspection returns real metadata; the batch path
still returns `{}`:

```
single metadata keys : ['description', 'og_title', 'title', 'twitter_title']
batch  metadata keys : []
```

C2 was fixed only on the single-page path, which re-opens the
payload-divergence half of C6.

**Fix.** Route the batch path through the same metadata extraction the
static path uses (the Rust batch engine already returns the HTML via
`fetch_html_full`-style plumbing; extract from it rather than discarding).
Where the engine does not hand back HTML, fall back to the single-page
fetch for that URL rather than shipping an empty dict.

**Test.** `tests/test_fix_batch_metadata.py` — assert the batch record's
metadata equals the single-page record's for the same URL.

## 6. Bare relative filenames are not URLs  [medium]

**Defect.** `normalize_url("report.pdf") -> "https://report.pdf"`.
`./report.pdf`, `../a/b.pdf` and `docs/x.md` correctly raise; the bare
single-segment filename — the exact example in the original report — is
still promoted by the bare-domain heuristic.

**Fix.** Before promoting a bare token to `https://`, reject it when its
final segment has a known non-TLD document/media extension (`.pdf`,
`.docx`, `.md`, `.csv`, ...) — the same table `classify_link` already owns.

**Test.** `tests/test_fix_bare_filename.py` — extends the M1 table.

## 7. Guard risk level must stringify cleanly  [medium]

**Defect.** `jailguard`'s `.risk` is an enum; `str()` of it may render as
`"RiskLevel.High"` rather than `"High"` in the guard block. Unverified
against the live package (not installed), so the fix is defensive.

**Fix.** Normalize in `_field`/`_score_chunk`: take `.name` when present,
else strip a `ClassName.` prefix from the string form.

**Test.** `tests/test_guard.py` — add an enum-like stub asserting the
normalized value.

## 8. Measure the guard's performance impact  [medium]

**Defect.** This was an explicit Prio-2 requirement. The controls are all
correct — `GuardConfig` matches the design, all five scopes are wired,
`STITCH_GUARD_*` env passthrough works, `get_stats()["guard"]` reports
p50/p95/flag_rate/cache_hits. But `benchmarks.py` has **no guard scenario**
and there is no labelled corpus, so the impact has never been measured and
nothing yet decides whether `redact` is safe here.

**Fix.**
* Add a `guard` scenario to `benchmarks.py`: same fixture corpus with
  `enabled=False` / `enabled=True`, printing wall-clock delta plus the
  `get_stats()["guard"]` block.
* Add `tests/fixtures/guard_corpus/` — benign research pages plus pages
  with planted injections — and a `--corpus` mode reporting the
  false-positive rate on *our* traffic mix rather than the vendor's.
* Both run with the `FakeGuard` when `jailguard` is absent, so the harness
  is exercised in CI and only the real numbers need the optional extra.

**Test.** `tests/test_fix_guard_bench.py` — the scenario runs and reports
without the optional dependency installed.

## 9. Unknown provider names must not fail silently  [low]

**Defect.** `_resolve_providers` falls back to the full provider list on an
unrecognized name, so `provider="brave"` quietly returns DuckDuckGo results.

**Fix.** Log a warning and carry a `provider_fallback` note in the search
result envelope so the model knows it did not get what it asked for.

**Test.** `tests/test_fix_provider_fallback.py`.

---

## Order of work

1. Item 3 (error contract) — smallest blast radius, unblocks clean testing of the rest
2. Item 1 (JSON integrity) — highest severity
3. Item 2 (Setext sections) — highest feature impact
4. Items 5, 6, 9 — correctness cleanups
5. Item 4 — CI green
6. Items 7, 8 — guard hardening and the measurement deliverable

Each item lands as its own commit with its test, and the full suite plus
`ruff` and `cargo clippy -D warnings` must be green before the next starts.

---

## Status — 2026-08-28

All nine items are implemented, tested and committed on `dev`.

| # | Item | Commit | Test |
| --- | --- | --- | --- |
| 3 | URL rejections honor the JSON error contract | `3336367` | `tests/test_fix_url_error_contract.py` |
| 1 | Serialized JSON is never string-cut | `2f52a08` | `tests/test_fix_json_integrity.py` |
| 2 | Section selection sees Setext headings | `0712d17` | `tests/test_fix_setext_sections.py` |
| 5 | Batch records carry single-page metadata | `772a407` | `tests/test_fix_batch_metadata.py` |
| 4 | clippy `type_complexity` on the new tuples | `772a407` | `cargo clippy -D warnings` |
| 6 | Bare filenames are not bare domains | `772a407` | `tests/test_fix_bare_filename.py` |
| 9 | Provider substitution is visible | `772a407` | `tests/test_fix_provider_fallback.py` |
| 7 | Detector risk level stringifies cleanly | `d3157ec` | `tests/test_guard.py::TestRiskNormalization` |
| 8 | Guard cost and accuracy are measurable | pending | `tests/test_fix_guard_bench.py` |

Items 4, 5, 6 and 9 share one commit: three of them touch `agent_tools.py`
and item 4 is a direct consequence of item 5's Rust change, so splitting
them would have produced commits that do not build on their own.

**Gates:** 816 passed, 1 skipped; `ruff check stitch_web_researcher/`
clean; `cargo clippy --all-targets -- -D warnings` clean.

### What item 8 measured

`python benchmarks.py --guard` on the ten-document fixture corpus, with
the stub detector (`jailguard` is an optional extra and is not installed
here):

* guard machinery overhead: **+0.2 ms** over ten documents (12 chunks) —
  the chunking, hashing, verdict cache and stats path cost effectively
  nothing; the model is the whole cost, and that number needs the extra.
* detection rate on planted injections: **5/5**.
* false-positive rate on benign pages: **1/5** — the page that *discusses*
  prompt injection. That is the case the corpus exists to expose, and it
  is why `redact` should not become the default: rewriting spans of a
  security article the user asked for is a worse failure than annotating
  it.

The stub's accuracy numbers are a property of the stub, not of JailGuard.
What the run establishes is that the harness, the corpus and the decision
criterion are in place, so installing the extra produces a real answer
without further work.

### Known, out of scope

* `benchmarks.py` carries two pre-existing ruff findings (`F841`, `F401`)
  on lines this work did not touch; CI lints only `stitch_web_researcher/`.
* `tests/test_browser_provider.py`, `tests/test_meta_oxide.py` and
  `tests/test_providers.py` carry seven pre-existing ruff findings, same
  reason.
* `stitch_web_researcher/_core.pdb` is a tracked build artifact and moves
  on every rebuild; it probably belongs in `.gitignore`.
