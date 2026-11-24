from gossamer.liveness import check_liveness, _service_liveness_result

def test_liveness_result():
    assert _service_liveness_result("https://x", 204, None) == {"url":"https://x","status":"ok","alive":True,"http_status":204}
    assert _service_liveness_result("https://x", 500, None)["status"] == "unreachable"
    assert _service_liveness_result("https://x", None, "boom") == {"url":"https://x","status":"error","alive":False,"http_status":None,"error":"boom"}

def test_check_liveness_uses_kernel():
    assert check_liveness("https://example.com", request_fn=lambda u,t: (200,None))["status"] == "ok"
    assert check_liveness("https://example.com", request_fn=lambda u,t: (None,"boom"))["status"] == "error"
