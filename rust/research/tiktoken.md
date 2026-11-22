# Research: tiktoken

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/tiktoken/json (version 0.14.0 at research time)
- Source: https://github.com/openai/tiktoken
- Rust crate docs: https://docs.rs/tiktoken-rs/latest/tiktoken_rs/
- Local evidence: `gossamer/token_budget.py`, root `Cargo.toml`

## What the original uses
Hard dependency `tiktoken>=0.5`. `token_budget.py` caches `Encoding` objects from
`tiktoken.get_encoding(name)`, prefers tiktoken's model map, and falls back to a local table
and finally `len(text) / 4` when tiktoken or an encoding is unavailable. Some encodings use
the Rust core path; others stay in Python.

## Upstream facts
PyPI: "tiktoken is a fast BPE tokeniser for use with OpenAI's models", MIT, Python >=3.9,
depends on `regex` and `requests`. The Rust `tiktoken-rs` crate is already a dependency
of the existing `gossamer-core` crate.

## Rust options
Use `tiktoken-rs` directly, keep the local model table, and preserve the exact fallback order.
Pay attention to how BPE rank files are obtained: the Python package may download/cache them,
while the Rust crate has its own loading rules. App cases must use encodings already available
offline.

## Fit and gaps
**Fit: high with recorded encoding gaps.** Token counts are deterministic for a fixed encoding,
but model-name resolution and unavailable-encoding fallbacks must be recorded from the Python
oracle.
