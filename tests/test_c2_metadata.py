"""C2 regression: the default (static) fetch path must return real metadata.

Before the fix, ``_static_fetch`` returned a hardcoded ``{}`` for metadata
(the Rust core only returned markdown + links), so ``inspect_html_page``
— the workhorse tool — almost always reported empty metadata, while the
browser path (opt-in) extracted it fine via meta-oxide. The Rust core now
has a ``fetch_html_full`` binding that keeps the raw HTML, and the static
path runs the same ``meta_extractor.extract_all`` on it.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from stitch_web_researcher import agent_tools
from stitch_web_researcher.agent_tools import WebResearcherToolbox

PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>C2 Test Page</title>
  <meta name="description" content="A page used to verify static-path metadata.">
  <link rel="canonical" href="https://example.com/c2-page">
  <meta property="og:title" content="OG Title C2">
  <meta property="og:type" content="article">
  <meta name="twitter:card" content="summary">
</head>
<body>
  <article><h1>Hello C2</h1><p>Content body for the static path.</p></article>
  <a href="/next">Next page</a>
</body>
</html>
"""

BARE_HTML = "<html><body><p>no metadata here</p></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/bare"):
            body = BARE_HTML.encode("utf-8")
        else:
            body = PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture()
def http_server():
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


class TestStaticPathMetadata:
    def test_inspect_page_returns_metadata_on_static_path(self, tmp_path, http_server):
        tb = _toolbox(tmp_path)
        result = json.loads(tb.inspect_html_page(http_server + "/page"))

        assert result["fetch_method"] == "static"
        assert result["cache_hit"] is False
        assert "Hello C2" in result["markdown"]

        meta = result["metadata"]
        assert meta, "static path must no longer return empty metadata"
        assert meta.get("title") == "C2 Test Page"
        assert meta.get("description") == (
            "A page used to verify static-path metadata."
        )
        assert meta.get("og_title") == "OG Title C2"
        assert meta.get("og_type") == "article"
        assert meta.get("twitter_card") == "summary"
        assert meta.get("canonical") == "https://example.com/c2-page"

    def test_cached_page_serves_metadata_too(self, tmp_path, http_server):
        tb = _toolbox(tmp_path)
        url = http_server + "/page"
        first = json.loads(tb.inspect_html_page(url))
        assert first["cache_hit"] is False

        second = json.loads(tb.inspect_html_page(url))
        assert second["cache_hit"] is True
        assert second["metadata"].get("title") == "C2 Test Page"

    def test_static_fetch_shape(self, tmp_path, http_server):
        tb = _toolbox(tmp_path)
        md, links, meta, method = tb._static_fetch(http_server + "/page")
        assert method == "static"
        assert "Hello C2" in md
        assert any(u.endswith("/next") and text == "Next page" for u, text in links)
        assert meta.get("meta", {}).get("title") == "C2 Test Page"

    def test_bare_page_metadata_is_empty_but_valid(self, tmp_path, http_server):
        """A page with no metadata tags still works (no crash, empty dict)."""
        tb = _toolbox(tmp_path)
        result = json.loads(tb.inspect_html_page(http_server + "/bare"))
        assert result["fetch_method"] == "static"
        assert result["markdown"].strip()
        assert result["metadata"] == {}


class TestFetchSmartPageFallback:
    def test_fallback_extracts_metadata(self, tmp_path, http_server, monkeypatch):
        """With browser_oxide 'unavailable', fetch_smart_page falls back to
        the static path and must still return metadata (C2)."""
        monkeypatch.setattr(agent_tools, "_browser_oxide_available", False)
        md, links, meta = agent_tools.fetch_smart_page(http_server + "/page")

        assert "Hello C2" in md
        assert meta.get("meta", {}).get("title") == "C2 Test Page"
        assert meta.get("opengraph", {}).get("title") == "OG Title C2"
        assert any(u.endswith("/next") for u, _ in links)

    def test_browser_path_still_works_with_metadata(self, tmp_path, http_server):
        """The browser path's metadata behavior is unchanged."""
        with patch(
            "stitch_web_researcher.agent_tools._fetch_with_browser_oxide",
            return_value=("md", [], {"meta": {"title": "browser title"}}),
        ):
            md, links, meta = agent_tools.fetch_smart_page("https://example.com")
        assert md == "md"
        assert meta["meta"]["title"] == "browser title"
