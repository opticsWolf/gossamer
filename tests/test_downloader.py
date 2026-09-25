"""Standalone download tests using a local HTTP server, never the network."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import pytest

from gossamer.agent_tools import WebResearcherToolbox
from gossamer.config import ToolboxConfig
from gossamer.ssrf import SsrfBlockedError

_PDF = b"%PDF-1.7\nexample pdf bytes\n%%EOF\n"
_ROUTES = {
    "/paper.pdf": (200, {"Content-Type": "application/pdf"}, _PDF),
    "/small.pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF-1"),
    "/large.pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF-" + b"x" * 100),
    "/wrong.pdf": (200, {"Content-Type": "application/octet-stream"}, b"not a pdf"),
    "/challenge.pdf": (
        200,
        {"Content-Type": "text/html; charset=utf-8"},
        b"<html><title>Just a moment</title>Cloudflare challenge</html>",
    ),
    "/html.pdf": (
        200,
        {"Content-Type": "text/html"},
        b"<html><body>ordinary HTML response</body></html>",
    ),
    "/redirect.pdf": (302, {"Location": "/paper.pdf"}, b""),
    "/redirect-private.pdf": (302, {"Location": "/private.pdf"}, b""),
    "/private.pdf": (200, {"Content-Type": "application/pdf"}, _PDF),
    "/missing.pdf": (404, {"Content-Type": "text/plain"}, b"not found"),
    "/partial.pdf": (206, {"Content-Type": "application/pdf"}, _PDF[:12]),
}


@pytest.fixture
def file_server():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            hits.append(path)
            status, headers, body = _ROUTES.get(
                path, (404, {"Content-Type": "text/plain"}, b"not found"),
            )
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                try:
                    self.wfile.write(body)
                except OSError:
                    # The downloader may reject a size-limited response as
                    # soon as its declared length is known.
                    pass

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _toolbox(monkeypatch, tmp_path, *, max_bytes=48):
    tb = WebResearcherToolbox(ToolboxConfig(
        cache_dir=str(tmp_path / "cache"),
        max_response_bytes=max_bytes,
        domain_delay=0,
        respect_robots=True,
    ))
    checks = []
    monkeypatch.setattr(tb, "_validate_url", lambda url: checks.append(url))
    monkeypatch.setattr(tb, "_robots_disallows", lambda _url: False)
    monkeypatch.setattr(tb, "_rate_limit_domain", lambda _url: None)
    return tb, checks


def _download(tb, source, output, **kwargs):
    return json.loads(tb.download_file(source, str(output), **kwargs))


def test_download_streams_pdf_atomically_and_tracks_redirect(file_server, monkeypatch, tmp_path):
    base, hits = file_server
    tb, validated = _toolbox(monkeypatch, tmp_path)
    output = tmp_path / "papers" / "paper.pdf"

    result = _download(tb, f"{base}/redirect.pdf", output)

    assert result["status"] == "downloaded"
    assert result["source"] == f"{base}/redirect.pdf"
    assert result["final_url"] == f"{base}/paper.pdf"
    assert result["bytes"] == len(_PDF)
    assert output.read_bytes() == _PDF
    assert result["content_type"].startswith("application/pdf")
    assert hits == ["/redirect.pdf", "/paper.pdf"]
    assert validated == [f"{base}/redirect.pdf", f"{base}/redirect.pdf", f"{base}/paper.pdf"]
    assert list(output.parent.glob("*.part")) == []
    assert list(output.parent.glob(".*.part")) == []


def test_download_does_not_overwrite_without_explicit_permission(file_server, monkeypatch, tmp_path):
    base, hits = file_server
    tb, _ = _toolbox(monkeypatch, tmp_path)
    output = tmp_path / "existing.pdf"
    output.write_bytes(b"keep me")

    result = _download(tb, f"{base}/paper.pdf", output)

    assert result["error"]["code"] == "file_exists"
    assert output.read_bytes() == b"keep me"
    assert hits == []

    result = _download(tb, f"{base}/paper.pdf", output, overwrite=True)
    assert result["status"] == "downloaded"
    assert output.read_bytes() == _PDF


def test_download_classifies_http_and_bot_wall_errors(file_server, monkeypatch, tmp_path):
    base, _ = file_server
    tb, _ = _toolbox(monkeypatch, tmp_path, max_bytes=512)

    missing = _download(tb, f"{base}/missing.pdf", tmp_path / "missing.pdf")
    challenge = _download(tb, f"{base}/challenge.pdf", tmp_path / "challenge.pdf")

    assert missing["error"]["code"] == "not_found"
    assert missing["error"]["http_status"] == 404
    assert challenge["error"]["code"] == "bot_wall"
    assert not (tmp_path / "missing.pdf").exists()
    assert not (tmp_path / "challenge.pdf").exists()


def test_download_validates_minimum_size_and_pdf_magic(file_server, monkeypatch, tmp_path):
    base, _ = file_server
    tb, _ = _toolbox(monkeypatch, tmp_path)

    tiny = _download(
        tb, f"{base}/small.pdf", tmp_path / "tiny.pdf", min_bytes=20,
    )
    invalid = _download(
        tb, f"{base}/wrong.pdf", tmp_path / "invalid.pdf", expected_format="pdf",
    )
    html = _download(
        tb, f"{base}/html.pdf", tmp_path / "html.pdf", expected_format="pdf",
    )

    assert tiny["error"]["code"] == "too_small"
    assert invalid["error"]["code"] == "invalid_file"
    assert html["error"]["code"] == "unexpected_content"
    assert not (tmp_path / "tiny.pdf").exists()
    assert not (tmp_path / "invalid.pdf").exists()
    assert not (tmp_path / "html.pdf").exists()


def test_download_rejects_partial_response_without_resume(file_server, monkeypatch, tmp_path):
    base, _ = file_server
    tb, _ = _toolbox(monkeypatch, tmp_path, max_bytes=512)

    result = _download(tb, f"{base}/partial.pdf", tmp_path / "partial.pdf")

    assert result["error"]["code"] == "partial_response"
    assert result["error"]["http_status"] == 206
    assert not (tmp_path / "partial.pdf").exists()


def test_download_enforces_configured_and_per_call_size_caps(file_server, monkeypatch, tmp_path):
    base, _ = file_server
    tb, _ = _toolbox(monkeypatch, tmp_path, max_bytes=64)

    configured = _download(tb, f"{base}/large.pdf", tmp_path / "configured.pdf")
    per_call = _download(
        tb, f"{base}/paper.pdf", tmp_path / "per-call.pdf", max_bytes=8,
    )

    assert configured["error"]["code"] == "too_large"
    assert configured["error"]["http_status"] == 200
    assert per_call["error"]["code"] == "too_large"
    assert not (tmp_path / "configured.pdf").exists()
    assert not (tmp_path / "per-call.pdf").exists()


def test_download_revalidates_redirect_target_and_robots(file_server, monkeypatch, tmp_path):
    base, hits = file_server
    tb, validated = _toolbox(monkeypatch, tmp_path)

    def reject_private(url):
        validated.append(url)
        if url.endswith("/private.pdf"):
            raise SsrfBlockedError("private address")

    monkeypatch.setattr(tb, "_validate_url", reject_private)
    rejected = _download(
        tb, f"{base}/redirect-private.pdf", tmp_path / "private.pdf",
    )
    assert rejected["error"]["code"] == "url_rejected"
    assert hits == ["/redirect-private.pdf"]
    assert validated[-1] == f"{base}/private.pdf"

    monkeypatch.setattr(tb, "_validate_url", lambda _url: None)
    monkeypatch.setattr(tb, "_robots_disallows", lambda url: url.endswith("/paper.pdf"))
    blocked = _download(tb, f"{base}/redirect.pdf", tmp_path / "robots.pdf")
    assert blocked["error"]["code"] == "robots_disallowed"
    assert hits[-1] == "/redirect.pdf"
    assert not (tmp_path / "robots.pdf").exists()
