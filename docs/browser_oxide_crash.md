# browser_oxide cold-render abort on example.com — investigation log

**Status:** open upstream; not filed as of 2026-11-18.
**Affected:** `browser-oxide` 0.1.0 / 0.1.2 / 0.1.3 (Python wheel **and** the
standalone `browser-oxide-mcp` 0.1.3 Windows binary).
**Host:** Windows 11 Pro 10.0.26200, AMD Ryzen 9 5950X, 32 GB RAM,
Python 3.13.14 (venv).

This file is the evidence package for an upstream issue. The short version:
a cold render of `https://example.com` aborts the whole process after V8's
25 s deadline watcher fires. Python cannot catch the abort. Other pages
(Hacker News, IANA, httpbin) render normally, so the engine is not broken for
every site. The same HTML + `/s.js` served from `gist.githack.com` also
renders, so the script content alone is not sufficient to reproduce it.

> gossamer now contains this by default: browser renders run in a short-lived
> child process (`gossamer/browser_isolation.py` +
> `gossamer/browser_worker.py`). The parent turns the abort/hang into a normal
> `BrowserRenderError`, and `browser-first`/`auto` fall back to the static
> fetch. See [Workarounds in gossamer](#workarounds-in-gossamer).

---

## 1. Reproduction

### Direct Python (smallest repro)

```python
from browser_oxide import Browser, Profile

b = Browser(profile=Profile.chrome())
try:
    page = b.navigate("https://example.com", max_iterations=1)
    print(page.verdict, len(page.html))
finally:
    b.close()
```

Run with the 0.1.3 wheel:

```bash
python -c 'from browser_oxide import Browser, Profile; b=Browser(profile=Profile.chrome()); p=b.navigate("https://example.com", max_iterations=1); print(p.verdict, len(p.html)); b.close()'
```

Observed (debug navigation enabled for the `[navigate]` line):

```text
$ BROWSER_OXIDE_DEBUG_NAV=1 python -c '...'
[navigate] iter=0 url=https://example.com html_len=577
[V8DeadlineWatcher] deadline 25000ms expired — firing terminate_execution
[V8DeadlineWatcher] terminate_execution returned true
memory allocation of 68719476736 bytes failed
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
```

The process dies; `try/except` cannot catch it. On Windows the exit code is
`3221226505` = `0xC0000409` (`STATUS_STACK_BUFFER_OVERRUN`, used by
`__fastfail`/abort). The allocation size is not stable: repeated runs
produced 16 GiB, 32 GiB, and 64 GiB (`17179869184`, `34359738368`,
`68719476736`). With very small `BROWSER_OXIDE_BUILD_BUDGET_MS` values the
process sometimes hangs after `terminate_execution` instead of aborting.

The initial HTML fetch succeeds (`html_len=577`), so this is not a TLS or DNS
failure. The abort happens while the engine builds the page (parse/layout/
script execution), after the document has arrived.

### Gossamer (same engine)

```bash
python -m gossamer.cli inspect https://example.com --use-smart browser
```

Before isolation this killed the CLI process with the same log. After
isolation it returns a static fallback (see §7).

### Standalone MCP binary (proves it is not the Python bindings)

Download `browser-oxide-mcp-x86_64-pc-windows-msvc.tar.gz` from the
[v0.1.3 release](https://github.com/yfedoseev/browser_oxide/releases/tag/v0.1.3),
then send JSON-RPC over stdio:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"fetch_page","arguments":{"url":"https://example.com","profile":"chrome"}}}
```

`tools/list` succeeds; `fetch_page` produces the same
`V8DeadlineWatcher` → `terminate_execution` → 64 GiB allocation failure and
the server exits with `3221226505`. So the Python wheel and the Rust MCP
binary fail identically.

### Versions

| Package | Version | Result |
|---|---|---|
| `browser-oxide` PyPI wheel | 0.1.0 | abort |
| `browser-oxide` PyPI wheel | 0.1.2 | abort |
| `browser-oxide` PyPI wheel | 0.1.3 | abort |
| `browser-oxide-mcp` Windows binary | 0.1.3 | abort |

Note: `browser_oxide.__version__` reports `"0.1.0"` even in the 0.1.3 wheel
(the native module's version string was not bumped); `pip show browser-oxide`
correctly reports `0.1.3`.

---

## 2. What works (same host, same wheel)

These URLs render normally, so the engine and the network stack are usable:

| URL | Result |
|---|---|
| `https://news.ycombinator.com/` | `verdict=pass`, 33,388 chars HTML, ~1.0 s |
| `https://www.iana.org/help/example-domains` | `verdict=thin-shell`, 6,765 chars, ~0.4 s |
| `https://httpbin.org/html` | `verdict=thin-shell`, 3,720 chars, ~0.6 s |
| same HTML + `/s.js` on `gist.githack.com` | `verdict=thin-shell`, ~14 KB, ~1.8 s |

