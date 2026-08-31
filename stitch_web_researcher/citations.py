"""Citation reconstruction and export (Plan workstream 1).

Reconstructs bibliographic records from the result dicts returned by the
scholarly :class:`~stitch_web_researcher.research_providers.ResourceAdapter`
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

APA (7th) and MLA (9th) formatters are **approximations**, not a full
CSL-STYLE processor: they cover the common journal-article shape and leave
unusual fields to best-effort. That limitation is documented in the tool
description so callers don't expect style-perfect output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


@dataclass
class BibliographicRecord:
    """A normalised bibliographic record.

    Missing fields are ``None`` (str) / ``[]`` (list); the mappers never
    raise on a partial result dict.
    """

    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)  # "Last, First" strings
    year: Optional[str] = None
    month: Optional[str] = None
    day: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    venue: Optional[str] = None          # journal / container title
    publisher: Optional[str] = None
    abstract: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)  # provider payload
    id: Optional[str] = None
    kind: Optional[str] = None


def _first(d: Dict[str, Any], *keys, default=None):
    """First non-empty value among *keys* in *d* (Crossref lists -> [0])."""
    for k in keys:
        if k in d:
            v = d[k]
            if isinstance(v, list):
                v = v[0] if v else ""
            if v:
                return v
    return default


def _normalize_authors(raw) -> List[str]:
    """Normalise the adapter ``authors`` value into ``"Last, First"`` strings.

    Handles every shape the scholarly adapters produce:
      * ``None`` / empty -> []
      * a ", "-joined string (Crossref family names, or OpenAlex display
        names) -> split on ", "
      * a list of ``{"family", "given"}`` dicts (Crossref native) -> one
        ``"family, given"`` string per author
      * a plain list of names -> str() each
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        out = []
        for a in raw:
            if isinstance(a, dict):
                fam = a.get("family") or a.get("name") or ""
                giv = a.get("given", "")
                out.append(f"{fam}, {giv}".strip() if giv else fam)
            else:
                s = str(a).strip()
                if s:
                    out.append(s)
        return out
    return [a.strip() for a in str(raw).split(",") if a.strip()]


