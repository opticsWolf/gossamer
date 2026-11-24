from gossamer import discovery

def test_feed_links():
    html = '''<LINK REL="alternate" TYPE="application/rss+xml; charset=utf-8" HREF="/feed.xml"><link rel="alternate" type="text/html" href="/x">'''
    out = discovery._service_find_feed_links(html, "https://x.test/page")
    assert out == [{"url": "https://x.test/feed.xml", "type": "application/rss+xml"}]
