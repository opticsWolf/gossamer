import pytest
from gossamer.open_access import normalize_doi

def test_normalize_doi():
    assert normalize_doi(" DOI:10.1234/Example ") == "10.1234/example"
    assert normalize_doi("https://doi.org/10.1234/Ab%2FC") == "10.1234/ab/c"
    with pytest.raises(ValueError, match="query or fragment"):
        normalize_doi("https://doi.org/10.1234/x?q=1")
    with pytest.raises(ValueError, match="expected a DOI"):
        normalize_doi("not-a-doi")
    with pytest.raises(ValueError, match="DOI must be a string"):
        normalize_doi(123)
