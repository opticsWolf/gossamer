"""C5 regression: inspect_html_structured must actually deliver the links
its description promises.

Before the fix, ``parse_html`` accepted ``links`` and ``max_links`` but
used neither, and ``ParsedDocumentPayload`` had no links field at all —
so the structured payload always came back with zero links.
"""

import json
from unittest.mock import patch

from stitch_web_researcher.agent_tools import WebResearcherToolbox
from stitch_web_researcher.structured_parser import (
    DocumentMetadata,
    FollowUpCandidate,
    ParsedDocumentPayload,
    StructuredOxideParser,
    build_follow_up_candidates,
    classify_link,
)


class TestParseHtmlLinks:
    def test_payload_has_links_field(self):
        assert "links" in ParsedDocumentPayload.model_fields
        payload = ParsedDocumentPayload(
            metadata=DocumentMetadata(file_name="x", format="html")
        )
        assert payload.links == []

    def test_parse_html_populates_links(self):
        payload = StructuredOxideParser.parse_html(
            markdown="# Hello",
            links=[
                ("https://example.com/a", "Page A"),
                ("https://example.com/report.pdf", "Report"),
                ("https://example.com/a", "Page A again"),  # dup
            ],
            html_metadata={},
            url="https://example.com/",
            max_links=10,
        )
        assert [c.url for c in payload.links] == [
            "https://example.com/a",
            "https://example.com/report.pdf",
        ]
        assert payload.links[0].title == "Page A"
        assert payload.links[0].type == "page"
        assert payload.links[1].type == "document"

    def test_parse_html_honors_max_links(self):
        links = [(f"https://example.com/{i}", f"p{i}") for i in range(30)]
        payload = StructuredOxideParser.parse_html(
            markdown="# Hi",
            links=links,
            html_metadata={},
            url="https://example.com/",
            max_links=20,
        )
        assert len(payload.links) == 20
        assert [c.url for c in payload.links] == [l[0] for l in links[:20]]

    def test_parse_html_untitled_fallback(self):
        payload = StructuredOxideParser.parse_html(
            markdown="# Hi",
            links=[("https://example.com/x", "  ")],
            html_metadata={},
            url="https://example.com/",
            max_links=5,
        )
        assert payload.links[0].title == "(untitled)"


class TestBuildFollowUpCandidates:
    def test_dedup_and_order(self):
        out = build_follow_up_candidates(
            [("https://a.com", "A"), ("https://a.com", "A2"), ("https://b.com", "")]
        )
        assert [c.url for c in out] == ["https://a.com", "https://b.com"]
        assert out[1].title == "(untitled)"
        assert all(isinstance(c, FollowUpCandidate) for c in out)

    def test_classification(self):
        assert classify_link("https://x.com/file.docx") == "document"
        assert classify_link("https://x.com/page/") == "page"


class TestStructuredToolEndToEnd:
    def _toolbox(self, tmp_path):
        return WebResearcherToolbox(
            cache_dir=str(tmp_path / "cache"), domain_delay=0.0, ddgs_delay=0.0
        )

    def test_inspect_html_structured_returns_links(self, tmp_path):
        tb = self._toolbox(tmp_path)
        fake_links = [
            ("https://example.com/doc.pdf", "The doc"),
            ("https://example.com/next", "Next page"),
        ]
        with patch.object(
            tb,
            "_fetch_html",
            return_value=("# Content", fake_links, {}, "static"),
        ):
            result = json.loads(tb.inspect_html_structured("https://example.com/"))

        assert "error" not in result
        assert result["links"] == [
            {"title": "The doc", "url": "https://example.com/doc.pdf", "type": "document"},
            {"title": "Next page", "url": "https://example.com/next", "type": "page"},
        ]

    def test_inspect_html_page_still_returns_links(self, tmp_path):
        """The non-structured tool is unaffected: full candidate list."""
        tb = self._toolbox(tmp_path)
        fake_links = [(f"https://example.com/{i}", f"p{i}") for i in range(50)]
        with patch.object(
            tb, "_fetch_html", return_value=("# C", fake_links, {}, "static")
        ):
            result = json.loads(tb.inspect_html_page("https://example.com/"))
        assert "error" not in result
        assert len(result["follow_up_links"]) == 50
