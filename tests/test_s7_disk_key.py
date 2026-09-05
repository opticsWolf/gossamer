"""S7: disk cache keys must not use MD5.

MD5 is fine as a cache-key digest (not a security boundary), but it
trips security scanners and breaks under FIPS-mode interpreters.
S7 switches the digest to blake2b with the same 16-byte length.
"""

import hashlib
import inspect

import gossamer.cache as cache_module
from gossamer.cache import Cache


class TestDiskKeyHash:
    def test_disk_key_is_blake2b_16_bytes(self, tmp_path):
        c = Cache(tmp_path / "cache")
        key = "page:https://example.com/a"
        expected = hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()
        assert c._disk_key(key) == expected
        assert len(c._disk_key(key)) == 32  # 16 bytes, hex-encoded

    def test_disk_files_use_blake2b_names(self, tmp_path):
        c = Cache(tmp_path / "cache")
        key = "page:https://example.com/a"
        c.put(key, "hello")
        expected = hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()
        assert (tmp_path / "cache" / f"{expected}.cache").exists()
        assert (tmp_path / "cache" / f"{expected}.meta").exists()
        # Round-trip still works.
        assert c.get(key) == "hello"

    def test_module_source_contains_no_md5(self):
        assert "md5" not in inspect.getsource(cache_module)