`example.com` (and `example.org`) fail. `example.com` currently returns a
577-byte page with an inline `<style>` and `<script src=/s.js>`; the old
example.com page had no script. The `/s.js` script is a small DOM mutation
that inserts multilingual paragraphs plus an inline SVG.

### Content-vs-origin experiment

I copied the exact example.com HTML into three temporary `text/html` files
served by `gist.githack.com` (a normal Cloudflare-fronted host):

1. no `<script>`;
2. `<script src="https://example.com/s.js"></script>`;
3. the same script inlined.

All three rendered successfully. The inline version's output was larger,
confirming the script executed. I also evaluated the exact `/s.js` source on
`httpbin.org/html` after load: it completed normally. Therefore:

* the script content alone does **not** reproduce the abort;
* the abort is tied to loading that page from the `example.com` origin
  (or to the build-phase timing/connection state on that origin), not to
  the DOM operations themselves.

This is the key clue for the upstream maintainer. A same-origin subresource
on the existing HTTP/2 connection, a Cloudflare response difference for the
engine's request, or a build-phase re-entrancy bug are all plausible; the
investigation did not isolate which one.

---

## 3. What did not help

All of these were tried against the direct Python repro unless noted.

| Attempt | Result |
|---|---|
| `BROWSER_OXIDE_HEAP_MAX_MB` = 64, 128, 512 | still aborts; allocation size still varied 16–64 GiB |
| `BROWSER_OXIDE_HEAP_INITIAL_MB` = 16, 32, 64 | still aborts |
| `BROWSER_OXIDE_BUILD_BUDGET_MS` = 1, 100, 1000, 120000 | aborts or hangs after `terminate_execution` |
| `BROWSER_OXIDE_NAV_BUDGET_MS` / `HOST_BUDGET_MS` = 5 s / 10 s | build still runs until the 25 s watcher; abort |
| `BROWSER_OXIDE_NO_SNAPSHOT_CACHE=1` | still aborts |
| `BROWSER_OXIDE_NO_SHARED_SESSION=1` | still aborts |
| `BROWSER_OXIDE_NO_SHARED_COOKIES=1` | hung past 80 s instead of aborting; not a fix |
| all three `NO_SHARED_*` flags together | still aborts |
| `BROWSER_OXIDE_NO_SHARED_ACCEPT_CH=1` | still aborts |
| `BROWSER_OXIDE_PROXY` to a local TCP-only CONNECT proxy | proxy sees `CONNECT example.com:443`, but still aborts (so not HTTP/3) |
| `Profile.firefox()` instead of `Profile.chrome()` | still aborts |
| `max_iterations=1` instead of 5 | still aborts (the build phase is before the iteration loop) |
| `V8_FLAGS=--jitless` / `--no-turbofan` | ignored; an invalid `V8_FLAGS` value produces no warning |
| `BROWSER_OXIDE_INIT_JS` probe (`globalThis.__bo_probe = 42`) | not visible after navigation in this build; no effect |
| `BROWSER_OXIDE_BLOCKER=1` + a `||example.com/s.js^` rule | no effect; still aborts (the `blocker` Cargo feature is off by default) |
| browser_oxide 0.1.0 / 0.1.2 | abort (not a 0.1.3 regression) |
| standalone MCP 0.1.3 binary | abort (not Python-specific) |
| local HTTPS server with a self-signed cert | rejected by certificate verification, so local content tests were not possible |

