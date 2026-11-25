from gossamer.resource_store import _service_detect_ext_from_magic
def test_magic():
    assert _service_detect_ext_from_magic(b"\x89PNG\r\n\x1a\nxx") == "png"
    assert _service_detect_ext_from_magic(b"RIFFxxxxWEBP") == "webp"
    assert _service_detect_ext_from_magic(b"nope") is None
