"""M17 — fetch_mode / use_smart dispatch: crawl, research, batch_inspect_pages.

Crawl, research and batch_inspect_pages all page-fetch through the same
pipeline as ``inspect_html_page``. This pins that each of them honours
``fetch_mode`` and the ``use_smart`` override, and that the static ->
stealth-browser fallback fires on JS-heavy pages.

Two layers of coverage:

* **Stubbed seam (deterministic, always runs).** The browser is replaced by
  a stub at the single module-level 3-tuple function both the ``auto`` branch
  and ``_browser_fetch`` call (``agent_tools._fetch_with_browser_oxide``). A
  local HTTP/1.1 server serves a text page (static returns real text -> no
  fallback) and an empty page (static returns non-text -> auto falls back).
  Each case gets a fresh toolbox + cache so nothing leaks across modes.

* **Real browser_oxide (external HTTPS, skips if the network/browser is
  unavailable).** The stealth engine verifies TLS certs and speaks HTTP/2
  over TLS, so it cannot reach a self-signed local server; we exercise it
  against ``https://example.com`` instead to prove the real engine is wired
  through the dispatch.
"""

import json
import os
import tempfile
import threading

import pytest
import gossamer.agent_tools as at
import gossamer.fetch as fetch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gossamer.agent_tools import WebResearcherToolbox, ToolboxConfig

_ALLOW = "GOSSAMER_ALLOW_PRIVATE"


# ───────────────────────────── fixtures ──────────────────────────────

class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            body, ct = b"User-agent: *\nAllow: /\n", "text/plain"
        elif self.path == "/text":
            # Real static text: fetch_mode="auto" must NOT fall back here.
            body = ('<html><head></head><body><h1>HEADING</h1>'
                    '<p>Real paragraph text for markdown extraction test.</p>'
                    '</body></html>').encode()
            ct = "text/html; charset=utf-8"
        else:
            # /js and anything else: empty static body -> the Rust static core
            # returns non-text markdown -> fetch_mode="auto" falls back to the
            # browser. (A <script> would be extracted as text and defeat it.)
            body = b'<html><head></head><body><div id="app"></div></body></html>'
            ct = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="session")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module", autouse=True)
def _allow_private():
    old = os.environ.get(_ALLOW)
    os.environ[_ALLOW] = "1"  # S1 SSRF guard otherwise rejects loopback
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(_ALLOW, None)


@pytest.fixture
def browser_stub():
    """Replace the browser seam with a deterministic stub; track calls.

    Patching the module-level 3-tuple function (not ``tb._fetch._browser_fetch``)
    is what makes both the ``auto`` branch and ``_browser_fetch`` use it;
    a 4-tuple stub would break the auto branch's ``md, links, meta = ...``
    unpacking.
    """
    calls = []
    orig, avail = fetch._fetch_with_browser_oxide, fetch._browser_oxide_available

    def fake(url):
        calls.append(url)
        return ("BROWSER-CONTENT:" + url, [], {"fetch_method": "browser"})

    fetch._fetch_with_browser_oxide = fake
    fetch._browser_oxide_available = True
    try:
        yield calls
    finally:
        fetch._fetch_with_browser_oxide = orig
        fetch._browser_oxide_available = avail


def make(mode):
    return WebResearcherToolbox(config=ToolboxConfig(
        fetch_mode=mode, respect_robots=False,
        cache_dir=tempfile.mkdtemp(), domain_delay=0, fetch_delay=0, ddgs_delay=0))


# ───────────────────────────── stubbed tests ─────────────────────────

class TestInspectDispatchMatrix:
    @pytest.mark.parametrize("path,mode,expect", [
        ("/text", "auto", "static"),
        ("/text", "browser", "browser"),
        ("/text", "static", "static"),
        ("/js", "auto", "stealth-fallback"),   # non-text static -> browser
        ("/js", "browser", "browser"),
        ("/js", "static", "static"),
    ])
    def test_inspect(self, server, browser_stub, path, mode, expect):
        calls = browser_stub
        tb = make(mode)
        r = json.loads(tb.inspect_html_page(server + path))
        assert r["fetch_method"] == expect
        if expect == "stealth-fallback":
            assert r["markdown"].startswith("BROWSER-CONTENT")
        if expect == "static":
            assert calls == []


