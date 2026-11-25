from gossamer.crawl import _service_crawl_is_document
def test_document_routing():
    assert _service_crawl_is_document("https://x/a.PDF")
    assert _service_crawl_is_document("https://x/feed.rss?x=1")
    assert not _service_crawl_is_document("https://x/page")
