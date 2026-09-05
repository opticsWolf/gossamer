# tests/test_m15_retry_after.py
"""M15 — 429/503 must be retryable and Retry-After honored.

The review found fetch_attempt only retried on status >= 500, so rate
limits — the exact case where backing off is most valuable — failed
immediately and any Retry-After header was ignored. The fix makes
429/503 retryable, parses the numeric Retry-After header (capped), and
extends the exponential backoff to at least the server-requested delay.
"""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gossamer._core import fetch_html_full

_ALLOW_PRIVATE_ENV = "GOSSAMER_ALLOW_PRIVATE"

_RECOVERED = (
    b"<!DOCTYPE html><html><head><title>Recovered</title></head>"
    b"<body><p>Recovered content after backoff.</p></body></html>"
)


class _FlakyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: dict = {}
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_GET(self):
        with self.lock:
            self.hits[self.path] = self.hits.get(self.path, 0) + 1
            n = self.hits[self.path]
        if self.path == "/flaky-429" and n == 1:
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/flaky-503" and n == 1:
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path.startswith("/flaky"):
            self._send_200()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _send_200(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_RECOVERED)))
        self.end_headers()
        self.wfile.write(_RECOVERED)


@pytest.fixture(scope="module")
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module", autouse=True)
def _allow_local_origin():
    """The S1 SSRF guard blocks loopback by default; its documented
    escape hatch lets these tests hit the local server."""
    old = os.environ.get(_ALLOW_PRIVATE_ENV)
    os.environ[_ALLOW_PRIVATE_ENV] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(_ALLOW_PRIVATE_ENV, None)
        else:
            os.environ[_ALLOW_PRIVATE_ENV] = old


class TestRateLimitRetries:
    def test_429_is_retried_and_retry_after_honored(self, local_server):
        url = f"{local_server}/flaky-429"
        start = time.monotonic()
        _html, md, _links, _removed, _prov = fetch_html_full(url)
        elapsed = time.monotonic() - start
        assert "Recovered" in md
        assert _FlakyHandler.hits["/flaky-429"] == 2
        # Retry-After: 1 must have extended the 500ms first backoff.
        assert elapsed >= 0.9

    def test_503_is_retried(self, local_server):
        url = f"{local_server}/flaky-503"
        _html, md, _links, _removed, _prov = fetch_html_full(url)
        assert "Recovered" in md
        assert _FlakyHandler.hits["/flaky-503"] == 2

    def test_404_is_not_retried(self, local_server):
        with pytest.raises(Exception, match="404"):
            fetch_html_full(f"{local_server}/missing")
        assert _FlakyHandler.hits["/missing"] == 1


class TestRetryAfterSource:
    """Source-level guard: the 429/503 branch and Retry-After parsing
    must stay in the Rust retry path."""

    @pytest.fixture(scope="class")
    def lib_source(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "src", "lib.rs"), encoding="utf-8") as f:
            return f.read()

    def test_429_and_503_are_retryable(self, lib_source):
        assert "code == 429 || code == 503 || code >= 500" in lib_source

    def test_retry_after_parsed_and_used(self, lib_source):
        assert "fn retry_after_seconds" in lib_source
        assert "RETRY_AFTER" in lib_source
        assert "delay.max(Duration::from_secs(secs))" in lib_source

    def test_retry_after_is_capped(self, lib_source):
        # A hostile Retry-After must not stall the retry loop forever.
        assert "CAP_SECS: u64 = 60" in lib_source
