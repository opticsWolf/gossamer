"""Tier 2.6 -- observability.

Two complementary surfaces:

* ``get_stats()["fetches"]``: Python-side, thread-safe fetch telemetry
  (latency percentiles over a bounded window, total bytes, per-domain
  request counts, error counts by exception class). The numbers come from
  a lightweight ``FetchStats`` recorder wrapped around the single fetch
  dispatch choke point, so every ``inspect_html_page`` / batch fetch is
  accounted for without touching the Rust ABI.

* ``GOSSAMER_RUST_LOG``: an opt-in bridge that forwards Rust ``tracing``
  events to Python ``logging``. Default-off; when set, the Rust HTTP path
  (per-hop status, 304 revalidations, errors, byte counts) appears in the
  Python log stream. ``init_rust_logging`` is idempotent and emits a single
  init marker so operators can confirm the bridge is live.

All tests are deterministic: the fetch layer is a fake
``_dispatch_fetch`` (the inner, uninstrumented method) and the Rust bridge
is exercised through the pure-Python logging capture, so no live network
and no real model/browser is involved.
"""

import json
import logging

import pytest

from gossamer import agent_tools
from gossamer.agent_tools import (
    FetchStats,
    ToolboxConfig,
    WebResearcherToolbox,
    _domain_of,
)


def _toolbox(tmp_path, **config_kwargs) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


# ────────────────────────────────────────────────────────────────
# FetchStats (pure unit)
# ────────────────────────────────────────────────────────────────
class TestFetchStats:
    def test_empty(self):
        d = FetchStats().to_dict()
        assert d["fetches"] == 0
        assert d["errors"] == 0
        assert d["bytes_downloaded"] == 0
        assert d["latency_ms"]["p50"] == 0.0
        assert d["requests_by_domain"] == {}
        assert d["errors_by_class"] == {}

    def test_records_success(self):
        fs = FetchStats()
        fs.record_success("example.com", 0.05, 1234)
        fs.record_success("example.com", 0.10, 566)
        fs.record_success("other.net", 0.01, 100)
        d = fs.to_dict()
        assert d["fetches"] == 3
        assert d["errors"] == 0
        assert d["bytes_downloaded"] == 1234 + 566 + 100
        assert d["requests_by_domain"]["example.com"] == 2
        assert d["requests_by_domain"]["other.net"] == 1

    def test_records_error(self):
        fs = FetchStats()
        with pytest.raises(ValueError):
            try:
                raise ValueError("bad")
            except ValueError as e:
                fs.record_error("example.com", 0.02, e)
                raise
        d = fs.to_dict()
        assert d["fetches"] == 1
        assert d["errors"] == 1
        assert d["errors_by_class"]["ValueError"] == 1
        assert d["requests_by_domain"]["example.com"] == 1

    def test_percentiles(self):
        fs = FetchStats(latency_window=100)
        for i in range(1, 101):
            fs.record_success("d", i * 0.001, 10)
        d = fs.to_dict()
        # latencies are 1ms..100ms
        assert d["latency_ms"]["p50"] == pytest.approx(50.0, rel=0.05)
        assert d["latency_ms"]["p95"] == pytest.approx(95.0, rel=0.1)
        assert d["latency_ms"]["p99"] == pytest.approx(99.0, rel=0.1)
        assert d["latency_ms"]["max"] == pytest.approx(100.0)

    def test_single_sample_percentiles(self):
        fs = FetchStats()
        fs.record_success("d", 0.25, 1)
        d = fs.to_dict()
        assert d["latency_ms"]["p50"] == pytest.approx(250.0)
        assert d["latency_ms"]["max"] == pytest.approx(250.0)

    def test_window_is_bounded(self):
        fs = FetchStats(latency_window=3)
        assert fs._latencies.maxlen == 3
        for i in range(10):
            fs.record_success("d", float(i), 1)
        # only the last 3 samples (7,8,9 seconds) survive the window
        d = fs.to_dict()
        assert d["latency_ms"]["max"] == pytest.approx(9000.0)

    def test_domain_sorted_by_count_desc(self):
        fs = FetchStats()
        fs.record_success("a", 0.1, 1)
        fs.record_success("b", 0.1, 1)
        fs.record_success("a", 0.1, 1)
        d = fs.to_dict()
        keys = list(d["requests_by_domain"].keys())
        assert keys[0] == "a"  # highest count first


class TestDomainOf:
    def test_netloc(self):
        assert _domain_of("https://example.com/path?q=1") == "example.com"

    def test_falls_back_to_raw(self):
        assert _domain_of("not a url") == "not a url"


