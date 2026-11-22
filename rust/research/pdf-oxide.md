# Research: pdf-oxide

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/pdf-oxide/json (version 0.3.78 at research time)
- Rust crate docs: https://docs.rs/pdf_oxide/latest/pdf_oxide/
- Local evidence: `gossamer/structured_parser.py:33-52`, `gossamer/document.py`,
  `gossamer/jats.py:102-105`

## What the original uses
Optional `documents` extra. `require_pdf_oxide()` lazily imports
`pdf_oxide.PdfDocument`. Gossamer calls `from_bytes(data).to_markdown_all()` and reads
per-page text, links, and table IR. JATS fallback also uses the markdown conversion.

## Upstream facts
PyPI summarizes it as a fast pure-Rust PDF toolkit exposed through PyO3, MIT OR Apache-2.0,
Python >=3.9. The only optional Python extra is OCR (`onnxruntime`); gossamer does not use OCR
in the paths inspected here. The Rust crate docs are available.

## Rust options
Use the `pdf_oxide` crate directly. Preserve the lazy import / install-hint behavior in the
Python reference and compare exact markdown, page, and table outputs in Rust replay cases.

## Fit and gaps
**Fit: high.** Main risks are extraction ordering and whitespace. No OCR parity work is planned
because the current gossamer paths do not enable it.
