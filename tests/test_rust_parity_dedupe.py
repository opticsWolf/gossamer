"""Parity: vendored pure-Python ``dedupe`` (v0.8.1) vs the Rust core.

``dedupe`` now extracts raw fields in Python and runs the collision loop
in ``src/dedupe.rs``. This file vendors the complete original
implementation and compares full ``(kept, dropped)`` outcomes — item
identity by index — over hand-picked cases plus a seeded fuzzer that
mixes dicts, attribute-objects, non-string values, and every ``by``
permutation.
"""

import random
from typing import Any, Mapping, Optional, Sequence

import pytest

from gossamer import _core
from gossamer.dedup import _extract_raw, dedupe


# ── vendored original (v0.8.1, before the Rust move) ──────────────

_V_DOI_KEYS = ("doi",)
_V_URL_KEYS = ("url",)
_V_TITLE_KEYS = ("title",)
_V_SNIPPET_KEYS = ("snippet", "summary", "description")


def _v_scalar(item, keys):
    if isinstance(item, Mapping):
        candidates = (item.get(k) for k in keys)
    else:
        candidates = (getattr(item, k, None) for k in keys)
    for v in candidates:
        if v:
            return v if isinstance(v, str) else str(v)
    return None


def _v_doi_key(item):
    doi = _v_scalar(item, _V_DOI_KEYS)
    return doi.strip().lower() if doi else None


def _v_url_key(item):
    from gossamer.config import canonical_url

    url = _v_scalar(item, _V_URL_KEYS)
    if not url:
        return None
    try:
        return canonical_url(url, query="drop")
    except ValueError:
        return None


def _v_hash_key(item):
    from gossamer.dedup import content_hash

    title = _v_scalar(item, _V_TITLE_KEYS) or ""
    snippet = _v_scalar(item, _V_SNIPPET_KEYS) or ""
    if not title and not snippet:
        return None
    return content_hash(f"{title.strip()}||{snippet.strip()}")


_V_FIELD_FOR = {"doi": _v_doi_key, "url": _v_url_key, "hash": _v_hash_key}


def _v_dedupe(results, by=("doi", "url", "hash")):
    kept, dropped, seen = [], [], {}
    for i, item in enumerate(results):
        reason = match = None
        for field in by:
            fn = _V_FIELD_FOR.get(field)
            if fn is None:
                continue
            key = fn(item)
            if not key:
                continue
            bucket = seen.setdefault(field, {})
            if key in bucket:
                reason, match = field, key
                break
        if reason is None:
            for field in by:
                fn = _V_FIELD_FOR.get(field)
                if fn is None:
                    continue
                key = fn(item)
                if key:
                    seen.setdefault(field, {})[key] = len(kept)
            kept.append(item)
        else:
            dropped.append({"index": i, "reason": reason, "match": match})
    return kept, dropped


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _sig(kept, items, dropped):
    """Index-based signature: identical iff outcomes agree exactly."""
    kept_idx = [items.index(k) for k in kept]
    return (
        kept_idx,
        [(d["index"], d["reason"], d["match"]) for d in dropped],
    )


CASES = [
    [],
    [{"title": "a"}, {"title": "b"}],
    [{"url": "https://example.com/a"}, {"url": "https://example.com/a?x=1"}],
    [
        {"doi": "10.1/ABC ", "title": "T"},
        {"doi": "10.1/abc", "title": "U"},
        {"doi": "  10.1/ABC", "title": "V"},
    ],
    [{"title": "Same", "snippet": "Body"}, {"title": "Same", "snippet": "Body"}],
    [{"title": "Same", "summary": "Body"}, {"title": "Same", "description": "Body"}],
    [{}, {}, {"url": ""}, {"doi": "   "}],
    [_Obj(url="https://example.com/a"), {"url": "https://example.com/a"}],
    [_Obj(doi="10.1/x"), {"doi": "10.1/X", "title": 1}],
    [{"url": "https://example.com/a"}, {"url": "not a url [[["}],
    [{"doi": 12345}, {"doi": "12345"}],
    [{"title": "T", "snippet": ["a", "b"]}, {"title": "T", "snippet": "['a', 'b']"}],
]

BYS = [
    ("doi", "url", "hash"),
    ("url",),
    ("hash",),
    ("doi",),
    ("bogus", "url"),
    ("bogus",),
    ("hash", "doi", "url"),
    (),
]


@pytest.mark.parametrize("by", BYS)
@pytest.mark.parametrize("items", CASES)
def test_dedupe_parity(tmp_path, monkeypatch, items, by):
    monkeypatch.chdir(tmp_path)
    want_kept, want_dropped = _v_dedupe(items, by)
    got_kept, got_dropped = dedupe(items, by)
    assert _sig(got_kept, items, got_dropped) == _sig(want_kept, items, want_dropped)


def _fuzz_items(rng):
    dois = [None, "10.1/abc", "10.1/ABC ", "  10.1/abc", "10.2/zzz", 12345, ""]
    urls = [None, "https://example.com/a", "https://www.EXAMPLE.com/a?x=1",
            "https://example.com/a?utm_x=1", "not a url", "", "example.com/b",
            "http://example.com:80/c/"]
    titles = [None, "Title One", "title one", "  Spaced  ", "", "T"]
    snips = [None, "Body text", "body text", "", ["a"], 42]
    items = []
    for _ in range(rng.randint(0, 12)):
        style = rng.random()
        kw = {
            "doi": rng.choice(dois),
            "url": rng.choice(urls),
            "title": rng.choice(titles),
        }
        slot = rng.choice(["snippet", "summary", "description", None])
        if slot:
            kw[slot] = rng.choice(snips)
        if style < 0.3:
            items.append(_Obj(**{k: v for k, v in kw.items() if v is not None}))
        else:
            items.append({k: v for k, v in kw.items() if v is not None or rng.random() < 0.2})
    return items


def test_fuzz_dedupe_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rng = random.Random(20260905)
    for _ in range(300):
        items = _fuzz_items(rng)
        by = tuple(rng.sample(["doi", "url", "hash", "bogus"],
                              rng.randint(0, 4)))
        want_kept, want_dropped = _v_dedupe(items, by)
        got_kept, got_dropped = dedupe(items, by)
        assert _sig(got_kept, items, got_dropped) == _sig(
            want_kept, items, want_dropped
        ), f"mismatch for by={by} items={items!r}"


def test_extract_raw_matches_scalar_semantics():
    # Spot-check the extraction layer directly.
    assert _extract_raw({"doi": " 10.1/X "})["doi"] == " 10.1/X "
    assert _extract_raw({"snippet": "", "summary": "s"})["summary"] == "s"
    assert _extract_raw({"title": 7})["title"] == "7"
    assert _extract_raw(_Obj(url="https://a.example"))["url"] == "https://a.example"
    empty = _extract_raw({})
    assert all(v is None for v in empty.values())
