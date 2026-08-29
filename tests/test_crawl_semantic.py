"""Semantic crawl scoring (v0.4.8, features A + B).

BM25/IDF frontier scoring over the pages fetched so far, anchor
context, URL path priors, and offline thesaurus query expansion.
Unit tests hit the pure helpers directly (no fetches); the crawl-level
tests use the same hermetic fake-fetch pattern as test_crawl.py.
"""

from __future__ import annotations

import json

import pytest

from stitch_web_researcher import agent_tools
from stitch_web_researcher.agent_tools import (
    ToolboxConfig,
    WebResearcherToolbox,
    _CrawlCorpus,
    _load_thesaurus,
)

ROOT = "https://example.com/"


def _toolbox(tmp_path, **config_kwargs):
    return WebResearcherToolbox(config=ToolboxConfig(
        respect_robots=False,
        domain_delay=0.0,
        fetch_delay=0.0,
        ddgs_delay=0.0,
        cache_dir=str(tmp_path / "cache"),
        **config_kwargs,
    ))


def _page(md, links=(), title=""):
    meta = {"meta": {"title": title}} if title else {}
    return (md, list(links), meta, "static")


def _fake_fetch(pages, fail_on=()):
    state = {"calls": []}

    def fake(url, use_smart=None):
        state["calls"].append(url)
        if url in fail_on:
            raise RuntimeError(f"connection refused: {url}")
        if url not in pages:
            raise RuntimeError(f"connection refused: {url}")
        return pages[url]

    fake.state = state
    return fake


def _result(tb, **kwargs):
    out = tb.crawl(root_url=kwargs.pop("root_url", ROOT), **kwargs)
    parsed = json.loads(out)
    assert "error" not in parsed, parsed
    return parsed


def _fetched_urls(parsed):
    return [p["url"] for p in parsed["pages"]]


# ── A: BM25/IDF, anchor context, path priors ──────────────


class TestIdfScoring:
    def test_rare_term_outweighs_common_term(self):
        corpus = _CrawlCorpus(min_corpus=3)
        corpus.add_page({"common", "alpha"})
        corpus.add_page({"common", "beta"})
        corpus.add_page({"common", "rare"})
        assert corpus.idf("rare") > corpus.idf("common")
        Q = {"rare", "common"}
        s_rare = WebResearcherToolbox._crawl_score(
            "https://example.com/rare", "rare", 1, Q, set(), corpus=corpus)
        s_common = WebResearcherToolbox._crawl_score(
            "https://example.com/common", "common", 1, Q, set(), corpus=corpus)
        assert s_rare > s_common

    def test_uniform_weights_until_min_corpus(self):
        corpus = _CrawlCorpus(min_corpus=3)
        corpus.add_page({"alpha"})  # n=1 -> flat weights, no path prior
        Q = {"alpha", "beta"}
        s = WebResearcherToolbox._crawl_score(
            "https://example.com/alpha", "alpha", 1, Q, {"alpha"},
            corpus=corpus)
        # Exact flat-weight recomputation of the v0.4.6 formula:
        # label {alpha}; query_cov 1/2 -> 0.35; ctx_cov 1/1 -> 0.3.
        assert s == pytest.approx(0.7 * 0.5 + 0.3 * 1.0)

    def test_idf_sharpens_as_corpus_grows(self):
        Q = {"alpha", "beta"}
        url = "https://example.com/alpha"
        c3 = _CrawlCorpus(min_corpus=3)
        for _ in range(3):
            c3.add_page({"alpha", "zeta"})
        s3 = WebResearcherToolbox._crawl_score(
            url, "alpha", 1, Q, set(), corpus=c3)
        c6 = _CrawlCorpus(min_corpus=3)
        for _ in range(6):
            c6.add_page({"alpha", "zeta"})
        s6 = WebResearcherToolbox._crawl_score(
            url, "alpha", 1, Q, set(), corpus=c6)
        # alpha is more common in the grown corpus -> weaker match.
        assert s6 < s3

    def test_legacy_path_is_exact_v046(self):
        url = "https://example.com/a"
        Q = {"deep", "learning"}
        P = {"deep", "learning", "platform"}
        s = WebResearcherToolbox._crawl_score(url, "Deep learning guide", 1, Q, P)
        # label {deep, learning, guide}; query_cov 2/2 -> 0.7;
        # ctx_cov 2/3 -> 0.2.
        assert s == pytest.approx(0.7 * 1.0 + 0.3 * (2 / 3))


