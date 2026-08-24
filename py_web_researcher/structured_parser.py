"""
Structured document extraction with Pydantic v2 validation.

Uses pdf_oxide and office_oxide to extract layout-aware content,
tables, and metadata from PDF / DOCX / XLSX / PPTX files.
All outputs are validated through Pydantic v2 schemas.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from pdf_oxide import PdfDocument
from office_oxide import Document as OfficeDoc

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# 1. Pydantic v2 Structural Schemas
# ────────────────────────────────────────────────────────────────

class DocumentMetadata(BaseModel):
    """
    Normalised metadata for documents and web pages.

    For files: populated from PDF XMP / Office property streams.
    For web pages: enriched with HTML metadata (OG, Twitter, JSON-LD, …)
    via meta-oxide.
    """

    file_name: str = Field("", description="Original file name or page URL slug.")
    file_size_bytes: int = Field(0, description="File size in bytes (0 for web pages).")
    format: str = Field("", description="Detected format (pdf, docx, html, …).")
    title: Optional[str] = Field(None, description="Document or page title.")
    author: Optional[str] = Field(None, description="Author or creator.")
    description: Optional[str] = Field(None, description="Page or document description.")
    created_at: Optional[datetime] = Field(None, description="UTC creation timestamp.")
    modified_at: Optional[datetime] = Field(None, description="UTC modification timestamp.")
    page_count: int = Field(default=1, description="Number of pages or sheets.")

    # ── HTML / web-page metadata (populated by meta-oxide) ──────
    canonical: Optional[str] = Field(None, description="Canonical URL.")
    language: Optional[str] = Field(None, description="Page language (e.g. 'en').")
    keywords: Optional[List[str]] = Field(None, description="Meta keywords.")
    robots: Optional[str] = Field(None, description="Robots directive.")

    # Open Graph
    og_title: Optional[str] = Field(None, description="og:title.")
    og_type: Optional[str] = Field(None, description="og:type (e.g. 'article').")
    og_image: Optional[str] = Field(None, description="og:image URL.")
    og_images: Optional[List[Dict[str, Any]]] = Field(None, description="All og:image entries.")
    og_description: Optional[str] = Field(None, description="og:description.")
    og_site_name: Optional[str] = Field(None, description="og:site_name.")
    og_url: Optional[str] = Field(None, description="og:url.")

    # Twitter Cards
    twitter_card: Optional[str] = Field(None, description="twitter:card type.")
    twitter_title: Optional[str] = Field(None, description="twitter:title.")
    twitter_description: Optional[str] = Field(None, description="twitter:description.")
    twitter_image: Optional[str] = Field(None, description="twitter:image URL.")
    twitter_site: Optional[str] = Field(None, description="twitter:site handle.")

    # Structured data
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
        # Try to get title/author from the IR JSON tree
        ir: Dict[str, Any] = {}
        try:
            ir = doc.to_ir_json()
        except Exception:
            pass  # to_ir_json may not be available for all formats

        title = ir.get("title") or ir.get("properties", {}).get("title")
        author = ir.get("author") or ir.get("properties", {}).get("author")

        # Page count: for PPTX = slide count, XLSX = sheet count, DOCX = 1
        page_count = 1
        if format_type in ("xlsx", "xls"):
            # Count sheets from IR if available
            sheets = ir.get("sheets")
            if isinstance(sheets, list):
                page_count = len(sheets)
        elif format_type in ("pptx", "ppt"):
            slides = ir.get("slides")
            if isinstance(slides, list):
                page_count = len(slides)

        return DocumentMetadata(
            file_name=path.name,
            file_size_bytes=path.stat().st_size,
            format=format_type,
            title=title if isinstance(title, str) else None,
            author=author if isinstance(author, str) else None,
            page_count=page_count,
            extra_meta={
                k: str(v)
                for k, v in (ir.get("properties") or {}).items()
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

        # ── PDF path ───────────────────────────────────────────
        if suffix == "pdf":
            logger.info("Parsing PDF: %s", path.name)
            with PdfDocument(str(path)) as doc:
                metadata = self._extract_pdf_metadata(path, doc)
                pages: List[ExtractedPage] = []

                for page in doc:
                    page_num = page.index + 1  # 1-based

                    # Extract text
                    raw_text = page.text

                    # Extract Markdown
                    markdown = page.markdown(detect_headings=detect_headings)

                    # Extract tables
                    tables = self._tables_from_pdf_page(page)

                    pages.append(
                        ExtractedPage(
                            page_number=page_num,
                            raw_text=raw_text,
                            markdown=markdown,
                            tables=tables,
                        )
                    )

                # Flatten per-page tables into top-level list too
                all_tables: List[ExtractedTable] = []
                for p in pages:
                    all_tables.extend(p.tables)

                return ParsedDocumentPayload(
                    metadata=metadata,
                    pages=pages,
                    tables=all_tables,
                )

        # ── Excel path ─────────────────────────────────────────
        elif suffix in ("xlsx", "xls", "xlsb", "ods"):
            logger.info("Parsing Excel: %s", path.name)
            with OfficeDoc.open(str(path)) as doc:
                metadata = self._extract_office_metadata(
                    path, doc, suffix or "xlsx"
                )

                # office_oxide gives us Markdown and plain text;
                # for tabular data we rely on the IR JSON
                md = doc.to_markdown()
                raw_text = doc.plain_text()

                # Try to extract sheet-level tables from IR
                tables = self._tables_from_office_ir(doc, suffix or "xlsx")

                pages = [
                    ExtractedPage(
                        page_number=1,
                        raw_text=raw_text,
                        markdown=md,
                    )
                ]

                return ParsedDocumentPayload(
                    metadata=metadata,
                    pages=pages,
                    tables=tables,
                )

        # ── Word path ──────────────────────────────────────────
        elif suffix in ("docx", "doc"):
            logger.info("Parsing Word: %s", path.name)
            with OfficeDoc.open(str(path)) as doc:
                metadata = self._extract_office_metadata(
                    path, doc, suffix or "docx"
                )

                md = doc.to_markdown()
                raw_text = doc.plain_text()

                pages = [
                    ExtractedPage(
                        page_number=1,
                        raw_text=raw_text,
                        markdown=md,
                    )
                ]

                return ParsedDocumentPayload(
                    metadata=metadata,
                    pages=pages,
                    tables=[],
                )

        # ── PowerPoint path ────────────────────────────────────
        elif suffix in ("pptx", "ppt"):
            logger.info("Parsing PowerPoint: %s", path.name)
            with OfficeDoc.open(str(path)) as doc:
                metadata = self._extract_office_metadata(
                    path, doc, suffix or "pptx"
                )

                md = doc.to_markdown()
                raw_text = doc.plain_text()

                pages = [
                    ExtractedPage(
                        page_number=1,
                        raw_text=raw_text,
                        markdown=md,
                    )
                ]

                return ParsedDocumentPayload(
                    metadata=metadata,
                    pages=pages,
                    tables=[],
                )

        else:
            raise ValueError(
                f"Unsupported file format '.{suffix}'. "
                "Supported: pdf, docx, xlsx, pptx, xls, ppt, xlsb, ods, doc."
            )

    # ── Office IR table extraction ─────────────────────────────

    @staticmethod
    def _sheet_to_table(sheet: Any) -> Optional[ExtractedTable]:
        """Convert one XLSX sheet IR node into an ExtractedTable.

        Returns None for sheets that are structurally unusable. Valid sheets
        always yield a table entry, even when no rows were found.
        """
        if not isinstance(sheet, dict):
            return None
        rows = sheet.get("rows", sheet.get("data", []))
        if not isinstance(rows, list):
            return None

        headers: List[str] = []
        data_rows: List[List[Any]] = []

        for row in rows:
            if not isinstance(row, (list, dict)):
                continue
            if isinstance(row, list):
                cells = [str(c) if c else "" for c in row]
            else:
                cells = [
                    str(row.get(f"col_{i}", row.get(str(i), ""))) or ""
                    for i in range(
                        max(
                            (
                                int(k.replace("col_", ""))
                                for k in row
                                if k.startswith("col_")
                            ),
                            default=0,
                        )
                        + 1
                    )
                ]
            if not cells:
                continue
            if not headers:
                headers = cells
            else:
                data_rows.append(cells)

        return ExtractedTable(
            name=sheet.get("name", "unnamed"),
            headers=headers,
            rows=data_rows,
        )

    @staticmethod
    def _tables_from_office_ir(
        doc: OfficeDoc, format_type: str
    ) -> List[ExtractedTable]:
        """
        Best-effort table extraction from office_oxide IR JSON.

        The IR structure varies by format. For XLSX we look for
        sheet arrays; for DOCX/PPTX we look for table nodes.
        """
        tables: List[ExtractedTable] = []

        try:
            ir = doc.to_ir_json()
        except Exception:
            return tables

        if not isinstance(ir, dict):
            return tables

        # ── Excel: look for sheets with cell grids ─────────────
        if format_type in ("xlsx", "xls"):
            sheets = ir.get("sheets", [])
            if isinstance(sheets, list):
                tables.extend(
                    sheet_table
                    for sheet_table in map(StructuredOxideParser._sheet_to_table, sheets)
                    if sheet_table is not None
                )

        # ── DOCX / PPTX: look for table nodes ─────────────────
        else:
            _walk_tables(ir, tables, f"{format_type}_table")

        return tables


    # ── HTML page extraction (unified with meta-oxide) ────────

    @staticmethod
    def parse_html(
        markdown: str,
        links: List[str],
        html_metadata: Dict[str, Any],
        url: str,
        max_links: int = 20,
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
        links : list[str]
            Follow-up links found on the page.
        html_metadata : dict
            Raw output from meta_oxide.extract_all().
        url : str
            The source URL.
        max_links : int
            Maximum links to include in the payload.

        Returns
        -------
        ParsedDocumentPayload
            Validated payload with metadata, pages, and links.
        """
        from py_web_researcher.meta_extractor import merge_into_document_metadata

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

        # Wrap markdown as a single page
        page = ExtractedPage(
            page_number=1,
            raw_text=markdown,  # markdown IS the text content for HTML
            markdown=markdown,
        )

        return ParsedDocumentPayload(
            metadata=metadata,
            pages=[page],
            tables=[],
        )


