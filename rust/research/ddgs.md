# Research: ddgs

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/ddgs/json (version 9.16.1 at research time)
- Docs/source: https://github.com/deedy5/ddgs
- Rust crate: https://docs.rs/ddgs/latest/ddgs/ (version 0.1.0, MIT,
  https://github.com/YuanBLQ/ddgs) — corrected after user note that crates.io/lib.rs has `ddgs`
- Local evidence: `gossamer/search_providers.py:400-430`, `gossamer/config.py`

## What the original uses
`DuckDuckGoProvider` lazily imports `DDGS` and calls:

```python
with DDGS() as ddgs:
    results = list(ddgs.text(query, max_results=max_results))
```

The provider applies `GOSSAMER_DDGS_DELAY` / `GOSSAMER_DDGS_JITTER` politeness settings.
No images/news/videos/books APIs are used.

## Upstream facts
PyPI describes DDGS as "Dux Distributed Global Search", a metasearch library aggregating web
search services. Requires Python >=3.10; core dependencies are `click`, `primp`, and
`lxml`. The README explicitly notes there is no official DuckDuckGo API and that the library
scrapes HTML endpoints.

## Rust crate facts
The Rust `ddgs` crate exposes `Ddgs::new() -> Result<Ddgs>`, async
`Ddgs::text(query) -> Result<Vec<TextResult>>`, and `text_with_options(query, TextOptions)`.
It also exposes `NewsOptions`, `BooksOptions`, `TextBackend`, `SafeSearch`, `Region`, and
`TimeLimit`. It depends on `reqwest`, `tokio`, `serde`, `serde_json`, `regex`, `url`, and
`thiserror`. This is a separate implementation from the Python package, not the same project.

## Rust options
1. Use the Rust `ddgs` crate directly for text search and map `TextResult` into gossamer's
   normalised search result shape.
2. Re-implement only the one `DDGS().text()` call with `reqwest` + `scraper` if the crate's
   endpoint/result differences prove too large.
3. Keep a Python subprocess bridge only if neither option preserves ADR-005 output.

## Fit and gaps
**Fit: medium to high, evidence updated.** The Rust crate covers the exact surface gossamer uses,
but it is a different project/version from Python `ddgs` 9.16.1. Record result normalization,
delay/jitter config, and error text. Do not treat live DDGS results as deterministic replay
fixtures.
