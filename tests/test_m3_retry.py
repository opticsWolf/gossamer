# tests/test_m3_retry.py
"""M3 — @retry was dead code on search_web (which swallows every
provider exception). The retry decorator now lives on
SearchProvider.search, where provider exceptions actually propagate,
so transient provider failures are retried 3x with exponential
backoff before the next provider is tried.

These tests use stub providers (no network). The retry sleeps are the
real 1s/2s backoff, so failure-path tests cost ~3s each.
"""

import json
import time

import pytest

from gossamer.agent_tools import WebResearcherToolbox
from gossamer.search_providers import SearchProvider, retry


class _FlakyProvider(SearchProvider):
    """Fails on the first N _search_impl calls, then succeeds."""

    name = "flaky"

    def __init__(self, fail_times: int, delay: float = 0.0, fetch_delay: float = 0.0):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(delay, fetch_delay)
        self.fail_times = fail_times
        self.calls = 0

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"transient failure #{self.calls}")
        return [{"title": "ok", "url": "https://example.com/ok", "snippet": "s"}]


class _AlwaysDownProvider(SearchProvider):
    name = "down"

    def __init__(self, delay: float = 0.0, fetch_delay: float = 0.0):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(delay, fetch_delay)
        self.calls = 0

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        self.calls += 1
        raise RuntimeError("provider down")


class TestProviderSearchRetry:
    def test_transient_failure_is_retried_then_succeeds(self):
        """One transient failure -> retried -> success (M3)."""
        prov = _FlakyProvider(fail_times=1)
        results = prov.search("query", max_results=2)
        assert prov.calls == 2  # 1 failure + 1 retry
        assert isinstance(results, list) and results[0]["url"].endswith("/ok")

    def test_gives_up_after_three_attempts(self):
        """All 3 attempts fail -> the exception propagates (M3)."""
        prov = _AlwaysDownProvider()
        with pytest.raises(RuntimeError, match="provider down"):
            prov.search("query")
        assert prov.calls == 3

    def test_search_web_falls_through_to_next_provider(self):
        """A fully-retried-down provider is skipped, the next one
        still serves the query (existing fallback + new retry)."""
        tb = WebResearcherToolbox(respect_robots=False)
        down = _AlwaysDownProvider()
        good = _FlakyProvider(fail_times=0)
        tb.providers = [down, good]
        result = tb.search_web("query")
        data = json.loads(result)
        assert "error" not in data
        assert data[0]["url"].endswith("/ok")
        assert down.calls == 3  # retry exhausted
        assert good.calls == 1

    def test_search_web_returns_error_dict_when_all_fail(self):
        """search_web itself never raises (it always returns a JSON
        string) — which is why @retry on it could never fire (M3)."""
        tb = WebResearcherToolbox(respect_robots=False)
        tb.providers = [_AlwaysDownProvider()]
        start = time.time()
        result = tb.search_web("query")
        elapsed = time.time() - start
        data = json.loads(result)
        assert "error" in data
        # 3 attempts with 1s + 2s backoff between them
        assert elapsed >= 2.9

    def test_retry_decorator_is_reexported_from_search_providers(self):
        """The utility moved next to SearchProvider (M3); it stays a
        plain decorator usable on any function."""
        attempts = 0

        @retry(max_attempts=2, delay=0.01, backoff=1.0)
        def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ValueError("nope")
            return "ok"

        assert flaky() == "ok"
        assert attempts == 2

    def test_rate_limit_enforced_on_every_attempt(self):
        """_enforce_delay runs inside _search_impl, so the rate limit
        applies to every retry attempt (no bypass)."""
        prov = _AlwaysDownProvider(delay=0.0)
        calls = 0
        real_enforce = prov._enforce_delay

        def counting_enforce():
            nonlocal calls
            calls += 1
            real_enforce()

        prov._enforce_delay = counting_enforce
        with pytest.raises(RuntimeError):
            prov.search("q")
        assert calls == 3  # once per attempt, including the retries
