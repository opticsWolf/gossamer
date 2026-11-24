from gossamer import search_providers as p

def test_retryable():
    assert p._service_retryable_status(429)
    assert not p._service_retryable_status(404)

def test_retry_wait():
    assert abs(p._service_retry_wait(0, 3, 1.0, 2.0, None, 0.05) - 1.05) < 1e-9
    assert p._service_retry_wait(2, 3, 1.0, 2.0, None, 0.05) is None
    assert p._service_retry_wait(0, 3, 1.0, 2.0, 90.0, 0.0) is None

def test_quota_and_politeness():
    assert p._service_quota_message("google", 100, 100, "day") == "'google' quota exhausted (100/100 per day); retry after the window resets"
    assert abs(p._service_politeness_wait(10.0, 10.5, 1.0, 0.2, 0.1) - 0.6) < 1e-9
