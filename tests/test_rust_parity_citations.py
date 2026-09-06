"""Parity: ``citations`` pure paths (v0.8.6) vs ``src/cite.rs``.

Covers record building (Crossref-native / OpenAlex / unified / partial
/ hostile shapes, incl. the propagating AttributeError/TypeError paths),
all four formatters (exact strings), and the citation-key quirks, over
hand-picked cases plus a seeded fuzzer over random records.

Out of contract on both sides (documented in src/cite.rs): scalar slots
holding JSON containers, top-level exotic `authors` values. The fuzzer
stays inside adapter-realistic shapes (str/list/dict/None + a few
numeric/bool scalars).
"""

import json
import random

import pytest

from gossamer import _core


# ── vendored originals (v0.8.6, ported paths only) ────────────────

import re as _re


def _v_first(d, *keys, default=None):
    for k in keys:
        if k in d:
            v = d[k]
            if isinstance(v, list):
                v = v[0] if v else ""
            if v:
                return v
    return default


def _v_normalize_authors(raw):
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


def _v_extract_date(result):
    published = result.get("published") or result.get("publication_date")
    if isinstance(published, dict):
        parts = (published.get("date-parts") or [])[0] or []

        def _g(i):
            return str(parts[i]) if i < len(parts) and parts[i] else None

        return {"year": _g(0), "month": _g(1), "day": _g(2)}
    if not published:
        return {"year": None, "month": None, "day": None}
    m = _re.match(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", str(published).strip())
    if not m:
        return {"year": None, "month": None, "day": None}
    return {"year": m.group(1), "month": m.group(2), "day": m.group(3)}


def _v_venue_from_raw(raw):
    if not raw:
        return None
    try:
        r = json.loads(raw)
    except (TypeError, ValueError):
        return None
    msg = r.get("message", r)
    venue = _v_first(msg, "container_title", "journal_title", default="")
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


def _v_abstract_from_raw(raw):
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


def _v_record(result):
    if not isinstance(result, dict):
        return {}
    date = _v_extract_date(result)
    doi = result.get("doi")
    if doi:
        doi = str(doi).strip() or None
    rec = {
        "title": _v_first(result, "title", default="") or None,
        "authors": _v_normalize_authors(result.get("authors") or result.get("author")),
        "year": date["year"], "month": date["month"], "day": date["day"],
        "doi": doi,
        "url": result.get("url") or None,
        "venue": result.get("venue") or _v_venue_from_raw(result.get("raw")),
        "publisher": _v_first(result, "publisher", default="") or None,
        "abstract": result.get("abstract") or _v_abstract_from_raw(result.get("raw")),
        "id": result.get("id") or None,
        "kind": result.get("kind") or None,
    }
    return rec


def _v_bibtex_escape(text):
    if not text:
        return text
    return (
        text.replace("{", r"\{").replace("}", r"\}")
        .replace("&", r"\&").replace("_", r"\_").replace("#", r"\#")
    )


def _v_citation_key(r):
    name = ""
    if r["authors"]:
        first = r["authors"][0]
        token = first.split(",")[-1].split()[-1] if first else ""
        name = _re.sub(r"[^A-Za-z0-9]", "", token)[:8]
    year = r["year"] or "0000"
    name = _re.sub(r"[^A-Za-z0-9]", "", name or "unknown")
    key = f"{name.lower()}{year}"
    return key if key else f"item{year}"


def _v_last_first(author):
    if "," in author:
        parts = [p.strip() for p in author.split(",", 1)]
        return parts[0], parts[1] if len(parts) > 1 else ""
    parts = author.split(" ")
    if len(parts) > 1:
        return parts[-1], " ".join(parts[:-1])
    return author, ""


def _v_bibtex(records):
    if not records:
        return ""
    out = []
    for r in records:
        kind = r.get("kind") or "article-journal"
        entry = "inproceedings" if "proceed" in (kind or "") else "article"
        lines = ["@" + entry + "{" + _v_citation_key(r) + ","]
        if r["authors"]:
            joined = " and ".join(_v_bibtex_escape(a) for a in r["authors"])
            lines.append(f"  author={_v_bibtex_escape(joined)},")
        if r.get("title"):
            lines.append(f"  title={_v_bibtex_escape(r['title'])},")
        if r.get("venue"):
            lines.append(f"  journal={_v_bibtex_escape(r['venue'])},")
        elif r.get("publisher"):
            lines.append(f"  publisher={_v_bibtex_escape(r['publisher'])},")
        if r.get("year"):
            lines.append(f"  year={r['year']},")
        if r.get("doi"):
            lines.append(f"  doi={_v_bibtex_escape(r['doi'])},")
        if r.get("url"):
            lines.append(f"  url={_v_bibtex_escape(r['url'])},")
        out.append("\n".join(lines) + "\n}")
    return "\n\n".join(out)


def _v_csl(records):
    items = []
    for r in records:
        author_list = []
        for a in r["authors"]:
            fam, giv = _v_last_first(a)
            author_list.append({"family": fam, "given": giv} if giv else {"family": fam})
        item = {"type": r.get("kind") or "article-journal",
                "ID": len(items) + 1, "title": r.get("title") or ""}
        if r.get("doi"):
            item["DOI"] = r["doi"]
        if r.get("url"):
            item["URL"] = r["url"]
        if author_list:
            item["author"] = author_list
        if r.get("venue"):
            item["container-title"] = [r["venue"]]
        if r.get("publisher"):
            item["publisher"] = r["publisher"]
        if r.get("year"):
            issued = {"date-parts": [[int(r["year"])]]}
            if r.get("month"):
                issued["date-parts"][0].append(int(r["month"][:2]))
            item["issued"] = issued
        if r.get("abstract"):
            item["abstract"] = _re.sub(r"\s+", " ", r["abstract"]).strip()
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)