class TestAnchorContext:
    def test_anchor_context_lifts_relevant_links(self):
        md = "A quiet page with one useful sentence about deep learning."
        ctx = WebResearcherToolbox._crawl_anchor_context(md, "useful sentence")
        assert {"deep", "learning"} <= ctx
        Q = {"deep", "learning"}
        s_plain = WebResearcherToolbox._crawl_score(
            "https://example.com/x", "guide", 1, Q, set())
        s_ctx = WebResearcherToolbox._crawl_score(
            "https://example.com/x", "guide", 1, Q, set(), label_extra=ctx)
        assert s_ctx > s_plain

    def test_anchor_context_empty_when_anchor_absent(self):
        ctx = WebResearcherToolbox._crawl_anchor_context(
            "nothing matches this label at all.", "absent label")
        assert ctx == frozenset()

    def test_anchor_context_capped_at_eight(self):
        words = " ".join(f"w{i}" for i in range(30))
        md = "Start padding. " + words + " End padding."
        ctx = WebResearcherToolbox._crawl_anchor_context(md, "w15")
        assert len(ctx) == 8  # the 30+ window tokens are capped


class TestPathPriors:
    def test_path_prior_table(self):
        T = WebResearcherToolbox
        for path in ("/docs/", "/guide/", "/guides/", "/blog/", "/api/",
                     "/changelog/", "/reference/"):
            assert T._crawl_path_prior(f"https://example.com{path}x") == 1.15
        for path in ("/pricing", "/careers", "/contact", "/about"):
            assert T._crawl_path_prior(f"https://example.com{path}") == 0.85
        assert T._crawl_path_prior("https://example.com/other/page") == 1.0

    def test_path_prior_gated_by_corpus_size(self):
        Q = {"deep"}
        corpus = _CrawlCorpus(min_corpus=3)
        corpus.add_page({"alpha"})  # n=1: flat regime, no prior
        s_docs = WebResearcherToolbox._crawl_score(
            "https://example.com/docs/deep", "x", 1, Q, set(), corpus=corpus)
        s_neutral = WebResearcherToolbox._crawl_score(
            "https://example.com/deep/docs", "x", 1, Q, set(), corpus=corpus)
        # Identical label token sets -> identical scores while degenerate.
        assert s_docs == pytest.approx(s_neutral)
        corpus.add_page({"beta"})
        corpus.add_page({"gamma"})  # n=3: prior kicks in
        s_docs = WebResearcherToolbox._crawl_score(
            "https://example.com/docs/deep", "x", 1, Q, set(), corpus=corpus)
        s_neutral = WebResearcherToolbox._crawl_score(
            "https://example.com/deep/docs", "x", 1, Q, set(), corpus=corpus)
        assert s_docs == pytest.approx(1.15 * s_neutral)


# ── B: offline thesaurus ──────────────────────────────────


