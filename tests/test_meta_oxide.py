"""
Tests for meta_extractor.py and meta-oxide integration.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from stitch_web_researcher.meta_extractor import (
    extract_all,
    extract_meta,
    extract_opengraph,
    extract_twitter,
    extract_jsonld,
    merge_into_document_metadata,
)


# ────────────────────────────────────────────────────────────────
# Sample HTML for testing
# ────────────────────────────────────────────────────────────────

SAMPLE_HTML = """
<html>
<head>
    <title>Test Page Title</title>
    <meta name="description" content="Test description text">
    <meta name="keywords" content="test, keywords, sample">
    <meta name="language" content="en">
    <meta name="robots" content="index, follow">
    <meta property="og:title" content="OG Page Title">
    <meta property="og:type" content="article">
    <meta property="og:image" content="https://example.com/og-image.png">
    <meta property="og:description" content="OG description">
    <meta property="og:site_name" content="Example Site">
    <meta property="og:url" content="https://example.com/page">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Twitter Card Title">
    <meta name="twitter:description" content="Twitter description">
    <meta name="twitter:image" content="https://example.com/twitter-img.png">
    <meta name="twitter:site" content="@example">
    <link rel="canonical" href="https://example.com/canonical-page">
    <link rel="alternate" hreflang="de" href="https://example.com/de">
    <script type="application/ld+json">
    {"@type":"Article","headline":"JSON-LD Headline","author":{"@type":"Person","name":"Jane Doe"}}
    </script>
    <script type="application/ld+json">
    {"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","name":"Home"}]}
    </script>
</head>
<body>
    <div class="h-card">
        <span class="p-name">Card Person</span>
        <a class="u-url" href="https://example.com/person">Profile</a>
    </div>
