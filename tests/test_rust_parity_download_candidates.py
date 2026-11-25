from gossamer.downloader import _service_candidate_list, _service_finalize_candidates
def test_candidates():
    assert _service_candidate_list("a", ["b","a"]) == {"candidates":["a","b"]}
    assert _service_candidate_list("", [])["error"]["code"] == "invalid_argument"
    attempts=[{"url":"a","status":"error","error":{"code":"not_found","message":"x"}}]
    out=_service_finalize_candidates("a",["a"],attempts)
    assert out["error"]["code"]=="all_sources_failed"
