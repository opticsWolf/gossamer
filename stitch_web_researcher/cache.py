"""
Two-tier caching layer for stitch_web_researcher.

Provides:
  - In-memory LRU cache (fast, session-scoped)
  - File-based TTL cache (persistent across sessions)
  - Configurable max entries, TTL, and cache directory
"""

import json
import logging
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Cache:
    """
    Two-tier cache with in-memory LRU and file-based TTL layers.

    Usage
    -----
    >>> cache = Cache(
    ...     cache_dir=".web_research_cache",
    ...     ttl_seconds=3600,
    ...     max_memory_entries=100,
    ... )
    >>> result = cache.get("https://example.com")
    >>> if result is None:
    ...     result = fetch_from_network(...)
    ...     cache.put("https://example.com", result)
    """

    def __init__(
        self,
        cache_dir: str = ".web_research_cache",
        ttl_seconds: int = 3600,
        max_memory_entries: int = 100,
    ):
        self.cache_path = Path(cache_dir)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_memory_entries = max_memory_entries

        # In-memory LRU: OrderedDict keyed by URL
        self._memory: OrderedDict[str, tuple[str, float]] = OrderedDict()

        # Stats
        self._hits = 0
        self._misses = 0
        self._memory_hits = 0
        self._disk_hits = 0

    def get(self, key: str) -> Optional[str]:
        """
        Retrieve cached content using two-tier lookup.

        1. Check in-memory LRU (fastest)
        2. Check file-based TTL (persistent)
        3. Return None if not found or expired

        Parameters
        ----------
        key : str
            Cache key (typically a URL or hash).

        Returns
        -------
        str or None
            Cached content if found and valid, None otherwise.
        """
        # ── Tier 1: In-memory LRU ──────────────────────────────
        if key in self._memory:
            content, timestamp = self._memory[key]
            if time.time() - timestamp <= self.ttl_seconds:
                # Move to end (most recently used)
                self._memory.move_to_end(key)
                self._hits += 1
                self._memory_hits += 1
                return content
            else:
                # Expired — remove from memory
                del self._memory[key]

        # ── Tier 2: File-based TTL ─────────────────────────────
        content = self._disk_get(key)
        if content is not None:
            self._hits += 1
            self._disk_hits += 1
            # Promote to memory (write-through)
            self._memory_put(key, content)
            return content

        # ── Cache miss ─────────────────────────────────────────
        self._misses += 1
        return None

    def put(self, key: str, content: str) -> None:
        """
        Store content in both memory and disk (write-through).

        Parameters
        ----------
        key : str
            Cache key (typically a URL or hash).
        content : str
            Content to cache.
        """
        self._memory_put(key, content)
        self._disk_put(key, content)

    def clear(self) -> None:
        """Clear both memory and disk caches."""
        self._memory.clear()
        self._clear_disk()

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        disk_size = self._disk_size_bytes()
        return {
            "memory_entries": len(self._memory),
            "memory_max": self.max_memory_entries,
            "disk_size_bytes": disk_size,
            "disk_size_human": self._human_size(disk_size),
            "cache_dir": str(self.cache_path),
            "ttl_seconds": self.ttl_seconds,
            "total_hits": self._hits,
            "total_misses": self._misses,
            "memory_hits": self._memory_hits,
            "disk_hits": self._disk_hits,
            "hit_rate": (
                round(self._hits / (self._hits + self._misses), 4)
                if (self._hits + self._misses) > 0
                else 0.0
            ),
        }

    # ── Internal: Memory LRU ──────────────────────────────────

    def _memory_put(self, key: str, content: str) -> None:
        """Insert into in-memory LRU, evicting oldest if at capacity."""
        if key in self._memory:
            self._memory.move_to_end(key)
            self._memory[key] = (content, time.time())
        else:
            # Evict oldest entries if at capacity
            while len(self._memory) >= self.max_memory_entries:
                self._memory.popitem(last=False)
            self._memory[key] = (content, time.time())

    # ── Internal: Disk TTL ────────────────────────────────────

    def _disk_key(self, key: str) -> str:
        """Convert cache key to filename-safe string."""
        import hashlib
        return hashlib.md5(key.encode()).hexdigest()

    def _disk_get(self, key: str) -> Optional[str]:
        """Retrieve from file-based cache if not expired."""
        safe_key = self._disk_key(key)
        cache_file = self.cache_path / f"{safe_key}.cache"
        meta_file = self.cache_path / f"{safe_key}.meta"

        if not cache_file.exists() or not meta_file.exists():
            return None

        try:
            meta = json.loads(meta_file.read_text())
            if time.time() - meta["timestamp"] > self.ttl_seconds:
                # Expired — clean up
                self._remove_disk_files(safe_key)
                return None
            return cache_file.read_text(encoding="utf-8")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Disk cache read error for %s: %s", key, e)
            return None

    def _disk_put(self, key: str, content: str) -> None:
        """Store content in file-based cache with metadata."""
        safe_key = self._disk_key(key)
        cache_file = self.cache_path / f"{safe_key}.cache"
        meta_file = self.cache_path / f"{safe_key}.meta"

        try:
            cache_file.write_text(content, encoding="utf-8")
            meta_file.write_text(json.dumps({"timestamp": time.time()}))
        except OSError as e:
            logger.warning("Disk cache write error for %s: %s", key, e)

    def _remove_disk_files(self, safe_key: str) -> None:
        """Remove cached files for a given key."""
        cache_file = self.cache_path / f"{safe_key}.cache"
        meta_file = self.cache_path / f"{safe_key}.meta"
        for f in (cache_file, meta_file):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass

    def _clear_disk(self) -> None:
        """Remove all cached files from disk."""
        try:
            shutil.rmtree(self.cache_path)
            self.cache_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Failed to clear disk cache: %s", e)

    def _disk_size_bytes(self) -> int:
        """Calculate total size of disk cache in bytes."""
        total = 0
        try:
            for f in self.cache_path.iterdir():
                if f.is_file():
                    total += f.stat().st_size
        except OSError:
            pass
        return total

    @staticmethod
    def _human_size(nbytes: int) -> str:
        """Format bytes as human-readable string."""
        for unit in ("B", "KB", "MB", "GB"):
            if nbytes < 1024:
                return f"{nbytes:.1f} {unit}"
            nbytes /= 1024
        return f"{nbytes:.1f} TB"