</body>
</html>
"""

BASE_URL = "https://example.com/page"


# ────────────────────────────────────────────────────────────────
# 1. extract_all
# ────────────────────────────────────────────────────────────────

class TestExtractAll:
    def test_returns_dict(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert isinstance(result, dict)

    def test_has_meta_key(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert "meta" in result

    def test_meta_has_title(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert result["meta"].get("title") == "Test Page Title"

    def test_meta_has_description(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert result["meta"].get("description") == "Test description text"

    def test_meta_has_canonical(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert "https://example.com/canonical-page" in result["meta"].get("canonical", "")

    def test_opengraph_has_title(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert result["opengraph"].get("title") == "OG Page Title"

    def test_opengraph_has_image(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert "og-image.png" in result["opengraph"].get("image", "")

    def test_twitter_has_card(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert result["twitter"].get("card") == "summary_large_image"

    def test_twitter_has_title(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert result["twitter"].get("title") == "Twitter Card Title"

    def test_jsonld_is_list(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        assert isinstance(result.get("jsonld", []), list)

    def test_jsonld_has_article(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        jsonld = result.get("jsonld", [])
        article_types = [obj.get("@type") for obj in jsonld]
        assert "Article" in article_types

    def test_jsonld_author_name(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        jsonld = result.get("jsonld", [])
        article = [obj for obj in jsonld if obj.get("@type") == "Article"]
        assert len(article) == 1
        assert article[0]["author"]["name"] == "Jane Doe"

    def test_rel_links_has_canonical(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        rel = result.get("rel_links", {})
        assert "canonical" in rel

    def test_microformats_has_hcard(self):
        result = extract_all(SAMPLE_HTML, BASE_URL)
        mf = result.get("microformats", {})
        assert "h-card" in mf

    def test_empty_html(self):
        result = extract_all("<html></html>", None)
        assert isinstance(result, dict)
        assert "meta" in result

    def test_without_base_url(self):
        result = extract_all(SAMPLE_HTML, None)
        assert isinstance(result, dict)


# ────────────────────────────────────────────────────────────────
# 2. Individual extractors
# ────────────────────────────────────────────────────────────────

class TestIndividualExtractors:
    def test_extract_meta(self):
        result = extract_meta(SAMPLE_HTML, BASE_URL)
        assert result.get("title") == "Test Page Title"

    def test_extract_opengraph(self):
        result = extract_opengraph(SAMPLE_HTML, BASE_URL)
        assert result.get("title") == "OG Page Title"

    def test_extract_twitter(self):
        result = extract_twitter(SAMPLE_HTML, BASE_URL)
        assert result.get("card") == "summary_large_image"

    def test_extract_jsonld(self):
        result = extract_jsonld(SAMPLE_HTML, BASE_URL)
        assert isinstance(result, list)
        assert len(result) >= 1


# ────────────────────────────────────────────────────────────────
# 3. merge_into_document_metadata
# ────────────────────────────────────────────────────────────────

class TestMergeIntoDocumentMetadata:
    def _full_extract(self):
        return extract_all(SAMPLE_HTML, BASE_URL)

    def test_merges_title(self):
        raw = self._full_extract()
        merged = merge_into_document_metadata(raw, {})
        assert merged["title"] == "Test Page Title"

    def test_merges_description(self):
        raw = self._full_extract()
        merged = merge_into_document_metadata(raw, {})
        assert merged["description"] == "Test description text"

    def test_merges_canonical(self):
        raw = self._full_extract()
        merged = merge_into_document_metadata(raw, {})
        assert "canonical" in merged

    def test_merges_og_fields(self):
        raw = self._full_extract()
        merged = merge_into_document_metadata(raw, {})
        assert merged.get("og_title") == "OG Page Title"
        assert merged.get("og_type") == "article"
        assert "og-image.png" in merged.get("og_image", "")

    def test_merges_twitter_fields(self):
        raw = self._full_extract()
        merged = merge_into_document_metadata(raw, {})
        assert merged.get("twitter_card") == "summary_large_image"
        assert merged.get("twitter_title") == "Twitter Card Title"

    def test_merges_jsonld(self):
        raw = self._full_extract()
        merged = merge_into_document_metadata(raw, {})
        assert "jsonld" in merged
        assert isinstance(merged["jsonld"], list)

    def test_preserves_existing_title(self):
        raw = self._full_extract()
        base = {"title": "Existing Title"}
        merged = merge_into_document_metadata(raw, base)
        assert merged["title"] == "Existing Title"  # not overwritten

    def test_empty_input(self):
        merged = merge_into_document_metadata({}, {"title": "Base"})
        assert merged["title"] == "Base"


# ────────────────────────────────────────────────────────────────
# 4. Toolbox integration (metadata in inspect_html_page)
# ────────────────────────────────────────────────────────────────

class TestToolboxMetadata:
    def test_compact_metadata(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox

        toolbox = WebResearcherToolbox()
        raw = extract_all(SAMPLE_HTML, BASE_URL)
        compact = toolbox._fetch._compact_metadata(raw)

        assert compact.get("title") == "Test Page Title"
        assert compact.get("og_title") == "OG Page Title"
        assert compact.get("twitter_card") == "summary_large_image"
        assert "jsonld" in compact
        assert len(compact["jsonld"]) <= 2  # capped at 2

    def test_compact_metadata_empty(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox

        toolbox = WebResearcherToolbox()
        assert toolbox._fetch._compact_metadata({}) == {}
        assert toolbox._fetch._compact_metadata(None) == {}

    def test_fetch_smart_page_returns_metadata(self):
        """Verify fetch_smart_page returns a 3-tuple (md, links, metadata)."""
        from stitch_web_researcher.agent_tools import fetch_smart_page

        # fetch_smart_page falls back to fetch_and_extract without browser_oxide
        result = fetch_smart_page("https://example.com")
        assert isinstance(result, tuple)
        assert len(result) == 3
        md, links, metadata = result
        assert isinstance(md, str)
        assert isinstance(links, list)
        assert isinstance(metadata, dict)
