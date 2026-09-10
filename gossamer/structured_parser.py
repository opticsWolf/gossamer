"""
Structured document extraction with Pydantic v2 validation.

Uses pdf_oxide and office_oxide to extract layout-aware content,
tables, and metadata from PDF / DOCX / XLSX / PPTX files.
All outputs are validated through Pydantic v2 schemas.

The extractors are optional dependencies (the ``documents`` extra);
this module imports fine without them and raises an actionable error
at parse time instead.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator

from gossamer import _core as _rust

# ────────────────────────────────────────────────────────────────
# Optional document extractors
#
# pdf_oxide / office_oxide ship in the ``documents`` extra
# (``pip install "gossamer-web[documents]"``). The import is
# best-effort so the rest of the package (HTML research, search) works
# without them; the error surfaces at parse time with an install hint.
# ────────────────────────────────────────────────────────────────

try:
    from pdf_oxide import PdfDocument
except ImportError:  # optional extra not installed
    PdfDocument = None

try:
    from office_oxide import Document as OfficeDoc
except ImportError:  # optional extra not installed
    OfficeDoc = None

DOCUMENTS_EXTRA_HINT = (
    "PDF/Office extraction requires the 'documents' extra — "
    'install it with: pip install "gossamer-web[documents]"'
)


def require_pdf_oxide():
    """Return the ``pdf_oxide.PdfDocument`` class, or raise with an install hint."""
    if PdfDocument is None:
        raise ImportError(DOCUMENTS_EXTRA_HINT)
    return PdfDocument


def require_office_oxide():
    """Return the ``office_oxide.Document`` class, or raise with an install hint."""
    if OfficeDoc is None:
        raise ImportError(DOCUMENTS_EXTRA_HINT)
    return OfficeDoc


logger = logging.getLogger(__name__)


def _office_cell_text(cell: Dict[str, Any]) -> str:
    """Flatten one IR table cell (content → paragraphs → text runs) to text."""
    parts: List[str] = []
    for block in cell.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            continue
        for run in block.get("content") or []:
            if isinstance(run, dict) and run.get("text"):
                parts.append(str(run["text"]).strip())
    return " ".join(p for p in parts if p)


def office_ir_tables(ir: Dict[str, Any]) -> List[Tuple[str, List[List[str]]]]:
    """All tables in an office IR dict as ``(section_title, rows)`` pairs.

    Rows keep their original order (the first row is the header row when
    the document has one). Works for every office format: office-oxide
    >= 0.1.10 renders DOCX/XLSX/PPTX tables uniformly under
    ``sections[].elements``.
    """
    out: List[Tuple[str, List[List[str]]]] = []
    for i, section in enumerate(ir.get("sections") or [], 1):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or f"Section {i}")
        for element in section.get("elements") or []:
            if not isinstance(element, dict) or element.get("type") != "table":
                continue
            rows = [
                [_office_cell_text(c) for c in row.get("cells") or []
                 if isinstance(c, dict)]
                for row in element.get("rows") or []
                if isinstance(row, dict)
            ]
            rows = [r for r in rows if r]
            if rows:
                out.append((title, rows))
    return out


class FollowUpCandidate(BaseModel):
    """A follow-up link candidate surfaced to the calling agent.

    Lives in this (schema) module so that both the page-inspection
    result (``InspectionResult``) and the structured payload
    (``ParsedDocumentPayload``) share one model without circular
    imports.
    """

    title: str = "(untitled)"
    url: str
    type: str = "page"  # 'page' -> inspect_html_page, 'document' -> extract_document


# M16: exactly the formats extract_document can deliver (pdf, OOXML and
# plain text). classify_link must never promise a format the extractor
# raises on — the model would be sent into a guaranteed failure loop.
# Legacy/binary formats (.doc/.xls/.ppt/.odt/.ods/.odp/.rtf/.epub) now
# classify as 'page'; if the model still calls extract_document on them,
# the extractor answers with an actionable conversion hint.
DOCUMENT_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".csv", ".txt", ".md",
    # Tier 3.10 (item 10): text-based web formats are extractable as text,
    # so links to them must route to extract_document, not page scraping.
    ".json", ".xml", ".rss", ".atom",
})


def classify_link(url: str) -> str:
    """Classify a URL as 'document' (needs extract_document) or 'page'
    (needs inspect_html_page), based on its path extension.

    Extension check in Rust (``src/miscutils.rs``); every non-``str``
    input took the original ``except`` arm to ``"page"``, so the
    wrapper returns that directly.
    """
    if not isinstance(url, str):
        return "page"
    return _rust.classify_link(url)


def parse_page_range(spec: str) -> tuple[int, Optional[int]]:
    """Parse a 1-based page-range spec into ``(start, end)``.

    Accepted forms (page numbers are 1-based and inclusive):

    - ``"10"``    -> ``(10, 10)``
    - ``"10-20"`` -> ``(10, 20)``
    - ``"10-"``   -> ``(10, None)``  (10 through the last page)
    - ``"-20"``   -> ``(1, 20)``     (first page through 20)

    ``end`` is ``None`` when the range is open-ended; the caller clamps
    it to the actual page count. Raises ``ValueError`` for anything
    that is not a well-formed positive page or range.

    Parsing in Rust (``src/miscutils.rs``); ``_parse_page_range_py``
    below keeps the original body verbatim as the parity oracle and
    as the fallback for non-string probes (identical errors).
    """
    probe = (spec or "")
    if not isinstance(probe, str):
        return _parse_page_range_py(spec)
    return _rust.parse_page_range(probe)


def _parse_page_range_py(spec: str) -> tuple[int, Optional[int]]:
    """Original pure-Python page-range parser (parity oracle)."""
    spec = (spec or "").strip()
    if not spec or spec == "-":
        return 1, None
    if "-" in spec:
        left, _, right = spec.partition("-")
        left = left.strip()
        right = right.strip()
        if "-" in right:
            raise ValueError(
                f"Invalid page range: expected N, N-M, N- or -M, got {spec!r}."
            )
        start = 1 if left == "" else _parse_page_number(left, spec)
        end = None if right == "" else _parse_page_number(right, spec)
    else:
        start = _parse_page_number(spec, spec)
        end = start
    if end is not None and end < start:
        raise ValueError(
            f"Invalid page range: start {start} is after end {end}."
        )
    return start, end


def _parse_page_number(raw: str, spec: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid page range: {raw!r} is not a page number."
        ) from None
    if value < 1:
        raise ValueError(
            "Invalid page range: page numbers are 1-based."
        )
    return value


def build_follow_up_candidates(
    anchored_links: List, max_links: int = 0
) -> List[FollowUpCandidate]:
    """Turn (url, anchor_text) pairs into validated FollowUpCandidate models.

    Deduplicates by URL and (when *max_links* > 0) truncates to that many.
    Each candidate carries the anchor text so the model can judge relevance
    by name, plus a 'type' hint: 'document' links should be fetched via
    extract_document, 'page' links via inspect_html_page.
    """
    out: List[FollowUpCandidate] = []
    seen: set = set()
    for item in anchored_links or []:
        if max_links > 0 and len(out) >= max_links:
            break
        if isinstance(item, tuple) and len(item) == 2:
            url, text = item
        else:
            url, text = str(item), ""
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            FollowUpCandidate(
                title=(text or "").strip() or "(untitled)",
                url=url,
                type=classify_link(url),
            )
        )
    return out


# ────────────────────────────────────────────────────────────────
# 1. Pydantic v2 Structural Schemas
# ────────────────────────────────────────────────────────────────

class FileMeta(BaseModel):
    """Core file/page identity fields (documents and web pages)."""

    file_name: str = Field("", description="Original file name or page URL slug.")
    file_size_bytes: int = Field(0, description="File size in bytes (0 for web pages).")
    format: str = Field("", description="Detected format (pdf, docx, html, …).")
    title: Optional[str] = Field(None, description="Document or page title.")
    author: Optional[str] = Field(None, description="Author or creator.")
    description: Optional[str] = Field(None, description="Page or document description.")
    created_at: Optional[datetime] = Field(None, description="UTC creation timestamp.")
    modified_at: Optional[datetime] = Field(None, description="UTC modification timestamp.")
    page_count: int = Field(default=1, description="Number of pages or sheets.")


class WebBasicsMeta(BaseModel):
    """Basic HTML-head metadata for web pages."""

    canonical: Optional[str] = Field(None, description="Canonical URL.")
    language: Optional[str] = Field(None, description="Page language (e.g. 'en').")
    keywords: Optional[List[str]] = Field(None, description="Meta keywords.")
    robots: Optional[str] = Field(None, description="Robots directive.")


class OpenGraphMeta(BaseModel):
    """Open Graph protocol fields."""

    og_title: Optional[str] = Field(None, description="og:title.")
    og_type: Optional[str] = Field(None, description="og:type (e.g. 'article').")
    og_image: Optional[str] = Field(None, description="og:image URL.")
    og_images: Optional[List[Dict[str, Any]]] = Field(None, description="All og:image entries.")
    og_description: Optional[str] = Field(None, description="og:description.")
    og_site_name: Optional[str] = Field(None, description="og:site_name.")
    og_url: Optional[str] = Field(None, description="og:url.")


class TwitterMeta(BaseModel):
    """Twitter Card fields."""

    twitter_card: Optional[str] = Field(None, description="twitter:card type.")
    twitter_title: Optional[str] = Field(None, description="twitter:title.")
    twitter_description: Optional[str] = Field(None, description="twitter:description.")
    twitter_image: Optional[str] = Field(None, description="twitter:image URL.")
    twitter_site: Optional[str] = Field(None, description="twitter:site handle.")


class StructuredDataMeta(BaseModel):
    """Structured-data and passthrough metadata sections."""

    jsonld: Optional[List[Dict[str, Any]]] = Field(None, description="JSON-LD / Schema.org objects.")
    microdata: Optional[List[Dict[str, Any]]] = Field(None, description="HTML5 microdata items.")
    microformats: Optional[Dict[str, Any]] = Field(None, description="Microformats (h-card, …).")
    dublin_core: Optional[Dict[str, Any]] = Field(None, description="Dublin Core metadata.")
    rdfa: Optional[List[Dict[str, Any]]] = Field(None, description="RDFa triples.")
    rel_links: Optional[Dict[str, List[str]]] = Field(None, description="Link relationships.")
    manifest: Optional[Dict[str, Any]] = Field(None, description="Web App Manifest link.")

    extra_meta: Dict[str, Any] = Field(
        default_factory=dict, description="Raw metadata key/value pairs."
    )


class DocumentMetadata(
    FileMeta,
    WebBasicsMeta,
    OpenGraphMeta,
    TwitterMeta,
    StructuredDataMeta,
):
    """
    Normalised metadata for documents and web pages.

    For files: populated from PDF XMP / Office property streams.
    For web pages: enriched with HTML metadata (OG, Twitter, JSON-LD, …)
    via meta-oxide.

    Deliberately **flat**: it is a public serialization schema consumed by
    LLM tooling, so all fields remain top-level. Logical grouping lives in
    the mixin bases (:class:`FileMeta`, :class:`WebBasicsMeta`,
    :class:`OpenGraphMeta`, :class:`TwitterMeta`,
    :class:`StructuredDataMeta`); composing them here changes nothing about
    the serialized output.
    """
    

class ExtractedTable(BaseModel):
    """Tabular grid data extracted from a PDF page or Excel sheet."""

    name: str = Field(..., description="Sheet name or page/table identifier.")
    headers: List[str] = Field(default_factory=list, description="Column header labels.")
    rows: List[List[Any]] = Field(default_factory=list, description="Data rows.")

    @field_validator("rows")
    @classmethod
    def check_grid_alignment(cls, v: List[List[Any]]) -> List[List[Any]]:
        if v and not all(isinstance(row, list) for row in v):
            raise ValueError("Every row must be a list of cell values.")
        return v


class ExtractedPage(BaseModel):
    """Text and Markdown content extracted from a single page or block."""

    page_number: int = Field(..., description="1-based page index.")
    raw_text: str = Field(..., description="Plain text extracted from the page.")
    markdown: str = Field(
        ..., description="Layout-aware Markdown representation of the page."
    )
    tables: List[ExtractedTable] = Field(
        default_factory=list, description="Tables found on this page."
    )


class ParsedDocumentPayload(BaseModel):
    """Unified output structure for LLM context windows or vector stores."""

    metadata: DocumentMetadata
    pages: List[ExtractedPage] = Field(default_factory=list)
    tables: List[ExtractedTable] = Field(default_factory=list)
    links: List[FollowUpCandidate] = Field(
        default_factory=list,
        description=(
            "Follow-up link candidates: anchored links for HTML pages; "
            "for files, URLs detected in the extracted text (title "
            "'(text)')."
        ),
    )
    # §7: optional prompt-injection guard block (present only when the
    # guard is enabled and a scanned scope was checked).
    guard: Optional[dict] = Field(
        default=None,
        description="Prompt-injection guard report (absent when guard disabled).",
    )

    def to_json(self, indent: int = 2) -> str:
        """Serialize the full payload to a pretty-printed JSON string."""
        return self.model_dump_json(indent=indent)


# ────────────────────────────────────────────────────────────────
# 2. Helper: parse XMP datetime strings
# ────────────────────────────────────────────────────────────────

def _parse_xmp_datetime(raw: Optional[str]) -> Optional[datetime]:
    """
    Best-effort parser for XMP datetime formats.

    Handles:
      - '2024-01-15T10:30:00Z'  (ISO 8601)
      - '2024-01-15T10:30:00+05:00'
      - '2024-01-15T10:30:00'   (no tz)
      - '2024-01-15'            (date only)
    """
    if not raw:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


# ────────────────────────────────────────────────────────────────
# 3. StructuredOxideParser
# ────────────────────────────────────────────────────────────────

class StructuredOxideParser:
    """
    Parses PDF / DOCX / XLSX / PPTX files into validated
    :class:`ParsedDocumentPayload` objects.

    Usage
    -----
    >>> parser = StructuredOxideParser()
    >>> payload = parser.parse_file("report.pdf")
    >>> print(payload.to_json())
    """

    # ── PDF metadata extraction ────────────────────────────────

    @staticmethod
    def _extract_pdf_metadata(
        path: Path, doc: PdfDocument
    ) -> DocumentMetadata:
        """Pull XMP metadata from a PdfDocument into DocumentMetadata."""
        raw_meta: Optional[Dict[str, Any]] = doc.xmp_metadata() or {}

        return DocumentMetadata(
            file_name=path.name,
            file_size_bytes=path.stat().st_size,
            format="pdf",
            title=raw_meta.get("dc_title"),
            author=(
                raw_meta.get("dc_creator")
                or raw_meta.get("dc_contributor")
            ),
            created_at=_parse_xmp_datetime(raw_meta.get("xmp_create_date")),
            modified_at=_parse_xmp_datetime(raw_meta.get("xmp_modify_date")),
            page_count=doc.page_count(),
            extra_meta={
                k: str(v)
                for k, v in raw_meta.items()
                if k
                not in {
                    "dc_title",
                    "dc_creator",
                    "dc_contributor",
                    "xmp_create_date",
                    "xmp_modify_date",
                }
            },
        )

    # ── Office metadata extraction ─────────────────────────────

    @staticmethod
    def _extract_office_metadata(
        path: Path, doc: OfficeDoc, format_type: str
    ) -> DocumentMetadata:
        """
        Pull properties from an office_oxide Document.

        office_oxide exposes minimal metadata, so we fall back
        to file-level info and the IR (internal representation)
        JSON for any extra keys.
        """
        # Try to get title/author from the IR (JSON string in
        # office-oxide >= 0.1.10; older versions returned a dict).
        ir: Dict[str, Any] = {}
        try:
            parsed = json.loads(doc.to_ir_json())
            if isinstance(parsed, dict):
                ir = parsed
        except Exception:
            pass  # to_ir_json may not be available for all formats

        meta = ir.get("metadata") if isinstance(ir.get("metadata"), dict) else {}
        title = meta.get("title") or ir.get("title")
        author = meta.get("author") or ir.get("author")

        # Page count: for PPTX = slide count, XLSX = sheet count, DOCX = 1.
        # In the sections-shaped IR each slide/sheet is one section.
        page_count = 1
        if format_type in ("xlsx", "xls", "pptx", "ppt"):
            sections = ir.get("sections")
            if isinstance(sections, list):
                page_count = max(1, len(sections))

        return DocumentMetadata(
            file_name=path.name,
            file_size_bytes=path.stat().st_size,
            format=format_type,
            title=title if isinstance(title, str) else None,
            author=author if isinstance(author, str) else None,
            page_count=page_count,
            extra_meta={
                k: str(v)
                for k, v in meta.items()
                if k not in ("title", "author")
            },
        )

    # ── Table extraction helpers ───────────────────────────────

    @staticmethod
    def _tables_from_pdf_page(page) -> List[ExtractedTable]:
        """
        Convert pdf_oxide PdfPage.tables (list[dict]) into
        ExtractedTable Pydantic models.

        Each table dict has keys like 'rows' where each row
        contains cells with 'text' and optionally 'bbox'.
        """
        tables = getattr(page, "tables", None)
        if not tables:
            return []

        extracted: List[ExtractedTable] = []
        for idx, tbl in enumerate(tables):
            rows_data = tbl.get("rows", [])
            if not rows_data:
                continue

            # First row might be headers
            headers: List[str] = []
            data_rows: List[List[Any]] = []

            for row_cells in rows_data:
                cells = [
                    cell.get("text", "").strip()
                    for cell in row_cells
                    if isinstance(cell, dict)
                ]
                if not cells:
                    continue
                if not headers:
                    headers = cells  # treat first row as header
                else:
                    data_rows.append(cells)

            extracted.append(
                ExtractedTable(
                    name=f"page_table_{idx}",
                    headers=headers,
                    rows=data_rows,
                )
            )
        return extracted

    # ── Main entry point ───────────────────────────────────────

    # Format families → handler methods (dispatch table).
    _SPREADSHEET_FORMATS = frozenset({"xlsx", "xls", "xlsb", "ods"})
    _OFFICE_TEXT_FORMATS = frozenset({"docx", "doc", "pptx", "ppt"})

    def _parse_pdf(
        self,
        path: Path,
        detect_headings: bool,
    ) -> ParsedDocumentPayload:
        """Parse a PDF into per-page content plus flattened tables."""
        logger.info("Parsing PDF: %s", path.name)
        PdfDoc = require_pdf_oxide()
        with PdfDoc(str(path)) as doc:
            metadata = self._extract_pdf_metadata(path, doc)
            pages: List[ExtractedPage] = []

            for page in doc:
                pages.append(
                    ExtractedPage(
                        page_number=page.index + 1,  # 1-based
                        raw_text=page.text,
                        markdown=page.markdown(detect_headings=detect_headings),
                        tables=self._tables_from_pdf_page(page),
                    )
                )

            # Flatten per-page tables into the top-level list too.
            all_tables: List[ExtractedTable] = [
                table for p in pages for table in p.tables
            ]
            return ParsedDocumentPayload(
                metadata=metadata,
                pages=pages,
                tables=all_tables,
            )

    def _parse_spreadsheet(self, path: Path) -> ParsedDocumentPayload:
        """Parse an Excel-family file; tables come from the office IR."""
        format_type = path.suffix.lower().lstrip(".") or "xlsx"
        logger.info("Parsing Excel: %s", path.name)
        Office = require_office_oxide()
        with Office.open(str(path)) as doc:
            metadata = self._extract_office_metadata(path, doc, format_type)
            tables = self._tables_from_office_ir(doc, format_type)
            pages = [
                ExtractedPage(
                    page_number=1,
                    raw_text=doc.plain_text(),
                    markdown=doc.to_markdown(),
                )
            ]
            return ParsedDocumentPayload(
                metadata=metadata,
                pages=pages,
                tables=tables,
            )

    def _parse_office_document(self, path: Path) -> ParsedDocumentPayload:
        """Parse a Word/PowerPoint file into a single-page payload."""
        format_type = path.suffix.lower().lstrip(".") or "docx"
        logger.info("Parsing %s: %s", format_type.upper(), path.name)
        Office = require_office_oxide()
        with Office.open(str(path)) as doc:
            metadata = self._extract_office_metadata(path, doc, format_type)
            pages = [
                ExtractedPage(
                    page_number=1,
                    raw_text=doc.plain_text(),
                    markdown=doc.to_markdown(),
                )
            ]
            return ParsedDocumentPayload(
                metadata=metadata,
                pages=pages,
                tables=[],
            )

    def parse_file(
        self,
        file_path: Union[str, Path],
        detect_headings: bool = True,
    ) -> ParsedDocumentPayload:
        """
        Parse a local file and return a validated ParsedDocumentPayload.

        Parameters
        ----------
        file_path : str | Path
            Path to the local file (PDF, DOCX, XLSX, PPTX).
        detect_headings : bool
            When True, pdf_oxide attempts heading detection for Markdown.

        Returns
        -------
        ParsedDocumentPayload
            Validated payload with metadata, pages, and tables.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        ValueError
            If the file format is unsupported.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found at: {path}")

        suffix = path.suffix.lower().lstrip(".")

        if suffix == "pdf":
            return self._parse_pdf(path, detect_headings=detect_headings)
        if suffix in self._SPREADSHEET_FORMATS:
            return self._parse_spreadsheet(path)
        if suffix in self._OFFICE_TEXT_FORMATS:
            return self._parse_office_document(path)

        raise ValueError(
            f"Unsupported file format '.{suffix}'. "
            "Supported: pdf, docx, xlsx, pptx, xls, ppt, xlsb, ods, doc."
        )

    # ── Office IR table extraction ─────────────────────────────

    @staticmethod
    def _tables_from_office_ir(
        doc: OfficeDoc, format_type: str
    ) -> List[ExtractedTable]:
        """
        Best-effort table extraction from office_oxide IR (JSON string in
        office-oxide >= 0.1.10). Tables live uniformly under
        ``sections[].elements`` for every office format; the first row of
        each table is treated as the header row.
        """
        tables: List[ExtractedTable] = []
        try:
            ir = json.loads(doc.to_ir_json())
        except Exception:
            return tables
        if not isinstance(ir, dict):
            return tables

        for idx, (sheet_title, rows) in enumerate(office_ir_tables(ir)):
            tables.append(
                ExtractedTable(
                    name=f"{sheet_title}_table_{idx}",
                    headers=rows[0],
                    rows=rows[1:],
                )
            )

        return tables


    # ── HTML page extraction (unified with meta-oxide) ────────

    @staticmethod
    def parse_html(
        markdown: str,
        links: List[tuple],
        html_metadata: Dict[str, Any],
        url: str,
        max_links: int = 20,
        tables: Optional[List[ExtractedTable]] = None,
    ) -> ParsedDocumentPayload:
        """
        Convert a fetched HTML page (markdown + links + metadata) into
        a validated ParsedDocumentPayload.

        This unifies the web-fetching pipeline with the structured
        document pipeline, so that HTML pages and PDF/DOCX files
        both produce the same ParsedDocumentPayload output.

        Parameters
        ----------
        markdown : str
            Markdown content extracted from the page.
        links : list[tuple(str, str)]
            Follow-up links found on the page as (url, anchor_text)
            pairs, as produced by the Rust core's link extractor.
        html_metadata : dict
            Raw output from meta_oxide.extract_all().
        url : str
            The source URL.
        max_links : int
            Maximum links to include in the payload.
        tables : list[ExtractedTable], optional
            Tables pre-extracted from the raw HTML (Tier 3.11); attached
            to both the payload and the single page, like the
            PDF/Office paths do.

        Returns
        -------
        ParsedDocumentPayload
            Validated payload with metadata, pages, and links.
        """
        from gossamer.meta_extractor import merge_into_document_metadata

        # Build base metadata from URL
        from urllib.parse import urlparse
        parsed_url = urlparse(url)
        slug = parsed_url.path.strip("/") or parsed_url.netloc
        if not slug:
            slug = url

        base_meta: Dict[str, Any] = {
            "file_name": slug,
            "format": "html",
            "canonical": url,
        }

        # Merge HTML metadata into base
        enriched = merge_into_document_metadata(html_metadata, base_meta)

        # Build DocumentMetadata from enriched dict
        # Filter to only valid DocumentMetadata fields
        dm_fields = set(DocumentMetadata.model_fields.keys())
        dm_data = {k: v for k, v in enriched.items() if k in dm_fields}

        # Ensure required fields
        dm_data.setdefault("file_name", slug)
        dm_data.setdefault("format", "html")

        try:
            metadata = DocumentMetadata(**dm_data)
        except Exception as e:
            logger.warning("Failed to build DocumentMetadata: %s — using fallback", e)
            metadata = DocumentMetadata(file_name=slug, format="html")

        # Tier 3.11: attach extracted tables (PDF/Office parity).
        tables = list(tables) if tables else []

        # Wrap markdown as a single page
        page = ExtractedPage(
            page_number=1,
            raw_text=markdown,  # markdown IS the text content for HTML
            markdown=markdown,
            tables=tables,
        )

        # C5: the tool description promises links in this payload —
        # actually populate them (deduped, titled, typed, capped).
        candidates = build_follow_up_candidates(links, max_links=max_links)

        return ParsedDocumentPayload(
            metadata=metadata,
            pages=[page],
            tables=tables,
            links=candidates,
        )

