from gossamer.agent_tools import WebResearcherToolbox


def test_url_error_and_host_key():
    error = WebResearcherToolbox._url_error("ftp://x", ValueError("Unsupported URL scheme: ftp"))
    assert error == {
        "url": "ftp://x",
        "error": "URL rejected: Unsupported URL scheme: ftp",
        "error_type": "ValueError",
    }
    assert WebResearcherToolbox._crawl_host_key("https://WWW.Example.com:8443/a") == "example.com"
    assert WebResearcherToolbox._crawl_host_key("not a url") == ""
