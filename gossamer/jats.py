"""JATS-first scholarly extraction route (bobine proposal §5).

DOI → JATS XML (EuropePMC / PMC OA / PLOS) → ``bobine.convert_jats`` →
markdown. All network goes through the toolbox fetch plumbing
(SSRF guard, robots, rate limit, byte cap); this module holds only the
pure helpers (DOI handling, response parsing, tarball picking, bobine
loading, byte conversion) so they stay unit-testable offline.

Handoff: ``docs/handoff_gossamer_jats.md``.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

BOBINE_FLOOR_VERSION = "0.7.0"
BOBINE_HINT = (
    "JATS extraction requires 'bobine>=0.7.0' — "
    'install it with: pip install "bobine>=0.7.0"'
)

EUROPEPMC_SEARCH_URL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
)
EUROPEPMC_FULLTEXT_TMPL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{id}/fullTextXML"
)
PMC_OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
PLOS_FILE_TMPL = (
    "https://journals.plos.org/{journal}/article/file?id={doi}&type=manuscript"
)

# Error text when pages= meets a JATS-resolvable source (§3.5).
PAGES_JATS_ERROR = (
    "pages={pages!r} is not supported for structured-source (JATS) extraction; "
    "re-run without pages or with --no-prefer-jats"
)

_CITATION_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


def require_bobine():
    """Return the ``bobine`` module, or raise with an install hint."""
    try:
        import bobine  # type: ignore
    except ImportError as exc:
        raise ImportError(BOBINE_HINT) from exc
    return bobine


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
    pmcid = raw_id if raw_id.upper().startswith("PMC") else "PMC" + raw_id
    source = str(chosen.get("source") or "PMC").strip() or "PMC"
    # The numeric id feeds the fullTextXML path; the PMCID feeds PMC OA.
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


def parse_oa_tgz_link(xml_bytes: bytes) -> Optional[str]:
    """``<link format="tgz" href="...">`` from a PMC OA ``oa.fcgi`` reply."""
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, UnicodeDecodeError, ValueError):
        return None

    def _local(tag) -> str:
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    for el in root.iter():
        if _local(el.tag) != "link":
            continue
        if (el.get("format") or "").lower() != "tgz":
            continue
        href = el.get("href")
        if href and href.strip():
            return href.strip()
    return None


def pick_nxml(names: list[str], pmcid: Optional[str] = None) -> Optional[str]:
    """The ``*.nxml`` member to convert (exactly one per package).

    Preference: basename matching the PMCID, else the single ``*.nxml``,
    else the first sorted ``*.nxml``.
    """
    nxml = [n for n in names if n.lower().endswith(".nxml")]
    if not nxml:
        return None
    if len(nxml) == 1:
        return nxml[0]
    if pmcid:
        base = pmcid.upper()
        for name in sorted(nxml):
            stem = Path(name).stem.upper()
            if stem == base or stem.startswith(base):
                return name
    return sorted(nxml)[0]


def extract_nxml_from_tgz(tgz_bytes: bytes, pmcid: Optional[str] = None) -> tuple[str, bytes]:
    """Return ``(member_name, xml_bytes)`` from a PMC OA tarball.

    Guards: gzip only, member cap, no absolute / ``..`` paths, size cap
    per member (32 MiB).
    """
    names: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(tgz_bytes), mode="r:gz") as tf:
            members = tf.getmembers()
            if len(members) > 512:
                raise ValueError("PMC OA tarball lists too many members")
            for m in members:
                name = m.name or ""
                if not name or name.startswith("/") or ".." in Path(name).parts:
                    continue
                names.append(name)
            target = pick_nxml(names, pmcid)
            if target is None:
                raise ValueError("PMC OA tarball contains no .nxml file")
            member = tf.getmember(target)
            if member.size is not None and member.size > 32 * 1024 * 1024:
                raise ValueError("PMC OA .nxml member too large")
            fh = tf.extractfile(member)
            if fh is None:
                raise ValueError(f"cannot read {target} from PMC OA tarball")
            return target, fh.read()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ValueError(f"invalid PMC OA tarball: {exc}") from exc


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
    """Convert JATS XML bytes via bobine; return markdown + counts.

    Returns ``{"markdown", "title", "n_formulas", "n_figures",
    "n_tables"}``. Raises ``RuntimeError``/``ImportError`` from bobine
    with stable prefixes intact (callers map ``not a JATS article`` to
    fall-through, everything else to a JSON error).
    """
    bobine = require_bobine()
    suffix = Path(filename).suffix.lower() or ".xml"
    if suffix not in (".xml", ".jats", ".nxml"):
        suffix = ".xml"
    with tempfile.TemporaryDirectory(prefix="gossamer-jats-") as work_dir:
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, prefix="gossamer-jats-src-"
        ) as tmp:
            tmp.write(xml_bytes)
            tmp_path = tmp.name
        try:
            doc = bobine.convert_jats(tmp_path, work_dir)
            md_path = str(getattr(doc, "md_path", ""))
            markdown = ""
            if md_path:
                try:
                    markdown = Path(md_path).read_text(encoding="utf-8")
                except OSError:
                    markdown = ""
            return {
                "markdown": markdown,
                "title": str(getattr(doc, "title", "") or ""),
                "n_formulas": int(getattr(doc, "n_formulas", 0) or 0),
                "n_figures": int(getattr(doc, "n_figures", 0) or 0),
                "n_tables": int(getattr(doc, "n_tables", 0) or 0),
            }
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass


def jats_cache_stem(source: str) -> str:
    """Filesystem-safe stem for stored JATS files (DOI-aware)."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", source).strip("_")
    return cleaned[:120] or "document"
