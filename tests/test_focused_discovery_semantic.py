"""Semantic crawl scoring (v0.4.8, features A + B).

BM25/IDF frontier scoring over the pages fetched so far, anchor
context, URL path priors, and offline thesaurus query expansion.
Unit tests hit the pure helpers directly (no fetches); the crawl-level
tests use the same hermetic fake-fetch pattern as test_crawl.py.
"""

from __future__ import annotations

import json

import pytest

from gossamer import agent_tools
from gossamer.crawl import Crawler
from gossamer.agent_tools import (
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
    out = tb.focused_discovery(root_url=kwargs.pop("root_url", ROOT), **kwargs)
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
        s_rare = Crawler._crawl_score(
            "https://example.com/rare", "rare", 1, Q, set(), corpus=corpus)
        s_common = Crawler._crawl_score(
            "https://example.com/common", "common", 1, Q, set(), corpus=corpus)
        assert s_rare > s_common

    def test_uniform_weights_until_min_corpus(self):
        corpus = _CrawlCorpus(min_corpus=3)
        corpus.add_page({"alpha"})  # n=1 -> flat weights, no path prior
        Q = {"alpha", "beta"}
        s = Crawler._crawl_score(
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
        s3 = Crawler._crawl_score(
            url, "alpha", 1, Q, set(), corpus=c3)
        c6 = _CrawlCorpus(min_corpus=3)
        for _ in range(6):
            c6.add_page({"alpha", "zeta"})
        s6 = Crawler._crawl_score(
            url, "alpha", 1, Q, set(), corpus=c6)
        # alpha is more common in the grown corpus -> weaker match.
        assert s6 < s3

    def test_legacy_path_is_exact_v046(self):
        url = "https://example.com/a"
        Q = {"deep", "learning"}
        P = {"deep", "learning", "platform"}
        s = Crawler._crawl_score(url, "Deep learning guide", 1, Q, P)
        # label {deep, learning, guide}; query_cov 2/2 -> 0.7;
        # ctx_cov 2/3 -> 0.2.
        assert s == pytest.approx(0.7 * 1.0 + 0.3 * (2 / 3))


class TestAnchorContext:
    def test_anchor_context_lifts_relevant_links(self):
        md = "A quiet page with one useful sentence about deep learning."
        ctx = Crawler._crawl_anchor_context(md, "useful sentence")
        assert {"deep", "learning"} <= ctx
        Q = {"deep", "learning"}
        s_plain = Crawler._crawl_score(
            "https://example.com/x", "guide", 1, Q, set())
        s_ctx = Crawler._crawl_score(
            "https://example.com/x", "guide", 1, Q, set(), label_extra=ctx)
        assert s_ctx > s_plain

    def test_anchor_context_empty_when_anchor_absent(self):
        ctx = Crawler._crawl_anchor_context(
            "nothing matches this label at all.", "absent label")
        assert ctx == frozenset()

    def test_anchor_context_capped_at_eight(self):
        words = " ".join(f"w{i}" for i in range(30))
        md = "Start padding. " + words + " End padding."
        ctx = Crawler._crawl_anchor_context(md, "w15")
        assert len(ctx) == 8  # the 30+ window tokens are capped


class TestPathPriors:
    def test_path_prior_table(self):
        T = Crawler
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
        s_docs = Crawler._crawl_score(
            "https://example.com/docs/deep", "x", 1, Q, set(), corpus=corpus)
        s_neutral = Crawler._crawl_score(
            "https://example.com/deep/docs", "x", 1, Q, set(), corpus=corpus)
        # Identical label token sets -> identical scores while degenerate.
        assert s_docs == pytest.approx(s_neutral)
        corpus.add_page({"beta"})
        corpus.add_page({"gamma"})  # n=3: prior kicks in
        s_docs = Crawler._crawl_score(
            "https://example.com/docs/deep", "x", 1, Q, set(), corpus=corpus)
        s_neutral = Crawler._crawl_score(
            "https://example.com/deep/docs", "x", 1, Q, set(), corpus=corpus)
        assert s_docs == pytest.approx(1.15 * s_neutral)


# ── B: offline thesaurus ──────────────────────────────────


class TestThesaurus:
    def test_expansion_capped_and_deterministic(self):
        T = Crawler
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
        s_base = Crawler._crawl_score(
            "https://example.com/b", "base", 1, Q, set(),
            corpus=corpus, base_terms={"base"})
        s_exp = Crawler._crawl_score(
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
            expanded, added = Crawler._crawl_expand_query({"deep"})
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
        tb._fetch._fetch_html = _fake_fetch({
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
        tb._fetch._fetch_html = _fake_fetch({
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
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nnotes only.\n")})
        parsed = _result(tb, query="notes")
        # "notes" is curation-excluded from the thesaurus -> no additions.
        assert parsed["query"] == "notes"


# ── C: richness payload + opt-in excerpts ─────────────────


class TestRichnessPayload:
    def test_content_chars_and_term_hits(self, tmp_path):
        child_md = "deep learning platform. deep deep.\n"
        P1 = "https://example.com/p1"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\ndeep learning hub.\n",
                        links=[(P1, "deep learning guide")], title="Hub"),
            P1: _page(child_md),
        })
        parsed = _result(tb, query="deep learning")
        root_rec = parsed["pages"][0]
        child_rec = [p for p in parsed["pages"] if p["url"] == P1][0]
        assert root_rec["content_chars"] == len("# Hub\n\ndeep learning hub.\n")
        # root body: deep x1 + learning x1 (expansions neural/nets: 0)
        assert root_rec["term_hits"] == 2
        assert child_rec["content_chars"] == len(child_md)
        # child body: deep x3 + learning x1 = 4 occurrences
        assert child_rec["term_hits"] == 4

    def test_excerpts_default_off_shape(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch(
            {ROOT: _page("# Hub\n\ndeep learning platform.\n")})
        parsed = _result(tb, query="deep learning")
        assert parsed["excerpts"] is False
        for p in parsed["pages"]:
            assert "excerpt" not in p
            assert "content_chars" in p
            assert "term_hits" in p


class TestExcerpts:
    def test_excerpt_is_densest_window(self, tmp_path):
        pad = "quiet filler text " * 40
        block = "deep learning deep learning deep learning " * 10
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page(pad + block + pad)})
        parsed = _result(tb, query="deep learning", excerpts=True)
        ex = parsed["pages"][0]["excerpt"]
        # the densest window sits inside the mid-page keyword block
        # (a padding window would carry zero query terms)
        assert ex.count("deep") >= 15
        assert ex.startswith("\u2026")
        assert ex.endswith("\u2026")

    def test_excerpt_full_coverage_has_no_ellipsis(self, tmp_path):
        md = "deep learning notes " * 5  # shorter than one window
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page(md)})
        parsed = _result(tb, query="deep learning", excerpts=True)
        assert parsed["pages"][0]["excerpt"] == md

    def test_excerpt_zero_density_absent(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch(
            {ROOT: _page("# Hub\n\nnothing relevant here at all.\n")})
        parsed = _result(tb, query="deep learning", excerpts=True)
        assert "excerpt" not in parsed["pages"][0]

    def test_excerpt_unit_tie_earliest_and_bounds(self):
        T = Crawler
        md = "a " * 50 + "deep " * 5 + "b " * 50 + "deep " * 5 + "c " * 50
        # Two windows tie at 10 hits -> the earliest window wins.
        ex = T._crawl_excerpt(md, {"deep"})
        assert ex == md[:300] + "\u2026"
        # A single window covering the whole text carries no ellipsis.
        assert T._crawl_excerpt("deep deep", {"deep"}) == "deep deep"
        # Zero density -> None.
        assert T._crawl_excerpt("no terms here", {"deep"}) is None


class TestBudgetWithRichness:
    def test_richness_and_excerpts_still_fit(self, tmp_path):
        # M11 regression: with stats and excerpts on, the payload either
        # fits whole or the tail-drop shrink keeps it valid and in budget.
        pages = {ROOT: _page(
            "# Hub\n\nplatform platform platform.\n",
            links=[(f"https://example.com/p{i}", "platform")
                   for i in range(4)])}
        for i in range(4):
            pages[f"https://example.com/p{i}"] = _page("platform " * 100)
        tb = _toolbox(tmp_path, max_markdown_chars=3000)
        tb._fetch._fetch_html = _fake_fetch(pages)
        parsed = _result(tb, max_pages=5, excerpts=True)
        raw = json.dumps(parsed)
        assert len(raw) <= 3000 + 128  # slack for re-serialization
        assert "pages" in parsed
        for p in parsed["pages"]:
            assert "content_chars" in p
            assert "term_hits" in p
        # a generous budget keeps the full payload, excerpts included
        tb2 = _toolbox(tmp_path, max_markdown_chars=8000)
        tb2._fetch._fetch_html = _fake_fetch(pages)
        full = _result(tb2, max_pages=5, excerpts=True)
        assert full["count"] == 5
        assert all("excerpt" in p for p in full["pages"])


# ── E1: search prior + E2: seed URLs + E3: cross-modal loop ──


class _SearchFake:
    """Minimal search-provider stand-in (records every search call)."""

    def __init__(self, results=None, exc=None):
        self.name = "fake"
        self._results = results or []
        self._exc = exc
        self.calls = []

    def search(self, query, max_results=5):
        self.calls.append(query)
        if self._exc is not None:
            raise self._exc
        return [dict(r) for r in self._results][:max_results]


def _search_results(*items):
    return [{"title": t, "url": u, "snippet": "snippet text"} for t, u in items]


class TestSearchPrior:
    def test_off_by_default(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("deep learning platform")})
        parsed = _result(tb, query="deep learning")
        assert parsed["search_prior"] is False
        assert "search_results" not in parsed

    def test_prior_feeds_frontier_in_rank_order(self, tmp_path):
        S1 = "https://example.com/s1"
        S2 = "https://example.com/s2"
        tb = _toolbox(tmp_path)
        prov = _SearchFake(_search_results(("s1", S1), ("s2", S2)))
        tb.providers = [prov]
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("deep learning platform", links=[]),
            S1: _page("deep learning overview.\n"),
            S2: _page("deep learning basics.\n"),
        })
        parsed = _result(tb, query="deep learning", search_prior=True)
        assert parsed["search_prior"] is True
        assert parsed["search_results"] == 2
        depths = {p["url"]: p["depth"] for p in parsed["pages"]}
        assert depths[S1] == 1
        assert depths[S2] == 1
        # Flat scores: the rank bonus (+0.1/1 > +0.1/2) decides the order.
        urls = _fetched_urls(parsed)
        assert urls.index(S1) < urls.index(S2)
        assert len(prov.calls) == 1
        assert prov.calls[0].startswith("site:example.com")

    def test_prior_floor_exempt(self, tmp_path):
        S1 = "https://example.com/irrelevant"
        tb = _toolbox(tmp_path)
        tb.providers = [_SearchFake(_search_results(("x", S1)))]
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("deep learning platform", links=[]),
            S1: _page("totally unrelated content.\n"),
        })
        parsed = _result(tb, query="deep learning", search_prior=True)
        # Score-0 candidate: the min_score floor does not apply to priors.
        assert S1 in _fetched_urls(parsed)

    def test_prior_provider_failure_is_fail_open(self, tmp_path):
        P1 = "https://example.com/p1"
        tb = _toolbox(tmp_path)
        tb.providers = [_SearchFake(exc=RuntimeError("boom"))]
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("deep learning platform",
                        links=[(P1, "deep learning guide")]),
            P1: _page("deep content.\n"),
        })
        parsed = _result(tb, query="deep learning", search_prior=True)
        # No crawl-level errors; the link-graph crawl completes as usual.
        assert parsed["errors"] == []
        assert P1 in _fetched_urls(parsed)
        assert parsed["search_results"] == 0

    def test_prior_error_payload_is_fail_open(self, tmp_path):
        tb = _toolbox(tmp_path)

        def fake_search(query, max_results=5, provider=None):
            return json.dumps({"error": "all providers failed"})

        tb.search_web = fake_search
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("deep learning platform")})
        parsed = _result(tb, query="deep learning", search_prior=True)
        assert parsed["search_results"] == 0
        assert parsed["errors"] == []

    def test_prior_document_result_routed_to_documents(self, tmp_path):
        PDF = "https://example.com/report.pdf"
        tb = _toolbox(tmp_path)
        tb.providers = [
            _SearchFake(_search_results(("deep learning report", PDF)))
        ]
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("deep learning platform")})
        parsed = _result(tb, query="deep learning", search_prior=True)
        # The document is collected (scored, floored) — never fetched.
        assert [d["url"] for d in parsed["documents"]] == [PDF]
        assert PDF not in _fetched_urls(parsed)

    def test_prior_repeat_crawl_reuses_search_cache(self, tmp_path):
        S1 = "https://example.com/s1"
        tb = _toolbox(tmp_path)
        prov = _SearchFake(_search_results(("s1", S1)))
        tb.providers = [prov]
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("deep learning platform", links=[]),
            S1: _page("deep learning overview.\n"),
        })
        p1 = _result(tb, query="deep learning", search_prior=True)
        p2 = _result(tb, query="deep learning", search_prior=True)
        # Tier 2.8 in-memory search cache: provider queried once, ever.
        assert len(prov.calls) == 1
        assert p1["search_results"] == 1
        assert p2["search_results"] == 1


