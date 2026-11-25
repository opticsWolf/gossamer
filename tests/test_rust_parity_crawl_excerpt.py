from gossamer.crawl import _service_crawl_excerpt, _service_crawl_expand_query
def test_excerpt():
    assert _service_crawl_excerpt("intro rust here", ["rust"], 5, 5) == "… rust…"
    assert _service_crawl_excerpt("nothing", ["rust"], 5, 5) is None
    expanded, added = _service_crawl_expand_query(["rust"], [["rust","cargo"]])
    assert "cargo" in expanded and added == 1
