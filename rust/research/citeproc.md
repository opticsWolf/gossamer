# Research: citeproc (citeproc-py)

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/citeproc-py/json (version 0.12.3 at research time)
- Source: https://github.com/citeproc-py/citeproc-py
- Local evidence: `gossamer/citations.py:158-258`, `pyproject.toml` citations extra

## What the original uses
The `citations` extra installs `citeproc-py>=0.11`, `citeproc-py-styles>=0.1.6`, and
`six>=1.16`. `_render_with_citeproc` lazily imports `Citation`, `CitationItem`,
`CitationStylesBibliography`, `CitationStylesStyle`, `plain`, and `CiteProcJSON`.
APA uses the bundled CSL style; MLA has no shipped style and always uses the local approximation.
Any import or render failure falls back to the pure-Python approximation.

## Upstream facts
PyPI summary: "Citations and bibliography formatter". Requires Python >=3.9 and depends on
`lxml`; `citeproc-py-styles` is an extra. The PyPI `license` field is empty, so check the
upstream repository before any code or style reuse. No Rust crate was found under the obvious
`citeproc` / `citeproc-rs` names during this research.

## Rust options
1. Keep the existing Rust approximation in `_core` as the baseline and treat CSL as a
   recorded gap.
2. Port only the APA CSL subset needed by `format_citations` and record all differences.
3. Do not copy citeproc-py code or bundled styles until the license is verified.

## Fit and gaps
**Fit: partial / deferred.** The compatibility contract is the fallback behavior and exact
approximation output, not full CSL. A later ADR is needed before changing citation text.
