"""Replayable parity pins for the deterministic FetchService kernels."""

import pytest

from gossamer import fetch as fetch_mod


def test_strategy_resolution_table():
    assert fetch_mod._service_strategy_for("auto", "auto") == "auto"
    assert fetch_mod._service_strategy_for("auto", "static") == "static-only"
    assert fetch_mod._service_strategy_for("auto", "browser") == "browser-first"
    assert fetch_mod._service_strategy_for("static", "AUTO") == "static-only"
    assert fetch_mod._service_strategy_for("browser", "auto") == "browser-only"


def test_invalid_strategy_raises():
    with pytest.raises(ValueError):
        fetch_mod._service_strategy_for("auto", "bogus")


def test_strategy_compatibility():
    assert fetch_mod._service_strategy_accepts("auto", "static", None) is True
    assert fetch_mod._service_strategy_accepts("static-only", "static", None) is True
    assert fetch_mod._service_strategy_accepts("static-only", "browser", None) is False
    assert fetch_mod._service_strategy_accepts(
        "browser-first", "static", "browser-first"
    ) is True
    assert fetch_mod._service_strategy_accepts("browser-first", "static", None) is False


def test_cached_and_raw_entry_compatibility():
    cached = ("# md", [], {"fetch_strategy": "browser-first"}, "static")
    assert fetch_mod._service_cached_matches_strategy(cached, "browser-first") is True
    assert fetch_mod._service_cached_matches_strategy(None, "auto") is False
    assert fetch_mod._service_cached_matches_strategy((), "auto") is False
    entry = {"method": "browser", "meta": {"fetch_strategy": "browser-first"}}
    assert fetch_mod._service_raw_entry_matches_strategy(entry, "browser-only") is True
    assert fetch_mod._service_raw_entry_matches_strategy({}, "auto") is False


def test_looks_like_text():
    assert fetch_mod._service_looks_like_text("# Hello\n\nworld") is True
    assert fetch_mod._service_looks_like_text("   ") is False
    assert fetch_mod._service_looks_like_text("\u0000\u0001\u0002") is False


def test_slice_markdown_resumes_without_overlap():
    markdown = "para one\n\npara two\n\npara three"
    first, offset, more = fetch_mod._service_slice_markdown(
        markdown, 0, 1, 12, 0, "gpt-4o"
    )
    assert first.startswith("para one")
    assert offset > len(first) - 6
    assert more is True
    second, end_offset, end_more = fetch_mod._service_slice_markdown(
        markdown, offset, 3, 12, 0, "gpt-4o"
    )
    assert end_offset == len(markdown)
    assert end_more is False
    assert len(second) > 0


def test_compact_metadata():
    raw = {
        "meta": {"title": "T", "canonical": "https://old.test"},
        "opengraph": {"title": "OG"},
        "twitter": {"card": "summary"},
        "jsonld": [{"a": 1}, {"b": 2}, {"c": 3}],
        "rel_links": {"canonical": ["https://new.test"], "alternate": ["/rss"]},
        "hidden_blocks_removed": 4,
    }
    out = fetch_mod._service_compact_metadata(raw)
    assert out["title"] == "T"
    assert out["og_title"] == "OG"
    assert out["canonical"] == "https://new.test"
    assert out["alternates"] == ["/rss"]
    assert len(out["jsonld"]) == 2
    assert out["hidden_blocks_removed"] == 4
    assert fetch_mod._service_compact_metadata({}) == {}


def test_format_follow_ups():
    out = fetch_mod._service_format_follow_ups(
        [("https://x.test/a", " A "), ("https://x.test/a", "dup"), ("https://x.test/r.pdf", "")]
    )
    assert [(c.url, c.title, c.type) for c in out] == [
        ("https://x.test/a", "A", "page"),
        ("https://x.test/r.pdf", "(untitled)", "document"),
    ]


def test_dispatch_decision_table():
    result = ("# md", [], {}, "static")
    assert fetch_mod._service_dispatch_decision(
        "https://x.test", "static-only", result, None, None, None, False, False
    ) == {"kind": "result", "value": result}
    assert fetch_mod._service_dispatch_decision(
        "https://x.test", "browser-only", result, None, None, None, True, False
    ) == {"kind": "browser"}
    assert fetch_mod._service_dispatch_decision(
        "https://x.test",
        "browser-first",
        result,
        None,
        ("# b", [], {}, "browser"),
        "boom",
        True,
        False,
    )["value"][3] == "static"
    with pytest.raises(RuntimeError):
        fetch_mod._service_dispatch_decision(
            "https://x.test", "auto", ("\u0000\u0001", [], {}, "static"), None, None, None, False, False
        )
    with pytest.raises(RuntimeError):
        fetch_mod._service_dispatch_decision(
            "https://x.test", "auto", None, "boom", None, None, False, False
        )
