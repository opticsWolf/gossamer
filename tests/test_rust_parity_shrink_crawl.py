import json
from gossamer.crawl import _service_shrink_crawl
def test_shrink():
    r={"pages":[{"url":f"https://x/{i}","markdown":"a"} for i in range(5)],"root":"https://x"}
    a=json.loads(_service_shrink_crawl(r,None)); assert len(a["pages"])==5
    b=json.loads(_service_shrink_crawl(r,1000)); assert len(b["pages"])==2 and b["pages_omitted"]==3
