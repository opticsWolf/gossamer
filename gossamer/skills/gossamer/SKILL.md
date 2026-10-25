---
name: gossamer
description: Web research toolkit (search, fetch pages as markdown, extract documents, patent/legal/financial domain research). Use whenever the task needs current external information — docs, APIs, papers, case law, market data, patents — instead of guessing from training data. Prefers cached, rate-limited, token-budgeted tools over raw curl/fetch loops.
---

# Gossamer

Web research toolkit: search the web, fetch pages as LLM-friendly markdown
with follow-up links, and extract text/tables from documents (PDF, DOCX,
XLSX, PPTX, feeds). Domain adapters route scholarly / legal / patent /
financial / geo queries to the right APIs. Everything is cached,
per-domain rate-limited, and token-budgeted.

## Access (use whichever the harness provides)

- **MCP tools** (`gossamer_*` in pi with directTools, or plain names in
  Codex/Claude Code): `web_search`, `inspect_html_page`,
  `batch_inspect_pages`, `download_file`, `locate_pdf`, `extract_document`,
  `discover_resources`, `crawl`, `manage_cache`, `research_by_category`,
  `export_citations`, `check_sources`.
- **MCP resource** `skills://gossamer`: this file, readable from any MCP
  host without a repo checkout.
- **CLI** (identical JSON, no MCP setup; 14 subcommands — the 12 MCP tools
  1:1, plus `categories` and `skills`): `search QUERY [--max-results N --max-tokens T
  --search-only --provider P --depth D]` · `research QUERY [--category C
  --provider P --providers P… --max-results N --filter F --select F --title T --author A]` · `inspect URL [--query Q --offset N
  --max-chunks N --structured --use-smart auto|browser|static]` ·
  `batch URL…` · `download URL -o PATH [--min-bytes N --max-bytes N --expect-format auto|pdf --overwrite --resume --try-mirrors URL…]` ·
  `locate-pdf DOI` · `extract FILE|URL [--pages A-B --structured --tables-as json|markdown|csv --store
  --store-dir D --include-images]` · `check URL… [--mode status|content]` ·
  `discover URL` · `crawl ROOT [--query Q --max-depth D --max-pages N
  --min-score S --same-host --excerpts --search-prior --seed-urls U…
  --use-smart auto|browser|static]` · `cache [--action prune|clear|reset]` ·
  `cite DOI|URL|PDF… [--style bibtex|csl-json|apa|mla --enrich --no-dedupe --from-pdf]` ·
  `categories` ·
  `skills list|show|install [--dest DIR…] [--project] [--skill NAME…] [--force]`.
  Run via the project venv
  (`…/.venv/Scripts/python.exe -m gossamer.cli …` on Windows); `skills
  show gossamer` prints this file, and `skills install` copies it into
  Claude Code / OpenCode skill directories. CLI defaults:
  `--max-results 5` (search/research), `--depth 5`, `--max-tokens 0`
  (budget default), crawl `--max-depth 3 --max-pages 15 --min-score 0.05`.

## Tool call signatures (defaults shown; one JSON envelope per call)

Required parameters have no default. `None`/empty means "not supplied";
`0` budgets mean "toolbox default".

### Search & read

```python
web_search(query, search_only=False, max_results=5, depth=5,
           max_tokens=0, provider="duckduckgo")
inspect_html_page(url, use_smart="auto", query=None, offset=0,
                  max_chunks=1, structured=False)
batch_inspect_pages(urls)
crawl(root_url, query=None, max_depth=3, max_pages=15, same_host=False,
      min_score=0.05, excerpts=False, search_prior=False, seed_urls=[],
      use_smart="auto")
```

`crawl` caps: `max_depth` hard cap 5, `max_pages` 1-50 (failed fetches do
not count). `use_smart="browser"` needs the `gossamer-web[browser]` extra.

### Documents & files

```python
extract_document(source, pages=None, structured=False, store=False,
                 store_dir=None, include_images=False, tables_as="json")
download_file(source, output_path, min_bytes=1, max_bytes=0,
              expected_format="auto", overwrite=False, resume=False,
              fallback_urls=[])
discover_resources(url)
check_sources(urls, mode="status")
```

