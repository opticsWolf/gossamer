"""Fetch-path politeness: configurable jitter + crawl-aware throttling.

The fetch path throttles same-domain fetches with ``fetch_interval +
uniform(0, fetch_jitter)``. Two properties are exercised here:

* ``fetch_jitter`` is configurable on ``ToolboxConfig`` (default 1.0 s, so
  the historical 0.5-1.5 s fetch gap is preserved unless the caller opts
  out).
* ``_rate_limit_domain`` is crawl-aware: with a ``politeness_root`` only
  fetches on that host are throttled -- cross-domain links skip politeness
  entirely (each is visited at most once), so a crawl never slows down on
  external hosts.
"""

from unittest.mock import patch

import pytest

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox


def _toolbox(**overrides) -> WebResearcherToolbox:
    cfg = dict(
        cache_dir="/tmp/politeness_cache",
        domain_delay=0.5,
        fetch_delay=0.3,
        fetch_mode="static",
        ddgs_delay=0.0,
    )
    cfg.update(overrides)
    return WebResearcherToolbox(ToolboxConfig(**cfg))


def _sleeps(tb):
    """Patch and collect the sleeps taken by _rate_limit_domain.

    Returns the patch handle; the caller inspects ``.call_args_list``.
    """
    return patch(
        "stitch_web_researcher.agent_tools.time.sleep",
        side_effect=lambda s: None,
    )


def _slept(call_list) -> list:
    """Extract the requested sleep durations from a mock call list."""
    return [c.args[0] for c in call_list]


# ─────────────────────────────────────────────────────────────────
# Configurable fetch jitter
# ─────────────────────────────────────────────────────────────────
class TestFetchJitterConfig:
    def test_default_is_one_second(self):
        assert _toolbox()._fetch_jitter == 1.0

    def test_configurable_values(self):
        assert _toolbox(fetch_jitter=0.0)._fetch_jitter == 0.0
        assert _toolbox(fetch_jitter=2.5)._fetch_jitter == 2.5

    def test_zero_jitter_sleeps_at_most_the_interval(self):
        tb = _toolbox(fetch_delay=0.3, fetch_jitter=0.0)
        with _sleeps(tb) as sleep:
            tb._rate_limit_domain("http://a.com/x")  # first visit: unseen
            tb._rate_limit_domain("http://a.com/y")  # repeat: sleeps interval
        sleeps = _slept(sleep.call_args_list)
        assert len(sleeps) == 1  # no jitter -> gap == search_interval
        assert 0.25 <= sleeps[0] <= 0.3

    def test_nonzero_jitter_expands_the_sleep(self):
        tb = _toolbox(fetch_delay=0.3, fetch_jitter=0.5)
        with _sleeps(tb) as sleep, patch(
            "stitch_web_researcher.agent_tools.random.uniform",
            return_value=0.5,  # max jitter
        ):
            tb._rate_limit_domain("http://a.com/x")  # first visit: unseen
            tb._rate_limit_domain("http://a.com/y")  # 0.3 + 0.5 = 0.8 - elapsed
        sleeps = _slept(sleep.call_args_list)
        assert len(sleeps) == 1
        assert 0.7 <= sleeps[0] <= 0.85


# ─────────────────────────────────────────────────────────────────
# Crawl-aware throttling (politeness_root)
# ─────────────────────────────────────────────────────────────────
class TestPolitenessRoot:
    def test_same_domain_is_throttled(self):
        tb = _toolbox(fetch_delay=0.3, fetch_jitter=0.0)
        with _sleeps(tb) as sleep:
            tb._rate_limit_domain("http://a.com/x", politeness_root="a.com")
            sleep.assert_not_called()  # first visit, unseen
            tb._rate_limit_domain("http://a.com/y", politeness_root="a.com")
            assert len(sleep.call_args_list) == 1
            assert 0.25 <= sleep.call_args_list[0].args[0] <= 0.35

    def test_cross_domain_skips_politeness(self):
        tb = _toolbox(fetch_delay=0.3, fetch_jitter=0.0)
        with _sleeps(tb) as sleep:
            # Prime the root domain.
            tb._rate_limit_domain("http://a.com/x", politeness_root="a.com")
            tb._rate_limit_domain("http://a.com/y", politeness_root="a.com")
            assert len(sleep.call_args_list) == 1
            sleep.reset_mock()
            # External host: skipped entirely, no throttle lock even taken.
            tb._rate_limit_domain("http://b.com/z", politeness_root="a.com")
            sleep.assert_not_called()

    def test_politeness_root_matches_host_key_stripping_www(self):
        # www.a.com has the same host key as a.com, so it is throttled (not
        # skipped) -- but as a distinct netloc it gets its own first-visit
        # before the throttle kicks in.
        tb = _toolbox(fetch_delay=0.3, fetch_jitter=0.0)
        with _sleeps(tb) as sleep:
            tb._rate_limit_domain("http://a.com/x", politeness_root="a.com")
            tb._rate_limit_domain("http://www.a.com/y", politeness_root="a.com")
            sleep.assert_not_called()  # first www.a.com visit is free
            tb._rate_limit_domain("http://www.a.com/z", politeness_root="a.com")
            assert len(sleep.call_args_list) == 1

    def test_without_politeness_root_every_domain_is_throttled(self):
        # Default (None) keeps the historical per-domain behaviour: a new
        # domain is free on first visit, throttled on repeat.
        tb = _toolbox(fetch_delay=0.3, fetch_jitter=0.0)
        with _sleeps(tb) as sleep:
            tb._rate_limit_domain("http://a.com/x")
            tb._rate_limit_domain("http://b.com/x")  # different domain, free
            sleep.assert_not_called()
            tb._rate_limit_domain("http://a.com/y")  # repeat a.com: sleeps
            assert len(sleep.call_args_list) == 1
