"""Parity: vendored pure-Python originals vs the Rust ports in ``_core``.

The originals below are verbatim copies of the implementations as of
v0.8.0 (before delegation). Every case asserts identical outcomes:
same return value, or same raise/no-raise (with equal messages).
A seeded fuzzer covers the combinatorial URL space where hand-picked
cases stop. If this file is green, the delegation is behavior-preserving.
"""

import json
import math
import random
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import pytest

from gossamer import _core


# ── vendored originals (v0.8.0) ──────────────────────────────────

_V_DOC_EXTS = frozenset({
    ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md",
    ".json", ".xml", ".rss", ".atom",
})
_V_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref_")


def _v_normalize_url(raw, base=None):
    s = (raw or '').strip().strip('"\'').strip("<>").strip()
    if not s:
        raise ValueError("Empty URL")
    if base and not urlparse(s).scheme and not s.startswith("//"):
        s = urljoin(base, s)
    if s.startswith("//"):
        s = "https:" + s
    parsed = urlparse(s)
    if " " in s:
        raise ValueError(f"Cannot interpret {raw!r} as a URL (contains spaces)")
    if not parsed.scheme:
        if s.startswith(("./", "../", ".\\", "..\\")) or Path(s).exists():
            raise ValueError(f"{raw!r} looks like a local file path, not a URL")
        candidate_host = parsed.path.split("/")[0]
        if "." not in candidate_host and candidate_host != "localhost":
            raise ValueError(f"{raw!r} does not look like a URL")
        if "/" not in s and any(
            candidate_host.lower().endswith(ext) for ext in _V_DOC_EXTS
        ):
            raise ValueError(f"{raw!r} looks like a local file path, not a URL")
        s = "https://" + s
        parsed = urlparse(s)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme in {raw!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"Cannot parse {raw!r} as a URL (no host)")
    return s


def _v_canonical_url(url, *, query="keep"):
    normalized = _v_normalize_url(url)
    parts = urlparse(normalized)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[len("www."):]
    port = parts.port
    default_port = {"http": 80, "https": 443}.get(parts.scheme)
    netloc = host if (port is None or port == default_port) else f"{host}:{port}"
    path = parts.path.rstrip("/") or "/"
    if query == "drop":
        query_str = ""
    else:
        items = parse_qsl(parts.query, keep_blank_values=True)
        if query == "drop-tracking":
            items = [
                (k, v) for k, v in items
                if not k.lower().startswith(_V_TRACKING)
            ]
        query_str = urlencode(sorted((k.lower(), v) for k, v in items))
    return urlunparse((parts.scheme, netloc, path, "", query_str, ""))


def _v_content_hash(text):
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


_V_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'\)\]\}，。、；！？（）【】「」『』]+"
)
_V_TRAILING_PUNCT = ".,;:!?…\"'"


def _v_extract_links(text, max_links=50):
    if not isinstance(text, str) or not text:
        return []
    if max_links is None or max_links <= 0:
        return []
    out, seen = [], set()
    for match in _V_URL_RE.finditer(text):
        url = match.group(0).rstrip(_V_TRAILING_PUNCT)
        if url.startswith("www."):
            url = "http://" + url
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_links:
            break
    return out


# ── comparison harness ───────────────────────────────────────────

def _compare(py_fn, rs_fn, *args, **kwargs):
    try:
        want = py_fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — parity includes error surface
        want_err = f"{type(e).__name__}: {e}"
    else:
        want_err = None
    try:
        got = rs_fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        got_err = f"{type(e).__name__}: {e}"
    else:
        got_err = None
    assert (want_err is None) == (got_err is None), (
        f"raise-mismatch for {args!r} {kwargs!r}: py={want_err!r} rs={got_err!r}"
    )
    if want_err is None:
        assert got == want, f"value-mismatch for {args!r} {kwargs!r}:\npy={want!r}\nrs={got!r}"
    else:
        assert got_err == want_err, (
            f"error-mismatch for {args!r} {kwargs!r}:\npy={want_err!r}\nrs={got_err!r}"
        )


CORPUS_URLS = [
    "example.com/a", "www.example.com", "https://www.Example.COM/",
    "http://example.com:80/a/", "https://example.com:8443/a/",
    "https://example.com/a?page=1", "https://example.com/a?b=2&a=1",
    "https://example.com/a?utm_source=x&id=7&FBCLID=zzz",
    "https://example.com/a?x=1&&y=&=z&a",
    "https://example.com/a?q=hello+world&e=%41%zz",
    "https://example.com/ü?q=ü", "https://münchen.de/",
    "http://user:pw@Example.com:81/a/./b/../c#frag",
    "http://[::1]:8080/p", "http://[::1]/", "//cdn.example.com/x",
    "https://example.com", "https://example.com/",
    "https://example.com/a///", "HTTPS://EXAMPLE.COM/A?B=C",
    "https://example.com:443/secure", "http://example.com:8080/",
    "https://example.com/a?ID=7&id=8", "https://example.com/?a=1&a=1",
    "  'https://example.com/spaced'  ", "<https://example.com/angled>",
    '"www.example.com/quoted"',
    "./report.pdf", "../a/b", "report.pdf", "justaword", "", "   ",
    "notaurl", "localhost:8000/api", "ftp://example.com/f",
    "gopher://x/", "https://exa mple.com/", "https://",
    "http://", "https://example.com:abc/", "https://example.com:99999/",
    "https://example.com/a#b#c", "https://example.com#frag",
    "/root/relative", "relative/path", "a?b=c",
    "https://example.com/%41%42", "https://example.com/a?x=%2F%3F",
    "https://example.com/a?caf%C3%A9=1", "https://example.com/;param",
    "https://user@example.com/", "https://example.com./",
    "https://example.com/a?mc_cid=1&gclid=2&ok=3",
]


