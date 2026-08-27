# tests/test_m9_http_pool.py
"""M9 — the Rust core must reuse one HTTP client across fetches.

The review found that build_client() ran per request (and per batch
task), so every fetch paid TLS handshake + connection-pool setup. The
fix makes the client a process-wide OnceLock singleton, like the shared
Tokio runtime. These tests exercise the shared-client path end-to-end
against a local HTTP server and pin the singleton at the source level.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stitch_web_researcher._core import batch_research, fetch_html_full

_ALLOW_PRIVATE_ENV = "STITCH_WEB_RESEARCHER_ALLOW_PRIVATE"

_PAGE = (
    b"<!DOCTYPE html><html><head><title>Pooled page</title></head>"
    b"<body><article><h1>Pooled</h1><p>Content served by the M9 fixture."
    b"</p><a href=\"/other\">other page</a></article></body></html>"
)


class _LocalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # keep test output clean

    def do_GET(self):
        body = _PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def local_server():
    """Local HTTP server for the shared-client path (P9 pattern)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalHandler)
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


class TestSharedClientFetches:
    """The singleton client must serve sequential and batched fetches."""

    def test_repeated_fetch_html_full(self, local_server):
        """Three sequential fetches share one client and all succeed."""
        url = f"{local_server}/one"
        for _ in range(3):
            html, md, links, removed = fetch_html_full(url)
            assert isinstance(html, str) and "Pooled" in html
            assert isinstance(md, str) and "Pooled" in md
            assert any("/other" in href for href, _text in links)

    def test_fetch_html_full_across_hosts_paths(self, local_server):
        """Different paths on the same host reuse the pooled connection."""
        for path in ("/a", "/b", "/c"):
            _html, md, _links, _removed = fetch_html_full(f"{local_server}{path}")
            assert "Pooled" in md

    def test_batch_research_uses_shared_client(self, local_server):
        """Batch tasks share the same singleton client."""
        urls = [f"{local_server}/batch-1", f"{local_server}/batch-2"]
        results = batch_research(urls)
        assert len(results) == 2
        for url, md, links in results:
            assert "Pooled" in md
            assert isinstance(links, list)


class TestClientSingletonSource:
    """Source-level guard: the per-call builder is gone, the OnceLock
    singleton is present, and every fetch path goes through it."""

    @pytest.fixture(scope="class")
    def lib_source(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "src", "lib.rs"), encoding="utf-8") as f:
            return f.read()

    def test_once_lock_client_declared(self, lib_source):
        assert "static CLIENT: OnceLock<reqwest::Client>" in lib_source

    def test_per_call_builder_removed(self, lib_source):
        assert "fn build_client" not in lib_source

    def test_fetch_paths_use_shared_client(self, lib_source):
        # All three fetch entry points (single, full, batch) must call
        # the singleton rather than constructing clients.
        assert lib_source.count("shared_client()") >= 4  # def + 3 call sites
        assert "http_fetch_html(shared_client()" in lib_source