`extract_document(pages="10-20")` is 1-based inclusive for PDFs (sheet
range for XLSX) and cannot combine with `store=True`; `include_images`
needs `store=True` and is PDF-only. `download_file(max_bytes=0)` uses the
configured cap; `fallback_urls` are caller-supplied mirrors, each checked
independently. `check_sources(mode="status")` is a HEAD probe;
`"content"` is a GET for hosts that reject HEAD.

### Research & citations

```python
research_by_category(query=None, category=None, max_results=5,
                     provider=None, filter=None, select=None, title=None,
                     author=None, providers=[])
locate_pdf(doi)
export_citations(results, style="bibtex", enrich=False, dedupe=True,
                 from_pdf=False)
manage_cache(action="prune")
```

`research_by_category(query)` with no category auto-routes; omit `query`
entirely to get the provider taxonomy. `filter`/`select`/`title`/`author`
are valid only with `provider="openalex"` and rejected otherwise;
`providers=[...]` is scholarly-only and mutually exclusive with
`provider`. `locate_pdf` locates candidates, it never downloads.

## Routing (don't guess — classify first)

`research` auto-routes, or pick explicitly. Category → default provider:

- `scholarly` → `openalex` by default; `crossref`, `arxiv`, `zenodo`, and `semanticscholar` are opt-in scholarly providers
- `legal` → `courtlistener` (US case law) · `oldp` (German cases) ·
  `hudoc` (ECtHR) · `ecfr`/`federalregister`/`govinfo` (US regs)
- `patent` → `epo` (worldwide via INPADOC) · `kipris` (Korea) ·
  `patentsview` (USPTO) · `lens` (WO/EP/DE/CN/US aggregator) — key-gated, fail fast without keys ·
  `google-patents` (keyless number lookup; free text via `site:patents.google.com` search)
- `financial` → `yahoo` (quotes) · `frankfurter` (FX) · `eurostat` /
  `bundesbank` / `bis` (EU macro) · `coingecko` (crypto)
- `geo` → `open-meteo` / `overpass` (weather, places, coordinates)
- anything else → general web search (`duckduckgo`)

`gossamer categories` prints this table live —
prefer it over memory when unsure.

`research --filter F --select F --title T --author A` passes OpenAlex-native controls and is valid
only with `--provider openalex`; gossamer rejects these options for other
providers rather than silently ignoring them. `--title`/`--author` map to the verified
`title.search`/`raw_author_name.search` filters and combine with `--filter`. `--providers` explicitly runs a
sequential scholarly merge by DOI/arXiv ID; it never runs by default and keeps
all per-provider source records.

Provider failures from `research` keep `results` as an empty list and put the
message in a top-level `error` field. The CLI exits nonzero for these failures.
For arXiv HTTP 406, which can reflect a temporary upstream edge/IP limit,
avoid immediate repeat calls and honor the provider's three-second minimum.

## Budgets (avoid harness timeouts)

- `use_smart="browser"` (JS rendering) needs the `gossamer-web[browser]`
  extra, Windows/macOS only — without it only static fetch runs, and even
  with it bot-walled sites (Cloudflare challenges, CAPTCHAs) still fail.
- Keep `max_pages` ≤ 15 on crawls; set an explicit `max_tokens` budget.
- Long extractions: `extract … --pages 10-20` instead of whole documents.
- `check` URLs with `--mode status` before fetching the shaky ones.

## Auth

API keys live in the keystore (`~/.gossamer/keys.json`;
`python -m gossamer.keystore --init`), never in harness configs or prompts.
Keyed providers raise an actionable error naming the exact variable
(e.g. `GOSSAMER_EPO_KEY`) — surface it to the user instead of retrying.
OpenAlex works keyless for casual use; `GOSSAMER_OPENALEX_KEY` is optional
and raises the API's daily budget. Set `GOSSAMER_OPENALEX_EMAIL` to send your
own `mailto` contact; gossamer does not invent a default email. OpenAlex
429s come in two documented kinds: a per-second rate limit (wait a moment
and retry) and a daily credit-budget refusal (`not_enough_credits` — wait
for the daily reset; the typed error carries `X-RateLimit-Reset` seconds).
Semantic Scholar can run keyless, but its unauthenticated pool is shared and
may return 429. Configure `GOSSAMER_SEMANTICSCHOLAR_API_KEY` for the
individual one-request-per-second allowance; if keyless use returns 429,
wait or set that exact variable rather than looping retries.

