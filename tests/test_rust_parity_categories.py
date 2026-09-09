"""Parity: ``research_categories.classify`` (v0.8.4) vs ``src/categories.rs``.

Three layers: (1) every Python keyword in isolation must route
identically through both implementations — this doubles as the
table-drift guard (a keyword added/removed/retargeted on either side
fails here); (2) hand-picked mixed queries incl. ties; (3) a seeded
fuzzer over keyword salads with case/punctuation mangling.
"""

import random

import pytest

from gossamer import _core
from gossamer import research_categories as rc


def _all_keywords():
    for cat in rc.CATEGORIES:
        if cat.keywords:
            for kw in cat.keywords:
                yield cat.name, kw


def test_table_sizes_match():
    # Per-category counts pin additions/removals to the right table.
    sizes = {cat.name: len(cat.keywords) for cat in rc.CATEGORIES}
    assert sizes == {
        "scholarly": 25, "legal": 38, "patent": 18,
        "financial": 45, "geo": 21, "general": 0,
    }


@pytest.mark.parametrize("category,kw", list(_all_keywords()))
def test_keyword_isolation_parity(category, kw):
    want = rc.classify(kw).name
    got = _core.classify_query(kw)
    assert got == want == category, f"keyword {kw!r}: py={want} rs={got}"


QUERIES = [
    "quantum computing patent",
    "EPO patent search ti=quantum",
    "prior art search KIPRIS",
    "patent infringement lawsuit damages",
    "BVerfG patent case",
    "pct of revenue grew",
    "filed an insurance claim",
    "stock photos of bill murray",
    "Dax Aktienkurs Dividende",
    "WEATHER Berlin morgen",
    "peer-reviewed journal paper on arxiv with doi",
    "supreme court appeal precedent",
    "euro-zone leitzins euribor geldpolitik",
    "heat wave coordinates rainfall",
    "menschenrechte egmr urteil",
    "",
    "   ",
    "a",
    "the",
    "???",
    "12345",
    "BVerfG-Urteil zum Eilantrag",
    "case-law regulation?",
    "(open access) [preprint]",
]


@pytest.mark.parametrize("query", QUERIES)
def test_mixed_query_parity(query):
    assert _core.classify_query(query) == rc.classify(query).name


def test_none_and_case_parity():
    assert _core.classify_query(None) == rc.classify(None).name == "general"
    assert _core.classify_query("PATENT") == "patent"
    assert _core.classify_query("  Patent  ") == "patent"


def test_fuzz_classify_parity():
    rng = random.Random(20260905)
    kws = [kw for _, kw in _all_keywords()]
    noise = ["the", "of", "hello", "x1", "foo-bar", "über", "123",
             "!!!", "and/or", "(x)", "dr.", "co."]
    for _ in range(500):
        bits = [rng.choice(kws) for _ in range(rng.randint(1, 4))]
        bits += [rng.choice(noise) for _ in range(rng.randint(0, 3))]
        rng.shuffle(bits)
        query = " ".join(bits)
        if rng.random() < 0.3:
            query = query.upper()
        if rng.random() < 0.2:
            query = query.replace(" ", rng.choice(["  ", ", ", " - ", " / "]))
        assert _core.classify_query(query) == rc.classify(query).name, query
