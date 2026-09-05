# tests/test_m13_rate_limit_copy.py
"""M13 — providers must not mutate or alias a caller-supplied
``RateLimit`` instance.

Before the fix, ``_init_rate_limit`` stored the passed ``RateLimit``
object directly and then wrote ``fetch_interval`` into it (when
``fetch_delay`` was given), so a RateLimit shared between two providers
was modified in place by the first construction.
"""

from gossamer.search_providers import (
    _DUCKDUCKGO_RATE_LIMIT,
    DuckDuckGoProvider,
    RateLimit,
)


class TestRateLimitIsolation:
    def test_fetch_delay_does_not_mutate_caller_instance(self):
        shared = RateLimit(search_interval=1.0, fetch_interval=0.5)
        DuckDuckGoProvider(delay=shared, fetch_delay=2.0)
        assert shared.fetch_interval == 0.5
        assert shared.search_interval == 1.0

    def test_shared_instance_is_copied_per_provider(self):
        shared = RateLimit(search_interval=1.0, fetch_interval=0.5)
        a = DuckDuckGoProvider(delay=shared, fetch_delay=2.0)
        b = DuckDuckGoProvider(delay=shared, fetch_delay=0.1)
        # Independent copies with independent overrides.
        assert a.rate_limit.fetch_interval == 2.0
        assert b.rate_limit.fetch_interval == 0.1
        assert a.rate_limit is not b.rate_limit
        assert a.rate_limit is not shared
        assert b.rate_limit is not shared
        # Later mutation of one provider does not leak.
        a.rate_limit.fetch_interval = 9.0
        assert b.rate_limit.fetch_interval == 0.1
        assert shared.fetch_interval == 0.5

    def test_float_delay_still_normalized(self):
        p = DuckDuckGoProvider(delay=1.5)
        assert p.rate_limit.search_interval == 1.5
        assert p.rate_limit.fetch_interval == RateLimit().fetch_interval

    def test_no_args_gives_provider_default(self):
        # DuckDuckGo falls back to its own politeness/quota default
        # (1.0 s + 1.0 s jitter, no server-side quota) rather than the bare
        # RateLimit() defaults -- this is the deliberate per-engine limit.
        p = DuckDuckGoProvider()
        assert p.rate_limit == _DUCKDUCKGO_RATE_LIMIT
        assert p.rate_limit.search_interval == 1.0
        assert p.rate_limit.jitter == 1.0
        assert p.rate_limit.quota is None
