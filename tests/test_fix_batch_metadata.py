# tests/test_fix_batch_metadata.py
"""Bugfix 5 — a batch record must carry the same metadata as a single read.

``_batch_result`` was documented as producing "the same shape as a
single-page ``inspect_html_page`` result", and Tier 1.3 promised batch
entries carry the same provenance. They did not: the Rust ``batch_research``
ABI returned only ``(url, markdown, links)``, so there was no HTML left to
run the metadata extractor over and every batch record came back with an
empty ``metadata`` block — no title, no description, no canonical URL.

The engine now returns ``(url, html, markdown, links)`` and the consumer
extracts metadata from that HTML, so the two paths agree. The tests below
compare the two *delivered payloads* for the same URL rather than checking
either one in isolation, which is how the gap survived the existing suite.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox

PAGE = (
    "<html><head>"
    "<title>Tomato blight</title>"
    '<meta name="description" content="Treating tomato blight early.">'
    '<meta property="og:title" content="Tomato blight (OG)">'
    '<link rel="canonical" href="https://example.org/blight">'
    "</head><body><main>"
    "<h1>Tomato blight</h1><p>Copper fungicide applied early helps.</p>"
    '<a href="/next">Next</a>'
    "</main></body></html>"
).encode()


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _toolbox(tmp_path, name):
    # Separate cache dirs: a shared page cache would let the single-page
    # read populate metadata the batch path never derived itself.
    return WebResearcherToolbox(
        ToolboxConfig(
            cache_dir=str(tmp_path / name), fetch_delay=0.0, ddgs_delay=0.0
        )
    )


@pytest.fixture
def single(tmp_path, server, monkeypatch):
    monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
    tb = _toolbox(tmp_path, "single")
    return json.loads(tb.inspect_html_page(f"{server}/p"))


@pytest.fixture
def batched(tmp_path, server, monkeypatch):
    monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
    tb = _toolbox(tmp_path, "batch")
    return json.loads(tb.batch_inspect_pages([f"{server}/p"]))[0]


class TestEngineAbi:
    def test_batch_research_returns_html(self, server, monkeypatch):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        from stitch_web_researcher._core import batch_research

        (url, html, markdown, links) = batch_research([f"{server}/p"])[0]
        assert url.startswith(server)
        assert html and "<title>Tomato blight</title>" in html
        assert markdown and "Copper fungicide" in markdown
        assert isinstance(links, list)


class TestMetadataParity:
    def test_batch_metadata_is_not_empty(self, batched):
        assert batched["metadata"], "batch records used to ship empty metadata"

    def test_same_keys_as_single_page(self, single, batched):
        assert set(batched["metadata"]) == set(single["metadata"])

    def test_same_values_as_single_page(self, single, batched):
        assert batched["metadata"] == single["metadata"]

    @pytest.mark.parametrize("field", ["title", "description", "og_title"])
    def test_named_fields_survive(self, batched, field):
        assert batched["metadata"][field]


class TestFailuresAreUnaffected:
    def test_error_entry_has_no_metadata_claim(self, tmp_path, monkeypatch):
        # A failed URL carries no HTML; it must still produce a clean error
        # record rather than tripping the extractor.
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        tb = _toolbox(tmp_path, "fail")
        # Port 1 on loopback refuses connections.
        out = json.loads(tb.batch_inspect_pages(["http://127.0.0.1:1/x"]))
        assert len(out) == 1
        assert out[0]["error"]
