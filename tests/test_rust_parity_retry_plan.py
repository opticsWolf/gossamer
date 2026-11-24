from gossamer.search_providers import _service_retry_plan

def test_retry_plan():
    assert _service_retry_plan("quota", None, 0, 3, 1.0, 2.0, None, 0.0) == {"action": "raise", "final": False, "cap": False}
    assert _service_retry_plan("status", 404, 0, 3, 1.0, 2.0, None, 0.0) == {"action": "raise", "final": False, "cap": False}
    assert _service_retry_plan("status", 429, 0, 3, 1.0, 2.0, None, 0.05) == {"action": "retry", "wait": 1.05}
    assert _service_retry_plan("transport", None, 2, 3, 1.0, 2.0, None, 0.0) == {"action": "raise", "final": True, "cap": False}
    assert _service_retry_plan("status", 503, 0, 3, 1.0, 2.0, 90.0, 0.0) == {"action": "raise", "final": False, "cap": True}