def _v_apa_author(author):
    fam, giv = _v_last_first(author)
    initials = " ".join(p[0] + "." for p in giv.split() if p) if giv else ""
    return f"{fam}, {initials}" if initials else fam


def _v_apa(records):
    lines = []
    for r in records:
        author_part = ", ".join(_v_apa_author(a) for a in r["authors"])
        year = f"({r['year']})" if r.get("year") else ""
        title = r.get("title") or ""
        venue = f"*{r['venue']}*" if r.get("venue") else ""
        if r.get("doi"):
            link = f" https://doi.org/{r['doi']}"
        elif r.get("url"):
            link = f" {r['url']}"
        else:
            link = ""
        lines.append(f"{author_part} {year}. {title}. {venue}{link}".strip())
    return "\n\n".join(lines)


def _v_mla(records):
    lines = []
    for r in records:
        author = (", ".join(r["authors"]) if r["authors"] else "").rstrip(".")
        title = chr(34) + r["title"] + chr(34) if r.get("title") else ""
        venue = f"*{r['venue']}*" if r.get("venue") else ""
        year = r.get("year") or ""
        if r.get("doi"):
            link = f" doi.org/{r['doi']}"
        elif r.get("url"):
            link = f" {r['url']}"
        else:
            link = ""
        lines.append(f"{author}. {title}. {venue}, {year}.{link}".strip())
    return "\n\n".join(lines)


# ── record corpus ────────────────────────────────────────────────

CROSSREF = {
    "title": "A large language model for all living things",
    "authors": [{"family": "Brown", "given": "Alex"},
                {"family": "Manage", "given": "Lucia"}],
    "published": {"date-parts": [[2020, 9, 24]]},
    "doi": "10.1038/s41586-020-0694-2",
    "url": "https://doi.org/10.1038/s41586-020-0694-2",
    "raw": json.dumps({"message": {
        "container_title": ["Nature Machine Intelligence"],
        "abstract": "A transformer model."}}),
}
ALEX = {
    "title": "Some practical hints for searching",
    "authors": "John Doe, Jane Smith",
    "published": "1990-09",
    "doi": "10.1109/5.12345",
    "url": "https://openalex.org/W1",
    "raw": json.dumps({"primary_location": {
        "journal": {"display_name": "Proceedings of the IEEE"}}}),
}
PARTIALS = [
    {},
    {"title": "Only a title"},
    {"title": "", "authors": [], "doi": "   "},
    {"authors": [{"family": "Solo"}]},
    {"authors": [{"name": "Named Only"}]},
    {"authors": ["A U Thor", ""]},
    {"authors": "Doe, John, Smith, Jane"},
    {"published": "2011-03-04"},
    {"published": "2011-03"},
    {"published": "2011"},
    {"published": "not a date"},
    {"published": ""},
    {"published": 2020},
    {"published": {"date-parts": [[2019]]}},
    {"published": {"date-parts": [[]]}},
    {"published": {"date-parts": []}},
    {"doi": 12345, "title": "numeric doi"},
    {"url": "https://example.com/?q=1&x=2", "kind": "proceedings-article",
     "publisher": "ACM", "id": "conf/1", "venue": ""},
    {"title": "A & B _C #D {E}", "authors": ["Doe, Jane"], "year": "2020",
     "doi": "10.1/xyz"},
    {"raw": json.dumps({"message": {"abstract": {"#text": "  padded  "}}})},
    {"raw": json.dumps({"abstract": {"a": "short-a"}})},
    {"title": "Ünïcödé — “quoted” ✓", "authors": ["Müller, Jörg"],
     "venue": "Zeitschrift für Test", "abstract": "line1\nline2  spaced"},
]


def _rec_to_rs(rec):
    return _core.citation_record_from_json(json.dumps(rec))


def _rec_sig(rec):
    r = _rec_to_rs(rec)
    return (r.title, tuple(r.authors), r.year, r.month, r.day, r.doi,
            r.url, r.venue, r.publisher, r.abstract, r.id, r.kind)


def _outcome(fn, *args):
    try:
        return False, fn(*args)
    except Exception as e:  # noqa: BLE001
        return True, f"{type(e).__name__}: {e}"


