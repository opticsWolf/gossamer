from gossamer.crawl import Crawler, _service_crawl_anchor_context, _service_crawl_path_prior, _service_crawl_term_hits
def test_context():
    assert "rust" in _service_crawl_anchor_context("about rust rust and python", "rust", 50, 8)
    assert _service_crawl_path_prior("https://x/docs/a") == 1.15
    assert _service_crawl_path_prior("https://x/pricing") == 0.85
    assert _service_crawl_term_hits("rust rust python", ["rust"]) == 2
