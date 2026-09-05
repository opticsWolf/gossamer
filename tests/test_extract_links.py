"""Deep-research support -- text-level link detection (feature 1).

Documents (PDF/DOCX/...) lose the <a href> structure of HTML pages; the
URLs written into their body text must still be recoverable, or the
agent cannot follow "links from PDFs". These tests cover the detector
itself (deterministic, stdlib-only) and its wiring into both
extract_document surfaces.
"""
from __future__ import annotations

import json

import pytest

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.structured_parser import ParsedDocumentPayload
from gossamer.text_links import extract_links


class TestExtractLinksUnit:
    def test_basic_http_and_https(self):
        text = "See https://example.com/a and http://example.org/b."
        assert extract_links(text) == [
            "https://example.com/a",
            "http://example.org/b",
        ]

    def test_www_is_promoted_to_http(self):
        assert extract_links("Visit www.example.com/docs") == [
            "http://www.example.com/docs"
        ]

    def test_bare_domain_without_www_or_scheme_is_not_matched(self):
        # In body text that shape is usually prose; no false positives.
        assert extract_links("see example.com/docs for details") == []

    def test_trailing_punctuation_is_stripped(self):
        text = "Links: https://example.com/a., https://example.com/b; (end)"
        assert extract_links(text) == [
            "https://example.com/a",
            "https://example.com/b",
        ]

    def test_cjk_sentence_punctuation_is_stripped(self):
        # No spaces: the CJK punctuation sits directly on the URL tail.
        text = "参考https://example.com/report。详见https://example.com/x，谢谢"
        assert extract_links(text) == [
            "https://example.com/report",
            "https://example.com/x",
        ]

    def test_markdown_link_target_stops_at_paren(self):
        text = 'Read [the docs](https://example.com/docs "v2") now.'
        assert extract_links(text) == ["https://example.com/docs"]

    def test_multiple_urls_on_one_line(self):
        text = "https://a.example/1 https://b.example/2 https://c.example/3"
        assert extract_links(text) == [
            "https://a.example/1",
            "https://b.example/2",
            "https://c.example/3",
        ]

    def test_dedupe_keeps_first_occurrence_order(self):
        text = (
            "https://example.com/a ... https://example.com/b ... "
            "https://example.com/a again"
        )
        assert extract_links(text) == [
            "https://example.com/a",
            "https://example.com/b",
        ]

    def test_max_links_caps_the_list(self):
        text = " ".join(f"https://example.com/{i}" for i in range(10))
        assert extract_links(text, max_links=3) == [
            "https://example.com/0",
            "https://example.com/1",
            "https://example.com/2",
        ]
        assert extract_links(text, max_links=0) == []

    def test_email_addresses_are_not_matched(self):
        assert extract_links("mail me at bob@example.com, ok?") == []

    def test_empty_and_non_string_input_never_raises(self):
        assert extract_links("") == []
        assert extract_links(None) == []  # type: ignore[arg-type]
        assert extract_links(42) == []  # type: ignore[arg-type]


class TestExtractDocumentWiring:
    @pytest.fixture
    def tb(self, tmp_path):
        return WebResearcherToolbox(
            config=ToolboxConfig(
                respect_robots=False,
                domain_delay=0.0,
                fetch_delay=0.0,
                ddgs_delay=0.0,
                cache_dir=str(tmp_path / "cache"),
            )
        )

    def test_extract_document_surfaces_text_links(self, tb, tmp_path):
        doc = tmp_path / "notes.md"
        doc.write_text(
            "Quarterly report.\n\n"
            "Source: https://example.com/data-2026 (internal).\n"
            "See also www.example.com/policy .\n",
            encoding="utf-8",
        )
        result = json.loads(tb.extract_document(str(doc)))
        assert result["links"] == [
            "https://example.com/data-2026",
            "http://www.example.com/policy",
        ]
        # The link list is independent of the content budget: it is
        # computed on the full content.
        assert "Source:" in result["content"]

    def test_extract_document_without_urls_has_empty_links(self, tb, tmp_path):
        doc = tmp_path / "plain.md"
        doc.write_text("No links here, just prose.", encoding="utf-8")
        result = json.loads(tb.extract_document(str(doc)))
        assert result["links"] == []

    def test_extract_document_cache_read_keeps_links(self, tb, tmp_path):
        doc = tmp_path / "cached.md"
        doc.write_text("One link: https://example.com/only.\n", encoding="utf-8")
        first = json.loads(tb.extract_document(str(doc)))
        second = json.loads(tb.extract_document(str(doc)))
        assert second["cache_hit"] is True
        assert second["links"] == first["links"] == ["https://example.com/only"]


class TestStructuredWiring:
    def test_payload_links_filled_from_page_text(self):
        from gossamer.structured_parser import (
            DocumentMetadata,
            ExtractedPage,
        )

        payload = ParsedDocumentPayload(
            metadata=DocumentMetadata(format="pdf"),
            pages=[
                ExtractedPage(
                    page_number=1,
                    raw_text=(
                        "Report. References: https://example.com/refs "
                        "and www.example.com/appendix ."
                    ),
                    markdown="Report.",
                )
            ],
        )
        # Mirror of the wiring in extract_document_structured.
        if not payload.links:
            from gossamer.structured_parser import (
                build_follow_up_candidates,
            )

            full_text = "\n".join(p.raw_text for p in payload.pages)
            payload.links = build_follow_up_candidates(
                [(u, "(text)") for u in extract_links(full_text)]
            )

        assert [c.url for c in payload.links] == [
            "https://example.com/refs",
            "http://www.example.com/appendix",
        ]
        assert all(c.title == "(text)" for c in payload.links)
