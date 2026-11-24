from gossamer.models import _normalize_batch_results

def test_batch_entries():
    ok = _normalize_batch_results([("https://x", "<b>hi</b>", "hi", ["https://y"], (200, "https://x", "text/html"))])[0]
    assert ok.ok and ok.markdown == "hi" and ok.prov == (200, "https://x", "text/html")
    bad = _normalize_batch_results([("https://z", None, None, None)])[0]
    assert not bad.ok and bad.error == "Unknown error"
