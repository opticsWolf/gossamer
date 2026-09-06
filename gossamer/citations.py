"""Citation reconstruction and export (Plan workstream 1).

Reconstructs bibliographic records from the result dicts returned by the
scholarly :class:`~gossamer.research_providers.ResourceAdapter`
adapters and renders them as BibTeX, CSL-JSON, APA or MLA.

The adapters already return the fields a citation needs -- ``doi``, ``id``,
``title``, ``authors``, ``published`` and the full provider payload in
``raw`` -- so :func:`record_from_result` builds a record with **no network
access**. :func:`enrich_with_doi` can optionally make one canonical DOI
lookup (Crossref/OpenAlex) to fill in a missing ``venue`` / ``abstract``;
the adapter is injectable so tests stay offline.

Formatters:

  * :func:`to_bibtex`  -- ``@article`` / ``@inproceedings``.
  * :func:`to_csl_json`-- the canonical machine hub (APA/MLA derive from it).
  * :func:`to_apa` / :func:`to_mla` -- style templates.

APA (7th) renders through **citeproc-py** with the bundled ``apa.csl`` style
for style-faithful output. MLA (9th) has no style shipped with citeproc-py,
so it uses the pure-Python approximation below. When citeproc-py is not
installed, APA also falls back to that approximation. The approximations
cover the common journal-article shape and leave unusual fields to
best-effort; that limitation is documented in the tool description so
callers don't expect style-perfect output.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from gossamer._core import (
    BibliographicRecord,
    cite_abstract_from_raw as _rs_abstract_from_raw,
    cite_apa_approx as _rs_apa,
    cite_bibtex as _rs_bibtex,
    cite_csl_json as _rs_csl,
    cite_mla_approx as _rs_mla,
    cite_venue_from_raw as _rs_venue_from_raw,
    citation_record_from_json as _rs_record,
)
from gossamer.dedup import dedupe as _shared_dedupe

__all__ = [
    "BibliographicRecord",
    "record_from_result",
    "enrich_with_doi",
    "dedupe_records",
    "to_bibtex",
    "to_csl_json",
    "to_apa",
    "to_mla",
    "format_citations",
]


# `BibliographicRecord` is the PyO3 class from `src/cite.rs`
# (re-exported above with identical constructor/attributes).


def _venue_from_raw(raw) -> Optional[str]:
    """Best-effort venue extraction from a provider's raw JSON payload."""
    # Implemented in Rust (src/cite.rs); kept as a thin wrapper for the
    # enrichment path, which works hit-by-hit over adapter dicts.
    if not raw:
        return None
    return _rs_venue_from_raw(raw) if isinstance(raw, str) else None


def _abstract_from_raw(raw) -> Optional[str]:
    """Best-effort abstract extraction from a provider's raw JSON payload."""
    if not raw:
        return None
    return _rs_abstract_from_raw(raw) if isinstance(raw, str) else None


def record_from_result(result: Dict[str, Any]) -> BibliographicRecord:
    """Build a :class:`BibliographicRecord` from one adapter result dict.

    Handles both the unified result shape (``authors`` a ", "-joined string,
    ``published`` a ``YYYY-MM-DD`` string) and Crossref's native shape
    (``authors`` a list of ``{family, given}`` dicts, ``published`` a
    ``{date-parts}`` dict). Never raises on a partial/foreign-shaped dict;
    missing fields stay ``None``/``[]``. ``venue`` / ``abstract`` are filled
    from ``raw`` when present, otherwise left for :func:`enrich_with_doi`.
    """
    if not isinstance(result, dict):
        return BibliographicRecord()
    # Record assembly in Rust (src/cite.rs) over a JSON snapshot; `extra`
    # keeps the live `raw` object (no serialization round-trip).
    # NOTE: error paths (non-object `raw` payloads, malformed date-parts)
    # propagate exactly as before — see tests/test_rust_parity_citations.py.
    rec = _rs_record(json.dumps(result, default=str))
    rec.extra = result.get("raw") or {}
    return rec


