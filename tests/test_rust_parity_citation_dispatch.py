import pytest
from gossamer.citations import format_citations, _service_citation_style, _service_citation_input

def test_citation_dispatch():
    assert _service_citation_style(None) == "bibtex"
    assert _service_citation_style("MLA") == "mla"
    with pytest.raises(ValueError, match="unknown citation style 'bad'"):
        _service_citation_style("bad")
    assert _service_citation_input(" 10.1234/AbC ") == {"doi":"10.1234/AbC","url":"https://doi.org/10.1234/AbC"}
    assert _service_citation_input(" https://x ") == {"doi":None,"url":"https://x"}
    assert format_citations(["10.1234/example"], style="csl-json")
