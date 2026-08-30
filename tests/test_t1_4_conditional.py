"""Tier 1.4 -- conditional revalidation of stale page-cache entries.

A static page entry stores the ETag / Last-Modified its server advertised.
When that entry later expires, ``inspect_html_page`` revalidates with a
conditional request (If-None-Match / If-Modified-Since) instead of
re-downloading the whole page:

* ``304 Not Modified`` -> the stored copy is re-freshened and re-served
  with no body download. ``revalidated`` is true, ``cache_hit`` is true,
  and the content / ``fetched_at`` / ``http_status`` are the originals.
* ``200`` -> the server changed; the fresh body is stored and served as a
  normal new fetch (``revalidated`` / ``cache_hit`` both false).
* No validators on the entry, a non-static entry, or a conditional
  failure -> fall back to a full fetch.

Revalidation is opt-out via ``ToolboxConfig.conditional_revalidation``
(wired to ``STITCH_CONDITIONAL_REVALIDATE`` in the MCP server).

All tests are deterministic: the network is a fake
``fetch_html_conditional`` and cache expiry is forced by ageing the
on-disk TTL metadata, so no live HTTP and no sleeps are involved.
"""

import json
import time

from stitch_web_researcher import fetch
from stitch_web_researcher import agent_tools
from stitch_web_researcher.agent_tools import (
    ToolboxConfig,
    WebResearcherToolbox,
)
from stitch_web_researcher.cache import Cache

PAGE = "Original page body that outlives a 304 round trip."
PROV_200 = (200, "https://example.com/a", "text/html; charset=utf-8")
PROV_304 = (304, "https://example.com/a", None)
URL = "https://example.com/a"


def _toolbox(tmp_path, **config_kwargs) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


def _meta_with_prov(etag=None, last_modified=None,
                    fetched_at="2026-01-01T00:00:00+00:00"):
    """A stored page-cache metadata dict carrying Tier 1.3 provenance."""
    prov = {
        "fetched_at": fetched_at,
        "http_status": 200,
        "final_url": "https://example.com/a",
        "content_type": "text/html; charset=utf-8",
    }
    if etag is not None:
        prov["etag"] = etag
    if last_modified is not None:
        prov["last_modified"] = last_modified
    return {"provenance": prov}


def _seed_expired_page(tb, url, md, links, meta, method="static",
                       age_seconds=10_000):
    """Store a page entry, then force it to be expired.

    The in-memory copy is dropped and the on-disk TTL timestamp is aged,
    so ``Cache.get`` reports a miss (and purges) while ``Cache.get_stale``
    can still read the validators - exactly the state revalidation needs.
    """
    tb._fetch._page_cache_put(url, md, links, meta, method)
    key = "page:" + tb._cache_key(url)
    tb.cache._memory.pop(key, None)
    safe = tb.cache._disk_key(key)
    meta_file = tb.cache.cache_path / f"{safe}.meta"
    meta_file.write_text(json.dumps({"timestamp": time.time() - age_seconds}))


def _cond_fake(payload, calls=None):
    """Fake ``fetch_html_conditional`` returning a fixed 8-tuple.

    Records each call so tests can assert whether a conditional (etag /
    last_modified supplied) or a plain fetch happened.
    """

    def fake(url, cap, max_bytes, etag=None, last_modified=None):
        if calls is not None:
            calls.append({"url": url, "etag": etag, "lm": last_modified})
        return payload

    return fake


