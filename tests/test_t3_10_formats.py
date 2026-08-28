# tests/test_t3_10_formats.py
"""Tier 3.10 / review item 10 -- more input formats.

The review: text/plain, .md, .csv, .json, .xml, RSS/Atom are cheap to
support and previously either errored out (M16) or got scraped as HTML.
M16 already covered TXT/MD/CSV; this item adds JSON (pretty-printed),
XML/RSS/Atom (feed-aware with raw-text fallback), and text-like
Content-Type routing for extension-less URLs.

All tests are deterministic and network-free: URL tests monkeypatch
_fetch_document_url.
"""

import json

import pytest

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.structured_parser import (
    DOCUMENT_EXTENSIONS,
    classify_link,
)

# What _extract_from_bytes can genuinely deliver after item 10.
SUPPORTED = {
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".csv", ".txt", ".md",
    ".json", ".xml", ".rss", ".atom",
}


def _toolbox(tmp_path) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            cache_dir=str(tmp_path / "cache"),
        )
    )


def _extract(tb, path):
    return json.loads(tb.extract_document(str(path)))


RSS2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test RSS Feed</title>
    <link>https://example.com</link>
    <item>
      <title>First post</title>
      <link>https://example.com/1</link>
      <description>First body text</description>
      <pubDate>Mon, 01 Jan 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second post</title>
      <link>https://example.com/2</link>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <entry>
    <title>Atom one</title>
    <link href="https://example.com/a1"/>
    <summary>Atom summary text</summary>
    <updated>2026-01-02T00:00:00Z</updated>
  </entry>
  <entry>
    <title>Atom two</title>
    <link href="https://example.com/a2"/>
  </entry>
</feed>
"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel rdf:about="https://example.com/feed">
    <title>Test RDF Feed</title>
  </channel>
  <item rdf:about="https://example.com/r1">
    <title>RDF item</title>
    <link>https://example.com/r1</link>
    <description>RDF body text</description>
    <dc:date>2026-01-03</dc:date>
  </item>
