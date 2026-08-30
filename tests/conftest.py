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
