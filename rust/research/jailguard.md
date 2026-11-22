# Research: jailguard

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/jailguard/json (version 0.1.2 at research time)
- Rust crate docs: https://docs.rs/jailguard/latest/jailguard/
- Source: https://github.com/yfedoseev/jailguard
- Local evidence: `gossamer/guard.py:274-345`

## What the original uses
Optional `guard = ["jailguard>=0.1.2"]` extra. `JailGuard` lazily imports the package,
loads a model once, and is fail-open: import/model errors disable the guard rather than breaking
requests. The code converts `result.risk` with `str()` because it is an enum.

## Upstream facts
PyPI describes it as a pure-Rust prompt-injection detector with a 1.5 MB embedded MLP classifier,
Apache-2.0/MIT, Python >=3.8. The docs.rs crate exists, so the PyO3 package is a wrapper over an
already-Rust implementation.

## Rust options
Use the `jailguard` crate directly behind the existing guard trait. Keep the fail-open policy
and the exact disable-reason / risk strings in recorded cases.

## Fit and gaps
**Fit: high.** This is the easiest of the unknown dependencies: the original is already a thin
binding. Preserve lazy initialization, caching, and error text; do not change the default off
behavior.
