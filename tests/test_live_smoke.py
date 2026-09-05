"""Live network smoke tests for the domain adapters (feature-flagged).

These make *real* calls to each provider, so they are skipped by default.
Enable with GOSSAMER_LIVE=1:

    GOSSAMER_LIVE=1 pytest tests/test_live_smoke.py

They are deliberately lightweight — a single search (+ a fetch where the
source supports lookups) per adapter — to exercise the end-to-end path
(request construction, auth injection, HTTP, response parsing) without
consuming rate budgets or asserting brittle exact values.

Key-gated adapters (PubMed / GitHub / FRED) use their key automatically when
the corresponding ``GOSSAMER_*`` env var is set; they still run keyless
otherwise, so they do not require a key to smoke-test.
"""

from __future__ import annotations

import pytest

from gossamer.research_providers import (
    ArxivAdapter,
    BisAdapter,
    BundesbankAdapter,
    CoinGeckoAdapter,
    CourtListenerAdapter,
    CrossrefAdapter,
    DoajAdapter,
    EcfrAdapter,
    EpoOpsAdapter,
    EurostatAdapter,
    FederalRegisterAdapter,
    FrankfurterAdapter,
    FredAdapter,
    GitHubAdapter,
    GovInfoAdapter,
    HudocAdapter,
    KiprisAdapter,
    NASAAdapter,
    NvdAdapter,
    OldpAdapter,
    OpenAlexAdapter,
    OpenLibraryAdapter,
    OpenMeteoAdapter,
    OverpassAdapter,
    PatentsViewAdapter,
    SoftwareHeritageAdapter,
    YahooFinanceAdapter,
    ZenodoAdapter,
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
    prov = OpenAlexAdapter(delay=0.0, email="probe@example.org")
    results = prov.search("quantum computing", max_results=3)
    assert results, "OpenAlex returned no results"
    _assert_common_result(results[0], source="openalex")
    assert results[0]["title"]  # OpenAlex search returns titles

    # fetch() the first result by its own id.
    one = prov.fetch(results[0]["id"])
    assert one and one[0]["id"] == results[0]["id"]


@pytest.mark.live
def test_crossref_search_and_fetch(live):
    prov = CrossrefAdapter(delay=0.0, email="probe@example.org")
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
# Fixed + wave-3 adapters (verified live 2026-09; keyless paths only)
# ────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_yahoo_search(live):
    prov = YahooFinanceAdapter(delay=0.0)
    results = prov.search("AAPL", max_results=2)
    assert results, "Yahoo returned no results"
    _assert_common_result(results[0], source="yahoo")
    assert results[0]["id"] == "AAPL"


@pytest.mark.live
def test_federal_register_keyless(live):
    prov = FederalRegisterAdapter(delay=0.0)
    results = prov.search("immigration", max_results=2)
    assert results, "Federal Register returned no results"
    _assert_common_result(results[0], source="federalregister")


@pytest.mark.live
def test_zenodo_search(live):
    prov = ZenodoAdapter(delay=0.0)
    results = prov.search("quantum", max_results=2)
    assert results, "Zenodo returned no results"
    _assert_common_result(results[0], source="zenodo")
    assert results[0]["title"]


@pytest.mark.live
def test_nasa_demo_key(live):
    prov = NASAAdapter(delay=0.0)
    results = prov.search("2024-01-01", max_results=2)
    assert results, "NeoWs returned no objects"
    _assert_common_result(results[0], source="nasa")


@pytest.mark.live
def test_nvd_search(live):
    prov = NvdAdapter(delay=0.0)
    results = prov.search("log4j", max_results=2)
    assert results, "NVD returned no results"
    _assert_common_result(results[0], source="nvd")
    assert results[0]["id"].startswith("CVE-")


@pytest.mark.live
def test_courtlistener_search(live):
    prov = CourtListenerAdapter(delay=0.0)
    results = prov.search("miranda", max_results=2)
    assert results, "CourtListener returned no results"
    _assert_common_result(results[0], source="courtlistener")


@pytest.mark.live
def test_ecfr_structure(live):
    prov = EcfrAdapter(delay=0.0)
    out = prov.fetch("21/113")
    assert out and out[0]["id"] == "21/113"
    assert out[0]["url"].startswith("https://www.ecfr.gov/")


@pytest.mark.live
def test_softwareheritage_origin(live):
    prov = SoftwareHeritageAdapter(delay=0.0)
    out = prov.search("https://github.com/python/cpython", max_results=1)
    assert out, "SWH origin lookup failed"
    _assert_common_result(out[0], source="softwareheritage")


@pytest.mark.live
def test_overpass_mirror(live):
    prov = OverpassAdapter(delay=0.0)
    out = prov.search(
        "[out:json];node(around:100,52.520008,13.404954)[amenity=cafe];out 1;",
        max_results=1,
    )
    assert out, "Overpass returned no elements"
    _assert_common_result(out[0], source="overpass")


@pytest.mark.live
def test_oldp_search(live):
    prov = OldpAdapter(delay=0.0)
    results = prov.search("Mietminderung", max_results=2)
    assert results, "OLDP returned no results"
    _assert_common_result(results[0], source="oldp")
    assert results[0]["title"]


@pytest.mark.live
def test_hudoc_search(live):
    prov = HudocAdapter(delay=0.0)
    results = prov.search("privacy", max_results=2)
    assert results, "HUDOC returned no results"
    _assert_common_result(results[0], source="hudoc")
    assert results[0]["id"]


@pytest.mark.live
def test_frankfurter_rates(live):
    prov = FrankfurterAdapter(delay=0.0)
    out = prov.search("USD/EUR", max_results=1)
    assert out and float(out[0]["fields"]["rate"]) > 0
    assert out[0]["id"] == "USD/EUR"


@pytest.mark.live
def test_eurostat_gdp(live):
    prov = EurostatAdapter(delay=0.0)
    out = prov.search("nama_10_gdp?freq=A&unit=CP_MEUR&na_item=B1GQ&geo=DE&time=2023")
    assert out and float(out[0]["fields"]["value"]) > 0


@pytest.mark.live
def test_bundesbank_rates(live):
    prov = BundesbankAdapter(delay=0.0)
    out = prov.search("BBEX3/D.USD.EUR.BB.AC.000?startPeriod=2024-01-02&endPeriod=2024-01-02")
    assert out and float(out[0]["fields"]["value"]) > 0


@pytest.mark.live
def test_bis_policy_rates(live):
    prov = BisAdapter(delay=0.0)
    out = prov.search("WS_CBPOL/M.XM.EUR?startPeriod=2024-01&endPeriod=2024-01")
    assert out, "BIS returned no observations"
    _assert_common_result(out[0], source="bis")


@pytest.mark.live
def test_govinfo_search(live):
    prov = GovInfoAdapter(delay=0.0)
    results = prov.search("clean water", max_results=2)
    assert results, "GovInfo returned no results"
    _assert_common_result(results[0], source="govinfo")


@pytest.mark.live
def test_coingecko_search(live):
    prov = CoinGeckoAdapter(delay=0.0)
    results = prov.search("bitcoin", max_results=2)
    assert results, "CoinGecko returned no results"
    _assert_common_result(results[0], source="coingecko")
    assert results[0]["id"] == "bitcoin"


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


# ────────────────────────────────────────────────────────────────
# Wave-4 patent adapters (all key-gated; skipped without keys)
# ────────────────────────────────────────────────────────────────

def _need_keys(*names):
    import os

    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")


@pytest.mark.live
def test_epo_ops_search(live):
    _need_keys("GOSSAMER_EPO_KEY", "GOSSAMER_EPO_SECRET")
    prov = EpoOpsAdapter(delay=0.0)
    results = prov.search("ti=quantum", max_results=2)
    assert results, "EPO OPS returned no results"
    _assert_common_result(results[0], source="epo")
    assert results[0]["id"]


@pytest.mark.live
def test_kipris_search(live):
    _need_keys("GOSSAMER_KIPRIS_KEY")
    prov = KiprisAdapter(delay=0.0)
    results = prov.search("quantum", max_results=2)
    assert results, "KIPRIS returned no results"
    _assert_common_result(results[0], source="kipris")


@pytest.mark.live
def test_patentsview_search(live):
    _need_keys("GOSSAMER_PATENTSVIEW_API_KEY")
    prov = PatentsViewAdapter(delay=0.0)
    results = prov.search("quantum", max_results=2)
    assert results, "PatentsView returned no results"
    _assert_common_result(results[0], source="patentsview")
    assert results[0]["id"]
