# tests/test_m7_bounded_state.py
"""M7 — unbounded per-process growth.

visited_urls (a set) and _domain_last_seen (a defaultdict whose *reads*
inserted unseen keys) grew without limit in a long-lived MCP server.
Both are now bounded FIFO OrderedDicts with caps that evict the oldest
entries. All tests run offline (respect_robots=False, zero delays).
"""

import time

from stitch_web_researcher.agent_tools import WebResearcherToolbox


def _toolbox() -> WebResearcherToolbox:
    tb = WebResearcherToolbox(respect_robots=False)
    tb._fetch_interval = 0  # no politeness sleep/jitter
    return tb


class TestVisitedUrlsBounded:
    def test_fifo_eviction_at_cap(self):
        tb = _toolbox()
        tb.VISITED_URL_CAP = 100
        for i in range(150):
            tb._mark_visited(f"https://example.com/p{i}")
        # Invariant: never over the cap (first overflow halves to cap//2,
        # then 49 further inserts grow it back to 99)
        assert len(tb.visited_urls) <= tb.VISITED_URL_CAP
        assert len(tb.visited_urls) == 99
        # Newest entries survive, oldest are evicted (p0..p50 gone)
        assert "https://example.com/p149" in tb.visited_urls
        assert "https://example.com/p100" in tb.visited_urls
        assert "https://example.com/p51" in tb.visited_urls
        assert "https://example.com/p50" not in tb.visited_urls
        assert "https://example.com/p0" not in tb.visited_urls

    def test_no_eviction_below_cap(self):
        tb = _toolbox()
        tb.VISITED_URL_CAP = 1000
        for i in range(500):
            tb._mark_visited(f"https://example.com/p{i}")
        assert len(tb.visited_urls) == 500
        assert "https://example.com/p0" in tb.visited_urls

    def test_revisit_keeps_position(self):
        """Re-marking an already-visited URL does not refresh its
        position (it is a dedup guard, not an LRU)."""
        tb = _toolbox()
        tb.VISITED_URL_CAP = 100
        tb._mark_visited("https://example.com/first")
        for i in range(150):
            tb._mark_visited(f"https://example.com/p{i}")
        # 'first' was inserted first -> evicted despite no re-mark
        assert "https://example.com/first" not in tb.visited_urls

    def test_reset_visited_clears_fifo(self):
        tb = _toolbox()
        tb._mark_visited("https://example.com/a")
        tb._mark_visited("https://example.com/b")
        tb.reset_visited()
        assert len(tb.visited_urls) == 0

    def test_membership_and_count_surface(self):
        tb = _toolbox()
        tb._mark_visited("https://example.com/x")
        assert "https://example.com/x" in tb.visited_urls
        stats = __import__("json").loads(tb.get_stats())
        assert stats["visited_urls_count"] == 1


class TestDomainTimestampsBounded:
    def test_read_does_not_insert(self):
        """The M7 bug: the defaultdict inserted an entry on every read
        of an unseen domain. OrderedDict.get must not."""
        tb = _toolbox()
        tb._rate_limit_domain("https://seen.example.com/")
        before = len(tb._domain_last_seen)
        assert tb._domain_last_seen.get("ghost.example.com", 0.0) == 0.0
        assert len(tb._domain_last_seen) == before
        assert "ghost.example.com" not in tb._domain_last_seen

    def test_bounded_by_cap(self):
        tb = _toolbox()
        tb.DOMAIN_TS_CAP = 10
        for i in range(15):
            tb._rate_limit_domain(f"https://d{i}.example.com/")
        assert len(tb._domain_last_seen) <= 10
        # Most recently seen domains survive
        assert "d14.example.com" in tb._domain_last_seen
        assert "d13.example.com" in tb._domain_last_seen
        # Oldest evicted
        assert "d0.example.com" not in tb._domain_last_seen
        assert "d4.example.com" not in tb._domain_last_seen

    def test_same_domain_no_growth(self):
        tb = _toolbox()
        for _ in range(50):
            tb._rate_limit_domain("https://one.example.com/")
        assert len(tb._domain_last_seen) == 1
        assert tb._domain_last_seen["one.example.com"] > 0

    def test_timestamps_are_current(self):
        tb = _toolbox()
        before = time.time()
        tb._rate_limit_domain("https://ts.example.com/")
        assert tb._domain_last_seen["ts.example.com"] >= before
