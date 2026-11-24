from gossamer.models import _service_fetch_stats_dict, FetchStats

def test_stats_parity_shapes():
    st = FetchStats()
    st.record_success("b.test", 0.00125, 10)
    st.record_success("a.test", 0.00225, 20)
    st.record_error("a.test", 0.00325, ValueError())
    expected = st.to_dict()
    got = _service_fetch_stats_dict(st._fetches, st._errors, st._bytes, list(st._latencies), st._by_domain, st._by_error)
    assert got == expected

def test_empty_stats():
    got = _service_fetch_stats_dict(0, 0, 0, [], {}, {})
    assert got["latency_ms"] == {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
