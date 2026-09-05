"""Shared pytest fixtures for the gossamer test suite.

SSRF escape hatch is OFF by default during test runs.

``GOSSAMER_ALLOW_PRIVATE`` is an operator-controlled escape
hatch (see ``gossamer.ssrf``). It exists for interactive
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

_ALLOW_PRIVATE_ENV = "GOSSAMER_ALLOW_PRIVATE"
_ALLOW_PRIVATE_ENV_LEGACY = "STITCH_WEB_RESEARCHER_ALLOW_PRIVATE"
_LIVE_ENV_LEGACY = "STITCH_LIVE"


@pytest.fixture(autouse=True, scope="session")
def ssrf_guard_active_by_default():
    """Keep the SSRF guard active for the entire test session.

    The ambient ``GOSSAMER_ALLOW_PRIVATE`` value (which an
    operator may have set for interactive local dev) is stashed and
    removed for the run, then restored on teardown. The legacy
    ``STITCH_*`` spellings are cleared too, so the fallback in
    :mod:`gossamer.env` cannot re-enable anything mid-suite.
    """
    stashed = {}
    for key in (_ALLOW_PRIVATE_ENV, _ALLOW_PRIVATE_ENV_LEGACY):
        stashed[key] = os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, original in stashed.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


# ────────────────────────────────────────────────────────────────
# Live network smoke tests (feature flag)
# ────────────────────────────────────────────────────────────────
#
# Live tests make real network calls to the providers under test. They are
# OFF by default so the normal suite stays fast, offline-friendly and hermetic
# (mirroring the SSRF escape-hatch philosophy above). They are enabled only
# with an explicit, operator-controlled opt-in: the ``GOSSAMER_LIVE`` env var.
# Key-gated providers additionally require their key env var to be set, or they
# skip. (Env-var gating only — no CLI option, so there is nothing to register
# and no way to enable live tests by accident.)

_LIVE_ENV = "GOSSAMER_LIVE"


def _live_enabled(env_value: Optional[str], _legacy: Optional[str] = None) -> bool:
    """Return True when live tests are opted-in.

    Enabled only by a truthy ``GOSSAMER_LIVE`` env var — one of ``1`` / ``true``
    / ``yes`` (case-insensitive). Off for any other value, so the default
    suite never touches the network.
    """

    # Legacy STITCH_LIVE still opts in (rename fallback).
    return ((env_value or "").strip().lower() in ("1", "true", "yes")) or (
        (_legacy or "").strip().lower() in ("1", "true", "yes")
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: live network smoke test; skipped unless GOSSAMER_LIVE=1.",
    )
    config.addinivalue_line(
        "markers",
        "area:<name>: logical test group (search/fetch/crawl/storage/"""
        "citations/phase3/security/other); used for subset runs (-m area_<name>).",
    )
    # Register each concrete area marker (e.g. ``area_search``) so subset
    # runs (-m area_search) and the auto-tagging below stay warning-free.
    for _area in sorted({_DEFAULT_AREA, *{_a for _, _a in _AREA_PREFIXES}}):
        config.addinivalue_line(
            "markers", f"area_{_area}: subset marker for the {_area} group."
        )


# Logical test groups, keyed by filename prefix. Ordered by specificity is
# handled dynamically (longest prefix wins) so a broad prefix like ``test_c``
# never swallows ``test_crawl`` / ``test_citations``. Anything unmatched falls
# back to ``_DEFAULT_AREA`` so no test ever loses its marker.
_AREA_PREFIXES = [
    ("test_citations", "citations"),
    ("test_dedup_liveness", "citations"),
    ("test_mcp_server", "citations"),
    ("test_p8_tool_registry", "citations"),
    ("test_search", "search"),
    ("test_research", "search"),
    ("test_providers", "search"),
    ("test_exa", "search"),
    ("test_meta_oxide", "search"),
    ("test_t2_6_observability", "search"),
    ("test_t2_8_search_cache", "search"),
    ("test_t2_9_async", "search"),
    ("test_t3_13_research", "search"),
    ("test_browser_provider", "search"),
    ("test_browser_integration", "search"),
    ("test_guard", "search"),
    ("test_focused_discovery", "crawl"),
    ("test_crawler", "crawl"),
    ("test_extract_links", "crawl"),
    ("test_t3_12_discovery", "crawl"),
    ("test_c", "crawl"),
    ("test_cache", "storage"),
    ("test_html_store", "storage"),
    ("test_resource_store", "storage"),
    ("test_p9_doc_store", "storage"),
    ("test_p2_doc_extra", "storage"),
    ("test_m10_batch_error", "storage"),
    ("test_phase3", "phase3"),
    ("test_plan_fixes", "phase3"),
    ("test_fix", "phase3"),
    ("test_s", "security"),
    ("test_s5_concurrency", "fetch"),
    ("test_t2_7_transport", "fetch"),
    ("test_t3_10_formats", "fetch"),
    ("test_t3_11_tables", "fetch"),
    ("test_m", "fetch"),
    ("test_t1", "fetch"),
]
_DEFAULT_AREA = "other"


def _area_for_node(node) -> str:
    """Best (longest-prefix) filename match -> logical area name."""
    name = node.path.name
    for prefix, area in sorted(
        _AREA_PREFIXES, key=lambda kv: len(kv[0]), reverse=True
    ):
        if name.startswith(prefix):
            return area
    return _DEFAULT_AREA


def pytest_collection_modifyitems(items):
    """Tag every collected test with an ``area:<name>`` marker.

    Purely additive: the marker is orthogonal to the existing ``slow`` /
    ``live`` markers and to any explicit ``@pytest.mark`` a test already
    carries, so subset runs (``-m area_search``) never disturb the normal
    suite.
    """
    for item in items:
        item.add_marker(getattr(pytest.mark, f"area_{_area_for_node(item)}"))


@pytest.fixture
def live(request):
    """Feature-gate a live test: skip it unless live mode is enabled.

    Depend on this fixture at the top of a ``@pytest.mark.live`` test. When
    live mode is off the test is skipped (not run, not failed), keeping the
    default suite offline-friendly.
    """

    if not _live_enabled(os.environ.get(_LIVE_ENV), os.environ.get(_LIVE_ENV_LEGACY)):
        pytest.skip("live test (enable with GOSSAMER_LIVE=1)")
    return True


@pytest.fixture
def live_key(request):
    """Like :func:`live`, but also skip when a provider key env var is unset.

    Parameterize with the env-var name to require, e.g.
    ``@pytest.mark.parametrize("live_key", ["GOSSAMER_GITHUB_TOKEN"],
    indirect=True)``.
    """

    if not _live_enabled(os.environ.get(_LIVE_ENV), os.environ.get(_LIVE_ENV_LEGACY)):
        pytest.skip("live test (enable with GOSSAMER_LIVE=1)")
    env_name = request.param
    key = os.environ.get(env_name)
    if not key:
        pytest.skip(f"{env_name} not set; live auth test skipped")
    return key
