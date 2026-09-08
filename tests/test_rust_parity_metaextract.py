"""Differential parity: Rust meta-oxide kernels vs the Python bridge (M22).

Oracle is the installed `meta_oxide` package (fork rev 81bdb53).
The Rust crate dependency vendors rev a55c09f, whose only delta is
the RDFa property+typeof recursion fix — so cycle inputs assert the
FIXED output (hardcoded) instead of oracle equality, and the fuzz
oracle-skip for those inputs stays (the installed package still
crashes on them). Two documented refinements assert their intended
shape instead of equality: `twitter` single uses the with-fallback
mapping (as `extract_all` always did), and `microformats` uses the
combined parser shape.
"""

import json

import pytest

meta_oxide = pytest.importorskip("meta_oxide")

from gossamer import _core


RICH = """<!DOCTYPE html><html lang="en"><head>
<title>Full Page</title>
<meta name="description" content="Desc here">
<meta name="keywords" content="a, b, c">
<meta name="author" content="Author">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<link rel="canonical" href="/canon">
<link rel="alternate" hreflang="de" href="/de">
<link rel="alternate" type="application/json+oembed" href="https://example.com/oembed.json" title="oE">
<link rel="manifest" href="/app.webmanifest">
<meta property="og:title" content="OG Title">
<meta property="og:type" content="article">
<meta property="og:image" content="https://example.com/og.png">
<meta property="og:image:width" content="1200">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@example">
<meta name="DC.title" content="DC Title">
<meta name="DC.creator" content="Creator">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Article","headline":"H",
 "author":{"@type":"Person","name":"Jane"},
 "about":[{"@type":"Thing","name":"A"},{"@type":"Thing","name":"B"}]}
</script>
</head><body>
<div itemscope itemtype="https://schema.org/Person">
<span itemprop="name">John</span>
<span itemprop="knows">Alice</span><span itemprop="knows">Bob</span>
<div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
<span itemprop="streetAddress">Main St</span></div>
</div>
<div class="h-card"><span class="p-name">Card Person</span>
<a class="u-url" href="/person">Profile</a></div>
<div class="h-entry"><span class="p-name">Post</span>
<time class="dt-published" datetime="2024-01-01">Jan</time></div>
<div class="h-cite"><span class="p-name">Cite</span></div>
<div vocab="https://schema.org/" typeof="Person">
<span property="name">RDFa Rita</span>
<span property="knows" resource="/alice">A</span>
</div>
<a rel="me" href="https://mastodon.example/@u">m</a>
</body></html>"""

PARTIAL_TWITTER = """<html><head><title>P</title>
<meta property="og:title" content="OG Only Title">
<meta property="og:description" content="OG Only Desc">
<meta property="og:image" content="https://example.com/o.png">
</head><body></body></html>"""

FIXTURES = [
    RICH,
    PARTIAL_TWITTER,
    "<html></html>",
    "",
    "<html><head><title>T &amp; Demo</title></head></html>",
    "<html><head><!-- <meta name=\"x\" content=\"y\"> --><title>R</title></head></html>",
    "<html><head><meta name=\"keywords\" content=\"\"></head></html>",
    "<div itemscope itemtype=\"\"><span itemprop=\"a\">1</span></div>",
    "<div typeof=\"\"><span property=\"p\">v</span></div>",
    "<div class=\"h-card\"><div class=\"h-card\"><span class=\"p-name\">N</span></div></div>",
    "<link rel=\"manifest\" href=\"/m.json\">",
    "not html at all < < > &",
    "<html><head><script type=\"application/ld+json\">{BROKEN</script></head></html>",
    "<html><head><script type=\"application/ld+json\">[1, \"two\", null, {\"@type\":\"X\"}]</script></head></html>",
    "Ünïcodé <title>日本語テスト</title> caf\xe9",
]


def _rs_all(html, base):
    return json.loads(_core.meta_extract_all(html, base))


def _has_rdfa_cycle(html):
    # Liberal same-tag typeof+property detector (both orders). The
    # fuzz alphabet never emits < > inside attribute values, so this
    # cannot miss a real match there; over-matching only loosens the
    # oracle comparison, never safety.
    import re
    tag = r"<[^<>]*"
    return bool(
        re.search(tag + r"\btypeof\s*=[^<>]*\bproperty\s*=", html,
                  re.I | re.S)
        or re.search(tag + r"\bproperty\s*=[^<>]*\btypeof\s*=", html,
                     re.I | re.S))


CYCLE_FIXTURES = [
    '<div typeof="summary" property="x">'
    '<div property="" rel="x">t</div></div>',
    '<div property="p" typeof="T">text</div>',
    '<span TYPEOF="T" PROPERTY="p">x</span>',
]


def test_rdfa_cycle_fixed():
    # Upstream fix (rev a55c09f): property+typeof no longer recurses —
    # the typeof side is captured by the element's own item and the
    # property falls back to the text value. Hardcoded: the installed
    # 81bdb53 oracle still stack-overflows on these.
    rs = _rs_all(CYCLE_FIXTURES[0], None)
    # Note the `""` key: the inner `<div property="">` carries an
    # empty property name, which upstream records verbatim.
    assert rs["rdfa"] == [{"type": ["summary"], "": "t", "x": "t"}], \
        rs["rdfa"]
    rs = _rs_all(CYCLE_FIXTURES[1], None)
    assert rs["rdfa"] == [{"type": ["T"], "p": "text"}], rs["rdfa"]
    rs = _rs_all(CYCLE_FIXTURES[2], None)
    assert rs["rdfa"] == [{"type": ["T"], "p": "x"}], rs["rdfa"]