class TestThesaurus:
    def test_expansion_capped_and_deterministic(self):
        T = WebResearcherToolbox
        clusters = (("alpha", "beta", "gamma", "delta", "epsilon", "zeta"),)
        expanded, added = T._crawl_expand_query({"alpha"}, clusters=clusters)
        # Cap: additions <= len(base) -> total never exceeds 2x base.
        assert added == 1
        assert expanded == {"alpha", "beta"}
        # Members are taken in cluster order, starting after the match.
        expanded2, added2 = T._crawl_expand_query({"zeta"}, clusters=clusters)
        assert (expanded2, added2) == ({"zeta", "alpha"}, 1)
        # Empty base is a no-op; unmatched base adds nothing.
        assert T._crawl_expand_query(set(), clusters=clusters) == (set(), 0)
        assert T._crawl_expand_query({"qq"}, clusters=clusters) == ({"qq"}, 0)

    def test_base_terms_outweigh_expansions(self):
        corpus = _CrawlCorpus(min_corpus=3)
        for _ in range(3):
            corpus.add_page({"u1"})
        Q = {"base", "exp"}  # "exp" is a thesaurus expansion
        s_base = WebResearcherToolbox._crawl_score(
            "https://example.com/b", "base", 1, Q, set(),
            corpus=corpus, base_terms={"base"})
        s_exp = WebResearcherToolbox._crawl_score(
            "https://example.com/e", "exp", 1, Q, set(),
            corpus=corpus, base_terms={"base"})
        assert s_base > s_exp

    def test_thesaurus_file_shape(self):
        _load_thesaurus.cache_clear()
        version, clusters = _load_thesaurus()
        _load_thesaurus.cache_clear()
        assert version == 1
        assert len(clusters) >= 30
        # Ultra-generic tokens are curation-excluded (they would dilute
        # half-weighted expansion on incidental text); this is what keeps
        # derived-query crawls discriminating.
        generic = {
            "platform", "note", "notes", "company", "guide", "guides",
            "page", "pages", "hub", "data", "cloud", "api", "apis",
            "search", "test", "testing", "index", "end", "front",
            "about", "contact", "us", "team", "here", "stuff", "details",
        }
        all_terms: set = set()
        for cluster in clusters:
            assert cluster  # no empty clusters
            for term in cluster:
                assert term == term.lower()
                assert term.isalnum()  # bare tokens, no phrases
                all_terms.add(term)
        assert not (all_terms & generic)
        assert len(all_terms) >= 150

    def test_thesaurus_fail_open(self, monkeypatch):
        _load_thesaurus.cache_clear()

        def broken(_path):
            raise FileNotFoundError(_path)

        monkeypatch.setattr(agent_tools.Path, "read_text", broken)
        try:
            version, clusters = _load_thesaurus()
            assert (version, clusters) == (0, ())
            # With no thesaurus, expansion is a no-op.
            expanded, added = WebResearcherToolbox._crawl_expand_query({"deep"})
            assert (expanded, added) == ({"deep"}, 0)
        finally:
            # The fail-open value is lru-cached for the process lifetime;
            # never leave it behind for later tests.
            _load_thesaurus.cache_clear()


class TestSemanticCrawlEndToEnd:
    def test_paraphrase_query_reaches_synonym_page(self, tmp_path):
        P1 = "https://example.com/p1"
        P2 = "https://example.com/p2"
        tb = _toolbox(tmp_path)
        tb._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\noverview of the site.\n", links=[
                (P1, "deep learning guide"), (P2, "about us")]),
            P1: _page("deep content.\n"),
        })
        parsed = _result(tb, query="neural nets")
        # The thesaurus bridges "neural nets" -> deep/learning.
        assert P1 in _fetched_urls(parsed)
        skipped = {s["url"]: s["reason"] for s in parsed["skipped"]}
        assert skipped.get(P2) == "below min score"
        assert parsed["query"] == "neural nets +2"

    def test_derived_query_expands_and_echoes(self, tmp_path):
        P1 = "https://example.com/p1"
        tb = _toolbox(tmp_path)
        tb._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nneural nets overview.\n",
                        links=[(P1, "deep learning guide")]),
            P1: _page("content.\n"),
        })
        parsed = _result(tb)
        assert parsed["query"].startswith("derived from root page +")
        assert int(parsed["query"].rsplit("+", 1)[1]) > 0
        assert P1 in _fetched_urls(parsed)

    def test_expanded_query_echo_reports_additions(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nnotes only.\n")})
        parsed = _result(tb, query="notes")
        # "notes" is curation-excluded from the thesaurus -> no additions.
        assert parsed["query"] == "notes"
