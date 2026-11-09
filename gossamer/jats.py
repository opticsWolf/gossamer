"""JATS-first scholarly extraction route (bobine proposal §5).

DOI → JATS XML (EuropePMC, with a PMC/{pmcid} retry, then PLOS) → in-house ``_core``
JATS kernel (ported from bobine 0.8.0 ``jats.rs``/``mml.rs``) →
markdown. All network goes through the toolbox fetch plumbing
(SSRF guard, robots, rate limit, byte cap); this module holds only the
pure helpers (DOI handling, response parsing, tarball picking, byte
conversion) so they stay unit-testable offline.

Handoff: ``docs/handoff_gossamer_jats.md``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from gossamer._core import jats_convert_jats

logger = logging.getLogger(__name__)

EUROPEPMC_SEARCH_URL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
)
EUROPEPMC_FULLTEXT_TMPL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{id}/fullTextXML"
)
PLOS_FILE_TMPL = (
    "https://journals.plos.org/{journal}/article/file?id={doi}&type=manuscript"
)

# Error text when pages= meets a JATS-resolvable source (§3.5).
PAGES_JATS_ERROR = (
    "pages={pages!r} is not supported for structured-source (JATS) extraction; "
    "re-run without pages or with --no-prefer-jats"
)

_CITATION_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


def extract_doi_candidate(text: str) -> Optional[str]:
    """First DOI-like substring in *text*, or None."""
    if not isinstance(text, str):
        return None
    m = _CITATION_DOI_RE.search(text)
    if not m:
        return None
    # Strip trailing punctuation that rides along in prose/URLs.
    return m.group(0).rstrip(").,;\"'<>")


def normalize_doi_or_none(value: str) -> Optional[str]:
    """``open_access.normalize_doi`` or None (never raises)."""
    try:
        from gossamer.open_access import normalize_doi
    except Exception:  # noqa: BLE001 - import-time safety
        return None
    try:
        return normalize_doi(value)
    except (ValueError, TypeError, AttributeError):
        return None


def doi_from_source_string(source: str) -> Optional[str]:
    """Best-effort DOI for a URL / bare-DOI / doi: string (no network).

    1. ``normalize_doi`` (bare, ``doi:``, ``doi.org`` URL).
    2. First DOI-like substring (covers publisher landing URLs such as
       PLOS ``?id=10.1371/...`` — query/fragment params riding along in
       the URL are cut before normalizing).
    """
    doi = normalize_doi_or_none(source)
    if doi:
        return doi
    candidate = extract_doi_candidate(source)
    if candidate:
        candidate = re.split(r"[?#&\s]", candidate, maxsplit=1)[0].rstrip("().,;\"'<>")
        return normalize_doi_or_none(candidate)
    return None


def doi_from_pdf_path(path: str | Path) -> tuple[Optional[str], Optional[str]]:
    """DOI from a local PDF without touching ``DocumentExtractor``.

    Reads the text layer via ``pdf-oxide`` directly (plus a latin-1 raw
    scan) and returns ``(doi, error)``. No ``extract_document`` call, so
    the JATS route can use this without recursing.
    """
    from gossamer.citations import find_doi_in_text

    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return None, f"cannot read PDF {path}: {exc}"
    texts: list[str] = []
    try:
        from gossamer.structured_parser import require_pdf_oxide

        texts.append(require_pdf_oxide().from_bytes(data).to_markdown_all())
    except Exception:  # noqa: BLE001 - fall back to raw-byte scan
        pass
    try:
        texts.append(data.decode("latin-1", errors="ignore"))
    except Exception:  # noqa: BLE001 - unreadable bytes mean no DOI
        pass
    for text in texts:
        try:
            doi = find_doi_in_text(text)
        except Exception:  # noqa: BLE001 - defensive
            continue
        if doi:
            normalized = normalize_doi_or_none(doi)
            return (normalized or doi), None
    return None, f"no DOI found in PDF {path}; pass a DOI explicitly"


def parse_europepmc_search(payload: dict) -> Optional[dict]:
    """Pick the PMC record from a EuropePMC search payload.

    Returns ``{"source": ..., "id": ..., "pmcid": ...}`` or None when no
    PMC full-text hit exists. ``id`` may arrive bare-numeric or already
    ``PMC``-prefixed — both are accepted.
    """
    try:
        results = (payload.get("resultList") or {}).get("result") or []
    except AttributeError:
        return None
    if not isinstance(results, list):
        return None
    chosen = None
    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("source") == "PMC":
            chosen = item
            break
    if chosen is None:
        for item in results:
            if isinstance(item, dict) and item.get("inPMC") == "Y":
                chosen = item
                break
    if chosen is None:
        return None
    raw_id = str(chosen.get("id") or "").strip()
    if not raw_id:
        return None
    # Prefer the record's own PMCID: for MED-sourced rows the numeric id
    # is a PMID, and "PMC" + PMID fabricates a PMCID that does not
    # exist (Tier-1 MED 404 + Tier-1b phantom 404). Fall back to the
    # prefix construction only when the record carries no pmcid field.
    claimed = str(chosen.get("pmcid") or "").strip()
    pmcid = claimed if claimed.upper().startswith("PMC") else (
        raw_id if raw_id.upper().startswith("PMC") else "PMC" + raw_id
    )
    source = str(chosen.get("source") or "PMC").strip() or "PMC"
    # The numeric id feeds the source-scoped fullTextXML path; the PMCID
    # feeds the Tier-1b PMC/{pmcid} retry.
    numeric = raw_id[3:] if raw_id.upper().startswith("PMC") else raw_id
    return {"source": source, "id": numeric, "pmcid": pmcid}


def europepmc_fulltext_url(source: str, numeric_id: str) -> str:
    return EUROPEPMC_FULLTEXT_TMPL.format(source=source, id=numeric_id)


def is_xml_body(content_type: Optional[str], body: bytes) -> bool:
    """Content-Type contains xml and the body starts with ``<``."""
    if not body:
        return False
    stripped = body.lstrip()
    if not stripped.startswith(b"<"):
        return False
    return "xml" in (content_type or "").lower()


def plos_journal_from_url(url: str) -> Optional[str]:
    """Journal path segment from a ``journals.plos.org`` landing URL."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001 - malformed URL
        return None
    if "plos.org" not in (parsed.hostname or "").lower():
        return None
    parts = [p for p in parsed.path.split("/") if p]
    return parts[0] if parts else None


def convert_jats_bytes(xml_bytes: bytes, filename: str = "article.xml") -> dict:
    """Convert JATS XML bytes via the in-house ``_core`` kernel.

    Returns ``{"markdown", "title", "n_formulas", "n_figures",
    "n_tables"}``. Raises ``ValueError`` with stable prefixes intact
    (callers map ``not a JATS article`` to fall-through, everything
    else to a JSON error). Pure: no temp files, no I/O.
    """
    _ = filename  # reserved: resolver paths keep the call shape.
    try:
        xml_text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"ValueError: XML parse: {exc}") from exc
    try:
        out = json.loads(jats_convert_jats(xml_text))
    except ValueError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"ValueError: JATS conversion failed: {exc}") from exc
    return {
        "markdown": str(out.get("markdown", "") or ""),
        "title": str(out.get("title", "") or ""),
        "n_formulas": int(out.get("n_formulas", 0) or 0),
        "n_figures": int(out.get("n_figures", 0) or 0),
        "n_tables": int(out.get("n_tables", 0) or 0),
    }


def jats_cache_stem(source: str) -> str:
    """Filesystem-safe stem for stored JATS files (DOI-aware)."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", source).strip("_")
    return cleaned[:120] or "document"
