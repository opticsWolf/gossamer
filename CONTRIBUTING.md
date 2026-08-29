# Contributing

Thanks for helping. This repo has a strict gate culture — please respect it,
it's the whole reason the test suite is trustworthy.

## Setup

```powershell
# Python 3.10+ (3.13 fine); a Rust toolchain is required (Rust core)
python -m venv .venv
.venv\Scripts\activate            # or .venv/bin/activate on unix
pip install -e ".[mcp,documents]"   # builds the Rust extension via maturin
```

Optional extras: `guard` (prompt-injection detector), `embed` (planned:
local embeddings). Dev-only deps in `requirements.txt`.

After touching `Cargo.toml` or `src/*.rs`:

```powershell
.venv\Scripts\python.exe -m maturin develop --release
```

## Gates (must be green before pushing)

```powershell
pytest                          # slow live tests are deselected by default
uv tool run ruff@0.16.4 check stitch_web_researcher/   # package dir only
cargo clippy --all-targets -- -D warnings
```

CI (`.github/workflows/ci.yml`) runs all three plus `pip-audit` +
`cargo audit` on ubuntu/windows × Python 3.10–3.13.

## Conventions

- **Test layout:** test files are named 1:1 after their origin
  (`test_s1_ssrf.py` = finding S1, `test_t3_13_research.py` = Tier 3.13,
  `test_crawl.py` = a feature). When you fix a finding or build a feature,
  the tests live in a file named after it.
- **Offline tests:** the test environment only resolves the `example.com`
  apex (S1 SSRF guard does real DNS). New tests that fetch must use
  `example.com` paths and the fake `_fetch_html` seam (4-tuple
  `(markdown, links, meta, method)`) — see `tests/test_crawl.py` for the
  pattern. Live probes go behind `@pytest.mark.slow`.
- **Commits:** `git commit -m 'single quoted message'` — no backticks, no
  apostrophes. One finding or one coherent feature per commit.
- **Version bumps:** feature/fix = +0.1 for a tier, +0.0.1 for a fix; no
  bump for docs/tests/git-hygiene only. Bump `pyproject.toml`,
  `Cargo.toml`, and `Cargo.lock` together. The README test-count badge must
  track the final count.
- **Plans:** working documents live at the repo root as
  `*_PLAN.md` / `CODE_REVIEW_*.md` (tracked history, not user docs).

## What we deliberately won't accept

- LLM calls inside the toolbox (the toolbox stays LLM-free; synthesis is
  the calling agent's job).
- A second network fetch path (everything goes through the page pipeline).
- Breaking the M11 invariant: tool payloads are always valid JSON within
  the output budget — shrink the content, never cut serialized JSON.
- New required dependencies for optional features (use extras + fail-open,
  the `[guard]` pattern).