class TestCrawlDispatch:
    def test_crawl_js_auto_falls_back_to_browser(self, server, browser_stub):
        calls = browser_stub
        make("auto").focused_discovery(server + "/js", max_pages=1)
        assert calls and all(u.endswith("/js") for u in calls)

    def test_crawl_js_browser_uses_browser(self, server, browser_stub):
        calls = browser_stub
        make("browser").focused_discovery(server + "/js", max_pages=1)
        assert calls and all(u.endswith("/js") for u in calls)

    def test_crawl_js_static_never_browser(self, server, browser_stub):
        calls = browser_stub
        make("static").focused_discovery(server + "/js", max_pages=1)
        assert calls == []


class TestResearchDispatch:
    def _search_stub(self, url):
        return lambda *a, **k: json.dumps(
            [{"url": url, "title": "t", "snippet": "s"}])

    def test_research_auto_falls_back(self, server, browser_stub):
        calls = browser_stub
        url = server + "/js"
        tb = make("auto"); tb.search_web = self._search_stub(url)
        src = json.loads(tb.research("topic", depth=1))["sources"][0]
        assert src["result"]["fetch_method"] == "stealth-fallback"
        assert calls == [url]

    def test_research_static(self, server, browser_stub):
        calls = browser_stub
        url = server + "/js"
        tb = make("static"); tb.search_web = self._search_stub(url)
        src = json.loads(tb.research("topic", depth=1))["sources"][0]
        assert src["result"]["fetch_method"] == "static"
        assert calls == []


class TestBatchDispatch:
    def test_batch_auto_falls_back_to_browser_for_js_page(self, server, browser_stub):
        # M17: batch "auto" runs the Rust static engine first, then falls
        # back to the Python stealth-browser seam for any page the static
        # engine couldn't render (empty / JS / non-text body) -- mirroring
        # single-page auto. The seam is called once, for the JS page only.
        calls = browser_stub
        entry = json.loads(make("auto").batch_inspect_pages([server + "/js"]))[0]
        assert calls == [server + "/js"]
        assert entry["fetch_method"] == "stealth-fallback"

    def test_batch_auto_static_page_stays_static(self, server, browser_stub):
        # A page the static engine CAN render stays static -- no browser
        # call, matching single-page auto.
        calls = browser_stub
        entry = json.loads(make("auto").batch_inspect_pages([server + "/text"]))[0]
        assert calls == []
        assert entry["fetch_method"] == "static"

    def test_batch_auto_fallback_updates_cache(self, server, browser_stub):
        # After a batch auto fallback renders the JS page with the browser,
        # a later single-page inspect of the same URL returns the browser
        # result (not the stale static non-text body) -- the cache is
        # overwritten with the method that actually served the page.
        tb = make("auto")
        tb.batch_inspect_pages([server + "/js"])
        r = json.loads(tb.inspect_html_page(server + "/js"))
        assert r["fetch_method"] == "stealth-fallback"
        assert r["markdown"].startswith("BROWSER-CONTENT")

    def test_batch_static(self, server, browser_stub):
        calls = browser_stub
        entry = json.loads(make("static").batch_inspect_pages([server + "/js"]))[0]
        assert entry["fetch_method"] == "static"
        assert calls == []

    def test_batch_browser(self, server, browser_stub):
        calls = browser_stub
        entry = json.loads(make("browser").batch_inspect_pages([server + "/js"]))[0]
        assert entry["fetch_method"] == "browser"
        assert calls == [server + "/js"]


# ─────────────────────────── real browser_oxide ──────────────────────

@pytest.fixture(scope="session")
def real_browser():
    """True if the real stealth engine renders an external HTTPS page."""
    try:
        r = json.loads(make("browser").inspect_html_page("https://example.com"))
        return (r.get("fetch_method") == "browser"
                and "Example Domain" in (r.get("markdown") or ""))
    except Exception:
        return False


class TestRealBrowserExampleCom:
    def test_browser_mode_uses_real_engine(self, real_browser):
        if not real_browser:
            pytest.skip("real browser_oxide unavailable (no network / engine)")
        r = json.loads(make("browser").inspect_html_page("https://example.com"))
        assert r["fetch_method"] == "browser"
        assert "Example Domain" in r["markdown"]

    def test_auto_uses_static_for_static_page(self, real_browser):
        if not real_browser:
            pytest.skip("real browser_oxide unavailable (no network / engine)")
        # example.com ships real static HTML, so auto must not waste the
        # browser: it stays static.
        r = json.loads(make("auto").inspect_html_page("https://example.com"))
        assert r["fetch_method"] == "static"