def test_extract_all_parity():
    for html in FIXTURES:
        for base in ("https://example.com/page", None, ""):
            rs = _rs_all(html, base)
            py = meta_oxide.extract_all(html, base)
            # microformats intentionally differs (combined vs named);
            # everything else must be byte-identical.
            rs_mf = rs.pop("microformats", None)
            py_mf = py.pop("microformats", None)
            assert rs == py, (html[:60], base)
            if py_mf is None:
                assert rs_mf is None, (html[:60], base)
            else:
                assert rs_mf is not None, (html[:60], base)
                assert set(rs_mf) >= set(py_mf), (html[:60], base)


def test_microformats_key_parity():
    # Combined parser is a superset; every named-format key the old
    # bridge found must still be present.
    rs = _rs_all(RICH, "https://example.com")
    assert "h-card" in rs["microformats"]
    assert "h-entry" in rs["microformats"]
    assert "h-cite" in rs["microformats"]
    card = rs["microformats"]["h-card"][0]
    assert card["type"] == ["h-card"]
    assert card["properties"]["name"] == ["Card Person"]


def test_singles_parity():
    for html in FIXTURES:
        for base in ("https://example.com/page", None):
            assert (json.loads(_core.meta_extract_meta(html, base))
                    == meta_oxide.extract_meta(html, base)), html[:40]
            assert (json.loads(_core.meta_extract_opengraph(html, base))
                    == meta_oxide.extract_opengraph(html, base)), html[:40]
            assert (json.loads(_core.meta_extract_jsonld(html, base))
                    == meta_oxide.extract_jsonld(html, base)), html[:40]


def test_twitter_fallback_documented():
    # The kernel uses the with-fallback mapping everywhere (as
    # extract_all always did): OG fills missing twitter fields.
    rs = json.loads(_core.meta_extract_twitter(PARTIAL_TWITTER, None))
    assert rs == meta_oxide.extract_twitter_with_fallback(
        PARTIAL_TWITTER, None)
    assert rs.get("title") == "OG Only Title"
    # ...which differs from the plain mapping exactly when twitter
    # tags are incomplete.
    plain = meta_oxide.extract_twitter(PARTIAL_TWITTER, None)
    assert plain.get("title") is None


def test_nul_boundaries():
    # NULs never reach the C boundary: HTML NULs become U+FFFD (what
    # the tokenizer does anyway); a NUL base becomes NULL.
    rs = _rs_all("a\0b<title>T</title>", None)
    assert rs["meta"]["title"] == "T"
    rs = _rs_all("<title>T</title>", "https://ex\0ample.com/")
    assert isinstance(rs, dict)


def test_fuzz():
    import random
    rng = random.Random(20260909)
    tags = ["meta", "link", "div", "span", "script", "a", "time"]
    attrs = ["name", "property", "content", "href", "class",
             "itemprop", "itemscope", "itemtype", "typeof",
             "property", "rel", "type", "datetime", "content",
             "resource", "vocab", "datatype", "about"]
    vals = ["title", "description", "og:title", "twitter:card",
            "h-card", "p-name", "Person", "name", "summary",
            "application/ld+json", "canonical", "me", "",
            "https://schema.org/Person", "x"]
    for trial in range(300):
        parts = []
        for _ in range(rng.randrange(1, 12)):
            tag = rng.choice(tags)
            a = " ".join(
                '%s="%s"' % (rng.choice(attrs), rng.choice(vals))
                for _ in range(rng.randrange(4)))
            text = rng.choice(["", "hello", "Ünï", "a b"])
            if rng.random() < 0.3:
                parts.append("<%s %s>" % (tag, a))
            else:
                parts.append("<%s %s>%s</%s>" % (tag, a, text, tag))
        html = "<html><head><title>F%d</title></head><body>%s</body></html>" % (
            trial, "".join(parts))
        base = rng.choice([None, "https://example.com/p", ""])
        rs = _rs_all(html, base)
        if _has_rdfa_cycle(html):
            # The installed 81bdb53 oracle would stack-overflow here;
            # the fixed kernel must still produce a well-formed section.
            assert isinstance(rs.get("rdfa", []), list), (trial, html)
            assert (json.loads(_core.meta_extract_meta(html, base))
                    == meta_oxide.extract_meta(html, base)), (trial, html)
            assert (json.loads(_core.meta_extract_jsonld(html, base))
                    == meta_oxide.extract_jsonld(html, base)), (trial, html)
            continue
        py = meta_oxide.extract_all(html, base)
        rs_mf = rs.pop("microformats", None)
        py_mf = py.pop("microformats", None)
        assert rs == py, (trial, html)
        assert (rs_mf is None) == (py_mf is None), (trial, html)
        assert (json.loads(_core.meta_extract_meta(html, base))
                == meta_oxide.extract_meta(html, base)), (trial, html)
        assert (json.loads(_core.meta_extract_opengraph(html, base))
                == meta_oxide.extract_opengraph(html, base)), (trial, html)
        assert (json.loads(_core.meta_extract_jsonld(html, base))
                == meta_oxide.extract_jsonld(html, base)), (trial, html)
