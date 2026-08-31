"""Document extraction + structured HTML inspection for the toolbox.

Phase 4 of the agent_tools.py composition split (god-class
reduction). The document-extraction and HTML-structured-inspection
concern was moved out of the ``WebResearcherToolbox`` facade into
this collaborator. The facade keeps thin delegations
(``extract_document``, ``extract_document_structured``,
``inspect_html_structured``) so the public import surface and tool
dispatch are unchanged.

``DocumentExtractor`` reads all shared toolbox state through
``self._tb`` (the toolbox), mirroring ``SearchService`` /
``FetchService``: a caller that reassigns toolbox attributes is seen
live instead of via a stale captured copy.
"""

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from stitch_web_researcher._core import extract_tables_from_html
from stitch_web_researcher.config import FetchMode, normalize_url
from stitch_web_researcher.guard import evaluate, wrap_untrusted
from stitch_web_researcher.models import ExtractionResult, _sha256_hex, _utc_now_iso
from stitch_web_researcher.ssrf import SsrfBlockedError
from stitch_web_researcher.structured_parser import (
    StructuredOxideParser,
    ParsedDocumentPayload,
    build_follow_up_candidates,
    require_office_oxide,
    require_pdf_oxide,
)
from stitch_web_researcher.resource_store import ResourceStore
from stitch_web_researcher.token_budget import count_tokens
from stitch_web_researcher.text_links import extract_links

logger = logging.getLogger(__name__)


def _sanitize_filename(name: str, fallback: str = "document") -> str:
    """Collapse ``name`` to a single safe basename (no separators, no ``..``)."""
    stem = Path(name).stem or ""
    stem = stem.replace("/", "_").replace("\\", "_").replace("..", "_").strip()
    stem = "".join(ch if (ch.isalnum() or ch in "-_ ") else "_" for ch in stem)
    stem = stem.strip("._-")
    return stem or fallback


