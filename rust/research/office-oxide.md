# Research: office-oxide

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/office-oxide/json (version 0.1.17 at research time)
- Rust crate docs: https://docs.rs/office_oxide/latest/office_oxide/
- Local evidence: `gossamer/structured_parser.py:38-60`, `gossamer/document.py`

## What the original uses
Optional `documents` extra. `require_office_oxide()` lazily imports
`office_oxide.Document`. Gossamer calls `from_bytes(data, fmt).to_markdown()` and reads
minimal metadata and table IR. Unsupported formats raise an install hint.

## Upstream facts
PyPI: "The fastest Office document processing library for Python — DOCX, XLSX, PPTX, DOC, XLS,
PPT", MIT OR Apache-2.0, Python >=3.10, no Python runtime dependencies. The Rust crate docs are
available, so this is another PyO3 wrapper over a Rust implementation.

## Rust options
Use the `office_oxide` crate directly with the same format codes and output conversion.
Keep the install-hint error text as a recorded gap for cases where the optional dependency is
absent in Python.

## Fit and gaps
**Fit: high.** Main risks are exact Markdown/table/metadata output and format detection. Use
fixture-based parity cases from `extract_document` before cutover.