def enrich_with_doi(
    record: BibliographicRecord,
    adapter: Optional[Any] = None,
) -> BibliographicRecord:
    """Fill missing ``venue`` / ``abstract`` via one canonical DOI lookup.

    Only runs when ``record.doi`` is set and *adapter* is provided. The
    adapter must implement ``fetch(doi) -> list[dict]`` returning the same
    result-dict shape as the scholarly adapters; the first hit's venue/
    abstract are merged in. Returns the same (mutated) record.

    *adapter* is injectable so tests pass a fake and stay offline.
    """
    if not record.doi or adapter is None:
        return record
    try:
        hits = adapter.fetch(record.doi) or []
    except Exception:  # noqa: BLE001 -- enrichment is best-effort, never fatal
        return record
    for h in hits:
        if not isinstance(h, dict):
            continue
        if not record.venue:
            v = h.get("venue") or _venue_from_raw(h.get("raw"))
            if v:
                record.venue = v
        if not record.abstract:
            ab = h.get("abstract")
            if isinstance(ab, dict):
                ab = ab.get("#text") or ab.get("a") or ""
            if not ab:
                ab = _abstract_from_raw(h.get("raw"))
            if ab:
                record.abstract = ab
        if record.venue and record.abstract:
            break
    return record


def dedupe_records(
    records: List[BibliographicRecord],
) -> List[BibliographicRecord]:
    """Collapse records sharing a DOI, then a normalised URL. Returns kept.

    Delegates to the shared :func:`gossamer.dedup.dedupe` so
    DOI/URL identity lives in one place (Workstream 2); the citation order
    (DOI first, then URL) is preserved via ``by=("doi", "url")``.
    """
    kept, _ = _shared_dedupe(records, by=("doi", "url"))
    return kept


# ────────────────────────────────────────────────────────────────
# Formatters
# ────────────────────────────────────────────────────────────────

def _author_last_first(author: str):
    """Return ``(family, given)`` from an author string (citeproc path).

    A comma means ``"Last, First"``; otherwise the final token is the
    family name. (The Rust port has its own copy; this stays for the
    citeproc-py branch so that path never changes.)
    """
    if "," in author:
        parts = [p.strip() for p in author.split(",", 1)]
        return parts[0], parts[1] if len(parts) > 1 else ""
    parts = author.split(" ")
    if len(parts) > 1:
        return parts[-1], " ".join(parts[:-1])
    return author, ""


def _csl_item_for_citeproc(record: BibliographicRecord, item_id: str) -> Dict[str, Any]:
    """Build one CSL item for citeproc-py's ``CiteProcJSON`` parser.

    Differs from spec CSL-JSON output (see :func:`to_csl_json`):

      * identifier key is lowercase ``"id"`` -- citeproc-py reads the
        lowercase key (uppercase ``"ID"`` raises ``UnboundLocalError``);
      * ``container-title`` is a bare string -- citeproc-py's plain
        formatter does a bare ``str()`` on list-valued fields and would
        otherwise emit a Python list repr such as ``['Proceedings']``.
    """
    author_list = []
    for a in record.authors:
        fam, giv = _author_last_first(a)
        author_list.append({"family": fam, "given": giv} if giv else {"family": fam})
    item: Dict[str, Any] = {
        "id": item_id,
        "type": record.kind or "article-journal",
        "title": record.title or "",
    }
    if record.doi:
        item["DOI"] = record.doi
    if record.url:
        item["URL"] = record.url
    if author_list:
        item["author"] = author_list
    if record.venue:
        item["container-title"] = record.venue
    if record.publisher:
        item["publisher"] = record.publisher
    if record.year:
        issued = {"date-parts": [[int(record.year)]]}
        if record.month:
            issued["date-parts"][0].append(int(record.month[:2]))
        item["issued"] = issued
    if record.abstract:
        item["abstract"] = re.sub(r"\s+", " ", record.abstract).strip()
    return item


