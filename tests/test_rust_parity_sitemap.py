from gossamer.discovery import _service_parse_sitemap
def test_sitemap():
    xml='<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/a</loc></url><url><loc>https://y/b</loc></url></urlset>'
    assert _service_parse_sitemap(xml,'https://x/sitemap.xml',10)=={"kind":"urlset","locs":["https://x/a","https://y/b"],"truncated":False}
    assert _service_parse_sitemap('<bad>','https://x',10) is None
