from gossamer.agent_tools import _service_throttle_gap

def test_throttle_gap():
    a = _service_throttle_gap(0.2, 0.5, 0.1, None)
    assert abs(a["gap"] - 0.6) < 1e-9 and abs(a["sleep"] - 0.4) < 1e-9
    assert _service_throttle_gap(1.0, 0.0, 0.3, None) == {"gap": 0.0, "sleep": 0.0}
    assert _service_throttle_gap(0.1, 0.2, 0.05, 2.0) == {"gap": 2.0, "sleep": 1.9}
