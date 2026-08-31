"""Tests for :mod:`stitch_web_researcher.resource_store` (offline)."""

import hashlib
import re

import pytest

from stitch_web_researcher.resource_store import ResourceStore


class FakeResp:
    def __init__(self, content: bytes, ctype: str):
        self.content = content
        self.status_code = 200
        self.headers = {"content-type": ctype}


class FakeClient:
    """Records requests and serves canned image bytes by URL."""

    def __init__(self, *, png=b"", jpeg=b"", miss=None, blocked=None):
        self.png = png
        self.jpeg = jpeg
        self.miss = miss or {}
        self.blocked = blocked or set()
        self.calls = []

    def get(self, url, headers=None, timeout=None, follow_redirects=False):
        self.calls.append(url)
        if url in self.blocked:
            from stitch_web_researcher.ssrf import SsrfBlockedError
            raise SsrfBlockedError("blocked")
        if url in self.miss:
            return FakeResp(b"", self.miss[url])
        if "logo" in url or url.endswith(".png"):
            return FakeResp(self.png or b"\x89PNG\r\n\x1a\nfakepng", "image/png")
        return FakeResp(self.jpeg or b"\xff\xd8\xff\xd8fakejpeg", "image/jpeg")


PNG = b"\x89PNG\r\n\x1a\nfakepng"
JPEG = b"\xff\xd8\xff\xd8fakejpeg"


def _md_images():
    return (
        "# Doc\n\n"
        "![logo](https://example.com/img/logo.png)\n\n"
        "![photo](https://example.com/img/photo.jpg \"A\")\n\n"
        "![dup](https://example.com/img/logo.png)\n"
    )


def test_extract_rewrites_absolute_refs_and_dedups(tmp_path):
    client = FakeClient(png=PNG, jpeg=JPEG)
    store = ResourceStore(client=client)
    res = store.extract(markdown=_md_images(), base_url="https://example.com/a.md",
                        out_dir=tmp_path, stem="paper")
    # two distinct images (logo dedup'd across two refs)
    assert res["referenced"] == 3  # three refs rewritten
    assert len(res["files"]) == 2  # but only two distinct files
    # every ref rewritten to a local relative path
    assert "https://example.com" not in res["markdown"]
    assert res["markdown"].count("./paper.files/") == 3  # logo x2 + photo
    # files actually written
    for f in res["files"]:
        assert (tmp_path / f).exists()
    # download deduped to 2 requests (logo twice -> once + photo)
    assert len(client.calls) == 2


def test_extract_resolves_relative_refs(tmp_path):
    client = FakeClient(png=PNG)
    store = ResourceStore(client=client)
    md = "![x](/assets/x.png)"
    res = store.extract(markdown=md, base_url="https://site.com/dir/page.md",
                        out_dir=tmp_path, stem="p")
    assert res["referenced"] == 1
    assert "./p.files/" in res["markdown"]
    assert "site.com" not in res["markdown"]


def test_extract_skips_non_images(tmp_path):
    client = FakeClient(miss={"https://x.com/d.pdf": "application/pdf"})
    store = ResourceStore(client=client)
    md = "![d](https://x.com/d.pdf)\n\n![img](https://x.com/i.png)"
    res = store.extract(markdown=md, base_url="https://x.com", out_dir=tmp_path, stem="p")
    assert res["referenced"] == 1
    assert any(s["url"].endswith(".pdf") for s in res["skipped"])


def test_extract_skips_ssrf_blocked(tmp_path):
    client = FakeClient(blocked={"https://169.254.169.254/metadata"})
    store = ResourceStore(client=client)
    md = "![meta](https://169.254.169.254/metadata)\n\n![ok](https://x.com/i.png)"
    res = store.extract(markdown=md, base_url="https://x.com", out_dir=tmp_path, stem="p")
    assert res["referenced"] == 1


def test_extract_embedded_appends_figures(tmp_path):
    store = ResourceStore()
    res = store.extract_embedded(
        markdown="# Doc\n",
        out_dir=tmp_path,
        stem="paper",
        images=[{"data": PNG}, {"data": JPEG}],
    )
    assert res["embedded"] == 2
    assert "## Figures" in res["markdown"]
    assert res["markdown"].count("./paper.files/") == 2
    for f in res["files"]:
        assert (tmp_path / f).exists()


def test_embedded_detects_ext_from_magic(tmp_path):
    store = ResourceStore()
    res = store.extract_embedded(
        markdown="", out_dir=tmp_path, stem="p", images=[{"data": PNG}],
    )
    # png magic -> .png filename
    assert any(f.endswith(".png") for f in res["files"])


def test_extract_no_refs_creates_no_folder(tmp_path):
    client = FakeClient(png=PNG)
    store = ResourceStore(client=client)
    res = store.extract(markdown="# no images here", base_url="https://example.com",
                        out_dir=tmp_path, stem="p")
    assert res["referenced"] == 0
    assert res["files"] == []
    # no <stem>.files directory created
    assert not (tmp_path / "p.files").exists()


def test_embedded_no_images_creates_no_folder(tmp_path):
    store = ResourceStore()
    res = store.extract_embedded(markdown="# doc", out_dir=tmp_path, stem="p", images=[])
    assert res["embedded"] == 0
    assert res["markdown"] == "# doc"
    assert not (tmp_path / "p.files").exists()
