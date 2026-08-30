"""Per-provider politeness + quota defaults.

Each legacy search engine falls back to its own ``RateLimit`` constant when
constructed without an explicit ``delay`` / ``RateLimit``, so the hard quota
limits and jitter are enforced per the engines' real limits. Explicit delays
or a caller-supplied ``RateLimit`` still win, and the module-level constants
are never mutated by construction.
"""

import pytest

from stitch_web_researcher.search_providers import (
    _BING_RATE_LIMIT,
    _DUCKDUCKGO_RATE_LIMIT,
    _EXA_RATE_LIMIT,
    _GOOGLE_RATE_LIMIT,
    BingProvider,
    DuckDuckGoProvider,
    ExaProvider,
    GoogleProvider,
    RateLimit,
)


class TestPerProviderDefaults:
    def test_duckduckgo_default(self):
        p = DuckDuckGoProvider()
        assert p.rate_limit == _DUCKDUCKGO_RATE_LIMIT
        assert p.rate_limit.search_interval == 0.5
        assert p.rate_limit.jitter == 0.25
        assert p.rate_limit.quota is None

    def test_google_default_has_daily_quota(self):
        p = GoogleProvider(api_key="k", cx="c")
        assert p.rate_limit == _GOOGLE_RATE_LIMIT
        assert p.rate_limit.search_interval == 0.2
        assert p.rate_limit.jitter == 0.1
        assert p.rate_limit.quota == 100
        assert p.rate_limit.quota_window == "day"

    def test_bing_default(self):
        p = BingProvider(api_key="k")
        assert p.rate_limit == _BING_RATE_LIMIT
        assert p.rate_limit.search_interval == 0.2
        assert p.rate_limit.jitter == 0.1
        assert p.rate_limit.quota is None

    def test_exa_default_has_monthly_quota(self):
        # Exa is implemented against the REST API with httpx (no SDK), so it
        # constructs without any optional install.
        p = ExaProvider(api_key="k")
        assert p.rate_limit == _EXA_RATE_LIMIT
        assert p.rate_limit.search_interval == 0.1
        assert p.rate_limit.jitter == 0.05
        assert p.rate_limit.quota == 1000
        assert p.rate_limit.quota_window == "month"

    def test_explicit_float_delay_wins_and_disables_jitter(self):
        # A float delay normalizes to a bare RateLimit(search_interval=...),
        # so jitter stays 0 -- the caller opted out of the provider default.
        p = DuckDuckGoProvider(delay=5.0)
        assert p.rate_limit.search_interval == 5.0
        assert p.rate_limit.jitter == 0.0
        assert p.rate_limit.quota is None

    def test_explicit_rate_limit_wins_and_is_copied(self):
        custom = RateLimit(
            search_interval=2.0, jitter=0.3, quota=42, quota_window="month"
        )
        p = DuckDuckGoProvider(delay=custom)
        assert p.rate_limit == custom
        assert p.rate_limit is not custom
        # The no-arg path's module-level constant is untouched.
        assert _DUCKDUCKGO_RATE_LIMIT.search_interval == 0.5
        assert _DUCKDUCKGO_RATE_LIMIT.jitter == 0.25

    def test_fetch_delay_override_applies_on_top_of_default(self):
        p = DuckDuckGoProvider(fetch_delay=3.0)
        assert p.rate_limit.search_interval == 0.5
        assert p.rate_limit.jitter == 0.25
        assert p.rate_limit.fetch_interval == 3.0

    def test_default_constants_are_not_mutated_across_instances(self):
        a = DuckDuckGoProvider(fetch_delay=9.0)
        b = DuckDuckGoProvider(fetch_delay=1.0)
        # Independent copies: the shared fetch_delay override must not leak
        # between instances nor back into the module-level constant.
        assert a.rate_limit.fetch_interval == 9.0
        assert b.rate_limit.fetch_interval == 1.0
        assert _DUCKDUCKGO_RATE_LIMIT.fetch_interval == RateLimit().fetch_interval
