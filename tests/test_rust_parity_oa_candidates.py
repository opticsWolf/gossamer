from gossamer.open_access import _oa_candidate, _unpaywall_candidate, unpaywall_candidates
def test_oa():
    loc={"pdf_url":"https://x/a.pdf","source":{"display_name":"X","id":"https://x"},"is_oa":True}
    c=_oa_candidate(loc,is_best=True,work_is_oa=False)
    assert c["kind"]=="pdf" and c["source"]=="X"
    u=_unpaywall_candidate({"url_for_pdf":"https://x/b.pdf","host_type":"publisher"},is_best=False)
    assert u["source"]=="publisher" and u["is_oa"] is True
    cs, work = unpaywall_candidates({"best_oa_location":{"url_for_pdf":"https://x/b.pdf"},"oa_locations":[{"url_for_pdf":"https://x/b.pdf"}],"is_oa":True})
    assert len(cs)==1 and work["is_oa"] is True