class TestSeedUrls:
    def test_seed_fetched_depth_zero_children_depth_one(self, tmp_path):
        SEED = "https://example.com/deep/seed"
        CHILD = "https://example.com/deep/child"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("deep learning platform", links=[]),
            SEED: _page("deep learning seed page.",
                        links=[(CHILD, "deep learning child")]),
            CHILD: _page("deep learning child page.\n"),
        })
        parsed = _result(tb, query="deep learning", seed_urls=[SEED])
        depths = {p["url"]: p["depth"] for p in parsed["pages"]}
        assert depths[SEED] == 0
        assert depths[CHILD] == 1
        assert parsed["errors"] == []

    def test_seed_equal_to_root_is_a_noop(self, tmp_path):
        tb = _toolbox(tmp_path)
        fake = _fake_fetch({ROOT: _page("deep learning platform")})
        tb._fetch._fetch_html = fake
        parsed = _result(tb, query="deep learning", seed_urls=[ROOT])
        assert fake.state["calls"].count(ROOT) == 1
        assert parsed["count"] == 1

    def test_seed_below_floor_skipped_with_reason(self, tmp_path):
        SEED = "https://example.com/irrelevant"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("deep learning platform", links=[]),
            SEED: _page("irrelevant page.\n"),
        })
        parsed = _result(tb, query="deep learning", seed_urls=[SEED])
        skipped = {s["url"]: s["reason"] for s in parsed["skipped"]}
        assert skipped.get(SEED) == "seed below min score"
        assert SEED not in _fetched_urls(parsed)

    def test_seed_external_host_skipped(self, tmp_path):
        EXT = "https://example.org/deep"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("deep learning platform")})
        parsed = _result(
            tb, query="deep learning", same_host=True, seed_urls=[EXT]
        )
        skipped = {s["url"]: s["reason"] for s in parsed["skipped"]}
        assert skipped.get(EXT) == "external host"

    def test_seed_fetch_failure_is_non_fatal(self, tmp_path):
        BAD = "https://example.com/deep/bad"
        GOOD = "https://example.com/deep/good"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch(
            {
                ROOT: _page("deep learning platform", links=[]),
                GOOD: _page("deep learning good page.\n"),
            },
            fail_on={BAD},
        )
        parsed = _result(
            tb, query="deep learning", seed_urls=[BAD, GOOD]
        )
        assert any(e["url"] == BAD for e in parsed["errors"])
        assert GOOD in _fetched_urls(parsed)

    def test_seed_ssrf_blocked(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GOSSAMER_ALLOW_PRIVATE", raising=False)
        META = "http://169.254.169.254/latest/meta-data"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("deep learning platform")})
        parsed = _result(tb, query="deep learning", seed_urls=[META])
        skipped = {s["url"]: s["reason"] for s in parsed["skipped"]}
        assert skipped.get(META) == "ssrf blocked"
        assert parsed["errors"] == []


class TestCrossModalLoop:
    def test_document_links_feed_next_crawl(self, tmp_path):
        # E3 (pattern, no mechanism): crawl -> documents -> agent reads the
        # document (extract_document surfaces its internal links) -> next
        # crawl seeded with those links.
        PDF = "https://example.com/report.pdf"
        U1 = "https://example.com/deep/one"
        U2 = "https://example.com/deep/two"
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("deep learning platform",
                        links=[(PDF, "deep learning annual report")]),
            U1: _page("deep learning detail one.\n"),
            U2: _page("deep learning detail two.\n"),
        })
        first = _result(tb, query="deep learning")
        assert [d["url"] for d in first["documents"]] == [PDF]
        # Simulate the extract_document step surfacing the links the PDF
        # contains (v0.4.5 text link detection).
        extracted = [U1, U2]
        second = _result(tb, query="deep learning", seed_urls=extracted)
        depths = {p["url"]: p["depth"] for p in second["pages"]}
        assert depths[U1] == 0
        assert depths[U2] == 0
        assert second["errors"] == []
