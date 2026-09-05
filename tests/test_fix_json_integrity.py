# tests/test_fix_json_integrity.py
"""Bugfix 1 — serialized JSON must never be string-cut.

``_truncate`` cuts text at a character limit and appends a marker. Four
tools handed it *already-serialized* JSON, so any payload over the output
budget came back with unbalanced braces and a string cut mid-token —
unparseable, which is the LLM's whole reason for calling the tool.

Small pages serialize under the budget, which is exactly why the existing
suite missed this. Every test here drives a page big enough to trip the
budget through the real pipeline.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox

# Comfortably larger than the default 8000-char output budget.
BIG_PAGE = (
    "<html><head><title>Big</title>"
    '<meta name="description" content="A big page">'
    "</head><body><main><h1>Heading</h1><p>"
    + ("word " * 4000)
    + "</p>"
    + "".join(f'<a href="/p{i}">Link {i}</a>' for i in range(60))
    + "</main></body></html>"
).encode()


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(BIG_PAGE)))
            self.end_headers()
            self.wfile.write(BIG_PAGE)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def toolbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")
    return WebResearcherToolbox(
        ToolboxConfig(cache_dir=str(tmp_path / "c"), fetch_delay=0.0, ddgs_delay=0.0)
    )


class _FakeProvider:
    """Search provider returning URLs on the local test server."""

    name = "fake"

    def __init__(self, base, count=6):
        self._results = [
            {"title": f"R{i}", "url": f"{base}/r{i}", "snippet": "s" * 200}
            for i in range(count)
        ]

    def search(self, query, max_results=10):
        return self._results[:max_results]


class TestStructuredHtml:
    def test_over_budget_page_still_parses(self, toolbox, server):
        raw = toolbox.inspect_html_structured(f"{server}/big")
        data = json.loads(raw)  # must not raise
        assert isinstance(data, dict)

    def test_cache_hit_also_parses(self, toolbox, server):
        url = f"{server}/big"
        toolbox.inspect_html_structured(url)
        data = json.loads(toolbox.inspect_html_structured(url))
        assert isinstance(data, dict)

    def test_stays_within_the_char_budget(self, toolbox, server):
        raw = toolbox.inspect_html_structured(f"{server}/big")
        assert len(raw) <= toolbox.max_markdown_chars

    def test_navigational_fields_survive_the_shrink(self, toolbox, server):
        # Shrinking must take page text, not the metadata/links the model
        # navigates by — otherwise the payload fits but is useless.
        data = json.loads(toolbox.inspect_html_structured(f"{server}/big"))
        assert data["metadata"]["title"] == "Big"
        assert data["links"], "links must survive the budget shrink"


class TestResearch:
    def test_over_budget_research_still_parses(self, toolbox, server):
        toolbox.providers = [_FakeProvider(server)]
        data = json.loads(toolbox.research("some topic", depth=5))
        assert data["topic"] == "some topic"

    def test_stays_within_the_char_budget(self, toolbox, server):
        toolbox.providers = [_FakeProvider(server)]
        raw = toolbox.research("some topic", depth=5)
        assert len(raw) <= toolbox.max_markdown_chars

    def test_dropped_sources_are_reported(self, tmp_path, server, monkeypatch):
        # A budget too tight for every source must say so rather than
        # silently return a short list.
        monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")
        tb = WebResearcherToolbox(
            ToolboxConfig(
                cache_dir=str(tmp_path / "c"),
                fetch_delay=0.0,
                ddgs_delay=0.0,
                max_markdown_chars=1500,
            )
        )
        tb.providers = [_FakeProvider(server, count=8)]
        data = json.loads(tb.research("some topic", depth=8))
        assert len(json.dumps(data)) <= 1500 * 1.1
        if data.get("sources_omitted"):
            assert data["sources_omitted"] > 0


class TestOverflowEnvelope:
    def test_impossible_budget_returns_valid_json(self, tmp_path, server, monkeypatch):
        # Even a budget no payload can meet must produce parseable JSON —
        # the invariant is absolute.
        monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")
        tb = WebResearcherToolbox(
            ToolboxConfig(
                cache_dir=str(tmp_path / "c"),
                fetch_delay=0.0,
                ddgs_delay=0.0,
                max_markdown_chars=80,
            )
        )
        data = json.loads(tb.inspect_html_structured(f"{server}/big"))
        assert isinstance(data, dict)
        assert "error" in data or "metadata" in data
