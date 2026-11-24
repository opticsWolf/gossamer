from gossamer.search_providers import _service_quota_period

def test_quota_period():
    assert _service_quota_period("day", "2026-11-23T12:34:56+00:00") == "2026-11-23"
    assert _service_quota_period("month", "2026-11-23T12:34:56+00:00") == "2026-11"
    assert _service_quota_period("other", "2026-11-23") == "2026-11"
