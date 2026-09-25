"""Identifier-only scholarly cross-provider merge tests."""

from gossamer.research_merge import merge_scholarly_records


def test_merge_normalizes_doi_forms_preserves_sources_and_conflicts():
    providers = [
        ("openalex", [{"doi": "https://doi.org/10.1234/Example", "title": "First title", "raw": "{}"}]),
        ("crossref", [{"doi": "doi:10.1234/example", "title": "First title", "raw": "{}"}]),
        ("semanticscholar", [{"doi": "10.1234/EXAMPLE", "title": "Different title", "raw": "{}"}]),
    ]

    merged = merge_scholarly_records(providers)

    assert len(merged) == 1
    record = merged[0]
    assert record["key"] == "doi:10.1234/example"
    assert record["canonical"]["title"] == "First title"
    assert record["sources"] == ["openalex", "crossref", "semanticscholar"]
    assert len(record["source_records"]) == 3
    assert record["conflicts"]["title"] == [
        {"provider": "openalex", "value": "First title"},
        {"provider": "semanticscholar", "value": "Different title"},
    ]


def test_merge_matches_versioned_arxiv_ids_using_versionless_key():
    providers = [
        ("arxiv", [{"source": "arxiv", "id": "https://arxiv.org/abs/2401.01234v1", "title": "Paper v1"}]),
        ("arxiv", [{"source": "arxiv", "id": "2401.01234v2", "title": "Paper v2"}]),
        ("semanticscholar", [{
            "source": "semanticscholar",
            "id": "S2-ID",
            "raw": '{"externalIds":{"ArXiv":"2401.01234"}}',
        }]),
    ]

    merged = merge_scholarly_records(providers)

    assert len(merged) == 1
    assert merged[0]["key"] == "arxiv:2401.01234"
    assert merged[0]["sources"] == ["arxiv", "semanticscholar"]
    assert len(merged[0]["source_records"]) == 3
    assert len(merged[0]["conflicts"]["title"]) == 2


def test_same_titles_without_strong_ids_are_never_merged():
    providers = [
        ("openalex", [{"title": "Same title", "raw": "{}"}]),
        ("crossref", [{"title": "Same title", "raw": "{}"}]),
    ]

    merged = merge_scholarly_records(providers)

    assert len(merged) == 2
    assert [item["key"] for item in merged] == [None, None]
    assert [item["sources"] for item in merged] == [["openalex"], ["crossref"]]


def test_invalid_doi_falls_back_to_arxiv_or_no_identifier():
    providers = [
        ("arxiv", [{"source": "arxiv", "id": "1707.06376v3", "doi": "not-a-doi"}]),
        ("openalex", [{"title": "No usable identifier", "doi": "bad", "raw": "{}"}]),
    ]

    merged = merge_scholarly_records(providers)

    assert merged[0]["key"] == "arxiv:1707.06376"
    assert merged[1]["key"] is None
