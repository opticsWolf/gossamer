"""Self-contained resource extraction for stored content.

When a page or document is stored as markdown, the markdown frequently
references images by URL. Two things happen depending on the source:

* **HTML pages** keep ``![alt](url)`` image refs in their markdown (and,
  via ``_absolutize_markdown_links``, those URLs are already absolute).
* **PDF / office converters drop images entirely** -- the markdown has no
  image refs at all, even when the source contains figures.

This module makes stored markdown fully self-contained by downloading those
images into a sibling ``<stem>.files/`` directory (next to the stored
``<stem>.md``) and rewriting the markdown to point at the local copies.

Two entry points, both sharing the same download/store/rewrite machinery:

* :meth:`ResourceStore.extract` -- pulls every ``![...](url)`` ref found in
  the markdown (URLs resolved against ``base_url``). Covers HTML and any
  document whose converter kept image refs.
* :meth:`ResourceStore.extract_embedded` -- for formats (notably PDF) whose
  converter drops images: hand it the raw image bytes and it writes them and
  appends a "Figures" section linking each, since there is no ref in the
  markdown to rewrite.

All network access goes through an injectable ``httpx`` client (a lazy
module-level client is used by default), so the module is fully testable
offline. Downloads honour the SSRF policy via :func:`validate_public_url`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from stitch_web_researcher.ssrf import validate_public_url, SsrfBlockedError

logger = logging.getLogger(__name__)

# Lazy, process-wide default client -- created on first use so importing this
# module (and the offline tests that inject a fake client) never open a socket.
_DEFAULT_CLIENT: httpx.Client | None = None


def _default_client() -> httpx.Client:
    """Return a lazily-created, shared :class:`httpx.Client`` (module-scoped)."""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = httpx.Client(follow_redirects=True, timeout=30.0)
    return _DEFAULT_CLIENT


# Any inline image ref (absolute OR relative), optional ``"title"``.
_MD_IMG_ANY_RE = re.compile(
    r"!\[([^\]]*)\]\(\s*([^)\s\"']+)(?:\s+\"([^\"]*)\")?\s*\)"
)

# Known image suffix -> canonical on-disk suffix (normalize .jpeg -> .jpg).
_IMAGE_EXTS = {
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".gif": "gif",
    ".webp": "webp",
    ".bmp": "bmp",
    ".svg": "svg",
    ".tiff": "tiff",
    ".tif": "tiff",
}

# Magic-byte prefixes -> canonical suffix. Checked first so an image served
# without a usable extension / content-type still lands with the right name.
_MAGIC_TO_EXT = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"\x42\x4d": "bmp",
}

_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_IMAGES = 200


def _detect_ext_from_magic(data: bytes) -> str | None:
    """Return a canonical image suffix from magic bytes, else ``None``."""
    for magic, ext in _MAGIC_TO_EXT.items():
        if data[: len(magic)] == magic:
            return ext
    # RIFF/AVI container: WebP is "RIFFxxxxWEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _detect_ext_from_content_type(ctype: str) -> str | None:
    ctype = (ctype or "").split(";")[0].strip().lower()
    if ctype == "image/svg+xml":
        return "svg"
    if ctype.startswith("image/"):
        return ctype.split("/", 1)[1] or None
    return None


def _safe_name(base: str, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-._")
    slug = slug[:80] or "asset"
    return f"{slug}.{ext}" if ext else slug


class ResourceStore:
    """Download image references (and injected embedded images) into a
    ``<stem>.files/`` sibling directory and rewrite the markdown to point at
    the local copies, returning a manifest describing what happened."""

    def __init__(
        self,
        *,
        headers: dict | None = None,
        client: httpx.Client | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        max_images: int = _DEFAULT_MAX_IMAGES,
    ) -> None:
        self._headers = headers
        # Fall back to the shared lazy client when none is injected (the
        # default construction path); tests inject a fake client instead.
        self._client = client if client is not None else _default_client()
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_images = max_images

    # -- public API ---------------------------------------------------------

    def extract(
        self,
        *,
        markdown: str,
        base_url: str,
        out_dir: Path,
        stem: str,
    ) -> dict:
        """Rewrite every ``![...](url)`` ref in *markdown* to a local copy.

        URLs are resolved against *base_url* (already-absolute URLs pass
        through). Images are written to ``<out_dir>/<stem>.files/`` and the
        markdown body is rewritten to reference them relatively. Returns a
        manifest with the rewritten ``markdown`` so the caller can persist it.

        The ``<stem>.files/`` directory is created lazily -- only when the
        first image is written -- so content with no image refs leaves no
        empty resource folder behind.
        """
        files_dir = Path(out_dir) / f"{stem}.files"

        seen_url: dict[str, str] = {}
        seen_hash: dict[str, str] = {}
        rewritten = markdown
        skipped: list[dict] = []
        count = 0

        def _rewrite(m: re.Match) -> str:
            nonlocal count
            alt = m.group(1)
            target = m.group(2)
            title = m.group(3)

            # Skip already-rewritten local refs and non-resource targets.
            if target.startswith("./") or target.startswith("#"):
                return m.group(0)
            if target.startswith(("mailto:", "tel:", "data:", "javascript:")):
                return m.group(0)

            # Host-root-relative (``/assets/x``) resolves against base_url's
            # host; absolute http(s) pass through unchanged.
            resolved = (
                target
                if target.startswith(("http://", "https://", "ftp://"))
                else urljoin(base_url, target)
            )
            local = self._store_url(resolved, files_dir, seen_url, seen_hash)
            if local is None:
                skipped.append({"url": resolved, "reason": "skipped"})
                return m.group(0)
            count += 1
            rel = f"./{stem}.files/{local}"
            if title:
                return f'![{alt}]({rel} "{title}")'
            return f"![{alt}]({rel})"

        rewritten = _MD_IMG_ANY_RE.sub(_rewrite, markdown)
        return {
            "markdown": rewritten,
            "dir": str(files_dir),
            "stem": stem,
            "referenced": count,
            "files": sorted({f"{stem}.files/{p}" for p in seen_url.values()}),
            "skipped": skipped,
        }

    def extract_embedded(
        self,
        *,
        markdown: str,
        out_dir: Path,
        stem: str,
        images: list[dict],
    ) -> dict:
        """Write *images* (``{data, filename?}``) that are *not* referenced in
        *markdown* into ``<stem>.files/`` and append a "Figures" section
        linking each. Used for formats whose converter dropped the images.

        Lazy: with no *images* supplied, no folder is created and the
        markdown is returned unchanged.
        """
        files_dir = Path(out_dir) / f"{stem}.files"
        if not images:
            return {
                "markdown": markdown,
                "dir": str(files_dir),
                "stem": stem,
                "referenced": 0,
                "embedded": 0,
                "files": [],
                "skipped": [],
            }

        injected: list[tuple[int, str]] = []
        written: dict[str, str] = {}
        for idx, img in enumerate(images, start=1):
            data = img.get("data")
            if not data:
                continue
            ext = img.get("ext") or _detect_ext_from_magic(data) or "img"
            ext = _IMAGE_EXTS.get("." + ext, ext)
            fname = img.get("filename") or _safe_name(f"figure-{idx}", ext)
            if fname not in written:
                self._write_bytes(files_dir, fname, data)
                written[fname] = fname
            injected.append((idx, fname))

        injected.sort(key=lambda t: t[0])
        md = markdown or ""
        if injected:
            md = md.rstrip() + "\n\n"
            md += f"## Figures\n\n"
            for idx, fname in injected:
                md += f"![figure {idx}](./{stem}.files/{fname})\n\n"

        return {
            "markdown": md,
            "dir": str(files_dir),
            "stem": stem,
            "referenced": 0,
            "embedded": len(injected),
            "files": sorted(f"{stem}.files/{p}" for p in written.values()),
            "skipped": [],
        }

    # -- internals ----------------------------------------------------------

    def _store_url(
        self, url: str, files_dir: Path,
        seen_url: dict[str, str], seen_hash: dict[str, str],
    ) -> str | None:
        if not url.startswith(("http://", "https://", "ftp://")):
            return None
        try:
            validate_public_url(url)
        except SsrfBlockedError:
            return None
        if url in seen_url:
            return seen_url[url]
        try:
            resp = self._client.get(
                url, headers=self._headers, timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.RequestError as e:
            logger.debug("ResourceStore download failed for %s: %s", url, e)
            return None
        except Exception as e:  # noqa: BLE001 - never let one asset fail a store
            logger.debug("ResourceStore download error for %s: %s", url, e)
            return None
        if resp.status_code >= 400:
            return None
        data = resp.content
        if not data or len(data) > self._max_bytes:
            return None
        ext = _detect_ext_from_content_type(resp.headers.get("content-type", ""))
        if not ext:
            ext = _detect_ext_from_magic(data)
        # Canonicalize (e.g. content-type "jpeg" -> "jpg"); reject unknown.
        ext = _IMAGE_EXTS.get("." + ext, ext)
        if not ext or ext not in _IMAGE_EXTS.values():
            return None
        digest = hashlib.sha256(data).hexdigest()[:12]
        if digest in seen_hash:
            return seen_hash[digest]
        base = Path(url.rstrip("/")).name or f"image-{len(seen_url) + 1}"
        stem_of = Path(base).stem
        candidate = _safe_name(stem_of or "image", ext)
        # Avoid collisions within one folder.
        n = 1
        while candidate in seen_hash.values():
            candidate = f"{stem_of or 'image'}-{n}-{digest}.{ext}"
            n += 1
        self._write_bytes(files_dir, candidate, data)
        seen_url[url] = candidate
        seen_hash[digest] = candidate
        return candidate

    @staticmethod
    def _write_bytes(files_dir: Path, fname: str, data: bytes) -> None:
        try:
            files_dir.mkdir(parents=True, exist_ok=True)
            (files_dir / fname).write_bytes(data)
        except OSError as e:
            logger.debug("ResourceStore write failed for %s: %s", fname, e)