## Config / cache (where stuff actually is)

- Cache: `GOSSAMER_CACHE_DIR` (see `mcp.json`) > `gossamer.json:cache_dir` > `./.gossamer_cache`.
- Keys: `$GOSSAMER_KEYSTORE` > `gossamer.json:keystore` > `~/.gossamer/keys.json` (created only via `keystore --init`; absent = normal, not a broken install).
- Check effective paths in `mcp.json` + `python -m gossamer.keystore --check`, not `~/.gossamer`.

## Cache behavior (when a result is cached)

- Page/document reads are cached: `inspect`/`extract` responses carry `cache_hit: true` when served from cache; repeat reads within the TTL skip the network. Paging (`--offset`/`--max-chunks`, `--pages`) reads the cached full text, so re-reads stay cheap.
- `search` results are cached per query for the configured TTL; repeats do not re-query the provider. Provider adapters (`research --provider …`) are live calls with per-domain rate limits, not result caches.
- `check`, `download`, `locate-pdf`, and `cite` are never cached: `check` probes liveness, `download` writes files, `locate-pdf` resolves current OA state, and `cite` formats on demand.
- Maintenance: `cache --action prune` drops expired/over-cap entries (keeps valid ones and visited URLs); `clear` wipes caches plus visited URLs for fully fresh fetches; `reset` forgets visited URLs only, to retry a failed page without clearing caches.
- No global PATH shim is provided: run the CLI from the project venv (`…/.venv/Scripts/python.exe -m gossamer.cli …` on Windows) or via MCP tools.

## Documents (PDF limits that matter)

- Tables render as markdown tables by default — no flag needed.
- Figures need `extract … --store --include-images` (PDF only): rasters land in `<stem>.files/` with a `## Figures` section; without the flag (or without `--store`) you get text-only and an empty manifest. Vector-only figures have no bytes to save.
- Large PDFs: use `--pages 10-20` ranges (cannot combine with `--store`).
- Use `download URL -o file.pdf --expect-format pdf` to save a remote file without parsing it; use `extract URL --store` when you also want extracted text. Downloads obey robots/SSRF checks, enforce a byte cap, and never bypass bot walls. Add `--resume` to continue an existing partial file with Range/If-Range (servers that ignore Range restart; unsatisfiable ranges keep the partial file). `--try-mirrors` accepts only caller-supplied, known OA/repository URLs; each is checked independently and attempts are returned with provenance.

## Literature collection workflow

1. Search via `research QUERY --category scholarly --max-results N`; use `--providers openalex arxiv` only when you explicitly want a sequential multi-provider merge.
2. Probe candidate pages with `check URL --mode status` before spending a full fetch/download.
3. For a DOI, run `locate-pdf DOI`, inspect the returned `candidates`, and prefer an OA PDF URL with clear source/license metadata. This step locates candidates; it does not fetch the file. OpenAlex runs first; Unpaywall v2 is queried only when `GOSSAMER_UNPAYWALL_EMAIL` is configured and OpenAlex yields nothing usable.
4. Download with `download URL -o paper.pdf --expect-format pdf`. If a known OA repository mirror is already available, pass it with `--try-mirrors URL…`; each URL is still checked independently and no challenge is bypassed.
5. Parse the saved file with `extract paper.pdf`; add `--store --store-dir DIR` if you also want the extracted Markdown/resources persisted.
6. Export a citation with `cite DOI --style bibtex` (or another supported style), or `cite paper.pdf [--from-pdf]` to cite a downloaded PDF via its detected DOI. Review license/access terms before redistributing any full text.
