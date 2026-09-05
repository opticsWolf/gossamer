"""Tests for the citation reconstruction / export module (Plan workstream 1).

All offline: no network. ``enrich_with_doi`` is exercised with a fake
adapter implementing ``fetch(doi) -> list[dict]``.
"""

from __future__ import annotations

import json

import pytest

from gossamer.citations import (
    BibliographicRecord,
    format_citations,
    dedupe_records,
    enrich_with_doi,
    record_from_result,
    to_apa,
    to_bibtex,
    to_csl_json,
    to_mla,
    _apa_approx,
    _mla_approx,
)


# ── fixtures: cross-shaped and openalex-shaped result dicts ───────────

CROSSREF = {
    "source": "crossref",
    "id": "10.1038/s41586-020-0694-2",
    "title": ["A large language model for all living things"],
    "url": "https://doi.org/10.1038/s41586-020-0694-2",
    "doi": "10.1038/s41586-020-0694-2",
    "published": {"date-parts": [[2020, 9, 24]]},
    "author": [
        {"family": "Brown", "given": "Alex"},
        {"family": "Manage", "given": "Lucia"},
    ],
    "container_title": ["Nature Machine Intelligence"],
    "raw": json.dumps(
        {
            "message": {
                "container_title": ["Nature Machine Intelligence"],
                "abstract": {"#text": "A transformer model."},
            }
        }
    ),
}

ALEX = {
    "source": "openalex",
    "id": "S10.1109/5.771073",
    "title": "Some practical hints for searching",
    "url": "https://doi.org/10.1109/5.771073",
    "doi": "10.1109/5.771073",
    "publication_date": "1990-09-01",
    "authors": "John Doe, Jane Smith",
    "citations": 1200,
    "raw": json.dumps(
        {
            "message": {
                "primary_location": {
                    "journal": {"display_name": "Proceedings of the IEEE"}
                }
            }
        }
    ),
}

CROSSREF_STR = {
    "source": "crossref",
    "id": "10.1000/ghi1234",
    "title": "A string-authored work",
    "url": "https://doi.org/10.1000/ghi1234",
    "doi": "10.1000/ghi1234",
    "published": "2011-03-04",
    "authors": "Doe, Smith",
    "raw": "",
}


# ── record_from_result ───────────────────────────────────────────────

class TestRecordFromResult:
    def test_crossref_native_shape(self):
        r = record_from_result(CROSSREF)
        assert r.title == "A large language model for all living things"
        assert r.doi == "10.1038/s41586-020-0694-2"
        assert r.year == "2020" and r.month == "9" and r.day == "24"
        # list-of-dict authors -> "Last, Given"
        assert r.authors == ["Brown, Alex", "Manage, Lucia"]
        # venue + abstract pulled from raw
        assert r.venue == "Nature Machine Intelligence"
        assert r.abstract == "A transformer model."

    def test_openalex_shape(self):
        r = record_from_result(ALEX)
        assert r.title == "Some practical hints for searching"
        assert r.year == "1990" and r.month == "09"
        assert r.authors == ["John Doe", "Jane Smith"]
        assert r.venue == "Proceedings of the IEEE"
        assert r.venue is None or isinstance(r.venue, str)

    def test_string_authors_and_date(self):
        r = record_from_result(CROSSREF_STR)
        assert r.authors == ["Doe", "Smith"]
        assert r.year == "2011" and r.month == "03" and r.day == "04"

    def test_partial_dict_does_not_raise(self):
        r = record_from_result({"title": "Only a title"})
        assert r.title == "Only a title"
        assert r.authors == []
        assert r.doi is None and r.year is None

    def test_non_dict_is_safe(self):
        assert record_from_result(None).title is None
        assert record_from_result("nope").title is None


# ── dedupe ───────────────────────────────────────────────────────────

class TestDedupe:
    def test_dedupe_by_doi(self):
        a = record_from_result(CROSSREF)
        b = record_from_result({**CROSSREF, "title": ["dup"]})
        assert len(dedupe_records([a, b])) == 1

    def test_dedupe_by_url(self):
        a = record_from_result(CROSSREF)
        b = record_from_result({**CROSSREF, "doi": "different", "id": "x"})
        assert len(dedupe_records([a, b])) == 1

    def test_trailing_slash_and_query_are_equal(self):
        a = BibliographicRecord(doi="10.1/x", url="https://doi.org/10.1/x?ref=1")
        b = BibliographicRecord(doi="10.1/x", url="https://doi.org/10.1/x/")
        kept = dedupe_records([a, b])
        assert len(kept) == 1

    def test_distinct_survive(self):
        a = record_from_result(CROSSREF)
        b = record_from_result(ALEX)
        assert len(dedupe_records([a, b])) == 2


