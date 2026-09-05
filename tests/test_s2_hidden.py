"""S2 regression: hidden HTML must not reach the model verbatim.

Before the fix, elements hidden with ``display:none``, ``hidden``,
``aria-hidden="true"``, ``visibility:hidden``, ``noscript``,
``<template>``, or off-screen positioning were parsed straight into the
delivered markdown — the classic carrier for indirect prompt injection
against browsing agents. The Rust core now re-serializes the
main-content fragment, skipping hidden subtrees, and reports the number
of removed nodes as ``metadata["hidden_blocks_removed"]``.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gossamer import _core
from gossamer.agent_tools import WebResearcherToolbox

HIDDEN_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>S2 Test Page</title>
</head>
<body>
  <main>
    <h1>Visible headline</h1>
    <p>Visible paragraph text.</p>
    <div style="display:none">SECRET display none payload</div>
    <span hidden>SECRET hidden attribute payload</span>
    <div aria-hidden="true">SECRET aria hidden payload</div>
    <div style="visibility:hidden">SECRET visibility payload</div>
    <div style="left:-9999px">SECRET off-screen payload</div>
    <noscript>SECRET noscript payload</noscript>
    <template>SECRET template payload</template>
    <a href="/next">Next page</a>
  </main>
</body>
</html>
"""

# Same page with a spaced style value to cover "display: none" variants.
HIDDEN_PAGE_SPACED = HIDDEN_PAGE.replace("display:none", "display: none")

VISIBLE_ONLY = """<html><body><main><p>Just visible content.</p></main></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = HIDDEN_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture()
def http_server(monkeypatch):
    # S1 blocks 127.0.0.1 targets; use the operator bypass for the
    # local test server.
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


class TestProcessRenderedHtml:
    """The browser-path binding strips hidden subtrees (S2)."""

    def test_hidden_text_removed_from_markdown(self):
        md, links, removed = _core.process_rendered_html(
            HIDDEN_PAGE, "https://example.com/"
        )
        assert "Visible headline" in md
        assert "Visible paragraph text." in md
        # No hidden payload survives into the delivered markdown.
        assert "SECRET" not in md
        # Seven hidden elements in the fixture.
        assert removed >= 6

    def test_spaced_style_variant_removed(self):
        md, _links, removed = _core.process_rendered_html(
            HIDDEN_PAGE_SPACED, "https://example.com/"
        )
        assert "SECRET" not in md
        assert removed >= 6

    def test_visible_only_page_reports_zero(self):
        md, _links, removed = _core.process_rendered_html(
            VISIBLE_ONLY, "https://example.com/"
        )
        assert "Just visible content." in md
        assert removed == 0

    def test_links_still_extracted(self):
        _md, links, _removed = _core.process_rendered_html(
            HIDDEN_PAGE, "https://example.com/"
        )
        assert "https://example.com/next" in links


class TestFetchHtmlFull:
    """The static-path binding returns the 5-tuple with provenance."""

    def test_five_tuple_and_hidden_stripped(self, http_server):
        html, md, links, removed, prov = _core.fetch_html_full(http_server + "/", 20)
        # Tier 1.3: provenance is (status, final_url, content_type).
        assert isinstance(prov, tuple) and len(prov) == 3
        assert prov[0] == 200
        assert prov[1] == http_server + "/"
        # Raw HTML is intact (metadata extraction still sees it)...
        assert "SECRET" in html
        # ...but the delivered markdown is clean.
        assert "SECRET" not in md
        assert "Visible headline" in md
        assert removed >= 6
        assert any(u.endswith("/next") for u, _text in links)


class TestExtractMainContentMarkdown:
    """The selector-label binding keeps its 2-tuple shape (C2 tests rely
    on it) and also strips hidden nodes from the returned fragment."""

    def test_two_tuple_and_hidden_stripped(self):
        label, md = _core.extract_main_content_markdown(HIDDEN_PAGE)
        assert label == "main"
        assert "Visible headline" in md
        assert "SECRET" not in md


class TestToolboxIntegration:
    """End-to-end: inspect_html_page hides hidden text and surfaces the
    counter in metadata."""

    def test_inspect_page_strips_hidden_and_reports_counter(
        self, tmp_path, http_server
    ):
        tb = _toolbox(tmp_path)
        result = json.loads(tb.inspect_html_page(http_server + "/"))
        assert "SECRET" not in result["markdown"]
        assert "Visible headline" in result["markdown"]
        assert result["metadata"].get("hidden_blocks_removed", 0) >= 6

    def test_cached_page_keeps_counter(self, tmp_path, http_server):
        tb = _toolbox(tmp_path)
        tb.inspect_html_page(http_server + "/")
        second = json.loads(tb.inspect_html_page(http_server + "/"))
        assert second["cache_hit"] is True
        assert "SECRET" not in second["markdown"]
        assert second["metadata"].get("hidden_blocks_removed", 0) >= 6
