from gossamer.models import _service_fetch_stats_record


def test_fetch_stats_record_success_case():
    state = {
        "latencies": [0.1, 0.2],
        "bytes": 5,
        "fetches": 2,
        "errors": 0,
        "by_domain": {"a.test": 2, "b.test": 1},
        "by_error": {},
    }
    out = _service_fetch_stats_record(
        state, "c.test", 0.3, 10, None, 2, 2
    )
    assert out["latencies"] == [0.2, 0.3]
    assert out["bytes"] == 15
    assert out["fetches"] == 3
    assert out["errors"] == 0
    assert list(out["by_domain"]) == ["b.test", "c.test"]
    assert out["by_domain"]["c.test"] == 1
    assert out["by_error"] == {}


def test_fetch_stats_record_error_case():
    state = {
        "latencies": [],
        "bytes": 0,
        "fetches": 0,
        "errors": 0,
        "by_domain": {},
        "by_error": {},
    }
    out = _service_fetch_stats_record(
        state, "example.com", 0.05, None, "ValueError", 4, 2
    )
    assert out == {
        "latencies": [0.05],
        "bytes": 0,
        "fetches": 1,
        "errors": 1,
        "by_domain": {"example.com": 1},
        "by_error": {"ValueError": 1},
    }


def test_fetch_stats_record_negative_bytes_and_existing_key():
    state = {
        "latencies": [0.1],
        "bytes": 1,
        "fetches": 1,
        "errors": 1,
        "by_domain": {"a.test": 5},
        "by_error": {"RuntimeError": 1},
    }
    out = _service_fetch_stats_record(
        state, "a.test", 0.2, 4, "RuntimeError", 2, 2
    )
    assert out["by_domain"]["a.test"] == 6
    assert out["by_error"]["RuntimeError"] == 2
    assert out["bytes"] == 5
