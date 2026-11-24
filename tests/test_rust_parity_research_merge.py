from gossamer.research_merge import merge_scholarly_records

def test_merge_scholarly_records():
    rows = merge_scholarly_records([
        ("openalex", [{"doi": "10.1234/AbC", "title": "T", "authors": "A"}]),
        ("crossref", [{"doi": "https://doi.org/10.1234/abc", "title": "T2", "authors": "B"}]),
        ("arxiv", [{"arxiv_id": "arXiv:2301.00123v2", "title": "P"}]),
    ])
    assert len(rows) == 2
    assert rows[0]["key"] == "doi:10.1234/abc"
    assert rows[0]["sources"] == ["openalex", "crossref"]
    assert rows[0]["conflicts"]["title"][1]["value"] == "T2"
    assert rows[1]["key"] == "arxiv:2301.00123"