def _cells_from_row(row: Any) -> List[str]:
    """Convert one IR table row (dict or list) into a list of cell strings.

    Returns an empty list for rows that carry no usable cells.
    """
    if isinstance(row, dict):
        return [
            str(c.get("text", c.get("value", "")))
            for c in row.get("cells", row.get("children", []))
            if isinstance(c, dict)
        ]
    if isinstance(row, list):
        return [str(c) if c else "" for c in row]
    return []


def _table_from_node(node: Any) -> Optional[ExtractedTable]:
    """Build an ExtractedTable from an IR dict node that looks like a table.

    Returns None when the node is not table-like or carries no usable rows.
    The caller is responsible for assigning the final table name.
    """
    if node.get("type") not in ("table",) and "rows" not in node:
        return None
    rows = node.get("rows", [])
    if not isinstance(rows, list):
        return None

    headers: List[str] = []
    data_rows: List[List[str]] = []
    for row in rows:
        cells = _cells_from_row(row)
        if not cells:
            continue
        if not headers:
            headers = cells
        else:
            data_rows.append(cells)

    if not headers and not data_rows:
        return None
    return ExtractedTable(name="", headers=headers, rows=data_rows)


def _walk_tables(
    node: Any,
    tables: List[ExtractedTable],
    prefix: str,
    idx_ref: Optional[List[int]] = None,
) -> None:
    """Recursively walk an IR dict/list looking for table structures."""
    if idx_ref is None:
        # Fresh counter per top-level call; numbering restarts per document.
        idx_ref = [0]
    if isinstance(node, dict):
        table = _table_from_node(node)
        if table is not None:
            idx_ref[0] += 1
            table.name = f"{prefix}_{idx_ref[0]}"
            tables.append(table)
        # Recurse into children
        for child in node.values():
            _walk_tables(child, tables, prefix, idx_ref)
    elif isinstance(node, list):
        for item in node:
            _walk_tables(item, tables, prefix, idx_ref)
