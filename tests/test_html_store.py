"""HTML resource store: ``inspect_html_page(url, store_dir=...)`` persists the
full page markdown plus its images to a ``<stem>.md`` / ``<stem>.files/`` pair.

The inspection markdown keeps ``![alt](url)`` image refs (already absolutized
by ``_absolutize_markdown_links``), so the store downloads each ref and
rewrites the body to local relative paths, leaving the store self-contained.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gossamer.agent_tools import WebResearcherToolbox

# Minimal PNG-prefixed bytes: the store only validates the magic header
# (and content-type), not that the PNG decodes. Distinct per path so the
# two image refs do not collapse through content-hash dedup.
def _png(tag: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag + bytes(range(32))

PAGE_HTML = """<!DOCTYPE html>
<html><head><title>Store Test</title></head>
<body>
<h1>Store Test Page</h1>
<p>Body text with an inline image.</p>
<img src="/assets/logo.png">
<p>Inline ref: ![inline](/img/inline.png) here.</p>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith(".png"):
            tag = b"LOGO" if "logo" in self.path else b"INLINE"
            body = _png(tag)
            ctype = "image/png"
        else:
            body = PAGE_HTML.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture()
def http_server(monkeypatch):
    monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _toolbox(tmp_path):
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"), domain_delay=0.0, ddgs_delay=0.0
    )


class TestHtmlStore:
    def test_store_writes_md_and_images(self, tmp_path, http_server):
        tb = _toolbox(tmp_path)
        out = tmp_path / "store"
        result = json.loads(
            tb.inspect_html_page(http_server + "/page", store_dir=str(out))
        )
        assert result["stored"] is True
        md_path = tmp_path / "store" / "page.md"
        assert md_path.exists()
        # Both image refs downloaded and rewritten to local relative paths.
        md = md_path.read_text(encoding="utf-8")
        assert "127.0.0.1" not in md  # no host refs remain
        assert "./page.files/" in md
        assert result["resources"]["referenced"] == 2
        files_dir = tmp_path / "store" / "page.files"
        assert files_dir.is_dir()
        assert len(list(files_dir.glob("*.png"))) == 2

    def test_store_creates_stem_from_url(self, tmp_path, http_server):
        tb = _toolbox(tmp_path)
        out = tmp_path / "store"
        tb.inspect_html_page(http_server + "/articles/hello-world", store_dir=str(out))
        # Stem derived from the last path segment, sanitized.
        assert (out / "hello-world.md").exists()

    def test_store_without_dir_returns_markdown(self, tmp_path, http_server):
        tb = _toolbox(tmp_path)
        result = json.loads(tb.inspect_html_page(http_server + "/page"))
        # Default path returns the markdown payload, not a manifest.
        assert result.get("stored") is None
        assert "content" in result or "markdown" in result
