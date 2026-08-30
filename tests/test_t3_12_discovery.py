"""Tier 3.12: sitemap-aware discovery (discover_resources).

All tests are network-free: the page fetch is faked through
``_fetch_html_with_html`` (5-tuple, Tier 3.11 seam) and the sitemap
probes through ``_static_fetch`` (5-tuple when keep_html=True).
"""

import json

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox

URL = "https://example.com/blog/post"
SITE_ROOT = "https://example.com"
SITEMAP_URL = "https://example.com/sitemap.xml"

PAGE_HTML = """
<html><head>
<link rel="alternate" type="application/rss+xml" title="Feed" href="/feeds/rss.xml">
<link rel="alternate" type="application/atom+xml" href="https://example.com/feeds/atom.xml">
<link rel="alternate" hreflang="en" href="/en">
<link rel="canonical" href="https://example.com/blog/post">
</head><body>hi</body></html>
"""

SITEMAP_INDEX = """
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/pages.xml</loc></sitemap>
  <sitemap><loc>/posts.xml</loc></sitemap>
</sitemapindex>
"""

PAGES_XML = """
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
  <url><loc>https://example.com/a</loc></url>
</urlset>
"""

POSTS_XML = """
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>c</loc></url>
</urlset>
"""


def _toolbox(tmp_path):
    # respect_robots=False: nothing here performs real fetches (S4), and
    # the discovery seams are faked in each test.
    return WebResearcherToolbox(
        config=ToolboxConfig(
            cache_dir=str(tmp_path / "cache"),
            domain_delay=0.0,
            ddgs_delay=0.0,
            respect_robots=False,
        )
    )


def _fake_page(html=PAGE_HTML):
    def fake(url, use_smart=None):
        assert url == URL
        return ("# md", [], {}, "static", html)

    return fake


def _fake_sitemaps(mapping):
    """Fake _static_fetch for sitemap probes.

    *mapping* maps sitemap URL -> raw XML text. Unknown URLs raise, as a
    404 would.
    """

    def fake(url, keep_html=False):
        assert keep_html is True
        if url not in mapping:
            raise RuntimeError(f"404 Not Found: {url}")
        return (None, [], {}, "static", mapping[url])

    return fake