</rdf:RDF>
"""

GENERIC_XML = b'<?xml version="1.0"?>\n<urlset>\n  <url><loc>https://example.com/1</loc></url>\n</urlset>\n'


class TestClassifyLink:
    @pytest.mark.parametrize("ext", [".json", ".xml", ".rss", ".atom"])
    def test_new_formats_classify_as_document(self, ext):
        assert classify_link(f"https://example.com/data/file{ext}") == "document"

    def test_advertised_set_subset_of_delivery_safe_set(self):
        # M16 invariant, extended for item 10.
        assert DOCUMENT_EXTENSIONS <= SUPPORTED


class TestJsonExtraction:
    def test_valid_json_is_pretty_printed(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "data.json"
        path.write_bytes(b'{"b": 2, "a": [1, 2]}')
        data = _extract(tb, path)
        assert data["content"] == json.dumps(
            {"b": 2, "a": [1, 2]}, indent=2, ensure_ascii=False
        )

    def test_invalid_json_falls_back_to_raw(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "broken.json"
        path.write_bytes(b'{"broken":')
        data = _extract(tb, path)
        assert data["content"] == '{"broken":'

    def test_json_bom_is_stripped(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "bom.json"
        path.write_bytes(b"\xef\xbb\xbf" + b'{"a": 1}')
        data = _extract(tb, path)
        assert data["content"].startswith('{\n  "a"')


class TestFeedExtraction:
    def test_rss2_entries_extracted(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "feed.rss"
        path.write_bytes(RSS2)
        content = _extract(tb, path)["content"]
        assert "Test RSS Feed" in content
        assert "Feed entries: 2" in content
        assert "**First post**" in content
        assert "Link: https://example.com/1" in content
        assert "Date: Mon, 01 Jan 2026 10:00:00 GMT" in content
        assert "First body text" in content
        assert "**Second post**" in content

    def test_atom_entries_extracted(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "feed.atom"
        path.write_bytes(ATOM)
        content = _extract(tb, path)["content"]
        assert "Test Atom Feed" in content
        assert "Feed entries: 2" in content
        assert "**Atom one**" in content
        assert "Link: https://example.com/a1" in content  # href attr
        assert "Date: 2026-01-02T00:00:00Z" in content
        assert "Atom summary text" in content

    def test_rdf_entries_extracted(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "feed.xml"
        path.write_bytes(RDF)
        content = _extract(tb, path)["content"]
        assert "Test RDF Feed" in content
        assert "**RDF item**" in content
        assert "Link: https://example.com/r1" in content
        assert "Date: 2026-01-03" in content  # dc:date local name

    def test_generic_xml_falls_back_to_raw(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "sitemap.xml"
        path.write_bytes(GENERIC_XML)
        content = _extract(tb, path)["content"]
        assert content == GENERIC_XML.decode("utf-8")

    def test_malformed_xml_falls_back_to_raw(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "broken.xml"
        raw = b'<rss><channel>unclosed'
        path.write_bytes(raw)
        content = _extract(tb, path)["content"]
        assert content == raw.decode("utf-8")

    def test_feed_entries_capped_at_50(self, tmp_path):
        items = "".join(
            f"<item><title>Post {i}</title><link>https://example.com/{i}</link></item>"
            for i in range(60)
        )
        feed = f'<rss version="2.0"><channel><title>Big Feed</title>{items}</channel></rss>'.encode()
        tb = _toolbox(tmp_path)
        path = tmp_path / "big.rss"
        path.write_bytes(feed)
        content = _extract(tb, path)["content"]
        assert "Feed entries: 50" in content
        assert content.count("- **") == 50
        assert "10 more entries not shown" in content


class TestContentTypeRouting:
    """Extension-less URLs: Content-Type decides, no extension needed."""

    def _patch_fetch(self, tb, body: bytes, content_type: str):
        def _fake(url):
            return body, {
                "fetched_at": None,
                "http_status": 200,
                "final_url": url,
                "content_type": content_type,
            }

        tb._fetch_document_url = _fake  # type: ignore[method-assign]

    def test_text_plain_url_without_extension(self, tmp_path):
        tb = _toolbox(tmp_path)
        self._patch_fetch(tb, b"hello plain world", "text/plain; charset=utf-8")
        data = json.loads(tb.extract_document("https://example.com/raw-text"))
        assert data["content"] == "hello plain world"

    def test_json_content_type_url_without_extension(self, tmp_path):
        tb = _toolbox(tmp_path)
        self._patch_fetch(tb, b'{"x": 1}', "application/json")
        data = json.loads(tb.extract_document("https://example.com/api/data"))
        assert data["content"] == json.dumps({"x": 1}, indent=2, ensure_ascii=False)

    def test_rss_content_type_url_without_extension(self, tmp_path):
        tb = _toolbox(tmp_path)
        self._patch_fetch(tb, RSS2, "application/rss+xml; charset=utf-8")
        data = json.loads(tb.extract_document("https://example.com/feeds/latest"))
        assert "Feed entries: 2" in data["content"]

    def test_binary_content_type_url_without_extension_still_errors(self, tmp_path):
        tb = _toolbox(tmp_path)
        self._patch_fetch(tb, b"\x00\x01binary", "application/octet-stream")
        data = json.loads(tb.extract_document("https://example.com/blob"))
        assert "error" in data
        assert "Unsupported document format" in data["error"]

    def test_known_suffix_beats_content_type(self, tmp_path):
        # A .json suffix routes by extension even if the content-type is
        # generic; the JSON pretty-print path is taken.
        tb = _toolbox(tmp_path)
        self._patch_fetch(tb, b'{"y": 9}', "application/octet-stream")
        data = json.loads(tb.extract_document("https://example.com/config.json"))
        assert data["content"] == json.dumps({"y": 9}, indent=2, ensure_ascii=False)
