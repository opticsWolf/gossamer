from gossamer.crawl import Crawler, _service_crawl_tokens, _service_crawl_topic_words
def test_tokens():
    assert _service_crawl_tokens("The QUICK brown fox") == ["brown","fox","quick"]
    assert _service_crawl_topic_words("rust rust python the rust", 2) == ["rust","python"]
