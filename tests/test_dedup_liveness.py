"""Workstream 2 -- shared dedup + source liveness (plan item 14/15).

Two offline, dependency-light units plus their wiring:

* :mod:`stitch_web_researcher.dedup` -- pure ``dedupe`` / ``content_hash``.
* :mod:`stitch_web_researcher.liveness` -- ``check_liveness`` status probe
  with injectable validator / throttle / request_fn (never touches the
  network in tests).
* ``WebResearcherToolbox.check_sources`` -- the toolbox method that ties
  them together (dedup the URLs, probe each politely).
* ``WebResearcherToolbox.research`` -- now reports ``dropped_dupes``.

Every test is deterministic: the network and the SSRF resolver are
injected/patched, never called.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stitch_web_researcher.agent_tools import (
    ToolboxConfig,
    WebResearcherToolbox,
)
from stitch_web_researcher.dedup import content_hash, dedupe
from stitch_web_researcher.liveness import check_liveness


class _Record:
    """BibliographicRecord-like object (attribute identity)."""

    def __init__(self, doi=None, url=None, title="", snippet=""):
        self.doi = doi
        self.url = url
        self.title = title
        self.snippet = snippet


# --------------------------------------------------------------------------
# dedup -- pure, offline
# --------------------------------------------------------------------------
class TestDedupe:
    def test_doi_is_case_insensitive(self):
        items = [
            {"doi": "10.1/A", "url": "http://x/1", "title": "One"},
            {"doi": "10.1/a", "url": "http://x/2", "title": "Same work"},
        ]
        kept, dropped = dedupe(items)
        assert len(kept) == 1
        assert dropped == [{"index": 1, "reason": "doi", "match": "10.1/a"}]

    def test_url_normalisation_collapses_variants(self):
        items = [
            {"url": "http://Example.com/P/"},
            {"url": "HTTP://example.com/P?utm=9#frag"},
            {"url": "https://example.com/other"},
        ]
        kept, dropped = dedupe(items)
        # Two variants of the same URL collapse; the third (distinct) URL is kept.
        assert [k["url"] for k in kept] == ["http://Example.com/P/", "https://example.com/other"]
        assert dropped[0]["reason"] == "url"
        # Case, default port, trailing slash, query and fragment are dropped;
        # path case is preserved (paths are case-sensitive).
        assert dropped[0]["match"] == "http://example.com/P"

    def test_hash_fallback_for_no_doi_no_url(self):
        items = [
            {"title": "Same title", "snippet": "same body"},
            {"title": "Same title", "snippet": "same body"},
            {"title": "Different", "snippet": "other body"},
        ]
        kept, dropped = dedupe(items)
        assert len(kept) == 2
        assert dropped[0]["reason"] == "hash"

    def test_doi_takes_priority_over_url(self):
        # Same DOI but a different-looking URL -- DOI wins, so it is a dup
        # on the DOI field (not silently kept as a distinct URL).
        items = [
            {"doi": "10.1/x", "url": "http://a"},
            {"doi": "10.1/X", "url": "http://b/track=1"},
        ]
        kept, dropped = dedupe(items)
        assert len(kept) == 1
        assert dropped[0]["reason"] == "doi"

    def test_preserves_first_seen_order(self):
        items = [{"doi": f"10.1/{i}"} for i in range(5)]
        kept, _ = dedupe(items)
        assert [k["doi"] for k in kept] == ["10.1/0", "10.1/1", "10.1/2", "10.1/3", "10.1/4"]

    def test_object_records_by_attribute(self):
        items = [_Record("10.1/x", "http://a"), _Record("10.1/x", "http://b")]
        kept, dropped = dedupe(items)
        assert len(kept) == 1 and dropped[0]["reason"] == "doi"

    def test_empty_and_no_matches(self):
        assert dedupe([]) == ([], [])
        # Distinct titles, nothing to collapse.
        kept, dropped = dedupe([{"title": "a"}, {"title": "b"}])
        assert len(kept) == 2 and dropped == []

    def test_dedupe_by_single_field(self):
        items = [{"url": "http://a"}, {"url": "http://a"}, {"url": "http://b"}]
        kept, dropped = dedupe(items, by=("url",))
        assert len(kept) == 2 and dropped[0]["reason"] == "url"

    def test_content_hash_is_stable_and_length(self):
        h = content_hash("hello")
        assert h == content_hash("hello")
        assert len(h) == 64  # sha256 hex digest
        assert h != content_hash("hello!")


# --------------------------------------------------------------------------
# liveness -- injectable, offline
# --------------------------------------------------------------------------
class TestCheckLiveness:
    def test_ok_for_2xx(self):
        res = check_liveness("https://example.com", request_fn=lambda u, t: (200, None))
        assert res["status"] == "ok"
        assert res["alive"] is True
        assert res["http_status"] == 200
        assert "error" not in res

    def test_unreachable_for_4xx_5xx(self):
        res = check_liveness("https://example.com", request_fn=lambda u, t: (404, None))
        assert res["status"] == "unreachable"
        assert res["alive"] is False

    def test_error_when_probe_fails(self):
        res = check_liveness("https://example.com", request_fn=lambda u, t: (None, "boom"))
        assert res["status"] == "error"
        assert res["error"] == "boom"
        assert res["http_status"] is None

    def test_3xx_is_alive(self):
        res = check_liveness("https://example.com", request_fn=lambda u, t: (301, None))
        assert res["status"] == "ok"

    def test_default_validator_blocks_private_ip(self):
        # The real SSRF guard (active in the test session) blocks a private
        # link-local address without any probe running.
        res = check_liveness("http://169.254.169.254/", request_fn=lambda u, t: (200, None))
        assert res["status"] == "blocked"
        assert res["alive"] is False

    def test_throttle_is_called_and_exceptions_swallowed(self):
        calls = []

        def throttle(url):
            calls.append(url)
            raise RuntimeError("politeness hiccup")  # must not abort the probe

        res = check_liveness(
            "https://example.com",
            request_fn=lambda u, t: (200, None),
            throttle=throttle,
        )
        assert calls == ["https://example.com"]
        assert res["status"] == "ok"  # throttle error did not break liveness

    def test_custom_validator_can_block(self):
        def deny(url):
            raise RuntimeError("nope")

        res = check_liveness("https://example.com", request_fn=lambda u, t: (200, None), validator=deny)
        assert res["status"] == "blocked"

    def test_timeout_passed_to_request_fn(self):
        seen = {}

        def req(url, timeout):
            seen["timeout"] = timeout
            return (200, None)

        check_liveness("https://example.com", timeout=4.2, request_fn=req)
        assert seen["timeout"] == 4.2


# --------------------------------------------------------------------------
# check_sources -- toolbox wiring
# --------------------------------------------------------------------------
def _toolbox(tmp_path=None, **overrides):
    import tempfile

    cache_dir = str(tmp_path / "cache") if tmp_path is not None else tempfile.mkdtemp()
    return WebResearcherToolbox(
        ToolboxConfig(
            respect_robots=False,
            cache_dir=cache_dir,
            **overrides,
        )
    )


class TestCheckSourcesTool:
    def _patched(self, tb, mapping):
        """Patch check_liveness to return canned statuses by URL substring."""

        def fake(url, timeout=None, throttle=None):
            for substr, status in mapping.items():
                if substr in str(url):
                    return {"url": url, "status": status, "alive": status == "ok", "http_status": 200}
            return {"url": url, "status": "error", "alive": False, "http_status": None, "error": "x"}

        return patch("stitch_web_researcher.agent_tools.check_liveness", fake)

    def test_accepts_strings_and_dicts(self):
        tb = _toolbox(None)
        with self._patched(tb, {"a": "ok", "b": "ok"}):
            out = json.loads(tb.check_sources(["https://x.com/a", {"url": "https://x.com/b"}]))
        assert out["count"] == 2
        assert out["summary"]["ok"] == 2

    def test_dedupes_before_probing(self):
        tb = _toolbox(None)
        probed = []

        def fake(url, timeout=None, throttle=None):
            probed.append(str(url))
            return {"url": url, "status": "ok", "alive": True, "http_status": 200}

        with patch("stitch_web_researcher.agent_tools.check_liveness", fake):
            out = json.loads(tb.check_sources(["https://x.com/a", {"url": "https://x.com/a/"}]))
        # The trailing-slash variant collapses onto the first (no-slash) URL.
        assert out["count"] == 1
        assert probed == ["https://x.com/a"]

    def test_summary_counts_all_statuses(self):
        tb = _toolbox(None)
        mapping = {"ok": "ok", "bad": "unreachable", "priv": "blocked", "net": "error"}
        with self._patched(tb, mapping):
            out = json.loads(tb.check_sources(["https://ok.com", "https://bad.com", "https://priv.com", "https://net.com"]))
        assert out["summary"] == {"ok": 1, "unreachable": 1, "blocked": 1, "error": 1}

    def test_empty_returns_zero(self):
        tb = _toolbox(None)
        with patch("stitch_web_researcher.agent_tools.check_liveness", side_effect=AssertionError("should not probe")):
            out = json.loads(tb.check_sources([]))
        assert out["count"] == 0
        assert out["summary"] == {"ok": 0, "unreachable": 0, "blocked": 0, "error": 0}

    def test_ignores_blank_and_non_string(self):
        tb = _toolbox(None)
        with self._patched(tb, {"a": "ok"}):
            out = json.loads(tb.check_sources(["", "   ", 123, None, {"url": ""}, {"url": "https://x.com/a"}]))
        assert out["count"] == 1

    def test_exception_does_not_crash_tool(self):
        tb = _toolbox(None)
        with patch("stitch_web_researcher.agent_tools.check_liveness", side_effect=RuntimeError("kaboom")):
            out = json.loads(tb.check_sources(["https://x.com/a"]))
        assert "kaboom" in out["error"]
        assert out["results"] == []

    def test_timeout_configured_from_config(self, tmp_path):
        tb = _toolbox(tmp_path, liveness_timeout=3.5)
        seen = {}

        def fake(url, timeout=None, throttle=None):
            seen["timeout"] = timeout
            return {"url": url, "status": "ok", "alive": True, "http_status": 200}

        with patch("stitch_web_researcher.agent_tools.check_liveness", fake):
            tb.check_sources(["https://x.com/a"])
        assert seen["timeout"] == 3.5


# --------------------------------------------------------------------------
# research() -- dropped_dupes integration
# --------------------------------------------------------------------------
class _FakeProvider:
    def __init__(self, results):
        self._results = results

    def search(self, query, max_results=5):
        return [dict(r) for r in self._results][:max_results]


def _fake_fetch(text="# fetched\n\nbody"):
    def fake(url, use_smart=None):
        return (text, [], {}, "static")
    return fake


class TestResearchDroppedDupes:
    def test_reports_dropped_dupes(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.providers = [_FakeProvider([
            {"title": "A1", "url": "https://example.com/a", "snippet": "s1"},
            {"title": "A2", "url": "https://example.com/a/", "snippet": "s1"},  # dup
            {"title": "B1", "url": "https://example.com/b", "snippet": "s2"},
        ])]
        tb._fetch._fetch_html = _fake_fetch()

        result = json.loads(tb.research("topic", depth=3))
        # Two URLs collapse to one dupe; both sources still fetched.
        assert result["dropped_dupes"] == 1
        assert [s["url"] for s in result["sources"]] == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        assert result["count"] == 2

    def test_zero_dropped_when_no_duplicates(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.providers = [_FakeProvider([
            {"title": "A", "url": "https://example.com/a", "snippet": "s1"},
            {"title": "B", "url": "https://example.com/b", "snippet": "s2"},
        ])]
        tb._fetch._fetch_html = _fake_fetch()
        result = json.loads(tb.research("topic", depth=3))
        assert result["dropped_dupes"] == 0
