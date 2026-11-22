# Research: browser-oxide

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/browser-oxide/json (version 0.1.3 at research time)
- Rust crate docs: https://docs.rs/browser_oxide/latest/browser_oxide/
- Local evidence: `gossamer/browser_isolation.py`, `gossamer/browser_worker.py`,
  `docs/browser_oxide_crash.md`

## What the original uses
- Optional extra `browser = ["browser-oxide>=0.1"]` in `pyproject.toml`.
- `browser_isolation.py` renders in a one-shot child process and maps `browser_oxide.Profile`
  names through `getattr`.
- `browser_worker.py` imports the package lazily only in the child.
- The process boundary exists because cold renders on some origins abort with a native
  `0xC0000409` in the upstream engine (details in `docs/browser_oxide_crash.md`).

## Upstream facts
PyPI describes it as a Rust stealth headless browser engine with Python bindings: own
BoringSSL TLS stack, DOM/JS, native fingerprint, no Chromium. License classifiers are
Apache-2.0 / MIT. Python wheels are not available on every platform; the project already
degrades to the static path when the package is absent.

## Rust options
- The underlying `browser_oxide` crate is available and is the most direct mapping.
- Keep the one-shot child-process boundary. Do not move rendering into the Rust app process:
  the abort is native and uncatchable in-process.

## Fit and gaps
**Fit: high for the engine, mandatory isolation.** The Rust port can call the crate directly,
but must preserve the existing opt-in/off-by-default posture, timeout floor, and
`BrowserRenderError` semantics. Unknown origins can still crash the child; app replay cases
must use the known-safe fixtures, not live defended sites.