# ── enrich_with_doi ──────────────────────────────────────────────────

class _FakeCrossref:
    """Fake adapter: fetch(doi) returns a canned unified result dict."""

    def __init__(self, payload):
        self._payload = payload

    def fetch(self, doi):
        d = dict(self._payload)
        d.setdefault("doi", doi)
        d.setdefault("title", ["T"])
        return [d]


class TestEnrichWithDoi:
    def test_fills_missing_venue_and_abstract(self):
        r = record_from_result({"title": "T", "doi": "10.1/x"})
        fake = _FakeCrossref(
            {
                "title": ["T"],
                "doi": "10.1/x",
                "container_title": ["The Journal"],
                "abstract": {"#text": "Full abstract."},
                "raw": json.dumps(
                    {
                        "message": {
                            "container_title": ["The Journal"],
                            "abstract": {"#text": "Full abstract."},
                        }
                    }
                ),
            }
        )
        out = enrich_with_doi(r, fake)
        assert out.venue == "The Journal"
        assert out.abstract == "Full abstract."

    def test_no_doi_is_noop(self):
        r = record_from_result({"title": "T"})
        assert enrich_with_doi(r, _FakeCrossref({})).venue is None

    def test_no_adapter_is_noop(self):
        r = record_from_result({**CROSSREF, "venue": None, "abstract": None})
        assert enrich_with_doi(r, None).doi == r.doi

    def test_adapter_exception_is_swallowed(self):
        class Boom:
            def fetch(self, doi):
                raise RuntimeError("down")

        r = record_from_result({**CROSSREF, "venue": None})
        assert enrich_with_doi(r, Boom()).doi == r.doi  # unchanged, no raise


# ── BibTeX ───────────────────────────────────────────────────────────

class TestToBibtex:
    def test_article_entry_shape(self):
        out = to_bibtex([record_from_result(ALEX)])
        assert out.startswith("@article{doe1990,")
        assert "author=John Doe and Jane Smith," in out
        assert "journal=Proceedings of the IEEE," in out
        assert "year=1990," in out
        assert "doi=10.1109/5.771073," in out

    def test_escapes_special_chars(self):
        r = BibliographicRecord(title="A & B _C #D {E}", year="2020", doi="")
        out = to_bibtex([r])
        assert "\\&" in out and "\\_" in out and "\\#" in out and "\\{" in out

    def test_multiple_records_separated(self):
        out = to_bibtex([record_from_result(CROSSREF), record_from_result(ALEX)])
        assert out.count("@article{") == 2

    def test_empty_returns_empty(self):
        assert to_bibtex([]) == ""

    def test_inproceedings_for_kind(self):
        r = record_from_result(ALEX)
        r.kind = "paper-in-proceedings"
        assert to_bibtex([r]).startswith("@inproceedings{")

    def test_formatters_are_pure_no_dedupe(self):
        # Dedup is owned by format_citations; the standalone formatters
        # render exactly what they are given.
        out = to_bibtex([record_from_result(CROSSREF), record_from_result(CROSSREF)])
        assert out.count("@article{") == 2


# ── CSL-JSON ─────────────────────────────────────────────────────────

class TestToCslJson:
    def test_valid_json_and_shape(self):
        items = json.loads(to_csl_json([record_from_result(ALEX)]))
        assert len(items) == 1
        it = items[0]
        assert it["type"] == "article-journal"
        assert it["DOI"] == "10.1109/5.771073"
        assert it["container-title"] == ["Proceedings of the IEEE"]
        assert it["issued"]["date-parts"] == [[1990, 9]]
        assert it["author"][0] == {"family": "Doe", "given": "John"}

    def test_crossref_author_last_first(self):
        items = json.loads(to_csl_json([record_from_result(CROSSREF)]))
        assert items[0]["author"][0]["family"] == "Brown"
        assert items[0]["author"][0]["given"] == "Alex"