def _render_with_citeproc(style_name: str, records: List[BibliographicRecord]) -> str:
    """Render *records* with citeproc-py using the named CSL style.

    Returns the bibliography as a single newline-joined string. Raises when
    citeproc-py is not installed or the style file is unavailable -- callers
    fall back to the pure-Python approximation so :func:`format_citations`
    never breaks.
    """
    from citeproc import Citation, CitationItem, CitationStylesBibliography, CitationStylesStyle
    from citeproc.formatter import plain
    from citeproc.source.json import CiteProcJSON

    items = [_csl_item_for_citeproc(r, str(i)) for i, r in enumerate(records)]
    style = CitationStylesStyle(style_name)  # StyleNotFoundError if unavailable
    bib = CitationStylesBibliography(style, CiteProcJSON(items), formatter=plain)
    for item in items:
        bib.register(Citation([CitationItem(item["id"])]))
    # The plain formatter yields one MixedString per entry; str() flattens
    # each entry's fragments into clean text.
    return "\n".join(str(entry) for entry in bib.bibliography())


def to_apa(records: List[BibliographicRecord]) -> str:
    """Render records as APA (7th).

    Uses citeproc-py with the bundled ``apa.csl`` style for style-faithful
    output. Falls back to the Rust approximation when citeproc-py is not
    installed or the style file is unavailable.
    """
    records = dedupe_records(records)
    try:
        return _render_with_citeproc("apa", records)
    except Exception:  # noqa: BLE001 -- citeproc is optional; never break export
        return _rs_apa(list(records))


def to_mla(records: List[BibliographicRecord]) -> str:
    """Render records as MLA (9th).

    Falls back to the Rust approximation -- no ``mla`` style ships with
    citeproc-py-styles, so MLA is always rendered by the approximation.
    """
    records = dedupe_records(records)
    try:
        return _render_with_citeproc("mla", records)
    except Exception:  # noqa: BLE001 -- citeproc is optional; never break export
        return _rs_mla(list(records))


def to_bibtex(records: List[BibliographicRecord]) -> str:
    """Render records as a BibTeX fragment (``@article`` default)."""
    # Implemented in Rust (src/cite.rs).
    return _rs_bibtex(list(records or []))


def to_csl_json(records: List[BibliographicRecord]) -> str:
    """Render records as a CSL-JSON array (the machine interchange hub)."""
    # Implemented in Rust (src/cite.rs).
    return _rs_csl(list(records or []))


# Private aliases kept for the approximation entry points (used by tests
# and referenced in older docs): they are the Rust approximations.
_apa_approx = _rs_apa
_mla_approx = _rs_mla


_FORMATTERS = {
    "bibtex": to_bibtex,
    "csl-json": to_csl_json,
    "apa": to_apa,
    "mla": to_mla,
}
FORMAT_STYLES = tuple(_FORMATTERS.keys())


def format_citations(
    results,
    style: str = "bibtex",
    enrich: bool = False,
    dedupe: bool = True,
    adapter: Optional[Any] = None,
) -> str:
    """Top-level entry point used by the toolbox tool.

    Parameters
    ----------
    results:
        Iterable of adapter result dicts **or** bare DOI / URL strings.
    style:
        One of ``bibtex`` / ``csl-json`` / ``apa`` / ``mla``.
    enrich:
        When true, run :func:`enrich_with_doi` per unique DOI (needs
        *adapter*; enrichment is best-effort and never raises).
    dedupe:
        Collapse records sharing a DOI or URL before formatting.
    adapter:
        Optional ``fetch(doi) -> list[dict]`` adapter for enrichment.

    Returns the formatted string (empty when there is nothing to cite).
    """
    style = (style or "bibtex").lower()
    if style not in _FORMATTERS:
        raise ValueError(
            f"unknown citation style {style!r}; expected one of {FORMAT_STYLES}"
        )
    records: List[BibliographicRecord] = []
    for item in results or []:
        if isinstance(item, dict):
            rec = record_from_result(item)
        elif isinstance(item, str):
            s = item.strip()
            if "/" in s and "http" not in s:
                rec = BibliographicRecord(doi=s, url=f"https://doi.org/{s}")
            else:
                rec = BibliographicRecord(url=s)
        else:
            continue
        if enrich:
            rec = enrich_with_doi(rec, adapter)
        records.append(rec)
    if dedupe:
        records = dedupe_records(records)
    return _FORMATTERS[style](records)
