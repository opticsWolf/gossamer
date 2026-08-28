"""
Tier 3.11 — HTML table extraction (review item 11).

``ExtractedTable`` existed but ``parse_html`` never populated it, so web
tables reached the model as ragged markdown. This wires real table
extraction into ``inspect_html_structured``:

* Rust ``extract_tables_from_html`` parses top-level ``<table>`` grids
  from the raw HTML (colspan/rowspan expanded into rectangular grids,
  header row detected via ``<th>``).
* ``_fetch_html_with_html`` is the 5-tuple fetch seam for the structured
  path (the M8 4-tuple ``_fetch_html`` is untouched).
* ``parse_html(tables=...)`` attaches the tables to both the payload and
  the single page, like the PDF/Office paths do.

Fully offline: the Rust extractor is tested directly on inline HTML, and
the tool wiring is tested with fetches spied.
"""

import json

from stitch_web_researcher._core import extract_tables_from_html
from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.structured_parser import (
    ExtractedTable,
    StructuredOxideParser,
)

URL = "https://example.com/tables"


def _toolbox(tmp_path, fetch_mode="static") -> WebResearcherToolbox:
    tb = WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            fetch_mode=fetch_mode,
            cache_dir=str(tmp_path / "cache"),
        )
    )
    tb._fetch_interval = 0  # no politeness sleep
    return tb


