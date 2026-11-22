# Research: dynamic code in `gossamer/`

Date: 2026-11-22. Scope: the `dynamic-code` risk reported at
`gossamer/research_categories.py:287` by `rust-anything analyze . --whole-app`.

## Finding

There is exactly one true dynamic import in the package:

```python
# gossamer/research_categories.py:285-288
def _import_path(dotted: str) -> object:
    module_name, _, attr = dotted.rpartition(".")
    module = __import__(module_name, fromlist=[attr])
    return getattr(module, attr)
```

No `eval`, `exec`, or `compile` calls exist under `gossamer/`.

`_import_path` has one caller: `_make_adapter(provider)`. It resolves a value from the module-level
`_ADAPTER_FACTORIES` dict and instantiates it. Every one of the 27 values has the same module prefix,
`gossamer.research_providers`, followed by one of 27 concrete adapter class names:

```text
openalex, crossref, arxiv, courtlistener, ecfr, federalregister, alphavantage,
yahoo, frankfurter, eurostat, bundesbank, bis, coingecko, zenodo, semanticscholar,
huggingface, huggingface-papers, overpass, oldp, hudoc, govinfo, epo, kipris,
patentsview, lens, google-patents, open-meteo
```

`research_providers.py` defines more adapter classes than these (for example `WorldBankAdapter`,
`FredAdapter`, `GitHubAdapter`, `PubmedAdapter`), but only the 27 above are reachable through
`research_categories`. The set is a closed constant, not plugin discovery.

## Why it was written this way

The source comment says the lazy import keeps `research_categories` importable when
`research_providers` is unavailable in a "slim runtime". There is no packaging marker, optional
extra, or test that removes `research_providers`, and no other documentation references a slim
runtime. `research_providers` itself has no import of `research_categories`, so a static import does
not create a cycle.

Conclusion: the lazy import is a defensive performance/import-time choice, not a detected public
extension point. `_ADAPTER_FACTORIES` is not in `__all__` and is not part of the CLI/MCP contract.

## Other `getattr` sites

These were checked and are not dynamic code blockers:

- `browser_worker.py:61`, `browser_isolation.py:321` — profile name -> `browser_oxide.Profile`
  attribute. Bounded by a fixed profile set; the Rust port can use an enum.
- `agent_tools.py:540` — `getattr(self, spec.method)` from `TOOL_REGISTRY`. Bounded by the 12 tool
  specs; the front door already dispatches those commands explicitly.
- `guard.py:323`, `search.py` sites, etc. — constant-name attribute access with defaults, used for
  optional attributes. Direct field access plus `Option` is sufficient in Rust.
- `search_providers.py:748` — `importlib.util.find_spec("browser_oxide")`, a feature probe, not a
  computed import.

## Contract that must be preserved

- Unknown provider error text from `_make_adapter`:
  `ValueError: no adapter registered for provider '<id>'`.
- The cache is per provider id and process-wide, guarded by a lock. Tests assert
  `_make_adapter("openalex") is _make_adapter("openalex")` and that a different provider is a
  different instance (`tests/test_research_categories.py:790-800`).
- `reset_adapter_cache()` clears the cache.
- Tests monkeypatch `_make_adapter`; those tests stay on the Python oracle and cannot be replayed
  directly against Rust. App-level cases should cover classification, provider validation, and
  error text without requiring live network calls.

## Recommended Rust treatment

Do **not** change the Python source. In the Rust port:

1. Replace `_import_path` with a static `ProviderKind` enum implementing `FromStr` over the 27 ids.
2. Map each variant directly to its adapter constructor; no runtime module loading.
3. Keep the process-wide cache as an explicit injected service (not a global `static`, per
   `M-AVOID-STATICS` in the Rust guidelines): a `Mutex<HashMap<ProviderKind, Arc<dyn Adapter>>>`
   owned by the application context.
4. Preserve provider-id strings, unknown-provider error text, and reset semantics in recorded cases.
5. Treat `research_providers` network calls as I/O behind a mockable boundary (`M-MOCKABLE-SYSCALLS`);
   app replay cases must not depend on live endpoints.

## Decision

The dynamic-code risk is **bounded and unblocking**. It does not require code generation,
`eval`-equivalent behavior, or a plugin loader. It can be represented as static dispatch, so
`gossamer/` can proceed to normal unit planning once the ADR-006 dependency research files are in
place.
