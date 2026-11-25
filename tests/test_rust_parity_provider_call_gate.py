from gossamer.search_providers import _service_provider_call_gate


def test_provider_call_gate():
    # No quota: deterministic politeness wait, no state rollover.
    out = _service_provider_call_gate(
        "duckduckgo",
        None,
        0,
        "day",
        None,
        "2026-01-01T00:00:00+00:00",
        10.0,
        9.0,
        1.0,
        0.5,
        0.25,
    )
    assert out["reset_quota"] is False
    assert out["period"] == "2026-01-01"
    assert out["used"] == 0
    assert out["error"] is None
    assert abs(out["wait"] - 0.25) < 1e-9
    assert out["quota_used_after"] == 0

    # Rolled day: reset the counter before evaluating the quota.
    out = _service_provider_call_gate(
        "google",
        3,
        9,
        "day",
        "2025-12-31",
        "2026-01-01T00:00:00+00:00",
        10.0,
        9.0,
        1.0,
        0.0,
        0.0,
    )
    assert out == {
        "reset_quota": True,
        "period": "2026-01-01",
        "used": 0,
        "error": None,
        "wait": 0.0,
        "quota_used_after": 1,
    }

    # Hard quota: exact Python-style error text, no wait, no increment.
    out = _service_provider_call_gate(
        "google",
        2,
        2,
        "day",
        "2026-01-01",
        "2026-01-01T12:00:00+00:00",
        10.0,
        9.0,
        1.0,
        0.0,
        0.0,
    )
    assert out["error"] == (
        "'google' quota exhausted (2/2 per day); retry after the window resets"
    )
    assert out["quota_used_after"] == 2