class TestRustTableExtraction:
    """Unit tests for the Rust extractor (no network)."""

    def test_simple_table_with_th_headers(self):
        html = (
            "<table><tr><th>Name</th><th>Qty</th></tr>"
            "<tr><td>Apple</td><td>3</td></tr></table>"
        )
        (name, headers, rows) = extract_tables_from_html(html)[0]
        assert name == "table-1"
        assert headers == ["Name", "Qty"]
        assert rows == [["Apple", "3"]]

    def test_no_th_means_no_headers(self):
        html = "<table><tr><td>a</td></tr><tr><td>b</td></tr></table>"
        (name, headers, rows) = extract_tables_from_html(html)[0]
        assert headers == []
        assert rows == [["a"], ["b"]]

    def test_single_th_still_marks_header_row(self):
        html = (
            "<table><tr><th>Col A</th><td>not a header</td></tr>"
            "<tr><td>1</td><td>2</td></tr></table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert headers == ["Col A", "not a header"]
        assert rows == [["1", "2"]]

    def test_th_in_data_row_is_not_header(self):
        """Only the FIRST row can become the header row."""
        html = (
            "<table><tr><td>1</td><td>2</td></tr>"
            "<tr><th>Mid</th><td>x</td></tr></table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert headers == []
        assert rows == [["1", "2"], ["Mid", "x"]]

    def test_caption_becomes_table_name(self):
        html = (
            "<table><caption>  Annual  Report  2026 </caption>"
            "<tr><th>K</th></tr></table>"
        )
        (name, headers, rows) = extract_tables_from_html(html)[0]
        assert name == "Annual Report 2026"
        assert headers == ["K"]
        assert rows == []

    def test_empty_caption_falls_back_to_numbered_name(self):
        html = (
            "<table><caption>   </caption><tr><th>a</th></tr></table>"
            "<table><tr><th>b</th></tr></table>"
        )
        names = [t[0] for t in extract_tables_from_html(html)]
        assert names == ["table-1", "table-2"]

    def test_thead_tbody_tfoot_structure(self):
        html = (
            "<table><thead><tr><th>H</th></tr></thead>"
            "<tbody><tr><td>1</td></tr></tbody>"
            "<tfoot><tr><td>sum</td></tr></tfoot></table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert headers == ["H"]
        assert rows == [["1"], ["sum"]]

    def test_colspan_expands_into_rectangular_grid(self):
        html = (
            "<table>"
            "<tr><td colspan=\"3\">wide</td></tr>"
            "<tr><td>a</td><td>b</td><td>c</td></tr>"
            "</table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert headers == []
        assert rows[0] == ["wide", "", ""]
        assert rows[1] == ["a", "b", "c"]

    def test_rowspan_expands_downward(self):
        html = (
            "<table>"
            "<tr><td rowspan=\"2\">tall</td><td>1</td></tr>"
            "<tr><td>2</td></tr>"
            "</table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert rows[0] == ["tall", "1"]
        assert rows[1] == ["", "2"]

    def test_rowspan_and_colspan_combined(self):
        html = (
            "<table>"
            "<tr><td rowspan=\"2\" colspan=\"2\">big</td><td>top</td></tr>"
            "<tr><td>bottom</td></tr>"
            "</table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert rows[0] == ["big", "", "top"]
        assert rows[1] == ["", "", "bottom"]

    def test_invalid_span_values_default_to_one(self):
        html = (
            "<table>"
            "<tr><td colspan=\"bogus\">a</td><td>b</td></tr>"
            "<tr><td>1</td><td>2</td></tr>"
            "</table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert rows[0] == ["a", "b"]
        assert rows[1] == ["1", "2"]

    def test_nested_tables_are_skipped(self):
        """Only top-level tables are extracted; the inner table's visible
        text stays part of the outer grid (the browser renders it inline)."""
        html = (
            "<table>"
            "<tr><td>outer</td><td>"
            "<table><tr><td>inner</td></tr></table>"
            "</td></tr>"
            "</table>"
        )
        result = extract_tables_from_html(html)
        # The inner <table> is nested -> skipped; only the outer one is
        # extracted, with the inner text visible inside its cell.
        assert len(result) == 1
        name, headers, rows = result[0]
        assert name == "table-1"
        assert rows == [["outer", "inner"]]

    def test_max_tables_cap(self):
        html = "".join(
            f"<table><tr><th>h{i}</th></tr></table>" for i in range(5)
        )
        result = extract_tables_from_html(html, max_tables=2)
        assert len(result) == 2
        assert [t[0] for t in result] == ["table-1", "table-2"]

    def test_max_rows_cap(self):
        html = "<table>" + "".join(f"<tr><td>r{i}</td></tr>" for i in range(10)) + "</table>"
        _, headers, rows = extract_tables_from_html(html, max_rows=4)[0]
        assert headers == []
        assert len(rows) == 4
        assert rows[0] == ["r0"]

    def test_cell_text_collapses_whitespace_and_caps_length(self):
        long_text = "word " * 400  # ~2000 chars of repeatable text
        html = f"<table><tr><td>{long_text}</td></tr></table>"
        _, headers, rows = extract_tables_from_html(html)[0]
        cell = rows[0][0]
        assert "  " not in cell  # collapsed to single spaces
        assert len(cell) <= 1000  # MAX_CELL_CHARS honored incl. ellipsis
        assert cell.endswith("...")

    def test_empty_and_ragged_rows_are_rectified(self):
        html = (
            "<table>"
            "<tr><td>a</td><td>b</td></tr>"
            "<tr><td>c</td></tr>"
            "</table>"
        )
        _, headers, rows = extract_tables_from_html(html)[0]
        assert rows[0] == ["a", "b"]
        assert rows[1] == ["c", ""]

    def test_no_tables_returns_empty_list(self):
        assert extract_tables_from_html("<p>just text</p>") == []

    def test_table_with_no_rows_is_skipped(self):
        assert extract_tables_from_html("<table></table>") == []

    def test_markup_inside_cell_is_not_included(self):
        html = "<table><tr><td><a href='/x'>link</a> text</td></tr></table>"
        _, headers, rows = extract_tables_from_html(html)[0]
        assert rows[0][0] == "link text"


class TestParseHtmlTables:
    """parse_html attaches tables to the payload and the single page."""

    def test_tables_populate_payload_and_page(self):
        tables = [
            ExtractedTable(name="sales", headers=["A"], rows=[["1"]]),
            ExtractedTable(name="table-2", headers=[], rows=[["x"]]),
        ]
        payload = StructuredOxideParser.parse_html(
            markdown="# t",
            links=[],
            html_metadata={},
            url=URL,
            tables=tables,
        )
        assert payload.tables == tables
        assert payload.pages[0].tables == tables
        data = json.loads(payload.to_json())
        assert data["tables"][0]["name"] == "sales"
        assert data["tables"][0]["rows"] == [["1"]]
        assert data["pages"][0]["tables"][1]["headers"] == []

    def test_default_is_empty_tables(self):
        payload = StructuredOxideParser.parse_html(
            markdown="# t", links=[], html_metadata={}, url=URL
        )
        assert payload.tables == []
        assert payload.pages[0].tables == []


class TestStructuredToolWiring:
    """inspect_html_structured extracts tables end-to-end (fetches spied)."""

    def test_structured_payload_contains_extracted_tables(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        html = (
            "<html><body><h1>Report</h1>"
            "<table><caption>Quarterly</caption>"
            "<tr><th>Q</th><th>V</th></tr>"
            "<tr><td>Q1</td><td>5</td></tr></table>"
            "</body></html>"
        )
        monkeypatch.setattr(
            tb,
            "_fetch_html_with_html",
            lambda url, use_smart=None: ("# Report", [], {}, "static", html),
        )
        result = json.loads(tb.inspect_html_structured(URL))
        assert result["tables"][0]["name"] == "Quarterly"
        assert result["tables"][0]["headers"] == ["Q", "V"]
        assert result["tables"][0]["rows"] == [["Q1", "5"]]
        assert result["pages"][0]["tables"] == result["tables"]

    def test_no_html_means_no_tables(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        # Browser path: no raw DOM, so no tables.
        monkeypatch.setattr(
            tb,
            "_fetch_html_with_html",
            lambda url, use_smart=None: ("# md", [], {}, "browser", None),
        )
        result = json.loads(tb.inspect_html_structured(URL))
        assert result["tables"] == []
        assert result["pages"][0]["tables"] == []

    def test_extractor_failure_degrades_to_no_tables(self, tmp_path, monkeypatch):
        """A broken Rust extractor must not break the structured fetch —
        the page still ships, just without tables."""
        tb = _toolbox(tmp_path)
        import stitch_web_researcher.agent_tools as agent_tools

        def boom(html, max_tables=20, max_rows=500):
            raise RuntimeError("extractor exploded")

        monkeypatch.setattr(agent_tools, "extract_tables_from_html", boom)
        monkeypatch.setattr(
            tb,
            "_fetch_html_with_html",
            lambda url, use_smart=None: (
                "# md",
                [],
                {},
                "static",
                "<table><tr><td>x</td></tr></table>",
            ),
        )
        result = json.loads(tb.inspect_html_structured(URL))
        assert "error" not in result
        assert result["tables"] == []

    def test_browser_mode_end_to_end_no_tables(self, tmp_path, monkeypatch):
        """fetch_mode=browser: the real dispatch runs, the browser fake
        serves the page, and html stays None -> empty tables."""
        tb = _toolbox(tmp_path, fetch_mode="browser")
        import stitch_web_researcher.agent_tools as agent_tools

        monkeypatch.setattr(agent_tools, "_browser_oxide_available", True)
        monkeypatch.setattr(
            agent_tools,
            "_fetch_with_browser_oxide",
            lambda url: ("# md", [], {"title": "t"}),
        )
        result = json.loads(tb.inspect_html_structured(URL))
        assert "error" not in result
        assert result["tables"] == []

    def test_page_path_unaffected(self, tmp_path, monkeypatch):
        """M8: inspect_html_page still uses the 4-tuple _fetch_html seam."""
        tb = _toolbox(tmp_path)
        monkeypatch.setattr(
            tb,
            "_fetch_html",
            lambda url, use_smart=None: ("# md", [], {}, "static"),
        )
        result = json.loads(tb.inspect_html_page(URL))
        assert "error" not in result
        assert "tables" not in result  # page path never had tables