def _extract_date(result: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Pull a ``(year, month, day)`` from either a date string or a Crossref
    ``{"date-parts": [[y, m, d]]}`` dict."""
    published = result.get("published") or result.get("publication_date")
    if isinstance(published, dict):
        parts = (published.get("date-parts") or [])[0] or []
        def _g(i):
            return str(parts[i]) if i < len(parts) and parts[i] else None
        return {"year": _g(0), "month": _g(1), "day": _g(2)}
    if not published:
        return {"year": None, "month": None, "day": None}
    m = re.match(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", str(published).strip())
    if not m:
        return {"year": None, "month": None, "day": None}
    return {"year": m.group(1), "month": m.group(2), "day": m.group(3)}


def _venue_from_raw(raw: Optional[str]) -> Optional[str]:
    """Best-effort venue extraction from a provider's raw JSON payload."""
    if not raw:
        return None
    try:
        r = json.loads(raw)
    except (TypeError, ValueError):
        return None
    msg = r.get("message", r)
    venue = _first(msg, "container_title", "journal_title", default="")
    if venue:
        return venue
    try:
        pl = msg.get("primary_location", {})
        jour = pl.get("journal", {}) if isinstance(pl, dict) else {}
        v = jour.get("display_name")
        if v:
            return v
    except (AttributeError, TypeError):
        pass
    return None


def _abstract_from_raw(raw: Optional[str]) -> Optional[str]:
    """Best-effort abstract extraction from a provider's raw JSON payload."""
    if not raw:
        return None
    try:
        r = json.loads(raw)
    except (TypeError, ValueError):
        return None
    msg = r.get("message", r)
    ab = msg.get("abstract")
    if isinstance(ab, str) and ab.strip():
        return ab.strip()
    if isinstance(ab, dict):
        t = ab.get("#text") or ab.get("a")
        if isinstance(t, str) and t.strip():
            return t.strip()
    try:
        pl = msg.get("primary_location", {})
        txt = pl.get("abstract", {}).get("text", "")
        if isinstance(txt, str) and txt.strip():
            return txt.strip()
    except (AttributeError, TypeError):
        pass
    return None


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
    date = _extract_date(result)
    doi = result.get("doi")
    if doi:
        doi = str(doi).strip() or None
    return BibliographicRecord(
        title=_first(result, "title", default="") or None,
        # ``authors`` is the adapter-unified key; ``author`` is Crossref's
        # native singular key -- accept either.
        authors=_normalize_authors(result.get("authors") or result.get("author")),
        year=date["year"],
        month=date["month"],
        day=date["day"],
        doi=doi,
        url=result.get("url") or None,
        venue=result.get("venue") or _venue_from_raw(result.get("raw")),
        publisher=_first(result, "publisher", default="") or None,
        abstract=result.get("abstract")
        or _abstract_from_raw(result.get("raw")),
        id=result.get("id") or None,
        kind=result.get("kind") or None,
        extra=result.get("raw") or {},
    )


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
    """Collapse records sharing a DOI, then a normalised URL. Returns kept."""
    seen_doi: set = set()
    seen_url: set = set()
    kept: List[BibliographicRecord] = []
    for r in records:
        if r.doi:
            key = r.doi.lower()
            if key in seen_doi:
                continue
            seen_doi.add(key)
        if r.url:
            u = re.sub(r"[?#].*$", "", str(r.url).rstrip("/")).lower()
            if u in seen_url:
                continue
            seen_url.add(u)
        kept.append(r)
    return kept


# ────────────────────────────────────────────────────────────────
# Formatters
# ────────────────────────────────────────────────────────────────

def _bibtex_escape(text: str) -> str:
    """Escape characters BibTeX treats specially inside a field value."""
    if not text:
        return text
    return (
        text.replace("{", r"\{").replace("}", r"\}")
        .replace("&", r"\&").replace("_", r"\_").replace("#", r"\#")
    )


def _citation_key(record: BibliographicRecord) -> str:
    """``firstauthoryear`` citation key (bibtex)."""
    name = ""
    if record.authors:
        first = record.authors[0]
        # first author string is "Last" (Crossref) or "First Last" (OpenAlex);
        # the family name is the last whitespace-delimited token either way.
        token = first.split(",")[-1].split()[-1] if first else ""
        name = re.sub(r"[^A-Za-z0-9]", "", token)[:8]
    year = record.year or "0000"
    name = re.sub(r"[^A-Za-z0-9]", "", name or "unknown")
    key = f"{name.lower()}{year}"
    return key if key else f"item{year}"


def _author_last_first(author: str):
    """Return ``(family, given)`` from an author string.

    A comma means ``"Last, First"``. Otherwise the string is treated as
    ``"First Last"`` (OpenAlex display names) and flipped so the final
    token is the family name.
    """
    if "," in author:
        parts = [p.strip() for p in author.split(",", 1)]
        return parts[0], parts[1] if len(parts) > 1 else ""
    parts = author.split(" ")
    if len(parts) > 1:
        return parts[-1], " ".join(parts[:-1])
    return author, ""


def to_bibtex(records: List[BibliographicRecord]) -> str:
    """Render records as a BibTeX fragment (``@article`` default)."""
    if not records:
        return ""
        return ""
    out = []
    for r in records:
        kind = r.kind or "article-journal"
        entry = "inproceedings" if "proceed" in (kind or "") else "article"
        key = _citation_key(r)
        header = "@" + entry + "{" + key + ","
        lines = [header]
        if r.authors:
            joined = " and ".join(_bibtex_escape(a) for a in r.authors)
            lines.append(f"  author={_bibtex_escape(joined)},")
        if r.title:
            lines.append(f"  title={_bibtex_escape(r.title)},")
        if r.venue:
            lines.append(f"  journal={_bibtex_escape(r.venue)},")
        elif r.publisher:
            lines.append(f"  publisher={_bibtex_escape(r.publisher)},")
        if r.year:
            lines.append(f"  year={r.year},")
        if r.doi:
            lines.append(f"  doi={_bibtex_escape(r.doi)},")
        if r.url:
            lines.append(f"  url={_bibtex_escape(r.url)},")
        out.append("\n".join(lines) + "\n}")
    return "\n\n".join(out)


def to_csl_json(records: List[BibliographicRecord]) -> str:
    """Render records as a CSL-JSON array (the machine interchange hub)."""
    items = []
    for r in records:
        author_list = []
        for a in r.authors:
            fam, giv = _author_last_first(a)
            author_list.append(
                {"family": fam, "given": giv} if giv else {"family": fam}
            )
        item: Dict[str, Any] = {
            "type": r.kind or "article-journal",
            "ID": len(items) + 1,
            "title": r.title or "",
        }
        if r.doi:
            item["DOI"] = r.doi
        if r.url:
            item["URL"] = r.url
        if author_list:
            item["author"] = author_list
        if r.venue:
            item["container-title"] = [r.venue]
        if r.publisher:
            item["publisher"] = r.publisher
        if r.year:
            issued = {"date-parts": [[int(r.year)]]}
            if r.month:
                issued["date-parts"][0].append(int(r.month[:2]))
            item["issued"] = issued
        if r.abstract:
            item["abstract"] = re.sub(r"\s+", " ", r.abstract).strip()
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)


def _apa_author(author: str) -> str:
    fam, giv = _author_last_first(author)
    initials = " ".join(p[0] + "." for p in giv.split() if p) if giv else ""
    # Keep the initial's trailing period; the format string adds the
    # sentence period before the year, so the group never double-periods.
    return f"{fam}, {initials}" if initials else fam


def to_apa(records: List[BibliographicRecord]) -> str:
    """Render records as APA (7th) approximations, one per line."""
    records = dedupe_records(records)
    lines = []
    for r in records:
        author_part = ", ".join(_apa_author(a) for a in r.authors)
        year = f"({r.year})" if r.year else ""
        title = r.title or ""
        venue = f"*{r.venue}*" if r.venue else ""
        if r.doi:
            link = f" https://doi.org/{r.doi}"
        elif r.url:
            link = f" {r.url}"
        else:
            link = ""
        # author_part already ends with the last initial's period, so the
        # year prefix starts with a bare space (no extra period).
        line = f"{author_part} {year}. {title}. {venue}{link}".strip()
        lines.append(line)
    return "\n\n".join(lines)


def to_mla(records: List[BibliographicRecord]) -> str:
    """Render records as MLA (9th) approximations, one per line."""
    records = dedupe_records(records)
    lines = []
    for r in records:
        author = (", ".join(r.authors) if r.authors else "").rstrip(".")
        title = f'"{r.title}"' if r.title else ""
        venue = f"*{r.venue}*" if r.venue else ""
        year = r.year or ""
        if r.doi:
            link = f" doi.org/{r.doi}"
        elif r.url:
            link = f" {r.url}"
        else:
            link = ""
        line = f"{author}. {title}. {venue}, {year}.{link}".strip()
        lines.append(line)
    return "\n\n".join(lines)


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
