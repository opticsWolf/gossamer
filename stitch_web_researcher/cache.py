"""
Two-tier caching layer for stitch_web_researcher.

Provides:
  - In-memory LRU cache (fast, session-scoped)
  - File-based TTL cache (persistent across sessions)
  - Configurable max entries, TTL, and cache directory
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
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

        # S5: guards the memory tier and the stat counters — the MCP SDK
        # dispatches synchronous tools on worker threads, so several
        # threads can be inside this Cache at once.
        self._lock = threading.Lock()

        # Stats
        self._hits = 0
        self._misses = 0
        self._memory_hits = 0
        self._disk_hits = 0

        # Drop *.tmp leftovers from a crash mid-write (S5).
        self._cleanup_stale_tmp()

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
        with self._lock:
            if key in self._memory:
                content, timestamp = self._memory[key]
                if time.time() - timestamp <= self.ttl_seconds:
                    # Move to end (most recently used)
                    self._memory.move_to_end(key)
                    self._hits += 1
                    self._memory_hits += 1
                    return content
                # Expired — remove from memory
                del self._memory[key]

        # ── Tier 2: File-based TTL ─────────────────────────────
        content = self._disk_get(key)
        if content is not None:
            with self._lock:
                self._hits += 1
                self._disk_hits += 1
            # Promote to memory (write-through)
            self._memory_put(key, content)
            return content

        # ── Cache miss ─────────────────────────────────────────
        with self._lock:
            self._misses += 1
        return None

    def get_stale(self, key: str) -> Optional[str]:
        """Read a cached entry **ignoring TTL**, without purging it.

        Tier 1.4: conditional revalidation needs an expired entry's stored
        content and validators *after* the normal ``get`` has already
        treated it as a miss (and purged it from disk). This method:

        * checks the memory tier without enforcing the TTL,
        * reads the disk file even when it is expired (no deletion),
        * does not touch hit/miss stats or re-insert into memory.

        Returns the raw cached string, or ``None`` if the key was never
        stored.
        """
        # Memory tier, TTL ignored.
        with self._lock:
            item = self._memory.get(key)
        if item is not None:
            return item[0]

        # Disk tier, TTL ignored, no purge.
        safe_key = self._disk_key(key)
        cache_file = self.cache_path / f"{safe_key}.cache"
        if not cache_file.exists():
            return None
        try:
            return cache_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Stale disk cache read error for %s: %s", key, e)
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
        """Clear both memory and disk caches.

        S6: disk clearing is *scoped* to cache-owned files -- the
        configured directory itself is never deleted and unrelated
        files are never touched, because ``cache_dir`` is
        user-configurable and ``clear_cache`` is LLM-invocable.
        """
        with self._lock:
            self._memory.clear()
        self._clear_disk()

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        disk_size = self._disk_size_bytes()
        with self._lock:
            entries = len(self._memory)
            hits, misses = self._hits, self._misses
            mem_hits, disk_hits = self._memory_hits, self._disk_hits
        return {
            "memory_entries": entries,
            "memory_max": self.max_memory_entries,
            "disk_size_bytes": disk_size,
            "disk_size_human": self._human_size(disk_size),
            "cache_dir": str(self.cache_path),
            "ttl_seconds": self.ttl_seconds,
            "total_hits": hits,
            "total_misses": misses,
            "memory_hits": mem_hits,
            "disk_hits": disk_hits,
            "hit_rate": (
                round(hits / (hits + misses), 4)
                if (hits + misses) > 0
                else 0.0
            ),
        }

    # ── Internal: Memory LRU ──────────────────────────────────

    def _memory_put(self, key: str, content: str) -> None:
        """Insert into in-memory LRU, evicting oldest if at capacity."""
        with self._lock:
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
        """Convert cache key to filename-safe string.

        S7: blake2b instead of MD5 -- same 16-byte digest length, but
        MD5 trips security scanners and FIPS-mode interpreters even
        when it is not used as a security boundary.
        """
        return hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()

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
        """Store content in file-based cache with metadata.

        S5: each file is written to a temp file in the same directory and
        ``os.replace``'d into place — atomic on POSIX *and* Windows — so a
        concurrent reader sees either the old file or the new one, never a
        half-written body.
        """
        safe_key = self._disk_key(key)
        cache_file = self.cache_path / f"{safe_key}.cache"
        meta_file = self.cache_path / f"{safe_key}.meta"

        try:
            self._atomic_write(cache_file, content)
            self._atomic_write(meta_file, json.dumps({"timestamp": time.time()}))
        except OSError as e:
            logger.warning("Disk cache write error for %s: %s", key, e)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Atomically write ``text`` to ``path`` (temp file + os.replace)."""
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _cleanup_stale_tmp(self) -> None:
        """Remove ``*.tmp`` leftovers from a crash mid-write (S5)."""
        try:
            for f in self.cache_path.glob("*.tmp"):
                try:
                    f.unlink()
                except OSError:
                    pass
        except OSError:
            pass

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
        """Remove cache-owned files (``*.cache``, ``*.meta``, ``*.tmp``).

        S6: never ``shutil.rmtree`` the configured directory and never
        touch unrelated files or subdirectories. The directory is
        user-configurable and ``clear_cache`` can be invoked by an LLM,
        so a misconfigured path must not become a data-loss path.
        """
        if not self.cache_path.exists():
            return
        removed = 0
        for f in self.cache_path.iterdir():
            if f.is_file() and f.suffix in (".cache", ".meta", ".tmp"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    logger.debug(
                        "Could not remove cache file %s", f, exc_info=True
                    )
        logger.info(
            "Disk cache cleared (%d files removed from %s)",
            removed,
            self.cache_path,
        )

    def _disk_size_bytes(self) -> int:
        """Calculate total size of disk cache in bytes.

        Only cache-owned files count (S5: temp files mid-replace are
        transient and may be in flight from another thread).
        """
        total = 0
        try:
            for f in self.cache_path.iterdir():
                if f.is_file() and f.suffix in (".cache", ".meta"):
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