I did not modify the Windows root store to trust a local MITM certificate.

---

## 4. Upstream reports checked (2026-11-18)

Searched `yfedoseev/browser_oxide` issues and PRs for `V8DeadlineWatcher`,
`deadline 25000ms`, `terminate_execution`, `memory allocation`,
`68719476736`, `SIGABRT`, `crash`, `allocation`, and `memory`.

No existing issue matches this cold-render abort. Closest reports:

* [#33 — PagePool leaks V8 heap memory](https://github.com/yfedoseev/browser_oxide/issues/33)
  (closed, fixed in 0.1.2): warm `Page` reuse grows the V8 heap ~10 MB/page.
  Not a cold-navigation abort.
* [#37 — deno_core 0.404 → 0.408: SIGABRT during V8 isolate construction in
  Linux debug builds](https://github.com/yfedoseev/browser_oxide/issues/37)
  (closed, fixed in 0.1.3): isolate constructed outside a tokio runtime
  aborts. This crash happens later, during page build.
* [denoland/rusty_v8 #2088 — terminate_execution() does not interrupt
  TurboFan-optimized loops calling Array.prototype.fill](https://github.com/denoland/rusty_v8/issues/2088)
  (open): adjacent V8 termination behavior, but a different symptom.

The v0.1.3 release notes document the tokio-runtime abort fix and the
environment-tunable heap limits; neither addresses this case.

Gossamer's own `CHANGELOG.md` 0.9.30 already records this upstream abort and
gates the live browser tests. This document is the fuller evidence package.

---

## 5. Hypotheses (not proven)

1. **Same-origin subresource / build-phase interaction.** The initial HTML is
   fetched, then the engine builds the page and fetches `/s.js` on the same
   HTTP/2 connection. The gist copy loads the same script cross-origin and
   succeeds. A stuck H2 stream or re-entrant DOM/layout update during the
   build phase would fit the 25 s deadline and the post-termination large
   allocation.
2. **Cloudflare response difference.** `example.com` is behind Cloudflare.
   The engine's request for `/s.js` (same-origin referer) may receive a
   different body than curl or a cross-origin request. The engine does not
   log the script response length, so this was not confirmed.
3. **V8 termination path.** `terminate_execution()` returns `true`, then the
   engine attempts a very large Rust allocation. This may be a V8 149.4
   cleanup/termination bug, but the fact that the gist page succeeds suggests
   the trigger is earlier than termination.
4. **The 64 GiB number is a symptom, not a configured heap limit.** Setting
   `BROWSER_OXIDE_HEAP_MAX_MB` did not control it. The value is likely a
   runaway buffer length or an allocation derived from corrupted state.

---

## 6. Minimal upstream bug report (copy-paste)

Title:

```text
[bug]: Cold render of example.com aborts on Windows 11 — V8DeadlineWatcher terminate_execution then 64 GiB allocation failure
```

Body:

~~~~markdown
## Summary

A cold `Browser.navigate("https://example.com")` aborts the process on
Windows 11 with the 0.1.3 Python wheel. The initial HTML fetch succeeds
(`html_len=577`), then the build phase runs until the 25 s V8 deadline
watcher fires, `terminate_execution()` returns true, and the process dies
with `memory allocation of 68719476736 bytes failed`. The same happens with
the standalone `browser-oxide-mcp` 0.1.3 Windows binary, so it is not a
Python-binding issue.

## Environment

- Windows 11 Pro 10.0.26200, AMD Ryzen 9 5950X, 32 GB RAM
- Python 3.13.14
- `browser-oxide` 0.1.3 from PyPI (wheel)
- Also `browser-oxide-mcp` 0.1.3 (x86_64-pc-windows-msvc release binary)
- `browser_oxide.__version__` reports `0.1.0` inside the 0.1.3 wheel

## Reproduction

```python
from browser_oxide import Browser, Profile

b = Browser(profile=Profile.chrome())
try:
    page = b.navigate("https://example.com", max_iterations=1)
    print(page.verdict, len(page.html))
finally:
    b.close()
```

Output:

```text
[navigate] iter=0 url=https://example.com html_len=577
[V8DeadlineWatcher] deadline 25000ms expired — firing terminate_execution
[V8DeadlineWatcher] terminate_execution returned true
memory allocation of 68719476736 bytes failed
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
```

Windows exit code: `3221226505` (`0xC0000409`,
`STATUS_STACK_BUFFER_OVERRUN`).

## What works

These render normally on the same host/wheel:

- `https://news.ycombinator.com/` — verdict pass, ~33 KB, ~1 s
- `https://www.iana.org/help/example-domains` — verdict thin-shell, ~7 KB
- `https://httpbin.org/html` — verdict thin-shell, ~4 KB
- the exact example.com HTML + `/s.js`, served from a `text/html` gist on
  `gist.githack.com` — renders in ~1.8 s, including the inline-script variant

The exact `/s.js` source evaluated on `httpbin.org/html` after load also
completes normally. So the script content alone is not enough; the abort is
tied to loading the page from the example.com origin (or to build-phase
timing/connection state on that origin).

## What does not help

- `BROWSER_OXIDE_HEAP_MAX_MB` / `HEAP_INITIAL_MB` (64–512 MB)
- `BROWSER_OXIDE_BUILD_BUDGET_MS` (1 ms–120 s)
- `BROWSER_OXIDE_NAV_BUDGET_MS` / `HOST_BUDGET_MS` (5–10 s)
- `BROWSER_OXIDE_NO_SNAPSHOT_CACHE`, `NO_SHARED_SESSION`,
  `NO_SHARED_COOKIES`, `NO_SHARED_ACCEPT_CH`
- a TCP-only CONNECT proxy (`BROWSER_OXIDE_PROXY`), so not HTTP/3
- `Profile.firefox()`, `max_iterations=1`
- browser-oxide 0.1.0 and 0.1.2 (not a 0.1.3 regression)
- `V8_FLAGS=--jitless` / `--no-turbofan` (appear to be ignored)
- `BROWSER_OXIDE_INIT_JS` (no visible effect in this build)
- `BROWSER_OXIDE_BLOCKER=1` + a `||example.com/s.js^` rule (no effect)

## Notes

- The allocation size varies: 16 GiB, 32 GiB, or 64 GiB across runs.
- With `BROWSER_OXIDE_BUILD_BUDGET_MS` set very low (1–1000 ms) the process
  sometimes hangs after `terminate_execution` instead of aborting.
- `example.com` now serves a 577-byte page with an inline `<style>` and
  `<script src=/s.js>`; the old page had no script.
- The 25 s watcher matches `BROWSER_OXIDE_BUILD_BUDGET_MS`'s default.
- Existing issues #33 (warm-reuse heap leak) and #37 (isolate construction
  SIGABRT) are different symptoms.
~~~~

---

## 7. Workarounds in gossamer

Implemented on the `dev_browser` branch:

* `gossamer/browser_isolation.py` runs every browser render in a one-shot
  child process (`gossamer/browser_worker.py`).
* The parent enforces `GOSSAMER_BROWSER_TIMEOUT` (default 45 s; minimum 5 s),
  parses the worker's JSON status, and reads the HTML from a temp file.
* A native abort, timeout, challenge, missing package, or empty render becomes
  `BrowserRenderError`: `browser-first` falls back to the static Rust fetch,
  and strict browser failures return a normal error instead of killing the
  host.
* The optional `BrowserOxideSearchProvider` uses the same worker unless
  isolation is disabled.
* `GOSSAMER_BROWSER_ISOLATION=0` restores in-process rendering for debugging;
  that also restores the native-abort risk.
* Live tests now use `https://news.ycombinator.com/` instead of
  `example.com`, so they exercise the engine without hitting the known abort.

Verified manually on the affected host:

```text
OK https://news.ycombinator.com/ html_len=33388
browser_oxide worker crashed for https://example.com/ (exit 0xC0000409 ...):
  memory allocation of 68719476736 bytes failed
CAUGHT BrowserRenderError ...
PARENT ALIVE
```

and through the toolbox:

```text
fetch_method= static
cache_hit= False
error= None
```


---

## Appendix: captured response bodies (2026-11-18)

The page and script change over time; these are the exact bodies that produced
the abort and the gist comparison. Both were fetched with `curl` from the same
host.

### `https://example.com/` (577 bytes)

```html
<!doctype html><html lang=en><head><meta charset=utf-8><link rel=icon href=data:,><meta name=viewport content="width=device-width,initial-scale=1"><title>Example Domain</title><style>html{color-scheme:light dark;background:light-dark(#eee,#222)}body{font:16px/1.6 system-ui,sans-serif;max-width:26em;margin:auto;padding:25vh 2em 2em;text-align:center}</style></head><body><p>This domain is for use in documentation examples without needing permission. This is not a service; avoid relying on it for testing and monitoring purposes.</p><script src=/s.js></script></body></html>

```

### `https://example.com/s.js` (1,963 bytes)

```javascript
var B=document.body,P,p;B.children[0].insertAdjacentHTML("afterend","<p lang=ar dir=rtl>هذا النطاق مُخصص للاستخدام في أمثلة التوثيق دون الحاجة إلى إذن. هذه ليست خدمة، يُرجى تجنب الاعتماد عليها لأغراض الاختبار والمراقبة.</p><p lang=zh>该域名仅用于文档示例，无需获得许可。这并非一项服务，请勿将其用于测试和监控目的。</p><p lang=fr>L’usage de ce domaine est réservé à des exemples de documentation, sans autorisation préalable.  Il ne s’agit pas d’un service ; son utilisation à des fins de test ou de surveillance est à éviter.</p><p lang=ru>Данный домен предназначен для использования в примерах документации без необходимости получения предварительного разрешения. Это не сервис; не рекомендуется его использование для тестирования и мониторинга.</p><p lang=es>Este dominio está destinado al uso en ejemplos de documentación sin necesidad de permiso. Esto no es un servicio; evitar utilizarlo para realizar pruebas o monitoreos.</p><a href=https://iana.org/help/example-domains>Learn more</a>");P=[...B.querySelectorAll("p")];P[0].lang="en";navigator.languages.some(l=>p=P.find(p=>p.lang==l.split("-")[0]));p=p||P[0];B.prepend(p);B.insertAdjacentHTML("afterbegin",'<style>svg{display:block;margin:-2.75em auto 0;opacity:.55}p+p{font-size:.875em;opacity:.6}</style><svg viewBox=0,0,20,20 width=44 height=44 fill=currentColor aria-hidden=true><path fill=none stroke=currentColor stroke-width=1.3 stroke-linejoin=round d="M6 4H3v12q4 0 7 1.5-1-3.5-4-4.5V2q3.5 1 4 3v12.5q3-1.5 7-1.5V4q-4.5 0-7 1"/><g transform=rotate(-6,13.6,8.3)><path id=q d="M11.5 6.6h1.8v1.8l-1 1.6-.6-.3.8-1.3h-1z"/><use href=#q x=2.4 /></g></svg>')

```
