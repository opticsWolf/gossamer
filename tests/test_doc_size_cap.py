"""S3 for the document path: URL downloads and local files honor max_response_bytes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox


@pytest.fixture
def allow_private(monkeypatch):
    monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")


class _BigHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    BODY = b"%PDF-1.4 " + b"x" * (200 * 1024)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/small"):
            body = b"hello world"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/chunked"):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            for i in range(0, len(self.BODY), 8192):
                self.wfile.write(self.BODY[i : i + 8192])
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(self.BODY)))
        self.end_headers()
        self.wfile.write(self.BODY)


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BigHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _toolbox(tmp_path, **kwargs):
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **kwargs,
        )
    )


class TestDocumentSizeCap:
    def test_url_declared_size_rejected(self, tmp_path, server, allow_private):
        tb = _toolbox(tmp_path, max_response_bytes=1000)
        payload = json.loads(tb.extract_document(server + "/big.pdf"))
        assert "error" in payload and "too large" in payload["error"]

    def test_url_streaming_cap_without_length(self, tmp_path, server, allow_private):
        tb = _toolbox(tmp_path, max_response_bytes=1000)
        payload = json.loads(tb.extract_document(server + "/chunked"))
        assert "error" in payload and "too large" in payload["error"]

    def test_url_under_cap_still_flows(self, tmp_path, server, allow_private):
        tb = _toolbox(tmp_path, max_response_bytes=1000)
        payload = json.loads(tb.extract_document(server + "/small.txt"))
        assert payload.get("content", "").strip() == "hello world"

    def test_local_file_over_cap_rejected(self, tmp_path):
        big = tmp_path / "big.txt"
        big.write_bytes(b"y" * 5000)
        tb = _toolbox(tmp_path, max_response_bytes=1000)
        payload = json.loads(tb.extract_document(str(big)))
        assert "error" in payload and "too large" in payload["error"]

    def test_local_file_under_cap_extracts(self, tmp_path):
        small = tmp_path / "small.txt"
        small.write_text("hello world", encoding="utf-8")
        tb = _toolbox(tmp_path, max_response_bytes=1000)
        payload = json.loads(tb.extract_document(str(small)))
        assert payload.get("content", "").strip() == "hello world"
