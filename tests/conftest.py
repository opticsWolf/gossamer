"""Shared pytest fixtures for the stitch-web-researcher test suite.

SSRF escape hatch is OFF by default during test runs.

``STITCH_WEB_RESEARCHER_ALLOW_PRIVATE`` is an operator-controlled escape
hatch (see ``stitch_web_researcher.ssrf``). It exists for interactive
local development and for tests that deliberately exercise loopback
servers. It must *not* leak from the ambient environment into the suite,
or the SSRF guard (S1) would appear to pass while being inert — a test
would report "guard blocks X" while the guard never ran.

This fixture clears the escape hatch for the whole session and restores
the original value afterwards, keeping the suite hermetic no matter how
it is invoked (CI, interactive, agent harness). Tests that need loopback
access set the variable themselves via ``monkeypatch.setenv`` (auto
undone per test); tests that assert the guard is active need nothing.
"""

from __future__ import annotations

import os

import pytest

_ALLOW_PRIVATE_ENV = "STITCH_WEB_RESEARCHER_ALLOW_PRIVATE"


@pytest.fixture(autouse=True, scope="session")
def ssrf_guard_active_by_default():
    """Keep the SSRF guard active for the entire test session.

    The ambient ``STITCH_WEB_RESEARCHER_ALLOW_PRIVATE`` value (which an
    operator may have set for interactive local dev) is stashed and
    removed for the run, then restored on teardown.
    """
    original = os.environ.get(_ALLOW_PRIVATE_ENV)
    os.environ.pop(_ALLOW_PRIVATE_ENV, None)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(_ALLOW_PRIVATE_ENV, None)
        else:
            os.environ[_ALLOW_PRIVATE_ENV] = original


# ────────────────────────────────────────────────────────────────
# Live network smoke tests (feature flag)
# ────────────────────────────────────────────────────────────────
#
# Live tests make real network calls to the providers under test. They are
# OFF by default so the normal suite stays fast, offline-friendly and hermetic
# (mirroring the SSRF escape-hatch philosophy above). They are enabled only
# with an explicit, operator-controlled opt-in: the ``STITCH_LIVE`` env var.
# Key-gated providers additionally require their key env var to be set, or they
# skip. (Env-var gating only — no CLI option, so there is nothing to register
# and no way to enable live tests by accident.)

_LIVE_ENV = "STITCH_LIVE"


def _live_enabled(env_value: Optional[str]) -> bool:
    """Return True when live tests are opted-in.

    Enabled only by a truthy ``STITCH_LIVE`` env var — one of ``1`` / ``true``
    / ``yes`` (case-insensitive). Off for any other value, so the default
    suite never touches the network.
    """

    return (env_value or "").strip().lower() in ("1", "true", "yes")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: live network smoke test; skipped unless STITCH_LIVE=1.",
    )


@pytest.fixture
def live(request):
    """Feature-gate a live test: skip it unless live mode is enabled.

    Depend on this fixture at the top of a ``@pytest.mark.live`` test. When
    live mode is off the test is skipped (not run, not failed), keeping the
    default suite offline-friendly.
    """

    if not _live_enabled(os.environ.get(_LIVE_ENV)):
        pytest.skip("live test (enable with STITCH_LIVE=1)")
    return True


@pytest.fixture
def live_key(request):
    """Like :func:`live`, but also skip when a provider key env var is unset.

    Parameterize with the env-var name to require, e.g.
    ``@pytest.mark.parametrize("live_key", ["STITCH_GITHUB_TOKEN"],
    indirect=True)``.
    """

    if not _live_enabled(os.environ.get(_LIVE_ENV)):
        pytest.skip("live test (enable with STITCH_LIVE=1)")
    env_name = request.param
    key = os.environ.get(env_name)
    if not key:
        pytest.skip(f"{env_name} not set; live auth test skipped")
    return key