class DocumentExtractor:
    """Document extraction + structured HTML inspection."""

    def __init__(self, tb):
        self._tb = tb

    # ───────────────────────────────
    # Document Extraction
    # ───────────────────────────────

    def _finish_document(self, result: ExtractionResult) -> str:
        """§7: run the guard over document content and serialize the result.

        Applies the guard mode (annotate wrap / redact / block withhold) to
        the delivered content, attaches ``result.guard``, and returns the
        JSON string.
        """
        block, redacted, withheld = evaluate(
            self._tb._guard,
            [("document_text", result.content)],
            main_scope="document_text",
        )
        if block is None:
            return result.model_dump_json()
        result.guard = block
        if withheld:
            return json.dumps(
                {
                    "error": "content withheld by prompt-injection guard",
                    "source": result.source,
                    "guard": block,
                },
                indent=2,
            )
        if block.get("action") == "redact" and redacted is not None:
            result.content = redacted
        elif block.get("action") == "annotate" and redacted:
            result.content = wrap_untrusted(redacted, result.source)
        result.content_tokens = count_tokens(result.content, self._tb.model_name)
        return result.model_dump_json()

    def extract_document(
        self,
        source: str,
        pages: Optional[str] = None,
        structured: bool = False,
        *,
        store: bool = False,
        store_dir: Optional[str] = None,
    ) -> str:
        """Extract text content from documents.

        Structured: PDF, DOCX, XLSX, PPTX. Text (Tier 3.10): TXT, MD, CSV,
        JSON (pretty-printed), XML, and RSS/Atom feeds (surfaced as readable
        entry lists). Extension-less URLs whose Content-Type is text-like
        (e.g. text/plain) are also delivered as text.

        Parameters
        ----------
        source : str
            URL or local file path.
        pages : str, optional
            1-based inclusive page range: ``"10"``, ``"10-20"``, ``"10-"``
            or ``"-20"``. For PDFs this selects pages; for XLSX it
            selects sheets (the parser's page blocks). Supported for
            formats the structured parser yields per-page data for
            (PDF, XLSX); for other formats the call errors with an
            actionable message. The full document stays cached under its
            own key, so range reads do not evict whole-document reads.
        store : bool
            When true, write two files — the original document bytes
            verbatim (``<stem><ext>``) and the full extracted text as
            markdown (``<stem>.md``) — under ``store_dir`` (default
            ``stored_documents/`` next to the working directory). The full
            (untruncated) extracted text is stored even though the returned
            body is budget-truncated. ``store`` cannot be combined with
            ``pages``.
        store_dir : str, optional
            Directory to write the stored files into. Created if missing.
        """
        if structured:
            return self._extract_document_structured_impl(source)
        try:
            source = normalize_url(source)  # may still be a local path
            is_url = True
        except ValueError:
            is_url = urlparse(source).scheme in ("http", "https")

        if is_url:
            try:
                self._tb._validate_url(source)
            except (ValueError, SsrfBlockedError) as e:
                return json.dumps(self._tb._url_error(source, e), indent=2)
            # S4: robots gate before rate-limiting, so a disallowed fetch
            # does not burn a politeness delay.
            if self._tb._robots_disallows(source):
                return json.dumps(
                    {
                        "error": (
                            f"Document fetch disallowed by robots.txt: {source}"
                        )
                    },
                    indent=2,
                )
            self._tb._rate_limit_domain(source)

        # Tier 1.2: explicit page-range reads go through the structured
        # parser (the only path with per-page structure) and are cached
        # under a range-specific key.
        if pages is not None and str(pages).strip():
            if store:
                return json.dumps(
                    {
                        "error": (
                            "store=True cannot be combined with pages=...; "
                            "store the full document instead"
                        )
                    },
                    indent=2,
                )
            return self._extract_document_pages(source, str(pages).strip(), is_url)

        cache_key = self._tb._cache_key(source) if is_url else source
        raw_bytes: Optional[bytes] = None
        prov: dict = {}
        cached = self._tb.cache.get(cache_key)

        if cached is not None:
            # Re-apply the budget on every read (C4): the cache holds
            # untruncated content and the budget may have changed since the
            # entry was stored — same read-time truncation as the page cache.
            full_content = cached
            cache_hit = True
            # Tier 1.3: the stored content carries no fetch timestamp, so
            # fetched_at stays None; the hash still ties the read back to
            # the stored bytes.
        else:
            try:
                if is_url:
                    if store:
                        content, prov, raw_bytes = self._download_and_extract(
                            source, with_bytes=True
                        )
                    else:
                        content, prov = self._download_and_extract(source)
                    full_content = content
                else:
                    full_content = self._extract_local(source)
                    prov = {"fetched_at": _utc_now_iso()}
            except Exception as e:
                logger.error("Document extraction failed for %s: %s", source, e)
                return json.dumps(
                    {"error": f"Document extraction failed: {str(e)}"}, indent=2
                )
            self._tb.cache.put(cache_key, full_content)
            cache_hit = False

        # Store the original document + full extracted markdown when asked.
        stored = None
        if store:
            if raw_bytes is None:
                # Cache hit: refetch fresh bytes for storage.
                if is_url:
                    raw_bytes, store_prov = self._fetch_document_url(source)
                else:
                    raw_bytes = Path(source).read_bytes()
                    store_prov = {"fetched_at": _utc_now_iso()}
            else:
                store_prov = prov
            stored = self._store_document(
                source, raw_bytes, full_content, is_url, store_dir, store_prov
            )

        truncated = self._tb._budget._truncate(
            full_content, self._tb.max_markdown_chars, self._tb.max_tokens
        )
        result = ExtractionResult(
            source=source,
            content=truncated,
            content_tokens=count_tokens(truncated, self._tb.model_name),
            cache_hit=cache_hit,
            # Hash ties the read back to the full untruncated content.
            content_hash=_sha256_hex(full_content),
            # Link detection runs on the full content, not the truncated
            # delivery — a budget cut must never lose links.
            links=extract_links(full_content),
            **prov,
        )
        if stored is not None:
            result.stored = stored
        return self._finish_document(result)

    def _store_document(self, source, raw_bytes, full_text, is_url, store_dir, prov=None):
        """Write the original document bytes + extracted markdown to disk.

        ``raw_bytes`` is the untouched document (stored verbatim); ``full_text``
        is the full, untruncated extracted text written as markdown. Returns a
        dict with the written paths and sizes.
        """
        out_dir = Path(store_dir) if store_dir else Path.cwd() / "stored_documents"
        out_dir.mkdir(parents=True, exist_ok=True)

        prov = prov or {}
        served = prov.get("final_url") or source
        orig_name = (
            Path(urlparse(served).path).name if is_url else Path(source).name
        )
        suffix = Path(orig_name).suffix
        if suffix.lower() in self._DOC_TEXT_SUFFIXES:
            # A real document/text extension is present; Path.stem already
            # stripped exactly it, so "1707.06376v1.pdf" -> stem
            # "1707.06376v1" (the id is preserved).
            stem = Path(orig_name).stem or _sanitize_filename(source)
        elif is_url:
            # A URL with no real extension: either extensionless, or an
            # arXiv-style version fragment ("/pdf/1707.06376v1" ->
            # ".06376v1", which Python wrongly treats as an extension).
            # Keep the whole name as the stem and derive the extension from
            # the Content-Type, so the id is preserved verbatim ->
            # "1707.06376v1.pdf". "pdf" is checked first because some
            # servers still emit ``application/pdf; charset=binary``.
            stem = orig_name or _sanitize_filename(source)
            ct = (prov.get("content_type") or "").lower()
            fmt = self._CONTENT_TYPE_FORMAT.get(ct.split(";", 1)[0].strip().lower())
            suffix = f".{fmt}" if fmt else ".bin"
        else:
            # A local file with a non-document extension (e.g. "image.png")
            # or extensionless: honor the original suffix; extensionless
            # falls back to ".bin".
            stem = Path(orig_name).stem or _sanitize_filename(source)
            suffix = suffix or ".bin"

        orig_path = out_dir / f"{stem}{suffix}"
        md_path = out_dir / f"{stem}.md"

        if raw_bytes is not None:
            orig_path.write_bytes(raw_bytes)
        md_path.write_text(full_text, encoding="utf-8")

        resources = self._store_resources(
            served, out_dir, stem, full_text, raw_bytes, suffix
        )

        return {
            "directory": str(out_dir),
            "original": str(orig_path),
            "markdown": str(md_path),
            "original_bytes": len(raw_bytes) if raw_bytes is not None else 0,
            "markdown_chars": len(full_text),
            "content_type": prov.get("content_type"),
            "resources": resources,
        }

    def _store_resources(
        self, served, out_dir, stem, full_text, raw_bytes, suffix
    ) -> dict:
        """Extract images referenced in the stored content into
        ``<stem>.files/`` and rewrite ``<stem>.md`` to point at them.

        HTML pages keep ``![alt](url)`` image refs (already absolute after
        inspection), so they download here and the markdown is rewritten to
        local paths. Text/office documents whose converter kept image refs
        are handled the same way.

        Note: PDF and office converters drop images from the extracted
        markdown, and the bundled pdf_oxide detects images but cannot extract
        their bytes, so embedded PDF images are not currently retrievable --
        this method then yields an empty manifest rather than erroring.

        Best-effort: never raises, so a resource download failure cannot
        fail the whole store.
        """
        try:
            store = ResourceStore(headers=self._tb._next_headers())
            manifest = store.extract(
                markdown=full_text, base_url=served, out_dir=out_dir, stem=stem,
            )
            if manifest["referenced"]:
                (Path(out_dir) / f"{stem}.md").write_text(
                    manifest["markdown"], encoding="utf-8"
                )
            return {
                "dir": manifest["dir"],
                "files": manifest["files"],
                "referenced": manifest["referenced"],
            }
        except Exception as e:  # noqa: BLE001 - resources are best-effort
            logger.warning("ResourceStore failed for %s: %s", served, e)
            return {"dir": None, "files": [], "referenced": 0}

    def _fetch_document_url(self, url: str) -> tuple[bytes, dict]:
        """Tier 1.3: download a document URL; return (bytes, provenance).

        Redirects are followed so provenance.final_url is the URL that
        actually served the bytes — final_url != url tells the model the
        content moved.
        """
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(url, headers=self._tb._next_headers())
            response.raise_for_status()
        prov = {
            "fetched_at": _utc_now_iso(),
            "http_status": response.status_code,
            "final_url": str(response.url),
            "content_type": response.headers.get("content-type"),
        }
        return response.content, prov

    def _download_and_extract(
        self, url: str, *, with_bytes: bool = False
    ) -> tuple:
        """Download a document from URL; return (content, prov[, raw_bytes]).

        Tier 3.10: when the URL carries no usable extension (or an
        unrecognized one) but the server says the body is text-like
        (Content-Type: text/plain, application/json, ...), the bytes are
        extracted as text instead of raising. When ``with_bytes`` is true
        the original document bytes are returned alongside the extracted
        text so a ``store`` call can write the verbatim original.
        """
        data, prov = self._fetch_document_url(url)
        try:
            content = self._extract_from_bytes(data, url)
        except ValueError:
            ct = (prov.get("content_type") or "").split(";", 1)[0].strip().lower()
            # Binary document (PDF/OOXML) at an extensionless URL -- arXiv's
            # /pdf/<id> is the common case -- routes by Content-Type.
            doc_fmt = self._CONTENT_TYPE_FORMAT.get(ct)
            if doc_fmt is not None:
                content = self._parse_document_bytes(data, doc_fmt)
            else:
                kind = self._TEXT_LIKE_CONTENT_TYPES.get(ct)
                if kind is None:
                    raise
                if kind == "json":
                    content = self._extract_json_text(data)
                elif kind == "xml":
                    content = self._extract_xml_feed(data)
                else:
                    content = data.decode("utf-8-sig", errors="replace")
        if with_bytes:
            return content, prov, data
        return content, prov

    def _parse_document_bytes(self, data: bytes, fmt: str) -> str:
        """Parse document bytes dispatched by canonical format name.

        Used when a URL gives no usable extension and the response
        Content-Type decides the format (e.g. arXiv's ``application/pdf``),
        so ``extract_document`` works on extensionless document URLs. This
        is the binary-document complement to the text-like fallback in
        ``_download_and_extract``.
        """
        if fmt == "pdf":
            return require_pdf_oxide().from_bytes(data).to_markdown_all()
        if fmt in ("docx", "xlsx", "pptx"):
            return require_office_oxide().from_bytes(data).to_markdown()
        # Known-but-unsupported legacy office formats: actionable error.
        suffix = {
            "doc": ".doc", "xls": ".xls", "ppt": ".ppt", "xlsb": ".xlsb",
        }.get(fmt, f".{fmt}")
        hint = self._UNSUPPORTED_FORMAT_HINTS.get(suffix)
        if hint:
            raise ValueError(f"Unsupported document format: {suffix} ({hint}).")
        raise ValueError(f"Unsupported document format: {suffix}")

    # Tier 1.2: page-range reads. The flat extractor joins all pages into
    # one string, so a range can only be served by re-deriving the
    # per-page structure via StructuredOxideParser (Rust, fast). Formats
    # without per-page structure (DOCX/PPTX parse as one page, text
    # formats have no pages) are refused with an actionable message.
    _PAGED_EXTRACT_SUFFIXES = (".pdf", ".xlsx")

    # Extensions authoritative for a stored original's name. An extension
    # outside this set (e.g. arXiv's /pdf/<id> -> ".06954v2") is treated as
    # a version fragment and resolved from the Content-Type instead.
    _DOC_TEXT_SUFFIXES = (
        ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
        ".xlsb", ".csv", ".txt", ".md", ".json", ".xml", ".rss", ".atom",
    )

    # Content-Type (sans parameters) -> canonical document format. Lets an
    # extensionless document URL -- arXiv serves /pdf/<id> as
    # application/pdf with no extension -- resolve to the right parser.
    _CONTENT_TYPE_FORMAT = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.msword": "doc",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.ms-powerpoint": "ppt",
    }
    def _extract_document_pages(
        self, source: str, pages_spec: str, is_url: bool
    ) -> str:
        """Serve one page range of a document (see extract_document)."""
        from stitch_web_researcher.structured_parser import parse_page_range

        try:
            start, end = parse_page_range(pages_spec)
        except ValueError as e:
            return json.dumps({"error": str(e)}, indent=2)

        suffix = (
            Path(urlparse(source).path).suffix if is_url else Path(source).suffix
        ).lower()
        if suffix not in self._PAGED_EXTRACT_SUFFIXES:
            return json.dumps(
                {
                    "error": (
                        f"Page selection is supported for PDF (pages) and "
                        f"XLSX (sheets); this source is "
                        f"{suffix or 'extensionless'}. Call extract_document "
                        f"without pages, or convert the file to PDF."
                    )
                },
                indent=2,
            )

        base_key = self._tb._cache_key(source) if is_url else source
        cache_key = f"{base_key}#pages={pages_spec}"
        cached = self._tb.cache.get(cache_key)
        if cached is not None:
            truncated = self._tb._budget._truncate(
                cached, self._tb.max_markdown_chars, self._tb.max_tokens
            )
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self._tb.model_name),
                    cache_hit=True,
                    page_range=pages_spec,
                    # Tier 1.3: hash of the stored range content (the store
                    # keeps no fetch timestamp, so fetched_at stays None).
                    content_hash=_sha256_hex(cached),
                )
            )

        try:
            payload, prov = self._parse_document_pages(source, is_url)
            total = len(payload.pages)
            if total == 0:
                return json.dumps(
                    {"error": f"Document has no pages: {source}"}, indent=2
                )
            start = max(start, 1)
            if start > total:
                return json.dumps(
                    {
                        "error": (
                            f"Page range {pages_spec!r} out of bounds: "
                            f"document has {total} page(s)."
                        )
                    },
                    indent=2,
                )
            end = min(end if end is not None else total, total)
            end = max(end, start)
            selected = payload.pages[start - 1 : end]
            content = "\n\n".join(p.markdown for p in selected).strip()
            if not content:
                return json.dumps(
                    {
                        "error": (
                            f"Page range {pages_spec!r} produced no text."
                        )
                    },
                    indent=2,
                )

            self._tb.cache.put(cache_key, content)
            truncated = self._tb._budget._truncate(
                content, self._tb.max_markdown_chars, self._tb.max_tokens
            )
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self._tb.model_name),
                    cache_hit=False,
                    page_range=pages_spec,
                    page_start=start,
                    page_end=end,
                    total_pages=total,
                    # Tier 1.3: hash of the delivered range plus download
                    # provenance (URL reads) or parse time (local reads).
                    content_hash=_sha256_hex(content),
                    **prov,
                )
            )
        except Exception as e:
            logger.error("Page-range extraction failed for %s: %s", source, e)
            return json.dumps(
                {"error": f"Page-range extraction failed: {str(e)}"}, indent=2
            )

    def _parse_document_pages(self, source: str, is_url: bool):
        """Parse a document into a ParsedDocumentPayload (URL or local).

        Returns (payload, provenance); for local files the provenance
        carries only the parse time.
        """
        import os
        import tempfile as tf

        parser = StructuredOxideParser()
        if not is_url:
            return parser.parse_file(source), {"fetched_at": _utc_now_iso()}
        data, prov = self._fetch_document_url(source)
        suffix = Path(urlparse(source).path).suffix.lower() or ".pdf"
        tmp_path = None
        try:
            with tf.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            return parser.parse_file(tmp_path), prov
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _extract_local(self, path: str) -> str:
        """Extract content from a local document file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        content = file_path.read_bytes()
        return self._extract_from_bytes(content, str(file_path))

    # M16: plain-text formats the extractor can really deliver — these are
    # exactly what DOCUMENT_EXTENSIONS (structured_parser) may advertise in
    # addition to pdf/OOXML. classify_link and _extract_from_bytes must stay
    # in sync, or the model is promised an extraction that would raise.
    # Tier 3.10 (item 10): JSON joins the text family (pretty-printed when
    # valid); XML/RSS/Atom get feed-aware extraction with a raw-text
    # fallback, so every advertised text format always delivers content.
    _TEXT_SUFFIXES = (".csv", ".txt", ".md", ".json")
    _XML_FEED_SUFFIXES = (".xml", ".rss", ".atom")
    # Content-types that mark a body as extractable text when the URL gives
    # no usable extension (extension-less API/feed/plain-text URLs).
    _TEXT_LIKE_CONTENT_TYPES = {
        "text/plain": "text",
        "text/markdown": "text",
        "text/csv": "text",
        "application/json": "json",
        "text/xml": "xml",
        "application/xml": "xml",
        "application/rss+xml": "xml",
        "application/atom+xml": "xml",
    }
    # Cap on entries surfaced from a feed; the output budget truncation
    # still applies on top.
    _FEED_MAX_ENTRIES = 50
    _UNSUPPORTED_FORMAT_HINTS = {
        ".doc": "convert the file to .docx",
        ".xls": "convert the file to .xlsx",
        ".ppt": "convert the file to .pptx",
        ".odt": "convert the file to .docx",
        ".ods": "convert the file to .xlsx",
        ".odp": "convert the file to .pptx",
        ".rtf": "convert the file to .docx or PDF",
        ".epub": "convert the file to PDF",
    }

    def _extract_from_bytes(self, data: bytes, source: str) -> str:
        """Extract text from document bytes based on file type."""
        suffix = Path(source).suffix.lower()

        if suffix == ".pdf":
            doc = require_pdf_oxide().from_bytes(data)
            return doc.to_markdown_all()
        elif suffix in (".docx", ".xlsx", ".pptx"):
            doc = require_office_oxide().from_bytes(data)
            return doc.to_markdown()
        elif suffix in self._TEXT_SUFFIXES:
            # M16: plain text is trivial to support and covers the
            # CSV/TXT/MD/JSON links that classify_link advertises.
            if suffix == ".json":
                return self._extract_json_text(data)
            return data.decode("utf-8-sig", errors="replace")
        elif suffix in self._XML_FEED_SUFFIXES:
            # Tier 3.10: RSS/Atom feeds become readable entry lists; any
            # other (or malformed) XML falls back to the raw text.
            return self._extract_xml_feed(data)
        elif suffix in self._UNSUPPORTED_FORMAT_HINTS:
            # M16: honest, actionable failure for formats we cannot parse.
            hint = self._UNSUPPORTED_FORMAT_HINTS[suffix]
            raise ValueError(
                f"Unsupported document format: {suffix} ({hint})."
            )
        else:
            raise ValueError(f"Unsupported document format: {suffix}")

    @staticmethod
    def _extract_json_text(data: bytes) -> str:
        """Tier 3.10: JSON as text — pretty-printed when valid, raw otherwise."""
        import json as _json

        raw = data.decode("utf-8-sig", errors="replace")
        try:
            obj = _json.loads(raw)
        except ValueError:
            return raw
        return _json.dumps(obj, indent=2, ensure_ascii=False)

    @staticmethod
    def _extract_xml_feed(data: bytes) -> str:
        """Tier 3.10: RSS/Atom/RDF feeds as readable entries.

        Uses the stdlib ElementTree (no new dependency). Feeds are detected
        by local tag name so namespaces are irrelevant. Any parse failure —
        or an XML document with no item/entry elements (e.g. a sitemap) —
        falls back to the raw text so a .xml/.rss/.atom source always
        delivers *something* readable.
        """
        import xml.etree.ElementTree as ET

        raw = data.decode("utf-8-sig", errors="replace")
        try:
            root = ET.fromstring(data)
        except (ET.ParseError, UnicodeDecodeError, ValueError):
            return raw

        def _local(tag) -> str:
            return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

        def _text(el, names) -> str:
            for child in el.iter():
                if _local(child.tag) in names and child.text and child.text.strip():
                    return child.text.strip()
            return ""

        entries = []
        for el in root.iter():
            if _local(el.tag) not in ("item", "entry"):
                continue
            title = _text(el, {"title"})
            link = ""
            for child in el.iter():
                if _local(child.tag) == "link":
                    href = child.get("href")
                    if href:
                        link = href
                    elif child.text and child.text.strip():
                        link = child.text.strip()
                    break
            desc = _text(el, {"description", "summary", "content"})
            date = _text(el, {"pubDate", "published", "updated", "date"})
            entries.append((title, link, date, desc))

        if not entries:
            return raw

        feed_title = ""
        for child in root:  # direct children: channel / feed
            feed_title = _text(child, {"title"})
            if feed_title:
                break

        lines = []
        if feed_title:
            lines.append(f"# {feed_title}")
            lines.append("")
        lines.append(f"Feed entries: {min(len(entries), 50)}")
        lines.append("")
        shown = 0
        for title, link, date, desc in entries:
            if shown >= 50:
                break
            shown += 1
            lines.append(f"- **{title or '(untitled)'}**")
            if link:
                lines.append(f"  Link: {link}")
            if date:
                lines.append(f"  Date: {date}")
            if desc:
                lines.append(f"  {desc}")
        if len(entries) > 50:
            lines.append("")
            lines.append(
                f"… {len(entries) - 50} more entries not shown "
                f"(capped at 50)."
            )
        return "\n".join(lines)

    # ───────────────────────────────
    # ───────────────────────────────
    # Structured Document Extraction
    # ───────────────────────────────

    def extract_document_structured(self, source: str) -> str:
        """Backwards-compatible wrapper: structured extraction.

        Kept so existing callers keep working; the P8 tool surface now
        exposes this through ``extract_document(structured=True)``.
        """
        return self._extract_document_structured_impl(source)

    def _extract_document_structured_impl(self, source: str) -> str:
        """
        Download (if URL) and parse a document into a structured
        ParsedDocumentPayload with metadata, pages, and tables.
        """
        import os
        import tempfile as tf

        parsed = urlparse(source)
        is_url = parsed.scheme in ("http", "https")

        if is_url:
            try:
                self._tb._validate_url(source)
            except (ValueError, SsrfBlockedError) as e:
                return json.dumps(self._tb._url_error(source, e), indent=2)
            # S4: robots gate before rate-limiting.
            if self._tb._robots_disallows(source):
                return json.dumps(
                    {
                        "error": (
                            f"Document fetch disallowed by robots.txt: {source}"
                        )
                    },
                    indent=2,
                )
            self._tb._rate_limit_domain(source)

        tmp_path = None
        try:
            if is_url:
                with httpx.Client(timeout=30) as client:
                    response = client.get(source, headers=self._tb._next_headers())
                    response.raise_for_status()

                suffix = Path(source).suffix.lower() or ".pdf"
                with tf.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(response.content)
                    tmp_path = tmp.name
            else:
                tmp_path = source

            parser = StructuredOxideParser()
            payload = parser.parse_file(tmp_path)

            # Files lose <a href> structure: detect URLs written into the
            # extracted text so documents get the same follow-up signal
            # HTML pages get. (Anchored links, when the parser provides
            # them, take precedence.)
            if not payload.links:
                full_text = "\n".join(p.raw_text for p in payload.pages)
                text_urls = extract_links(full_text)
                if text_urls:
                    payload.links = build_follow_up_candidates(
                        [(u, "(text)") for u in text_urls]
                    )

            if is_url and tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            # Budget the page text, never the serialized JSON: a string
            # cut here would hand the model unparseable output.
            payload_json = payload.to_json()
            return self._tb._budget._fit_json(
                lambda b: self._tb._budget._shrink_parsed_payload(payload_json, b),
                self._tb.max_markdown_chars,
                self._tb.max_tokens,
                {
                    "source": source,
                    "error": "document too large for the output budget",
                    "hint": "narrow the read with the pages parameter",
                },
            )

        except Exception as e:
            logger.error("Structured extraction failed for %s: %s", source, e)
            if is_url and tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            return json.dumps(
                {"error": f"Structured document extraction failed: {str(e)}"}, indent=2
            )

    # ───────────────────────────────
    # HTML Structured Inspection
    # ───────────────────────────────

    # Tier 3.11: caps for HTML table extraction (a page with a 10,000-row
    # table must not drown the token budget in table JSON).
    _HTML_MAX_TABLES = 20
    _HTML_MAX_TABLE_ROWS = 500

    def _extract_html_tables(self, html: str):
        """Tier 3.11: extract HTML tables into ExtractedTable objects.

        Best effort: extraction errors are logged and yield no tables; the
        page content itself is never affected. Returns a list of
        ExtractedTable (name, headers, rows) in document order.
        """
        from stitch_web_researcher.structured_parser import ExtractedTable

        try:
            raw = extract_tables_from_html(
                html, self._HTML_MAX_TABLES, self._HTML_MAX_TABLE_ROWS
            )
        except Exception as e:
            logger.warning("HTML table extraction failed: %s", e)
            return []
        return [
            ExtractedTable(name=name, headers=headers, rows=rows)
            for name, headers, rows in raw
        ]

    def inspect_html_structured(self, url: str, use_smart: str = FetchMode.AUTO.value) -> str:
        """Backwards-compatible wrapper: structured output from ``inspect_html_page``.

        Kept so existing callers keep working; the P8 tool surface now
        exposes this through ``inspect_html_page(..., structured=True)``.
        """
        return self._inspect_html_structured_impl(url, use_smart)

    def _inspect_html_structured_impl(
        self, url: str, use_smart: str = FetchMode.AUTO.value
    ) -> str:
        """
        Fetch a web page and return it as a structured ParsedDocumentPayload
        with metadata (OG, Twitter, JSON-LD), markdown content, tables,
        and links.

        Unifies the HTML fetching pipeline with the structured document
        pipeline so that web pages and file documents produce the same
        ParsedDocumentPayload output.

        Parameters
        ----------
        url : str
            The URL to inspect.
        use_smart : {"auto", "browser", "static"}
            Per-call render strategy. "auto" (default) follows fetch_mode
            (static first, stealth browser on failure/non-text); "browser"
            renders with the headless browser first (static on failure);
            "static" is static-only.

        Returns
        -------
        str
            JSON-serialised ParsedDocumentPayload (token-truncated).
        """
        url, url_error = self._tb._prepare_url(url)
        if url_error is not None:
            return json.dumps(url_error, indent=2)

        # Cache stores the untruncated payload JSON; budgets are re-applied
        # on every read so changed limits are honored. A cached result is
        # served on repeat visits too (C3: data beats a warning).
        cached_json = self._tb.cache.get("structured:" + self._tb._cache_key(url))

        try:
            if cached_json is not None:
                logger.info("Cache hit (structured) for %s", url)
                return self._tb._budget._fit_json(
                    lambda b: self._tb._budget._shrink_parsed_payload(cached_json, b),
                    self._tb.max_markdown_chars,
                    self._tb.max_tokens,
                    {
                        "url": url,
                        "error": "page too large for the output budget",
                        "cache_hit": True,
                    },
                )
            # S4: robots.txt compliance -- only on the fetch path.
            if self._tb._robots_disallows(url):
                logger.warning("URL disallowed by robots.txt: %s", url)
                return json.dumps(
                    {"warning": "URL disallowed by robots.txt", "url": url}, indent=2
                )
            if not self._tb._claim_in_flight(url):
                logger.warning("URL already visited or in flight: %s", url)
                return json.dumps(
                    {"warning": "URL already visited", "url": url}, indent=2
                )
            self._tb._rate_limit_domain(url)

            markdown, links, html_metadata, fetch_method, html = (
                self._tb._fetch._fetch_html_with_html(url, use_smart)
            )

            # Tier 3.11: extract tables from the raw HTML (static path
            # only; browser renders expose no raw DOM).
            tables = self._extract_html_tables(html) if html else []

            # Build structured payload via unified parser
            parser = StructuredOxideParser()
            payload = parser.parse_html(
                markdown=markdown,
                links=links,
                html_metadata=html_metadata,
                url=url,
                max_links=self._tb.max_links,
                tables=tables,
            )
            # §7: guard the payload before caching so repeat reads carry
            # the already-scanned result.
            if self._tb._fetch._scan_structured_guard(payload, url):
                self._tb._release_in_flight(url)  # S5: stays retryable
                return json.dumps(
                    {
                        "error": "content withheld by prompt-injection guard",
                        "url": url,
                        "guard": payload.guard,
                    },
                    indent=2,
                )

            payload_json = payload.to_json()
            self._tb.cache.put("structured:" + self._tb._cache_key(url), payload_json)
            self._tb._mark_visited(url)  # success only (C3)
            self._tb._release_in_flight(url)  # S5
            return self._tb._budget._fit_json(
                lambda b: self._tb._budget._shrink_parsed_payload(payload_json, b),
                self._tb.max_markdown_chars,
                self._tb.max_tokens,
                {"url": url, "error": "page too large for the output budget"},
            )
        except Exception as e:
            self._tb._release_in_flight(url)  # S5: stays retryable
            logger.error("Structured HTML inspection failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"Structured HTML inspection failed: {str(e)}"}, indent=2
            )
