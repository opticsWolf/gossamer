"""S4: robots.txt compliance (Disallow/Allow, Crawl-delay, opt-out).

Integration tests run a local ThreadingHTTPServer that serves a
robots.txt; parser-level tests use an injected fake HTTP client.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.robots import RobotsChecker

ROBOTS_TXT = (
    "User-agent: *\n"
    "Disallow: /private/\n"
    "Allow: /private/public.txt\n"
)

CRAWL_DELAY_ROBOTS = (
    "User-agent: *\n"
    "Crawl-delay: 1\n"
)

_PAGES = {
    "/open": b"<html><body><h1>Open page</h1></body></html>",
    "/private/secret": b"<html><body><h1>Secret page</h1></body></html>",
    "/private/public.txt": b"<html><body><h1>Public doc</h1></body></html>",
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        srv = self.server
        with srv.lock:
            srv.times.setdefault(path, []).append(time.monotonic())
        if path == "/robots.txt":
            if srv.robots_404:
                self._send(404, b"")
                return
            self._send(200, srv.robots.encode(), "text/plain")
            return
        if path in _PAGES:
            self._send(200, _PAGES[path])
            return
        self._send(404, b"")

    def _send(self, code: int, body: bytes, ctype: str = "text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server(robots: str, robots_404: bool = False) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.lock = threading.Lock()
    srv.times = {}
    srv.robots = robots
    srv.robots_404 = robots_404
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def _url(server: ThreadingHTTPServer, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _times(server: ThreadingHTTPServer, path: str) -> list:
    with server.lock:
        return list(server.times.get(path, []))


def _toolbox(server, tmp_path, respect_robots: bool = True) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        ToolboxConfig(
            cache_dir=str(tmp_path / "cache"),
            domain_delay=0.0,
            fetch_delay=0.0,  # explicit 0 beats the provider default
            ddgs_delay=0.0,
            fetch_mode="static",
            respect_robots=respect_robots,
        )
    )


@pytest.fixture()
def server():
    srv = _start_server(ROBOTS_TXT)
    yield srv
    srv.shutdown()
    srv.server_close()


class TestToolboxRobotsGate:
    def test_disallowed_path_returns_warning_without_fetch(
        self, server, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        tb = _toolbox(server, tmp_path)
        url = _url(server, "/private/secret")
        res = json.loads(tb.inspect_html_page(url))
        assert "robots" in res.get("warning", "")
        assert res.get("url") == url
        # The page itself was never fetched; only robots.txt was.
        assert _times(server, "/private/secret") == []
        assert len(_times(server, "/robots.txt")) >= 1

    def test_allowed_path_fetches(self, server, tmp_path, monkeypatch):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        tb = _toolbox(server, tmp_path)
        res = json.loads(tb.inspect_html_page(_url(server, "/open")))
        assert "warning" not in res
        assert "Open page" in res.get("markdown", "")

    def test_longer_allow_rule_overrides_disallow(
        self, server, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        tb = _toolbox(server, tmp_path)
        res = json.loads(tb.inspect_html_page(_url(server, "/private/public.txt")))
        assert "warning" not in res
        assert "Public doc" in res.get("markdown", "")

    def test_opt_out_fetches_disallowed(self, server, tmp_path, monkeypatch):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        tb = _toolbox(server, tmp_path, respect_robots=False)
        res = json.loads(tb.inspect_html_page(_url(server, "/private/secret")))
        assert "warning" not in res
        assert "Secret page" in res.get("markdown", "")

    def test_batch_skips_disallowed(self, server, tmp_path, monkeypatch):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        tb = _toolbox(server, tmp_path)
        res = json.loads(
            tb.batch_inspect_pages(
                [_url(server, "/open"), _url(server, "/private/secret")]
            )
        )
        assert [entry.get("url") for entry in res] == [_url(server, "/open")]
        assert _times(server, "/private/secret") == []

    def test_structured_disallowed(self, server, tmp_path, monkeypatch):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        tb = _toolbox(server, tmp_path)
        res = json.loads(
            tb.inspect_html_structured(_url(server, "/private/secret"))
        )
        assert "robots" in res.get("warning", "")
        assert _times(server, "/private/secret") == []

    def test_robots_404_allows(self, tmp_path, monkeypatch):
        srv = _start_server("", robots_404=True)
        try:
            monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
            tb = _toolbox(srv, tmp_path)
            res = json.loads(tb.inspect_html_page(_url(srv, "/open")))
            assert "warning" not in res
            assert "Open page" in res.get("markdown", "")
        finally:
            srv.shutdown()
            srv.server_close()


class TestCrawlDelay:
    def test_crawl_delay_enforced_between_fetches(self, tmp_path, monkeypatch):
        srv = _start_server(CRAWL_DELAY_ROBOTS)
        try:
            monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
            tb = _toolbox(srv, tmp_path)
            # Two different allowed pages so the second is a real fetch,
            # not a page-cache hit (C3).
            tb.inspect_html_page(_url(srv, "/open"))
            tb.inspect_html_page(_url(srv, "/private/public.txt"))
            first = _times(srv, "/open")[0]
            second = _times(srv, "/private/public.txt")[0]
            gap = second - first
            assert gap >= 0.9, f"Crawl-delay not honored (gap={gap:.2f}s)"
            assert gap <= 3.0, f"gap far larger than Crawl-delay ({gap:.2f}s)"
        finally:
            srv.shutdown()
            srv.server_close()


class _FakeResp:
    def __init__(self, status: int, content: bytes):
        self.status_code = status
        self.content = content


class _FakeClient:
    def __init__(self, text: str = "", status: int = 200, raise_exc: bool = False):
        self._text = text
        self._status = status
        self._raise = raise_exc

    def get(self, url, headers=None):
        if self._raise:
            raise OSError("simulated network failure")
        return _FakeResp(self._status, self._text.encode())


class TestRobotsParser:
    def _checker(self, text, ua="Mozilla/5.0 TestBot/1.0", **kw) -> RobotsChecker:
        return RobotsChecker(
            enabled=True, user_agent=ua, client=_FakeClient(text, **kw)
        )

    def test_disallow_prefix(self):
        c = self._checker("User-agent: *\nDisallow: /files/\n")
        assert not c.is_allowed("http://example.com/files/a.txt")
        assert c.is_allowed("http://example.com/other")

    def test_allow_longest_match_wins(self):
        c = self._checker(
            "User-agent: *\nDisallow: /files/\nAllow: /files/public.txt\n"
        )
        assert c.is_allowed("http://example.com/files/public.txt")
        assert not c.is_allowed("http://example.com/files/other.txt")

    def test_wildcard_and_anchor(self):
        c = self._checker(
            "User-agent: *\nDisallow: /se*et\nDisallow: /anchored/$\n"
        )
        # Wildcard: any run of characters between /se and et.
        assert not c.is_allowed("http://example.com/secret")
        assert not c.is_allowed("http://example.com/seXXet")
        # Rules are anchored to the start of the path.
        assert c.is_allowed("http://example.com/xsecret")
        assert c.is_allowed("http://example.com/sec")  # no trailing 'et'
        # Trailing $ anchors the end of the path: exact match only.
        assert not c.is_allowed("http://example.com/anchored/")
        assert c.is_allowed("http://example.com/anchored")
        assert c.is_allowed("http://example.com/anchored/page")

    def test_ua_specific_group_beats_star(self):
        text = (
            "User-agent: TestBot\nAllow: /x/\n"
            "User-agent: *\nDisallow: /x/\n"
        )
        assert self._checker(text, ua="TestBot/1.0").is_allowed(
            "http://example.com/x/y"
        )
        assert not self._checker(text, ua="OtherBot/2.0").is_allowed(
            "http://example.com/x/y"
        )

    def test_crawl_delay_falls_back_to_star_group(self):
        text = "User-agent: TestBot\nUser-agent: *\nCrawl-delay: 2.5\n"
        assert self._checker(text, ua="TestBot/1.0").crawl_delay(
            "http://example.com/"
        ) == 2.5

    def test_403_disallows_all(self):
        c = self._checker("ignored", status=403)
        assert not c.is_allowed("http://example.com/anything")
        assert c.crawl_delay("http://example.com/") is None

    def test_fetch_error_allows(self):
        c = self._checker("ignored", raise_exc=True)
        assert c.is_allowed("http://example.com/anything")

    def test_disabled_checker_allows(self):
        c = RobotsChecker(enabled=False)
        assert c.is_allowed("http://example.com/anything")
        assert c.crawl_delay("http://example.com/") is None

    def test_query_string_is_matched(self):
        c = self._checker("User-agent: *\nDisallow: /dl?token=\n")
        assert not c.is_allowed("http://example.com/dl?token=abc")
        assert c.is_allowed("http://example.com/dl")