@pytest.mark.parametrize("rec", [CROSSREF, ALEX] + PARTIALS)
def test_record_parity(rec):
    py_raised, py_want = _outcome(_v_record, rec)
    rs_raised, rs_got = _outcome(_rec_sig, rec)
    assert (py_raised, rs_raised) == (py_raised, py_raised), rec
    if py_raised:
        assert rs_got == py_want, rec
        return
    want = py_want
    got = rs_got
    assert got == (want["title"], tuple(want["authors"]), want["year"],
                   want["month"], want["day"], want["doi"], want["url"],
                   want["venue"], want["publisher"], want["abstract"],
                   want["id"], want["kind"]), rec


ERROR_RAWS = [
    "[1, 2]",
    '"just a string"',
    "123",
    "true",
    "{\"message\": null}",
]


@pytest.mark.parametrize("raw", ERROR_RAWS)
def test_raw_error_paths_parity(raw):
    rec = {"title": "T", "raw": raw}
    py_raised, py_val = _outcome(_v_record, rec)
    rs_raised, rs_val = _outcome(_rec_sig, rec)
    assert (py_raised, rs_raised) == (True, True), raw
    assert rs_val == py_val, raw


def test_unparseable_raw_is_missing():
    for raw in ["", "{nope", None]:
        rec = {"title": "T", "raw": raw}
        assert _v_record(rec)["venue"] is None
        assert _rec_to_rs(rec).venue is None


def _fmt_records():
    originals = [
        CROSSREF, ALEX,
        {"title": "T", "authors": ["Solo"], "published": "2021"},
        {},
    ]
    return [(_v_record(o), o) for o in originals]


def _rs_records(vrecs):
    return [_core.citation_record_from_json(json.dumps(o)) for _, o in vrecs]


@pytest.mark.parametrize("fmt,rs_fn,v_fn", [
    ("bibtex", _core.cite_bibtex, _v_bibtex),
    ("csl", _core.cite_csl_json, _v_csl),
    ("apa", _core.cite_apa_approx, _v_apa),
    ("mla", _core.cite_mla_approx, _v_mla),
])
def test_formatter_exact_parity(fmt, rs_fn, v_fn):
    pairs = _fmt_records()
    vrecs = [v for v, _ in pairs]
    assert rs_fn(_rs_records(pairs)) == v_fn(vrecs), fmt
    assert rs_fn([]) == v_fn([]) == ("" if fmt != "csl" else "[]"), fmt


def test_fuzz_records_and_formats():
    rng = random.Random(20260905)
    firsts = ["Doe", "Smith", "Müller", "John Doe", "A", ""]
    givens = ["Jane", "Jörg Q.", "", "X"]
    titles = ["T", "A & B _C", "Ünïcödé ✓", "", "with, comma"]
    venues = [None, "J Test", "Proc. of X", ""]
    years = [None, "2020", "1999", ""]
    months = [None, "03", "9", ""]
    for _ in range(150):
        n_auth = rng.randint(0, 3)
        authors = []
        for _ in range(n_auth):
            if rng.random() < 0.5:
                authors.append({"family": rng.choice(firsts),
                                "given": rng.choice(givens)})
            else:
                authors.append(rng.choice(firsts) + rng.choice([", ", " "])
                               + rng.choice(givens))
        rec = {
            "title": rng.choice(titles) or None,
            "authors": authors or None,
            "published": rng.choice([
                None, f"{rng.choice(years) or '2020'}-{rng.choice(months) or '01'}",
                {"date-parts": [[int(y) for y in [rng.choice(['2020', '1999'])]]]},
                "nodate",
            ]),
            "doi": rng.choice([None, "10.1/abc", " 10.2/x "]),
            "url": rng.choice([None, "https://example.com/a?x=1&y=2"]),
            "venue": rng.choice(venues),
            "kind": rng.choice([None, "article-journal", "proceedings-paper"]),
        }
        want = _v_record(rec)
        got = _rec_sig(rec)
        assert got == (want["title"], tuple(want["authors"]), want["year"],
                       want["month"], want["day"], want["doi"], want["url"],
                       want["venue"], want["publisher"], want["abstract"],
                       want["id"], want["kind"]), rec
    # Format a slice of the fuzzed records through every formatter.
    pubs = ["202%d-01" % (i % 10) for i in range(8)]
    recs = [_v_record({
        "title": "T%d" % i, "authors": ["Doe, J%d" % i],
        "published": pubs[i],
        "doi": "10.1/x%d" % i, "venue": "V",
    }) for i in range(8)]
    rss = [_core.citation_record_from_json(json.dumps({
        "title": v["title"], "authors": v["authors"],
        "published": pubs[i], "doi": v["doi"], "venue": v["venue"],
    })) for i, v in enumerate(recs)]
    assert _core.cite_bibtex(rss) == _v_bibtex(recs)
    assert _core.cite_csl_json(rss) == _v_csl(recs)
    assert _core.cite_apa_approx(rss) == _v_apa(recs)
    assert _core.cite_mla_approx(rss) == _v_mla(recs)
