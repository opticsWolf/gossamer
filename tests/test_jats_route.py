"""JATS-first scholarly route (docs/handoff_gossamer_jats.md §5).

Offline unit (no network, no models, no ORT): the vendored
``jats-chem-olive-2024.xml`` fixture drives routing, fallback, flags,
store, and parity through stubbed fetch + conversion layers.
Live-marked tests need network + ``bobine>=0.7.0`` and never gate CI.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from gossamer import jats as J
from gossamer.config import ToolboxConfig
from gossamer.agent_tools import WebResearcherToolbox

FIXTURE = Path(__file__).parent / "fixtures" / "jats-chem-olive-2024.xml"
CHEM_DOI = "10.1371/journal.pone.0306040"

FAKE_MD = """# Olive leaf extract-assisted green synthesis of silver nanoparticles

## Methods

Sulfonamide group (SO$_{2}$NH) and display:

$$x^{2} + y^{2} \\tag{Eq 1}$$

![Fig 1: Schematic representation](info:doi/10.1371/journal.pone.0306040.g001)

| col1 | col2 |
| --- | --- |
| a | b |

[ [`ref01`]]
"""

FAKE_CONV = {
    "markdown": FAKE_MD,
    "title": "Olive leaf extract-assisted green synthesis of silver nanoparticles",
    "n_formulas": 2,
    "n_figures": 7,
    "n_tables": 2,
}


def make_tb(tmp_path, **kw):
    # respect_robots=False: the fetch layer is stubbed with canned bytes,
    # so robots/crawl-delay must never touch the network (suite convention).
    cfg = ToolboxConfig(
        cache_dir=str(tmp_path / "cache"), respect_robots=False, **kw)
    tb = WebResearcherToolbox(cfg)
    tb._fetch_interval = 0
    tb._fetch_jitter = 0
    tb.domain_delay = 0
    return tb


def stub_fetch(tb, mapping, counter=None):
    """Monkeypatch DocumentExtractor._fetch_document_url with canned bytes.

    Longest keys match first (``fullTextXML`` URLs also contain
    ``europepmc``), and the OA locator is stubbed to no candidates so
    PLOS is never attempted unless a test opts in — the suite stays
    offline and fast.
    """
    tb._oa_locator.locate = lambda doi: {  # noqa: SLF001
        "doi": doi, "status": "closed_access", "candidates": []}  # noqa: SLF001
    def _fake(url):
        if counter is not None:
            counter.append(url)
        for key in sorted(mapping, key=len, reverse=True):
            if key in url:
                body, ctype, status = mapping[key]
                return body, {
                    "fetched_at": "2026-10-28T00:00:00",
                    "http_status": status,
                    "final_url": url,
                    "content_type": ctype,
                }
        raise AssertionError(f"unexpected fetch: {url}")
    tb._doc._fetch_document_url = _fake  # noqa: SLF001


def stub_convert(monkeypatch, conv=None):
    monkeypatch.setattr(
        J, "convert_jats_bytes", lambda *a, **k: dict(conv or FAKE_CONV)
    )


def europepmc_search_bytes(pmcid="1234567", source="PMC"):
    return json.dumps({
        "resultList": {"result": [{"source": source, "id": pmcid, "inPMC": "Y"}]}
    }).encode()


# ── pure helpers (no network, no bobine) ──

def test_parse_europepmc_search_prefers_pmc():
    payload = {"resultList": {"result": [
        {"source": "MED", "id": "999", "inPMC": "N"},
        {"source": "PMC", "id": "1234567", "inPMC": "Y"},
    ]}}
    picked = J.parse_europepmc_search(payload)
    assert picked == {"source": "PMC", "id": "1234567", "pmcid": "PMC1234567"}


def test_parse_europepmc_search_accepts_prefixed_id():
    picked = J.parse_europepmc_search(
        {"resultList": {"result": [{"source": "PMC", "id": "PMC765", "inPMC": "Y"}]}}
    )
    assert picked["pmcid"] == "PMC765" and picked["id"] == "765"


def test_parse_europepmc_search_none_without_pmc():
    assert J.parse_europepmc_search({"resultList": {"result": []}}) is None
    assert J.parse_europepmc_search({}) is None


def test_parse_oa_tgz_link():
    xml = (b'<records><record id="PMC1">'
           b'<link format="tgz" href="https://x/y.tar.gz"/></record></records>')
    assert J.parse_oa_tgz_link(xml) == "https://x/y.tar.gz"
    assert J.parse_oa_tgz_link(b"<records/>") is None
    assert J.parse_oa_tgz_link(b"<records><record/></records>") is None


def test_pick_nxml_prefers_pmcid():
    names = ["a/readme.txt", "b/PMC1.nxml", "c/other.nxml"]
    assert J.pick_nxml(names, "PMC1").endswith("PMC1.nxml")
    assert J.pick_nxml(["x.nxml"]) == "x.nxml"
    assert J.pick_nxml(["x.txt"]) is None


def test_extract_nxml_from_tgz_roundtrip():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"<article/>"
        info = tarfile.TarInfo("oa_package/PMC1.nxml")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    name, out = J.extract_nxml_from_tgz(buf.getvalue(), "PMC1")
    assert name.endswith(".nxml") and out == b"<article/>"


def test_is_xml_body():
    assert J.is_xml_body("application/xml", b"  <article/>")
    assert J.is_xml_body("text/xml; charset=utf-8", b"<?xml version='1.0'?><a/>")
    assert not J.is_xml_body("application/xml", b"{}")
    assert not J.is_xml_body("text/html", b"<html/>")
    assert not J.is_xml_body("application/json", b"<article/>")
    assert not J.is_xml_body("application/xml", b"")


def test_plos_journal_from_url():
    assert J.plos_journal_from_url("https://journals.plos.org/plosone/article?id=x") == "plosone"
    assert J.plos_journal_from_url("https://example.com/y") is None


def test_doi_from_source_string():
    assert J.doi_from_source_string(CHEM_DOI) == CHEM_DOI
    assert J.doi_from_source_string(f"https://doi.org/{CHEM_DOI}") == CHEM_DOI
    assert J.doi_from_source_string("https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0306040&type=manuscript") == CHEM_DOI
    assert J.doi_from_source_string("https://example.com/paper.pdf") is None


# ── offline routing (stubbed fetch + conversion) ──

def test_jats_route_europepmc_hit(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    xml = FIXTURE.read_bytes()
    stub_fetch(tb, {
        "europepmc": (europepmc_search_bytes(), "application/json", 200),
        "fullTextXML": (xml, "application/xml", 200),
    })
    stub_convert(monkeypatch)
    out = json.loads(tb.extract_document(CHEM_DOI, tables_as="markdown"))
    assert out["jats_source"] == "europepmc"
    assert out["resolved_doi"] == CHEM_DOI
    assert out["n_formulas"] == 2 and out["n_figures"] == 7 and out["n_tables"] == 2
    assert out["content"].startswith("# Olive leaf")
    assert "## Methods" in out["content"]
    # balanced dollars
    assert out["content"].count("$$") % 2 == 0


def test_jats_fallback_pmc_oa(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"<article/>"
        info = tarfile.TarInfo("oa_package/PMC999.nxml")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    oa_xml = (b'<records><record id="PMC999">' b'<link format="tgz" href="https://example.com/oa.tar.gz"/>' b'</record></records>')
    stub_fetch(tb, {
        "europepmc": (europepmc_search_bytes(pmcid="999"), "application/json", 200),
        "fullTextXML": (b"<html>nope</html>", "text/html", 200),
        "oa.fcgi": (oa_xml, "application/xml", 200),
        "oa.tar.gz": (buf.getvalue(), "application/x-gzip", 200),
    })
    stub_convert(monkeypatch)
    out = json.loads(tb.extract_document(CHEM_DOI, tables_as="markdown"))
    assert out["jats_source"] == "pmc-oa"


def test_jats_fallthrough_when_unresolvable(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    calls = []
    stub_fetch(tb, {}, counter=calls)
    stub_convert(monkeypatch)
    # Non-DOI URL: zero extra JATS requests, normal PDF path (stubbed).
    monkeypatch.setattr(tb._doc, "_download_and_extract",
                        lambda *a, **k: ("plain pdf text", {"content_type": "application/pdf"}))
    out = json.loads(tb.extract_document("https://example.com/paper.pdf"))
    assert out.get("jats_source") is None
    assert not [u for u in calls if "europepmc" in u or "ncbi" in u or "plos" in u]


def test_jats_local_xml_direct(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    stub_convert(monkeypatch)
    out = json.loads(tb.extract_document(str(FIXTURE)))
    assert out["jats_source"] == "local"
    assert out["n_tables"] == 2


def test_jats_local_xml_not_article_falls_through(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    feed = tmp_path / "feed.xml"
    feed.write_text("<rss><channel><title>t</title></channel></rss>", encoding="utf-8")
    def _boom(*a, **k):
        raise RuntimeError("JATS error: not a JATS article (root=rss)")
    monkeypatch.setattr(J, "convert_jats_bytes", _boom)
    out = json.loads(tb.extract_document(str(feed)))
    # Falls through to the normal XML feed path, which delivers entries/raw.
    assert out.get("jats_source") is None
    assert out["content"]


# ── flag matrix (§3.5) ──

def test_jats_pages_with_doi_errors(tmp_path):
    tb = make_tb(tmp_path)
    out = json.loads(tb.extract_document(CHEM_DOI, pages="10-20", tables_as="markdown"))
    assert "error" in out and "prefer-jats" in out["error"].lower() or "JATS" in out["error"]


def test_jats_structured_skips_route(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    calls = []
    stub_fetch(tb, {}, counter=calls)
    stub_convert(monkeypatch)
    tb.extract_document(CHEM_DOI, structured=True)
    assert "europepmc" not in "".join(calls)


def test_jats_tables_json_skips_spreadsheet(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    calls = []
    stub_fetch(tb, {}, counter=calls)
    stub_convert(monkeypatch)
    monkeypatch.setattr(tb._doc, "_download_and_extract",
                        lambda *a, **k: ("sheet!", {"content_type": "x"}))
    out = json.loads(tb.extract_document("https://example.com/wb.xlsx", tables_as="json"))
    assert out.get("jats_source") is None
    assert not [u for u in calls if "europepmc" in u]


def test_jats_store_writes_xml_and_md(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    xml = FIXTURE.read_bytes()
    stub_fetch(tb, {
        "europepmc": (europepmc_search_bytes(), "application/json", 200),
        "fullTextXML": (xml, "application/xml", 200),
    })
    stub_convert(monkeypatch)
    store_dir = tmp_path / "stored"
    out = json.loads(tb.extract_document(
        CHEM_DOI, tables_as="markdown", store=True, store_dir=str(store_dir)))
    stored = out["stored"]
    assert Path(stored["original"]).suffix == ".xml"
    assert Path(stored["markdown"]).suffix == ".md"
    assert Path(stored["original"]).is_file() and Path(stored["markdown"]).is_file()


def test_jats_include_images_noop_note(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    xml = FIXTURE.read_bytes()
    stub_fetch(tb, {
        "europepmc": (europepmc_search_bytes(), "application/json", 200),
        "fullTextXML": (xml, "application/xml", 200),
    })
    stub_convert(monkeypatch)
    out = json.loads(tb.extract_document(
        CHEM_DOI, tables_as="markdown", store=True,
        store_dir=str(tmp_path / "s"), include_images=True))
    assert out["stored"]["resources"]["note"] == "JATS figures are remote hrefs, not saved"


def test_jats_cache_hit_keeps_provenance(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    xml = FIXTURE.read_bytes()
    stub_fetch(tb, {
        "europepmc": (europepmc_search_bytes(), "application/json", 200),
        "fullTextXML": (xml, "application/xml", 200),
    })
    stub_convert(monkeypatch)
    first = json.loads(tb.extract_document(CHEM_DOI, tables_as="markdown"))
    assert first["cache_hit"] is False
    # Second read: resolver stubbed to explode — must come from cache.
    def _boom(url):
        raise AssertionError(f"network on cache hit: {url}")
    tb._doc._fetch_document_url = _boom  # noqa: SLF001
    second = json.loads(tb.extract_document(CHEM_DOI, tables_as="markdown"))
    assert second["cache_hit"] is True
    assert second["jats_source"] == "europepmc"
    assert second["n_formulas"] == 2


def test_jats_prefer_false_skips_route(tmp_path, monkeypatch):
    tb = make_tb(tmp_path)
    tb._robots.is_allowed = lambda url: True  # noqa: SLF001
    calls = []
    stub_fetch(tb, {}, counter=calls)
    stub_convert(monkeypatch)
    monkeypatch.setattr(tb._doc, "_download_and_extract",
                        lambda *a, **k: ("pdf text", {"content_type": "application/pdf"}))
    out = json.loads(tb.extract_document(
        "https://example.com/paper.pdf", prefer_jats=False))
    assert out.get("jats_source") is None
    assert not [u for u in calls if "europepmc" in u]


# ── real conversion (bobine installed; no network, no models) ──

def test_jats_real_conversion_matches_handoff_census():
    bobine = pytest.importorskip("bobine")  # noqa: F841
    out = J.convert_jats_bytes(FIXTURE.read_bytes(), FIXTURE.name)
    assert out["title"].startswith("Olive leaf extract-assisted green synthesis")
    assert (out["n_formulas"], out["n_figures"], out["n_tables"]) == (2, 7, 2)
    md = out["markdown"]
    assert md.startswith("# Olive leaf")
    assert "## Methods" in md
    assert md.count("$$") == 4  # two display blocks, labels verbatim per §2.3
    # NOTE: literal backslash-t-a-g below — do not "simplify" this into a
    # shell one-liner: \t mangles into TAB there and the assertion silently
    # passes on zero hits (bitten once already; the \tag IS in the output).
    assert "\\tag{Eq 1}$$" in md and "\\tag{Eq 2}$$" in md
    assert md.count("$") % 2 == 0  # balanced dollars (check_dollars guarantee)
    import re as _re

    assert len([line for line in md.splitlines() if line.startswith("|")]) >= 10  # GFM tables
    assert len(_re.findall(r"!\[", md)) == 7  # remote figure hrefs


# ── live-marked (network + bobine; never gate CI) ──

@pytest.mark.slow
def test_jats_live_europepmc_shape(tmp_path):
    pytest.importorskip("bobine")
    tb = make_tb(tmp_path)
    out = json.loads(tb.extract_document(CHEM_DOI, tables_as="markdown"))
    assert out.get("jats_source") in ("europepmc", "pmc-oa", "plos") or "error" in out