class TestFeedDiscovery:
    def test_feed_links_found_and_absolutized(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({})
        result = json.loads(tb.discover_resources(URL))
        assert "error" not in result
        assert result["feeds"] == [
            {
                "url": "https://example.com/feeds/rss.xml",
                "type": "application/rss+xml",
            },
            {
                "url": "https://example.com/feeds/atom.xml",
                "type": "application/atom+xml",
            },
        ]
        assert result["site_root"] == SITE_ROOT

    def test_hreflang_and_non_feed_alternates_ignored(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({})
        result = json.loads(tb.discover_resources(URL))
        types = [f["type"] for f in result["feeds"]]
        assert "application/rss+xml" in types
        # The hreflang alternate has no feed type -> not a feed.
        assert all(t.startswith(tuple(tb._discovery._FEED_TYPE_PREFIXES)) for t in types)

    def test_charset_in_type_is_normalized(self, tmp_path):
        html = (
            '<link rel="alternate" type="application/rss+xml; charset=utf-8"'
            ' href="/f.xml">'
        )
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page(html=html)
        tb._fetch._static_fetch = _fake_sitemaps({})
        result = json.loads(tb.discover_resources(URL))
        assert result["feeds"][0]["type"] == "application/rss+xml"
        assert result["feeds"][0]["url"] == "https://example.com/f.xml"

    def test_browser_page_without_html_has_no_feeds(self, tmp_path):
        tb = _toolbox(tmp_path)

        def fake_browser(url, use_smart=None):
            return ("# md", [], {}, "browser", None)

        tb._fetch._fetch_html_with_html = fake_browser
        tb._fetch._static_fetch = _fake_sitemaps({})
        result = json.loads(tb.discover_resources(URL))
        assert result["feeds"] == []
        assert "error" not in result

    def test_page_fetch_failure_returns_error(self, tmp_path):
        tb = _toolbox(tmp_path)

        def boom(url, use_smart=None):
            raise RuntimeError("connection refused")

        tb._fetch._fetch_html_with_html = boom
        result = json.loads(tb.discover_resources(URL))
        assert "error" in result
        assert "connection refused" in result["error"]
        assert result["url"] == URL


class TestSitemapDiscovery:
    def test_urlset_pages_discovered_and_deduped(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({SITEMAP_URL: PAGES_XML})
        result = json.loads(tb.discover_resources(URL))
        assert result["sitemaps"] == [
            {"url": SITEMAP_URL, "kind": "urlset", "count": 3}
        ]
        # /a is absolutized and deduped against the explicit duplicate.
        assert result["urls"] == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        assert result["count"] == 2
        assert result["truncated"] is False

    def test_sitemap_index_followed_with_relative_child(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps(
            {
                SITEMAP_URL: SITEMAP_INDEX,
                "https://example.com/pages.xml": PAGES_XML,
                "https://example.com/posts.xml": POSTS_XML,
            }
        )
        result = json.loads(tb.discover_resources(URL))
        kinds = {s["url"]: s["kind"] for s in result["sitemaps"]}
        assert kinds[SITEMAP_URL] == "index"
        assert kinds["https://example.com/pages.xml"] == "urlset"
        # /posts.xml (relative) was absolutized against the index URL.
        assert kinds["https://example.com/posts.xml"] == "urlset"
        assert result["urls"] == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]

    def test_missing_sitemap_degrades_gracefully(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({})  # 404
        result = json.loads(tb.discover_resources(URL))
        assert "error" not in result
        assert result["sitemaps"] == []
        assert result["urls"] == []
        assert len(result["feeds"]) == 2  # page feeds still reported

    def test_malformed_sitemap_xml_skipped(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({SITEMAP_URL: "this is not xml <"})
        result = json.loads(tb.discover_resources(URL))
        assert "error" not in result
        assert result["sitemaps"] == []

    def test_non_sitemap_xml_skipped(self, tmp_path):
        """A /sitemap.xml that is a regular XML document (no urlset /
        sitemapindex root) yields nothing."""
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps(
            {SITEMAP_URL: "<config><item>x</item></config>"}
        )
        result = json.loads(tb.discover_resources(URL))
        assert result["sitemaps"] == []
        assert result["urls"] == []

    def test_total_url_cap_marks_truncated(self, tmp_path):
        """The total URL cap is enforced across sitemaps (3 x 500)."""

        def urlset(n, prefix):
            urls = "".join(
                f"<url><loc>/{prefix}{i}</loc></url>" for i in range(n)
            )
            return (
                "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                + urls
                + "</urlset>"
            )

        mapping = {
            SITEMAP_URL: (
                "<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                "<sitemap><loc>https://example.com/u1.xml</loc></sitemap>"
                "<sitemap><loc>https://example.com/u2.xml</loc></sitemap>"
                "<sitemap><loc>https://example.com/u3.xml</loc></sitemap>"
                "</sitemapindex>"
            ),
            "https://example.com/u1.xml": urlset(500, "a"),
            "https://example.com/u2.xml": urlset(500, "b"),
            "https://example.com/u3.xml": urlset(500, "c"),
        }
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps(mapping)
        result = json.loads(tb.discover_resources(URL))
        assert result["count"] == 1000
        assert result["truncated"] is True
        assert len(result["urls"]) == 1000

    def test_per_sitemap_cap_marks_truncated(self, tmp_path):
        urls = [f"<url><loc>/p{i}</loc></url>" for i in range(600)]
        xml = f"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{'' .join(urls)}</urlset>"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({SITEMAP_URL: xml})
        result = json.loads(tb.discover_resources(URL))
        assert result["sitemaps"][0]["count"] == 500
        assert result["truncated"] is True
        assert result["count"] == 500

    def test_index_hops_are_bounded(self, tmp_path):
        """An index chain deeper than _DISCOVER_MAX_INDEX_HOPS stops
        being followed (no unbounded crawl)."""

        def index_of(child):
            return (
                "<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                f"<sitemap><loc>{child}</loc></sitemap>"
                "</sitemapindex>"
            )

        mapping = {
            SITEMAP_URL: index_of("https://example.com/s1.xml"),
            "https://example.com/s1.xml": index_of("https://example.com/s2.xml"),
            "https://example.com/s2.xml": index_of("https://example.com/s3.xml"),
            "https://example.com/s3.xml": index_of("https://example.com/s4.xml"),
        }
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps(mapping)
        result = json.loads(tb.discover_resources(URL))
        fetched = {s["url"] for s in result["sitemaps"]}
        assert fetched == {
            SITEMAP_URL,
            "https://example.com/s1.xml",
            "https://example.com/s2.xml",
            "https://example.com/s3.xml",
        }
        # s4 sits one hop beyond the bound and must never be probed.
        assert "https://example.com/s4.xml" not in fetched

    def test_duplicate_sitemaps_fetched_once(self, tmp_path):
        """A sitemap index listing itself must not loop the probe."""
        self_indexing = (
            "<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            f"<sitemap><loc>{SITEMAP_URL}</loc></sitemap>"
            "<sitemap><loc>https://example.com/pages.xml</loc></sitemap>"
            "</sitemapindex>"
        )
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps(
            {SITEMAP_URL: self_indexing, "https://example.com/pages.xml": PAGES_XML}
        )
        result = json.loads(tb.discover_resources(URL))
        root_fetches = sum(1 for s in result["sitemaps"] if s["url"] == SITEMAP_URL)
        assert root_fetches == 1
        assert result["urls"] == [
            "https://example.com/a",
            "https://example.com/b",
        ]


class TestToolIntegration:
    def test_execute_tool_dispatch(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({SITEMAP_URL: PAGES_XML})
        result = json.loads(tb.execute_tool("discover_resources", {"url": URL}))
        assert "error" not in result
        assert result["url"] == URL

    def test_page_not_marked_visited_after_discovery(self, tmp_path):
        """Discovery is metadata-level: the page stays inspectable."""
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html_with_html = _fake_page()
        tb._fetch._static_fetch = _fake_sitemaps({})
        json.loads(tb.discover_resources(URL))
        # A subsequent page-path fetch of the same URL must not hit the
        # "already visited" warning.
        tb._fetch._fetch_html = lambda url, use_smart=None: ("# md", [], {}, "static")
        result = json.loads(tb.inspect_html_page(URL))
        assert "warning" not in result

    def test_robots_disallow_short_circuits(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        monkeypatch.setattr(tb, "_robots_disallows", lambda url: True)
        result = json.loads(tb.discover_resources(URL))
        assert "warning" in result
        assert "robots" in result["warning"]

    def test_registry_spec_matches_method_signature(self, tmp_path):
        """P8: the registry params must match discover_resources()."""
        from stitch_web_researcher.agent_tools import TOOL_REGISTRY

        tb = _toolbox(tmp_path)
        spec = next(s for s in TOOL_REGISTRY if s.name == "discover_resources")
        assert callable(getattr(tb, spec.method))
        # No arguments -> TypeError-free default construction check.
        kwargs = spec.kwargs({"url": URL})
        assert kwargs == {"url": URL}
