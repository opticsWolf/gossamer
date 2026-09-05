"""S6: clear_cache is scoped to cache-owned files.

Before the fix, ``Cache.clear()`` ran ``shutil.rmtree(cache_dir)``.
``cache_dir`` is user-configurable and ``clear_cache`` is exposed to the
LLM over MCP, so a misconfigured path was a data-loss footgun: the
entire directory (and everything in it) vanished. Now clearing removes
only ``*.cache``, ``*.meta`` and ``*.tmp`` files, never the directory
itself, and never unrelated files.
"""

import json

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.cache import Cache


class TestScopedClearDisk:
    def test_removes_cache_files_keeps_directory_and_foreign_files(self, tmp_path):
        c = Cache(tmp_path / "cache")
        c.put("k1", "v1")
        c.put("k2", "v2")

        # Plant a foreign file and a foreign subdirectory in the cache dir.
        (tmp_path / "cache" / "notes.txt").write_text("user data")
        foreign_dir = tmp_path / "cache" / "user_stuff"
        foreign_dir.mkdir()
        (foreign_dir / "keep.txt").write_text("user data")

        c.clear()

        # Directory survives with all foreign content intact...
        assert (tmp_path / "cache").is_dir()
        assert (tmp_path / "cache" / "notes.txt").read_text() == "user data"
        assert (foreign_dir / "keep.txt").read_text() == "user data"
        # ...and all cache-owned files are gone.
        assert not list((tmp_path / "cache").glob("*.cache"))
        assert not list((tmp_path / "cache").glob("*.meta"))
        assert not list((tmp_path / "cache").glob("*.tmp"))
        # Memory tier cleared too.
        assert c.stats()["memory_entries"] == 0
        assert c.get("k1") is None

    def test_clear_missing_directory_is_safe(self, tmp_path):
        c = Cache(tmp_path / "does_not_exist")
        c.clear()  # must not raise
        c.clear()  # ...and not on the second call either

    def test_clear_removes_stale_tmp_files(self, tmp_path):
        d = tmp_path / "cache"
        d.mkdir()
        (d / "leftover.cache.deadbeef.tmp").write_text("half-written")
        c = Cache(d)
        # Re-init already cleans stale tmp; clear() must handle them too
        # if they appear later.
        (d / "new.cache.cafebabe.tmp").write_text("half-written")
        c.clear()
        assert not list(d.glob("*.tmp"))


class TestToolboxClearCache:
    def _toolbox(self, tmp_path) -> WebResearcherToolbox:
        return WebResearcherToolbox(
            ToolboxConfig(
                cache_dir=str(tmp_path / "cache"),
                domain_delay=0.0,
                fetch_delay=0.0,
                ddgs_delay=0.0,
                fetch_mode="static",
            )
        )

    def test_scoped_clear_resets_visited_and_keeps_foreign_files(self, tmp_path):
        tb = self._toolbox(tmp_path)
        tb.visited_urls["https://example.com/old"] = None  # M7: bounded FIFO
        tb.cache.put("page:example", json.dumps({"markdown": "x"}))
        (tmp_path / "cache" / "readme.md").write_text("not a cache file")

        out = json.loads(tb.clear_cache())

        assert out["cache_cleared"] is True
        # C3 behavior preserved: visited set reset so URLs can be re-fetched.
        assert len(tb.visited_urls) == 0  # M7: visited is now a bounded FIFO
        assert tb._in_flight == set()
        # Cache file gone, foreign file and directory intact.
        assert not list((tmp_path / "cache").glob("*.cache"))
        assert (tmp_path / "cache").is_dir()
        assert (tmp_path / "cache" / "readme.md").read_text() == "not a cache file"
