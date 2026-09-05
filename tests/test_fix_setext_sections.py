# tests/test_fix_setext_sections.py
"""Bugfix 2 — section selection must see the headings the pipeline emits.

``_HEADING_RE`` matched ATX only, on the stated assumption that "the Rust
html2md converter emits ATX". It does not: h1/h2 come out as Setext and
only h3+ as (closed) ATX. So on a real page the splitter found no
headings, the document collapsed into one ``(intro)`` section, and BM25
had nothing to choose between — Tier 1.1 silently did nothing on exactly
the pages it exists for.

Every test here goes through the real converter rather than hand-written
ATX fixtures, which is what let the defect through.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gossamer import _core
from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.sections import (
    select_relevant_sections,
    split_sections,
)

HTML = (
    "<html><body><main>"
    "<h1>Alpha</h1><p>aaa about tomatoes</p>"
    "<h2>Bravo</h2><p>bbb copper fungicide details</p>"
    "<h3>Charlie</h3><p>ccc</p>"
    "</main></body></html>"
)


def _converter_markdown(html=HTML):
    markdown, _links, _hidden = _core.process_rendered_html(html, "https://e.com")
    return markdown


class TestRealConverterOutput:
    def test_converter_still_emits_setext(self):
        # If this ever fails the converter changed; the regex below should
        # be revisited rather than the assumption re-hardcoded.
        md = _converter_markdown()
        assert "Alpha\n=" in md
        assert "Bravo\n-" in md

    def test_all_heading_levels_are_found(self):
        anchors = [s.anchor for s in split_sections(_converter_markdown())]
        assert anchors == ["Alpha", "Bravo", "Charlie"]

    def test_closing_hashes_do_not_leak_into_the_anchor(self):
        # html2md emits "### Charlie ###"; the anchor must be "Charlie".
        anchors = [s.anchor for s in split_sections(_converter_markdown())]
        assert "Charlie" in anchors
        assert not any("#" in a for a in anchors)

    def test_relevant_section_survives_selection(self):
        md = _converter_markdown()
        sel = select_relevant_sections(md, "copper fungicide", 60)
        assert sel.total_sections == 3
        assert "copper fungicide" in sel.markdown


class TestFalsePositiveGuards:
    @pytest.mark.parametrize(
        "markdown",
        [
            "text\n\n---\n\nmore",  # thematic break
            "- item\n----------\n",  # list item above a rule
            "| a | b |\n|---|---|\n| 1 | 2 |",  # table separator
            "para\n\n\n=\n",  # single-char underline
        ],
    )
    def test_rules_and_tables_are_not_headings(self, markdown):
        anchors = [s.anchor for s in split_sections(markdown)]
        assert anchors in ([], ["(intro)"]), anchors


class TestEndToEnd:
    @pytest.fixture
    def server(self):
        body = (
            "<html><head><title>T</title></head><body><main>"
            "<h1>Introduction</h1><p>" + ("filler " * 900) + "</p>"
            "<h2>Tomato blight treatment</h2>"
            "<p>Copper fungicide applied early stops tomato blight. "
            + ("detail " * 40)
            + "</p>"
            "<h2>Unrelated appendix</h2><p>" + ("filler " * 900) + "</p>"
            "</main></body></html>"
        ).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_address[1]}"
        srv.shutdown()

    def test_query_keeps_the_relevant_section_off_a_real_page(
        self, tmp_path, server, monkeypatch
    ):
        monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")
        tb = WebResearcherToolbox(
            ToolboxConfig(
                cache_dir=str(tmp_path / "c"), fetch_delay=0.0, ddgs_delay=0.0
            )
        )
        data = json.loads(
            tb.inspect_html_page(f"{server}/p", query="tomato blight treatment")
        )
        assert data["sections_available"] >= 3
        assert 0 < data["sections_selected"] < data["sections_available"]
        # The section that answers the query must survive head-first cutting.
        assert "Copper fungicide" in data["markdown"]
