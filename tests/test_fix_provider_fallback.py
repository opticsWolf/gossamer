# tests/test_fix_provider_fallback.py
"""Bugfix 9 — provider substitution must be visible to the caller.

``_resolve_providers`` fell back to the full provider list whenever the
requested name did not match, so ``provider="brave"`` returned DuckDuckGo
results under no label at all. A model that asked for one engine and
silently got another will draw conclusions about coverage it never had.

Two distinct cases, two distinct answers:

* an **unrecognized** name is a caller mistake — reject it up front with
  the list of available providers so the model can correct its own call;
* a **recognized but unregistered** name is ordinary failover — answer
  the query, but carry a ``provider_fallback`` note in the envelope.
"""

import json

import pytest

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox


class _FakeProvider:
    def __init__(self, name):
        self.name = name

    def search(self, query, max_results=10):
        return [
            {"title": f"{self.name} hit", "url": f"https://{self.name}.test/x",
             "snippet": "s"}
        ]


@pytest.fixture
def toolbox(tmp_path):
    tb = WebResearcherToolbox(
        ToolboxConfig(cache_dir=str(tmp_path / "c"), fetch_delay=0.0, ddgs_delay=0.0)
    )
    tb.providers = [_FakeProvider("duckduckgo")]
    return tb


class TestUnknownProviderIsRejected:
    def test_unknown_name_returns_an_error(self, toolbox):
        data = json.loads(toolbox.search_web("q", provider="brave"))
        assert "Unknown search provider" in data["error"]

    def test_error_lists_what_is_available(self, toolbox):
        data = json.loads(toolbox.search_web("q", provider="brave"))
        assert data["available_providers"] == ["duckduckgo"]

    def test_no_results_are_returned_under_the_wrong_label(self, toolbox):
        data = json.loads(toolbox.search_web("q", provider="brave"))
        assert "results" not in data
        assert not isinstance(data, list)


class TestKnownButUnregisteredCarriesANote:
    def test_note_names_the_requested_provider(self, toolbox):
        # "google" is a recognized name; it is simply not registered here.
        data = json.loads(toolbox.search_web("q", provider="google"))
        assert data["provider_fallback"]["requested"] == "google"
        assert data["provider_fallback"]["used"] == ["duckduckgo"]

    def test_results_are_still_delivered(self, toolbox):
        data = json.loads(toolbox.search_web("q", provider="google"))
        assert data["results"][0]["title"] == "duckduckgo hit"

    def test_note_survives_the_result_cache(self, toolbox):
        toolbox.search_web("q", provider="google")
        data = json.loads(toolbox.search_web("q", provider="google"))
        assert data["provider_fallback"]["requested"] == "google"


class TestHonoredRequestsAreUnchanged:
    def test_registered_provider_returns_the_bare_list(self, toolbox):
        # The common shape must not change for callers that got what they
        # asked for.
        data = json.loads(toolbox.search_web("q", provider="duckduckgo"))
        assert isinstance(data, list)
        assert data[0]["title"] == "duckduckgo hit"

    def test_alias_is_honored_without_a_note(self, toolbox):
        data = json.loads(toolbox.search_web("q2", provider="ddg"))
        assert isinstance(data, list)

    def test_no_provider_requested_returns_the_bare_list(self, toolbox):
        data = json.loads(toolbox.search_web("q3"))
        assert isinstance(data, list)
