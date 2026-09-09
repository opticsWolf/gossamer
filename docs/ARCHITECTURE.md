# gossamer — architecture (v0.9.0)

## Layers

```
LLM / user
  │  MCP stdio (mcp_server.py) · CLI (cli.py) · skills/SKILL.md
  ▼
WebResearcherToolbox (agent_tools.py) — facade, no logic
  │  config.py: ToolboxConfig + TOOL_REGISTRY (single source of truth
  │  for MCP schemas, CLI parity, execute_tool dispatch)
  ▼
Collaborators (orchestration, all Python)
  fetch.py · search.py · crawl.py · document.py · discovery.py
  search_providers.py · research_providers.py (HTTP/keys/rate/retry)
  cache.py · resource_store.py · guard.py · citations.py · budget.py
  │  JSON strings down, JSON strings up
  ▼
_core (Rust, src/) — pure kernels, no I/O, no secrets
  adapters.rs (35 providers) · metaextract.rs (meta_oxide crate)
  xmlatom.rs · ssrf.rs · robots.rs · guard.rs · cite.rs
  categories.rs · tokens.rs · sections.rs · dedupe.rs · budget.rs
  textlinks.rs · urls.rs · cacheutils.rs · miscutils.rs · pycompat.rs
  │  Oxide extractors alongside (pdf_oxide, office_oxide)
```

## The contract

- **JSON-string boundary.** Rust functions take strings, return JSON
  strings. No Python objects cross; `raw` payloads are re-attached
  Python-side via `json.dumps` (byte-identical). Lone surrogates and
  NaN cannot cross PyO3 — documented, tested, accepted.
- **Wrappers own the edges.** URL building, params, auth headers, rate
  limits, retries, and error precedence stay Python. Rust owns
  row-building and text shaping. Type gates run first so exotic inputs
  raise byte-identical errors.
- **Parity, not trust.** Every kernel ships with a differential test
  (`tests/test_rust_parity_*.py`): vendored Python oracle vs Rust,
  parametrized edge cases, seeded fuzz, hostile wrapper tests. A port
  is done only when the oracle agrees.

## Data flows

- **Search** (`web_search`): providers in priority order (failover) or
  merged (`search_merge`); result-level cache; DDG/Google/Bing/Exa.
- **Inspect** (`inspect_html_page`): SSRF → robots → rate limit →
  static fetch (Rust) or stealth browser → markdown + metadata
  (meta-oxide in-core) + tables → page cache → budget truncation.
- **Extract** (`extract_document`): URL/local → bytes → oxide
  converters (tables render by default) → optional `pages=` slicing →
  optional `store=` (+ `include_images=` for PDF figures into
  `<stem>.files/` + `## Figures`).
- **Crawl** (`crawl`): priority-frontier BFS (`score × 0.7^depth`,
  BM25 idfs over the live corpus + thesaurus expansion + anchor
  context + URL priors); documents collected, never fetched.
- **Research** (`research_by_category`): keyword classifier (Euro
  terms folded in) → domain adapters → per-source records.

## State

- **Cache** (`cache.py`): memory LRU + disk TTL, byte-capped LRU
  eviction, scoped clears, `prune()`; object stays Python, filename
  + size helpers in Rust (`cacheutils.rs`).
- **Stores** (`resource_store.py`): downloaded/embedded page assets.
- **Secrets** (`keystore.py`, `settings.py`, `env.py`): env wins over
  file; keystore 0600; `--check` never prints secrets. Rust never
  touches secrets (no HTTP/keys there to need them).

## Deliberately Python

MCP server (`mcp` SDK), toolbox facade, CLI (`argparse`), pydantic
models, oxide orchestration, wall-clock provenance, `urljoin`
absolutizer, `env/settings/keystore` discovery, ddgs engine. Porting
these buys nothing — the harness contract is already JSON.
