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
        max_disk_bytes: int = 0,
    ):
        self.cache_path = Path(cache_dir)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_memory_entries = max_memory_entries
        # Tier 2.5: optional byte cap for the disk tier (0 = unlimited).
        # When set, least-recently-used entries are evicted to stay under it;
        # file mtime is the LRU signal and is touched on every disk read.
        self.max_disk_bytes = max_disk_bytes

        # In-memory LRU: OrderedDict keyed by URL
        self._memory: OrderedDict[str, tuple[str, float]] = OrderedDict()

        # S5: guards the memory tier and the stat counters — the MCP SDK
        # dispatches synchronous tools on worker threads, so several
        # threads can be inside this Cache at once.
        self._lock = threading.Lock()
        # Tier 2.5: guards all disk mutations (write, eviction, prune,
        # clear) so size-cap enforcement never races with a writer.
        self._disk_lock = threading.Lock()

        # Stats
        self._hits = 0
        self._misses = 0
        self._memory_hits = 0
        self._disk_hits = 0
        self._evictions = 0

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
        with self._disk_lock:
            self._disk_put(key, content)
            self._enforce_disk_cap()

    def clear(self) -> None:
        """Clear both memory and disk caches.

        S6: disk clearing is *scoped* to cache-owned files -- the
        configured directory itself is never deleted and unrelated
        files are never touched, because ``cache_dir`` is
        user-configurable and ``clear_cache`` is LLM-invocable.
        """
        with self._lock:
            self._memory.clear()
        with self._disk_lock:
            self._clear_disk()

    def prune(self) -> Dict[str, Any]:
        """Remove expired entries, enforce the size cap, drop stale tmp.

        Tier 2.5: disk entries are TTL-expired lazily on read, so entries
        for URLs never requested again linger forever. ``prune`` sweeps them
        proactively and keeps the cache under ``max_disk_bytes``. Safe to call
        on a schedule; returns a summary of what it did.
        """
        with self._disk_lock:
            removed_expired = self._prune_expired()
            removed_evicted = self._enforce_disk_cap()
            self._cleanup_stale_tmp()
            entries = self._disk_entries()
        return {
            "removed_expired": removed_expired,
            "removed_evicted": removed_evicted,
            "total_entries": len(entries),
            "total_bytes": sum(size for _m, size, _p in entries),
            "max_disk_bytes": self.max_disk_bytes,
        }

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        entries = self._disk_entries()
        disk_size = sum(size for _m, size, _p in entries)
        with self._lock:
            mem_entries = len(self._memory)
            hits, misses = self._hits, self._misses
            mem_hits, disk_hits = self._memory_hits, self._disk_hits
            evictions = self._evictions
        return {
            "memory_entries": mem_entries,
            "memory_max": self.max_memory_entries,
            "disk_entries": len(entries),
            "disk_size_bytes": disk_size,
            "disk_size_human": self._human_size(disk_size),
            "disk_max_bytes": self.max_disk_bytes,
            "disk_evictions": evictions,
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
            content = cache_file.read_text(encoding="utf-8")
            # Tier 2.5: refresh the mtime so size-cap LRU eviction keeps
            # actively-read entries (cheap metadata-only update).
            try:
                os.utime(cache_file, None)
            except OSError:
                pass
            return content
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

    def _disk_entries(self):
        """Snapshot of disk entries as (mtime, size, prefix), oldest first.

        ``size`` counts both the ``.cache`` and ``.meta`` files. Only file
        metadata is read (no content, no meta parse), so this is cheap enough
        to run after every write. Callers that mutate based on the snapshot
        must hold ``_disk_lock``.
        """
        entries = []
        try:
            files = list(self.cache_path.iterdir())
        except OSError:
            return entries
        for f in files:
            if not f.is_file() or f.suffix != ".cache":
                continue
            prefix = f.name[: -len(".cache")]
            meta = self.cache_path / f"{prefix}.meta"
            try:
                st = f.stat()
            except OSError:
                continue
            size = st.st_size
            if meta.exists():
                try:
                    size += meta.stat().st_size
                except OSError:
                    pass
            entries.append((st.st_mtime, size, prefix))
        entries.sort()
        return entries

    def _enforce_disk_cap(self) -> int:
        """Evict least-recently-used entries until under ``max_disk_bytes``.

        No-op when the cap is 0 (unlimited) or the cache already fits. Returns
        the number of entries evicted. Caller must hold ``_disk_lock``.
        """
        if self.max_disk_bytes <= 0:
            return 0
        entries = self._disk_entries()
        total = sum(size for _m, size, _p in entries)
        if total <= self.max_disk_bytes:
            return 0
        before = total
        evicted = 0
        for _mtime, size, prefix in entries:  # oldest first
            if total <= self.max_disk_bytes:
                break
            self._remove_disk_files(prefix)
            total -= size
            evicted += 1
        if evicted:
            self._evictions += evicted
            logger.info(
                "Disk cache over cap (%.1f > %.1f KiB): evicted %d oldest entries"
                " (now %.1f KiB)",
                before / 1024,
                self.max_disk_bytes / 1024,
                evicted,
                total / 1024,
            )
        return evicted

    def _prune_expired(self) -> int:
        """Remove TTL-expired entries. Caller must hold ``_disk_lock``."""
        now = time.time()
        removed = 0
        for _mtime, _size, prefix in self._disk_entries():
            meta_file = self.cache_path / f"{prefix}.meta"
            if not meta_file.exists():
                continue
            try:
                meta_ts = json.loads(meta_file.read_text()).get("timestamp")
            except (OSError, json.JSONDecodeError):
                continue
            if meta_ts is not None and now - meta_ts > self.ttl_seconds:
                self._remove_disk_files(prefix)
                removed += 1
        return removed

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

    @staticmethod
    def _human_size(nbytes: int) -> str:
        """Format bytes as human-readable string."""
        for unit in ("B", "KB", "MB", "GB"):
            if nbytes < 1024:
                return f"{nbytes:.1f} {unit}"
            nbytes /= 1024
        return f"{nbytes:.1f} TB"