@pytest.mark.parametrize("raw", CORPUS_URLS)
@pytest.mark.parametrize("mode", ["keep", "drop", "drop-tracking", "bogus"])
def test_canonical_parity(tmp_path, monkeypatch, raw, mode):
    monkeypatch.chdir(tmp_path)  # isolate Path.exists() behavior
    _compare(_v_canonical_url, _core.canonical_url, raw, query=mode)


@pytest.mark.parametrize("raw", CORPUS_URLS)
def test_normalize_parity(tmp_path, monkeypatch, raw):
    monkeypatch.chdir(tmp_path)
    _compare(_v_normalize_url, _core.normalize_url, raw)


def test_normalize_with_base_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for base in ["https://example.com/dir/page", "https://example.com/dir/"]:
        for ref in ["/abs", "rel", "../up", "?q=1", "#f", "//other.com/x"]:
            _compare(_v_normalize_url, _core.normalize_url, ref, base)


def test_content_hash_parity():
    for text in ["", "hello", "üñíçødé ✓", "a" * 10000, "line1\nline2"]:
        assert _core.content_hash(text) == _v_content_hash(text)


def test_extract_links_parity():
    cases = [
        "See https://example.com/report and www.example.com/docs.",
        "see example.com/docs for details",
        "Go to https://example.com/a, then https://example.com/a.",
        "CJK：https://example.com/中，next www.example.com/文。",
        "trailing (https://example.com/x) [y](https://example.com/z).",
        "",
    ]
    for text in cases:
        for cap in [1, 2, 50]:
            assert _core.text_links_scan(text, cap) == _v_extract_links(text, cap)


def _fuzz_urls(rng):
    schemes = ["http://", "https://", "HTTP://", "//", "", "ftp://"]
    hosts = ["example.com", "WWW.Example.COM", "münchen.de", "localhost",
             "a.b", "x", "user:pw@h.com", "[::1]", "[::1", "h.com:80",
             "h.com:abc", "h.com:99999", "h.com:", "h.com./", "127.0.0.1:8000",
             "x]", "[v1.fe]", "[vZZZ]", "[1.2.3.4]", "\u2100x.com",
             "[fe80::1%25eth0]", "u:p@[::1]:80", "a[b", "FE80::1%tESt"]
    paths = ["", "/", "/a", "/a/", "/a//b", "/ü/ß", "/a/../b", "/a/./b",
             "/a b", "/%41", "/a?no", "/;p", "/a;b;c", "/\ttab"]
    queries = ["", "a=1", "b=2&a=1", "utm_x=1&k=2", "x", "=y", "a&&b=2",
               "q=a+b", "e=%zz", "u=%C3%BC", "K=1&k=2", "a=1&a=1",
               "mc_a=1&FBCLID=2&gclid=3&ref_x=4&ok=5"]
    frags = ["", "#f", "#a#b"]
    wraps = ["{}", "  {}  ", "'{}'", '"{}"', "<{}>", "//{}"]
    urls = []
    for _ in range(400):
        u = (rng.choice(schemes) + rng.choice(hosts) + rng.choice(paths)
             + ("?" + rng.choice(queries) if rng.random() < 0.7 else "")
             + rng.choice(frags))
        if rng.random() < 0.3:
            u = rng.choice(wraps).format(u)
        urls.append(u)
    urls += ["report.pdf", "./x", "word", "", "a b", "https://"]
    return urls


def test_fuzz_url_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rng = random.Random(20260905)
    for raw in _fuzz_urls(rng):
        for mode in ("keep", "drop", "drop-tracking"):
            _compare(_v_canonical_url, _core.canonical_url, raw, query=mode)
        _compare(_v_normalize_url, _core.normalize_url, raw)


def test_fuzz_extract_links_parity():
    rng = random.Random(20260905)
    bits = ["https://example.com/", "www.example.com/a", "example.com",
            "text words", "，", ".", ",", "(parens)", "[link](https://example.com/t)",
            "https://example.com/ü", "a@b", "x" * 200]
    for _ in range(200):
        text = " ".join(rng.choice(bits) for _ in range(rng.randint(1, 12)))
        assert _core.text_links_scan(text, 50) == _v_extract_links(text, 50)


def test_document_extensions_agree_with_structured_parser():
    # The Rust DOCUMENT_EXTENSIONS is a separate copy: any drift fails here.
    from gossamer.structured_parser import DOCUMENT_EXTENSIONS

    for ext in DOCUMENT_EXTENSIONS:
        with pytest.raises(ValueError, match="local file path"):
            _core.normalize_url(f"file{ext}")
    assert _core.normalize_url("file.unknown") == "https://file.unknown"
