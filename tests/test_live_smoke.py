"""Live network smoke tests for the domain adapters (feature-flagged).

These make *real* calls to each provider, so they are skipped by default.
Enable with STITCH_LIVE=1:

    STITCH_LIVE=1 pytest tests/test_live_smoke.py

They are deliberately lightweight — a single search (+ a fetch where the
source supports lookups) per adapter — to exercise the end-to-end path
(request construction, auth injection, HTTP, response parsing) without
consuming rate budgets or asserting brittle exact values.

Key-gated adapters (PubMed / GitHub / FRED) use their key automatically when
the corresponding ``STITCH_*`` env var is set; they still run keyless
otherwise, so they do not require a key to smoke-test.
"""

from __future__ import annotations

import pytest

from stitch_web_researcher.research_providers import (
    ArxivAdapter,
    CrossrefAdapter,
    DoajAdapter,
    FredAdapter,
    GitHubAdapter,
    OpenAlexAdapter,
    OpenLibraryAdapter,
    OpenMeteoAdapter,
    PubmedAdapter,
    WorldBankAdapter,
)

# Stable, long-lived reference ids used by the fetch() smoke checks.
_WORLD_BANK_POPULATION = "SP.POP.TOTL"
_FRED_GDP = "GDP"


def _assert_common_result(rec: dict, *, source: str) -> None:
    """The normalized record shape every adapter must produce."""
    assert isinstance(rec, dict)
    assert rec.get("source") == source, rec
    # At least one meaningful field must be present. (World Bank keyword
    # search is retired and returns a title-bearing note with no id/url.)
    assert rec.get("id") or rec.get("title") or rec.get("url"), rec


# ────────────────────────────────────────────────────────────────
# Scholarly
# ────────────────────────────────────────────────────────────────


@pytest.mark.live
def test_openalex_search_and_fetch(live):
    prov = OpenAlexAdapter(delay=0.0, email="stitch-test@example.org")
    results = prov.search("quantum computing", max_results=3)
    assert results, "OpenAlex returned no results"
    _assert_common_result(results[0], source="openalex")
    assert results[0]["title"]  # OpenAlex search returns titles

    # fetch() the first result by its own id.
    one = prov.fetch(results[0]["id"])
    assert one and one[0]["id"] == results[0]["id"]


@pytest.mark.live
def test_crossref_search_and_fetch(live):
    prov = CrossrefAdapter(delay=0.0, email="stitch-test@example.org")
    results = prov.search("quantum computing", max_results=3)
    assert results, "Crossref returned no results"
    _assert_common_result(results[0], source="crossref")

    dois = [r["doi"] for r in results if r.get("doi")]
    target = dois[0] if dois else results[0]["id"]
    one = prov.fetch(target)
    assert one and one[0]["id"]


@pytest.mark.live
def test_arxiv_search_and_fetch(live):
    prov = ArxivAdapter(delay=0.0)
    results = prov.search("quantum", max_results=3)
    assert results, "arXiv returned no results"
    _assert_common_result(results[0], source="arxiv")
    assert results[0]["title"]

    one = prov.fetch(results[0]["id"])
    assert one and one[0]["id"]


@pytest.mark.live
def test_pubmed_search_and_fetch(live):
    prov = PubmedAdapter(delay=0.0)
    results = prov.search("quantum biology", max_results=3)
    assert results, "PubMed returned no ids"
    _assert_common_result(results[0], source="pubmed")
    assert results[0]["id"].isdigit()

    one = prov.fetch(results[0]["id"])
    assert one and one[0]["id"] == results[0]["id"]


@pytest.mark.live
def test_doaj_search(live):
    prov = DoajAdapter(delay=0.0)
    results = prov.search("open access", max_results=3)
    assert results, "DOAJ returned no results"
    _assert_common_result(results[0], source="doaj")
    assert results[0]["title"]


# ────────────────────────────────────────────────────────────────
# Library / Geo
# ────────────────────────────────────────────────────────────────


@pytest.mark.live
def test_openlibrary_search_and_fetch(live):
    prov = OpenLibraryAdapter(delay=0.0)
    results = prov.search("the lord of the rings", max_results=3)
    assert results, "Open Library returned no results"
    _assert_common_result(results[0], source="openlibrary")
    assert results[0]["title"]

    one = prov.fetch(results[0]["id"])
    assert one and one[0]["id"]


@pytest.mark.live
def test_open_meteo_search_and_fetch(live):
    prov = OpenMeteoAdapter(delay=0.0)
    results = prov.search("Berlin", max_results=1)
    assert results, "Open-Meteo geocode returned no results"
    _assert_common_result(results[0], source="open-meteo")

    # fetch() with the coordinates we just geocoded.
    lat_lon = results[0]["id"]  # "lat,lon"
    one = prov.fetch(lat_lon)
    assert one and one[0]["source"] == "open-meteo"


# ────────────────────────────────────────────────────────────────
# Financial / Tech
# ────────────────────────────────────────────────────────────────


@pytest.mark.live
def test_worldbank_fetch(live):
    prov = WorldBankAdapter(delay=0.0)
    # World Bank keyword search is retired; it returns a structured note.
    note = prov.search("population total", max_results=1)
    assert note and "unavailable" in note[0]["title"].lower()
    _assert_common_result(note[0], source="worldbank")

    # The data endpoint still works: fetch a known series.
    one = prov.fetch(_WORLD_BANK_POPULATION)
    assert one and one[0]["id"] == _WORLD_BANK_POPULATION
    assert one[0]["fields"]["worldbank"]["observations"]


@pytest.mark.live
def test_fred_fetch(live):
    prov = FredAdapter(delay=0.0)
    one = prov.fetch(_FRED_GDP)
    assert one and one[0]["id"] == _FRED_GDP
    # A well-known series should return at least one observation.
    assert one[0]["fields"]["fred"]["observations"]


@pytest.mark.live
def test_github_search(live):
    prov = GitHubAdapter(delay=0.0)
    results = prov.search("rust", max_results=3)
    assert results, "GitHub returned no results"
    _assert_common_result(results[0], source="github")
    assert results[0]["url"]


# ────────────────────────────────────────────────────────────────
# Feature-flag logic (no network)
# ────────────────────────────────────────────────────────────────


def test_live_flag_off_by_default():
    from tests.conftest import _live_enabled

    assert _live_enabled(None) is False
    assert _live_enabled("") is False
    assert _live_enabled("0") is False
    assert _live_enabled("no") is False


def test_live_flag_on():
    from tests.conftest import _live_enabled

    assert _live_enabled("1") is True
    assert _live_enabled("true") is True
    assert _live_enabled("YES") is True
