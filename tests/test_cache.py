"""
Tests for the two-tier Cache layer.
"""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from py_web_researcher.cache import Cache


# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Return a temporary directory for cache tests."""
    return str(tmp_path / "test_cache")


@pytest.fixture
def cache(tmp_cache_dir):
    """Return a Cache instance with short TTL for testing."""
    return Cache(cache_dir=tmp_cache_dir, ttl_seconds=2, max_memory_entries=5)


# ────────────────────────────────────────────────────────────────
# Basic put/get
# ────────────────────────────────────────────────────────────────

class TestCacheBasic:
    def test_put_and_get(self, cache):
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing(self, cache):
        assert cache.get("nonexistent") is None

    def test_overwrite(self, cache):
        cache.put("key1", "value1")
        cache.put("key1", "value2")
        assert cache.get("key1") == "value2"

    def test_multiple_keys(self, cache):
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")
        assert cache.get("a") == "1"
        assert cache.get("b") == "2"
        assert cache.get("c") == "3"

    def test_empty_value(self, cache):
        cache.put("empty", "")
        assert cache.get("empty") == ""

    def test_large_value(self, cache):
        large = "x" * 100_000
        cache.put("large", large)
        assert cache.get("large") == large


# ────────────────────────────────────────────────────────────────
# TTL expiry
# ────────────────────────────────────────────────────────────────

class TestCacheTTL:
    def test_ttl_expiry(self, tmp_cache_dir):
        short_cache = Cache(cache_dir=tmp_cache_dir, ttl_seconds=1, max_memory_entries=10)
        short_cache.put("key1", "value1")
        assert short_cache.get("key1") == "value1"
        time.sleep(1.5)
        assert short_cache.get("key1") is None

    def test_ttl_not_expired(self, tmp_cache_dir):
        long_cache = Cache(cache_dir=tmp_cache_dir, ttl_seconds=60, max_memory_entries=10)
        long_cache.put("key1", "value1")
        assert long_cache.get("key1") == "value1"


# ────────────────────────────────────────────────────────────────
# LRU eviction
# ────────────────────────────────────────────────────────────────

class TestCacheLRU:
    def test_evict_oldest(self, tmp_cache_dir):
        lru_cache = Cache(cache_dir=tmp_cache_dir, ttl_seconds=3600, max_memory_entries=3)
        lru_cache.put("a", "1")
        lru_cache.put("b", "2")
        lru_cache.put("c", "3")
        # Add 4th key — should evict "a" from memory
        lru_cache.put("d", "4")
        assert lru_cache._memory.get("a") is None  # evicted from memory
        assert lru_cache._memory.get("d") is not None

    def test_access_refreshes_lru(self, tmp_cache_dir):
        lru_cache = Cache(cache_dir=tmp_cache_dir, ttl_seconds=3600, max_memory_entries=3)
        lru_cache.put("a", "1")
        lru_cache.put("b", "2")
        lru_cache.put("c", "3")
        # Access "a" to make it recently used
        lru_cache.get("a")
        # Add 4th key — should evict "b" (oldest unused)
        lru_cache.put("d", "4")
        assert lru_cache._memory.get("a") is not None  # still in memory
        assert lru_cache._memory.get("b") is None  # evicted


# ────────────────────────────────────────────────────────────────
# Two-tier behavior
# ────────────────────────────────────────────────────────────────

class TestTwoTierCache:
    def test_promote_disk_to_memory(self, cache):
        # First put writes to both memory and disk
        cache.put("key1", "value1")
        # Clear memory
        cache._memory.clear()
        # Get should read from disk and promote to memory
        result = cache.get("key1")
        assert result == "value1"
        assert "key1" in cache._memory

    def test_disk_persists_across_instances(self, tmp_cache_dir):
        c1 = Cache(cache_dir=tmp_cache_dir, ttl_seconds=3600)
        c1.put("persist", "data")
        c2 = Cache(cache_dir=tmp_cache_dir, ttl_seconds=3600)
        assert c2.get("persist") == "data"


# ────────────────────────────────────────────────────────────────
# Clear
# ────────────────────────────────────────────────────────────────

class TestCacheClear:
    def test_clear_all(self, cache):
        cache.put("a", "1")
        cache.put("b", "2")
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert len(cache._memory) == 0

    def test_clear_disk_files(self, cache):
        cache.put("a", "1")
        cache_files = list(Path(cache.cache_path).glob("*.cache"))
        assert len(cache_files) > 0
        cache.clear()
        cache_files_after = list(Path(cache.cache_path).glob("*.cache"))
        assert len(cache_files_after) == 0


# ────────────────────────────────────────────────────────────────
# Stats
# ────────────────────────────────────────────────────────────────

class TestCacheStats:
    def test_stats_structure(self, cache):
        stats = cache.stats()
        assert "memory_entries" in stats
        assert "memory_max" in stats
        assert "disk_size_bytes" in stats
        assert "disk_size_human" in stats
        assert "cache_dir" in stats
        assert "ttl_seconds" in stats
        assert "total_hits" in stats
        assert "total_misses" in stats
        assert "memory_hits" in stats
        assert "disk_hits" in stats
        assert "hit_rate" in stats

    def test_stats_hits_misses(self, cache):
        cache.put("a", "1")
        cache.get("a")  # hit
        cache.get("missing")  # miss
        stats = cache.stats()
        assert stats["total_hits"] == 1
        assert stats["total_misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_stats_disk_size(self, cache):
        cache.put("data", "x" * 1000)
        stats = cache.stats()
        assert stats["disk_size_bytes"] > 0
        assert "KB" in stats["disk_size_human"] or "B" in stats["disk_size_human"]

    def test_stats_empty(self, cache):
        stats = cache.stats()
        assert stats["memory_entries"] == 0
        assert stats["total_hits"] == 0
        assert stats["hit_rate"] == 0.0


# ────────────────────────────────────────────────────────────────
# Human size formatting
# ────────────────────────────────────────────────────────────────

class TestHumanSize:
    def test_bytes(self):
        assert Cache._human_size(0) == "0.0 B"
        assert Cache._human_size(500) == "500.0 B"

    def test_kb(self):
        assert "KB" in Cache._human_size(1500)

    def test_mb(self):
        assert "MB" in Cache._human_size(1_500_000)

    def test_gb(self):
        assert "GB" in Cache._human_size(1_500_000_000)
