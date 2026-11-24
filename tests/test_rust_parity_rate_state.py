from gossamer.research_providers import _rate_state_from_headers

def test_rate_state():
    s = _rate_state_from_headers({"X-RateLimit-Remaining":"3","X-RateLimit-Limit":"10","Retry-After":"2"}, 5.0)
    assert (s.rps, s.remaining, s.retry_after, s.exhausted) == (10.0, 3, 2.0, False)
    s = _rate_state_from_headers({"X-Rate-Limit-Limit":"bad","X-Rate-Limit-Remaining":"x","X-Rate-Limit-Reset":"20"}, 5.0)
    assert (s.rps, s.remaining, s.reset_seconds) == (5.0, None, 20)
    s = _rate_state_from_headers({}, None)
    assert s.to_dict() == {"rps_limit":None,"remaining":None,"reset_seconds":None,"retry_after":None,"exhausted":False}
