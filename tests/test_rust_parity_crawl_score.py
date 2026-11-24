from gossamer.crawl import _service_crawl_score

def test_crawl_score():
    assert abs(_service_crawl_score(["rust","lang"],["rust"],["lang"],None,None,0,1.0)-0.85)<1e-9
    idf={"rust":2.0,"lang":1.0}
    assert abs(_service_crawl_score(["rust","lang"],["rust"],["lang"],idf,None,1,1.0)-0.8)<1e-9
    assert _service_crawl_score([],[],[],None,None,0,1.0)==0.0