class TestConditionalRevalidation:
    def test_fresh_fetch_stores_validators(self, tmp_path, monkeypatch):
        """A fresh static fetch records the ETag / Last-Modified for later."""
        monkeypatch.setattr(
            fetch, "fetch_html_conditional",
            _cond_fake((False, "<html></html>", PAGE, [], 0,
                        PROV_200, '"v1"', "Wed, 01 Jan 2026 00:00:00 GMT")),
        )
        tb = _toolbox(tmp_path)
        data = json.loads(tb.inspect_html_page(URL))
        assert data["revalidated"] is False
        assert data["cache_hit"] is False
        # The validators must be persisted in the page-cache entry.
        raw = tb.cache.get_stale("page:" + tb._cache_key(URL))
        entry = json.loads(raw)
        prov = entry["meta"]["provenance"]
        assert prov["etag"] == '"v1"'
        assert prov["last_modified"] == "Wed, 01 Jan 2026 00:00:00 GMT"

    def test_304_revalidates_preserves_content_and_time(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            fetch, "fetch_html_conditional",
            _cond_fake((True, "", "", [], 0, PROV_304, '"v1"', None), calls),
        )
        tb = _toolbox(tmp_path)
        _seed_expired_page(
            tb, URL, PAGE, [("https://example.com/l", "L")],
            _meta_with_prov(etag='"v1"'),
        )
        data = json.loads(tb.inspect_html_page(URL))
        # A conditional request was sent carrying the stored ETag.
        assert calls == [{"url": URL, "etag": '"v1"', "lm": None}]
        assert data["revalidated"] is True
        assert data["cache_hit"] is True
        # Content and the original fetch facts are preserved.
        assert data["markdown"] == PAGE
        assert data["fetched_at"] == "2026-01-01T00:00:00+00:00"
        assert data["http_status"] == 200
        # The re-put re-freshened the entry (TTL reset) and kept the ETag.
        raw = tb.cache.get_stale("page:" + tb._cache_key(URL))
        assert json.loads(raw)["meta"]["provenance"]["etag"] == '"v1"'

    def test_304_adopts_rotated_validators(self, tmp_path, monkeypatch):
        """A 304 that advertises new validators updates the stored copy."""
        monkeypatch.setattr(
            fetch, "fetch_html_conditional",
            _cond_fake((True, "", "", [], 0, PROV_304, '"v2"', "NEW-LM")),
        )
        tb = _toolbox(tmp_path)
        _seed_expired_page(
            tb, URL, PAGE, [], _meta_with_prov(etag='"v1"'),
        )
        data = json.loads(tb.inspect_html_page(URL))
        assert data["revalidated"] is True
        raw = tb.cache.get_stale("page:" + tb._cache_key(URL))
        prov = json.loads(raw)["meta"]["provenance"]
        assert prov["etag"] == '"v2"'
        assert prov["last_modified"] == "NEW-LM"
        # fetched_at is the original, not the revalidation time.
        assert prov["fetched_at"] == "2026-01-01T00:00:00+00:00"

    def test_200_refetches_new_content(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            fetch, "fetch_html_conditional",
            _cond_fake((False, "<html></html>", "NEW CONTENT", [], 0,
                        PROV_200, '"v2"', None), calls),
        )
        tb = _toolbox(tmp_path)
        _seed_expired_page(
            tb, URL, PAGE, [], _meta_with_prov(etag='"v1"'),
        )
        data = json.loads(tb.inspect_html_page(URL))
        # The conditional request went out, the server said 200, so the
        # fresh body is served as a normal new fetch (not a cache hit).
        assert calls == [{"url": URL, "etag": '"v1"', "lm": None}]
        assert data["revalidated"] is False
        assert data["cache_hit"] is False
        assert data["markdown"] == "NEW CONTENT"

    def test_no_validators_plain_fetch(self, tmp_path, monkeypatch):
        """An entry without ETag / Last-Modified cannot be revalidated."""
        calls = []
        monkeypatch.setattr(
            fetch, "fetch_html_conditional",
            _cond_fake((False, "<html></html>", "FRESH", [], 0,
                        PROV_200, None, None), calls),
        )
        tb = _toolbox(tmp_path)
        _seed_expired_page(
            tb, URL, PAGE, [], _meta_with_prov(),  # no etag / last_modified
        )
        data = json.loads(tb.inspect_html_page(URL))
        assert data["revalidated"] is False
        assert data["cache_hit"] is False
        assert data["markdown"] == "FRESH"
        # No conditional was attempted - the single call is a plain fetch.
        assert calls and calls[0]["etag"] is None and calls[0]["lm"] is None

    def test_browser_entry_skips_revalidation(self, tmp_path, monkeypatch):
        """Only static entries revalidate; a stale browser entry refetches."""
        calls = []
        monkeypatch.setattr(
            fetch, "fetch_html_conditional",
            _cond_fake((False, "<html></html>", "FRESH", [], 0,
                        PROV_200, None, None), calls),
        )
        tb = _toolbox(tmp_path, fetch_mode="static")
        _seed_expired_page(
            tb, URL, PAGE, [], _meta_with_prov(etag='"v1"'), method="browser",
        )
        data = json.loads(tb.inspect_html_page(URL))
        assert data["revalidated"] is False
        assert data["cache_hit"] is False
        assert data["markdown"] == "FRESH"
        # The stored ETag was NOT used for a conditional request.
        assert calls and calls[0]["etag"] is None

    def test_flag_off_disables_revalidation(self, tmp_path, monkeypatch):
        """conditional_revalidation=False forces a plain full fetch."""
        calls = []
        monkeypatch.setattr(
            fetch, "fetch_html_conditional",
            _cond_fake((False, "<html></html>", "FRESH", [], 0,
                        PROV_200, None, None), calls),
        )
        tb = _toolbox(tmp_path, conditional_revalidation=False)
        _seed_expired_page(
            tb, URL, PAGE, [], _meta_with_prov(etag='"v1"'),
        )
        data = json.loads(tb.inspect_html_page(URL))
        assert data["revalidated"] is False
        assert data["cache_hit"] is False
        assert data["markdown"] == "FRESH"
        # No conditional attempt: the stale entry was never read.
        assert calls and calls[0]["etag"] is None

    def test_conditional_failure_falls_back_to_full_fetch(self, tmp_path, monkeypatch):
        """If the conditional request raises, fall back to a full fetch."""
        calls = []

        def fake(url, cap, max_bytes, etag=None, last_modified=None):
            calls.append({"url": url, "etag": etag, "lm": last_modified})
            if etag is not None or last_modified is not None:
                raise RuntimeError("conditional fetch failed")
            return (False, "<html></html>", "FRESH", [], 0, PROV_200, None, None)

        monkeypatch.setattr(fetch, "fetch_html_conditional", fake)
        tb = _toolbox(tmp_path)
        _seed_expired_page(
            tb, URL, PAGE, [], _meta_with_prov(etag='"v1"'),
        )
        data = json.loads(tb.inspect_html_page(URL))
        assert data["revalidated"] is False
        assert data["cache_hit"] is False
        assert data["markdown"] == "FRESH"
        # One conditional attempt (failed) + one plain fetch (succeeded).
        assert [c["etag"] for c in calls] == ['"v1"', None]

    def test_stale_entry_corrupt_returns_none(self, tmp_path):
        tb = _toolbox(tmp_path)
        key = "page:" + tb._cache_key(URL)
        tb.cache.put(key, "{not valid json")
        assert tb._fetch._stale_page_entry(URL) is None

    def test_stale_entry_missing_returns_none(self, tmp_path):
        tb = _toolbox(tmp_path)
        assert tb._fetch._stale_page_entry("https://example.com/never") is None