# ────────────────────────────────────────────────────────────────
# Dispatch instrumentation (the _fetch_html_dispatch wrapper)
# ────────────────────────────────────────────────────────────────
class TestDispatchInstrumentation:
    def test_success_is_recorded(self, tmp_path):
        tb = _toolbox(tmp_path)
        md = "# Hello"

        def fake_dispatch(url, use_smart=None):
            return (md, [("https://example.com/x", "x")], {}, "static")

        tb._fetch._dispatch_fetch = fake_dispatch
        out = tb._fetch._fetch_html_dispatch("https://example.com/a")
        assert out[0] == md
        s = tb._fetch_stats.to_dict()
        assert s["fetches"] == 1
        assert s["errors"] == 0
        assert s["requests_by_domain"]["example.com"] == 1
        assert s["bytes_downloaded"] == len(md.encode("utf-8"))

    def test_error_is_recorded_and_reraised(self, tmp_path):
        tb = _toolbox(tmp_path)

        def fake_dispatch(url, use_smart=None):
            raise RuntimeError("boom")

        tb._fetch._dispatch_fetch = fake_dispatch
        with pytest.raises(RuntimeError):
            tb._fetch._fetch_html_dispatch("https://example.com/b")
        s = tb._fetch_stats.to_dict()
        assert s["fetches"] == 1
        assert s["errors"] == 1
        assert s["errors_by_class"]["RuntimeError"] == 1
        assert s["requests_by_domain"]["example.com"] == 1

    def test_non_string_markdown_counts_zero_bytes(self, tmp_path):
        tb = _toolbox(tmp_path)

        def fake_dispatch(url, use_smart=None):
            return (None, [], {}, "static")  # defensive: markdown slot empty

        tb._fetch._dispatch_fetch = fake_dispatch
        tb._fetch._fetch_html_dispatch("https://example.com/c")
        assert tb._fetch_stats.to_dict()["bytes_downloaded"] == 0

    def test_get_stats_includes_fetches(self, tmp_path):
        tb = _toolbox(tmp_path)

        def fake_dispatch(url, use_smart=None):
            return ("body", [], {}, "static")

        tb._fetch._dispatch_fetch = fake_dispatch
        tb._fetch._fetch_html_dispatch("https://example.com/d")
        stats = json.loads(tb.get_stats())
        assert "fetches" in stats
        assert stats["fetches"]["fetches"] == 1
        assert "latency_ms" in stats["fetches"]
        assert stats["fetches"]["requests_by_domain"]["example.com"] == 1


# ────────────────────────────────────────────────────────────────
# Config wiring
# ────────────────────────────────────────────────────────────────
class TestConfig:
    def test_window_default(self, tmp_path):
        tb = _toolbox(tmp_path)
        assert tb._fetch_stats._latencies.maxlen == 1024

    def test_window_override(self, tmp_path):
        tb = _toolbox(tmp_path, fetch_stats_window=7)
        assert tb._fetch_stats._latencies.maxlen == 7


# ────────────────────────────────────────────────────────────────
# Rust tracing -> Python logging bridge
# ────────────────────────────────────────────────────────────────
class TestRustLoggingBridge:
    def test_init_emits_marker_record(self):
        from gossamer._core import init_rust_logging

        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        root = logging.getLogger()
        root.addHandler(handler)
        prev = root.level
        root.setLevel(logging.DEBUG)
        try:
            assert init_rust_logging("info") is True
        finally:
            root.setLevel(prev)
            root.removeHandler(handler)
        msgs = [r.getMessage() for r in records]
        assert any("rust logging bridge initialized" in m for m in msgs)

    def test_init_is_idempotent(self):
        from gossamer._core import init_rust_logging

        assert init_rust_logging("debug") is True
        assert init_rust_logging("warn") is True  # second call only re-levels

    def test_bridge_off_by_default(self, monkeypatch):
        # When GOSSAMER_RUST_LOG is unset the toolbox must not initialise the
        # bridge (keeps the default logging path clean).
        monkeypatch.delenv("GOSSAMER_RUST_LOG", raising=False)
        calls = []
        monkeypatch.setattr(
            agent_tools,
            "_init_rust_logging",
            lambda level: calls.append(level) or True,
        )
        agent_tools._rust_log_initialized = False
        agent_tools._maybe_init_rust_logging()
        assert calls == []


class TestBoundedGrowth:
    def test_fetch_stats_maps_stay_bounded(self):
        from gossamer.models import FetchStats

        stats = FetchStats()
        for i in range(FetchStats.MAX_KEYS + 100):
            stats.record_success(f"d{i}.example.com", 0.01, 10)
        assert len(stats._by_domain) <= FetchStats.MAX_KEYS
        # Totals are unaffected by eviction.
        assert stats.to_dict()["fetches"] == FetchStats.MAX_KEYS + 100

    def test_guard_verdict_cache_stays_bounded(self):
        from gossamer.guard import GuardConfig, JailGuardGuard

        class _StubDetector:
            def detect(self, chunk):
                return {"score": 0.1, "risk": "Low", "is_injection": False}

        guard = JailGuardGuard(GuardConfig(enabled=True))
        guard._jg = _StubDetector()  # skip model download; exercise insert path
        total = JailGuardGuard.MAX_VERDICTS + 50
        for i in range(total):
            guard._score_chunk(f"distinct chunk payload {i}")
        assert len(guard._verdicts) <= JailGuardGuard.MAX_VERDICTS
        # Fresh chunks still score (no crash, no unbounded growth).
        score, _risk, _inj, hit = guard._score_chunk("distinct chunk payload 0")
        assert score == 0.1
