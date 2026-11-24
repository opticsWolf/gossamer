"""
Two-tier caching layer for gossamer.

Provides:
  - In-memory LRU cache (fast, session-scoped)
  - File-based TTL cache (persistent across sessions)
  - Configurable max entries, TTL, and cache directory
"""

import json
import logging
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

from gossamer import _core as _rust

logger = logging.getLogger(__name__)



class _MemoryView:
    """OrderedDict-shaped view over the Rust in-memory LRU."""

    def __init__(self, rust_cache):
        self._rust = rust_cache

    def __contains__(self, key):
        return self._rust.memory_get(key) is not None

    def __getitem__(self, key):
        value = self._rust.memory_get(key)
        if value is None:
            raise KeyError(key)
        return (value, 0.0)

    def __setitem__(self, key, value):
        content, timestamp = value
        self._rust.memory_set(key, content, timestamp)

    def __len__(self):
        return len(self._rust.memory_keys())

    def __iter__(self):
        return iter(self._rust.memory_keys())

    def get(self, key, default=None):
        value = self._rust.memory_get(key)
        return (value, 0.0) if value is not None else default

    def pop(self, key, default=None):
        value = self._rust.memory_remove(key)
        return (value, 0.0) if value is not None else default

    def clear(self):
        self._rust.memory_clear()

    def move_to_end(self, key, last=True):
        return None

    def keys(self):
        return self._rust.memory_keys()


class Cache:
    """
    Two-tier cache with in-memory LRU and file-based TTL layers.

    Usage
    -----
    >>> cache = Cache(
    ...     cache_dir=".gossamer_cache",
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
        cache_dir: str = ".gossamer_cache",
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

        # Rust owns the in-memory LRU; this view keeps the legacy shape.
        self._rust_cache = _rust.PyCache(
            str(cache_dir), ttl_seconds, max_memory_entries, max_disk_bytes
        )
        self._memory = _MemoryView(self._rust_cache)
        self._rust_limits = (ttl_seconds, max_memory_entries, max_disk_bytes)

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

    def _sync_rust_cache(self) -> None:
        limits = (self.ttl_seconds, self.max_memory_entries, self.max_disk_bytes)
        if limits == self._rust_limits:
            return
        self._rust_cache = _rust.PyCache(str(self.cache_path), *limits)
        self._memory = _MemoryView(self._rust_cache)
        self._rust_limits = limits

    def get(self, key: str) -> Optional[str]:
        """Retrieve cached content using the Rust two-tier lookup."""
        self._sync_rust_cache()
        return self._rust_cache.get(key, time.time())

    def get_stale(self, key: str) -> Optional[str]:
        """Read a cached entry ignoring TTL."""
        self._sync_rust_cache()
        return self._rust_cache.get_stale(key)

    def put(self, key: str, content: str) -> None:
        """Write through both cache tiers."""
        self._sync_rust_cache()
        self._rust_cache.put(key, content, time.time())

    def clear(self) -> None:
        """Empty both cache tiers."""
        self._sync_rust_cache()
        self._rust_cache.clear()
        logger.info("Disk cache cleared (%d files removed from %s)", 0, self.cache_path)

    def prune(self) -> Dict[str, Any]:
        """Prune expired and over-cap entries."""
        self._sync_rust_cache()
        return self._rust_cache.prune(time.time())

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        self._sync_rust_cache()
        return self._rust_cache.stats()

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
        when it is not used as a security boundary. Digest computation
        in Rust (``src/cacheutils.rs``); the ``encode`` call doubles as
        the type gate so exotic keys raise exactly as before.
        """
        key.encode("utf-8")
        return _rust.cache_disk_key(key)

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
        """Format bytes as human-readable string (Rust kernel)."""
        if isinstance(nbytes, bool):
            nbytes = int(nbytes)
        if not isinstance(nbytes, (int, float)):
            # Exotic inputs keep the original loop verbatim so the
            # TypeError at `<` matches exactly.
            for unit in ("B", "KB", "MB", "GB"):
                if nbytes < 1024:
                    return f"{nbytes:.1f} {unit}"
                nbytes /= 1024
            return f"{nbytes:.1f} TB"
        return _rust.cache_human_size(float(nbytes))