class TestCacheGetStale:
    """``Cache.get_stale`` reads ignoring TTL without purging (Tier 1.4)."""

    def test_fresh_entry_returns_content(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path), ttl_seconds=3600)
        cache.put("k", "value")
        assert cache.get_stale("k") == "value"

    def test_missing_key_returns_none(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path), ttl_seconds=3600)
        assert cache.get_stale("absent") is None

    def test_reads_expired_disk_entry_without_purging(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path), ttl_seconds=1)
        cache.put("k", "old")
        # Age the on-disk TTL metadata so the entry is expired.
        safe = cache._disk_key("k")
        (cache.cache_path / f"{safe}.meta").write_text(
            json.dumps({"timestamp": time.time() - 1000})
        )
        cache._memory.clear()  # drop the fresh in-memory copy

        # get_stale still sees it (TTL ignored, no purge)...
        assert cache.get_stale("k") == "old"
        # ...while a normal get reports a miss and purges it.
        assert cache.get("k") is None
        assert cache.get_stale("k") is None

    def test_get_stale_does_not_purge_expired_entry(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path), ttl_seconds=1)
        cache.put("k", "old")
        safe = cache._disk_key("k")
        (cache.cache_path / f"{safe}.meta").write_text(
            json.dumps({"timestamp": time.time() - 1000})
        )
        cache._memory.clear()
        # Reading stale must NOT delete the disk file.
        cache.get_stale("k")
        assert (cache.cache_path / f"{safe}.cache").exists()

    def test_get_stale_ignores_memory_ttl(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path), ttl_seconds=1)
        cache.put("k", "value")
        # Age only the in-memory timestamp; disk stays fresh.
        cache._memory["k"] = ("value", time.time() - 1000)
        # get() misses the aged memory entry but promotes from disk...
        assert cache.get("k") == "value"
        # ...and get_stale reads memory directly, TTL ignored.
        cache._memory["k"] = ("value", time.time() - 1000)
        assert cache.get_stale("k") == "value"