# ── APA / MLA ────────────────────────────────────────────────────────

class TestApamla:
    """Approximation formatters -- the citeproc-py fallback.

    These call the pure approximations directly, so they are deterministic
    whether or not citeproc-py is installed.
    """

    def test_apa_shape(self):
        out = _apa_approx([record_from_result(ALEX)])
        assert out.startswith("Doe, J.")
        assert "(1990)" in out
        assert "Some practical hints for searching" in out
        assert "*Proceedings of the IEEE*" in out  # approx uses markdown italics
        assert "https://doi.org/10.1109/5.771073" in out
        assert ".." not in out  # no double period

    def test_mla_shape(self):
        out = _mla_approx([record_from_result(CROSSREF)])
        assert '"A large language model for all living things"' in out
        assert "*Nature Machine Intelligence*" in out
        assert "doi.org/10.1038/s41586-020-0694-2" in out
        assert out.startswith("Brown, Alex")

    def test_apa_no_double_period(self):
        out = _apa_approx([record_from_result(ALEX)])
        assert "J.. " not in out


# ── citeproc-py integration (optional dependency) ────────────────

class TestCiteprocIntegration:
    """APA via citeproc-py and the graceful fallback.

    citeproc-py is purely local (no network), so these stay offline. The
    missing-dependency path is exercised by blocking the import.
    """

    def test_apa_via_citeproc(self):
        pytest.importorskip("citeproc")
        out = to_apa([record_from_result(ALEX)])
        assert out.startswith("Doe, J.")
        assert "(1990)" in out
        assert "Some practical hints for searching" in out
        assert "Proceedings of the IEEE" in out
        assert "https://doi.org/10.1109/5.771073" in out
        # citeproc's plain formatter emits no markdown italics
        assert "*" not in out

    def test_apa_falls_back_without_citeproc(self, monkeypatch):
        # Simulate citeproc-py not being installed.
        import sys
        monkeypatch.setitem(sys.modules, "citeproc", None)
        out = to_apa([record_from_result(ALEX)])
        # Fallback approximation: markdown italics present again.
        assert "*Proceedings of the IEEE*" in out
        assert out.startswith("Doe, J.")

    def test_mla_uses_fallback_no_style(self):
        # No 'mla' style ships with citeproc-py-styles, so MLA is always
        # rendered by the approximation (never citeproc).
        out = to_mla([record_from_result(CROSSREF)])
        approx = _mla_approx([record_from_result(CROSSREF)])
        assert out == approx
        assert "*Nature Machine Intelligence*" in out


# ── format_citations (top-level) ─────────────────────────────────────

class TestFormatCitations:
    def test_all_styles(self):
        for style in ("bibtex", "csl-json", "apa", "mla"):
            out = format_citations([CROSSREF], style=style)
            assert out, f"empty output for style {style}"

    def test_bare_dois(self):
        out = format_citations(["10.1038/s41586-020-0694-2"], style="bibtex")
        assert "10.1038/s41586-020-0694-2" in out

    def test_bare_urls(self):
        out = format_citations(["https://example.com/paper"], style="apa")
        assert "example.com" in out

    def test_dedupe_flag(self):
        out = format_citations(
            ["10.1/x", "10.1/x", ALEX], style="bibtex", dedupe=True
        )
        assert out.count("@article{") == 2

    def test_no_dedupe(self):
        out = format_citations(
            ["10.1/x", "10.1/x"], style="bibtex", dedupe=False
        )
        assert out.count("@article{") == 2

    def test_enrich_with_fake_adapter(self):
        fake = _FakeCrossref(
            {
                "title": ["T"],
                "doi": "10.1/x",
                "container_title": ["J"],
                "abstract": {"#text": "abs"},
                "raw": json.dumps(
                    {"message": {"container_title": ["J"], "abstract": {"#text": "abs"}}}
                ),
            }
        )
        out = format_citations(["10.1/x"], style="bibtex", enrich=True, adapter=fake)
        # enrichment doesn't change bibtex much, but must not raise
        assert "10.1/x" in out

    def test_unknown_style_raises(self):
        with pytest.raises(ValueError):
            format_citations([CROSSREF], style="ris")

    def test_empty_input(self):
        assert format_citations([], style="bibtex") == ""
