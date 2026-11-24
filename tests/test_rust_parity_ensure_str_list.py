import pytest
from gossamer.config import ensure_str_list

def test_ensure_str_list():
    assert ensure_str_list(None, "urls") == []
    assert ensure_str_list(" a ", "urls") == [" a "]
    assert ensure_str_list("  ", "urls") == []
    assert ensure_str_list(("a", "b"), "urls") == ["a", "b"]
    with pytest.raises(TypeError, match="urls must be a list of strings, got an item of type int"):
        ensure_str_list(["a", 1], "urls")
    with pytest.raises(TypeError, match="urls must be a list of strings \(a bare string is also accepted and wrapped\), got dict"):
        ensure_str_list({}, "urls")
