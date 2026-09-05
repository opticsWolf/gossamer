"""Focused crawl (deep-research support).

A bounded best-first crawl over the link graph: the frontier is ranked
by relevance (score x 0.7^depth), so the page budget goes to the most
relevant links. All tests are deterministic: the fetch seam is faked
and every URL is an example.com path (the only apex that resolves in
offline test environments; _validate_url does real SSRF checks).
"""

from __future__ import annotations

import json

import pytest

from gossamer.agent_tools import (
    TOOL_REGISTRY,
    ToolboxConfig,
    WebResearcherToolbox,
)

ROOT = "https://example.com/"
HOME = "https://example.com/"

A = "https://example.com/a"
A1 = "https://example.com/a/1"
A2 = "https://example.com/a/2"
B = "https://example.com/b"
PDF = "https://example.com/report.pdf"


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
    """Fake fetch 4-tuple: (markdown, links, meta, method)."""
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


class TestRootHandling:
    def test_root_fetch_failure_kills_crawl(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({}, fail_on=())  # root not in pages
        parsed = json.loads(tb.focused_discovery(root_url=ROOT))
        assert "root fetch failed" in parsed["error"]

    def test_root_fetch_failure_returns_error_dict(self, tmp_path):
        """The impl reports robots/visited failures as {warning} dicts,
        not exceptions — the crawl must classify them as root failures."""
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({})

        def impl(url, use_smart, query, offset, max_chunks, politeness_root=None):
            return json.dumps(
                {"warning": "URL disallowed by robots.txt", "url": url}
            )

        tb._fetch._inspect_html_page_impl = impl
        parsed = json.loads(tb.focused_discovery(root_url=ROOT))
        assert "robots" in parsed["error"]

    def test_invalid_root_urls(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({})
        for bad in ("not a url", "ftp://example.com", "javascript:alert(1)"):
            parsed = json.loads(tb.focused_discovery(root_url=bad))
            assert "error" in parsed

    def test_root_only_when_max_depth_zero(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform deep learning notes.\n",
                        links=[(A, "Deep learning guide")]),
        })
        parsed = _result(tb, max_depth=0)
        assert parsed["count"] == 1
        assert parsed["pages"][0]["depth"] == 0
        assert parsed["stop"] == "frontier exhausted"


class TestRelevanceFrontier:
    def test_relevance_focuses_the_crawl(self, tmp_path):
        """query 'deep learning' follows /a, not the company blurb /b."""
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page(
                "# Research Hub\n\nA deep learning research platform. "
                "Guides about deep learning.\n",
                links=[(A, "Deep learning guide"),
                       (B, "About the company"),
                       (PDF, "Deep learning annual report")],
                title="Research Hub",
            ),
            A: _page(
                "# Deep Learning Guide\n\nDeep learning fundamentals on "
                "the platform.\n",
                links=[(A1, "Deep learning basics"), (A2, "Contact us")],
                title="Deep Learning Guide",
            ),
            A1: _page("Basics page.\n"),
        })
        parsed = _result(tb, query="deep learning")
        assert _fetched_urls(parsed) == [ROOT, A, A1]
        assert [p["depth"] for p in parsed["pages"]] == [0, 1, 2]
        # Root is the seed (score 1.0); deeper pages carry their
        # depth-decayed effective score.  The semantic thesaurus
        # expansion dilutes query coverage by the expanded terms
        # (base terms keep full weight, expansions weigh half), so the
        # pins are the v0.4.6 values minus that dilution.
        assert parsed["pages"][0]["score"] == 1.0
        assert 0.4 <= parsed["pages"][1]["score"] < 0.6
        assert 0.25 <= parsed["pages"][2]["score"] < 0.5
        # The echo reports how many thesaurus terms were added.
        assert parsed["query"].endswith(" +2")
        # /b and /a/2 were ranked out; /report.pdf routed to the ranked
        # documents list (record shape since v0.4.8 step 2).
        docs = parsed["documents"]
        assert [d["url"] for d in docs] == [PDF]
        assert docs[0]["anchor"] == "Deep learning annual report"
        assert docs[0]["score"] > 0
        assert parsed["documents_total"] == 1
        assert parsed["documents_below_score"] == 0
        skipped = {s["url"]: s["reason"] for s in parsed["skipped"]}
        assert skipped[B] == "below min score"
        assert skipped[A2] == "below min score"
        assert parsed["errors"] == []
        assert parsed["stop"] == "frontier exhausted"
        assert parsed["count"] == 3

    def test_flat_scores_degrade_to_plain_bfs(self, tmp_path):
        """Identical scores everywhere => discovery order, i.e. BFS."""
        tb = _toolbox(tmp_path)
        X = "https://example.com/x"
        Y = "https://example.com/y"
        X1 = "https://example.com/x/1"
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Notes\n\nnotes notes notes.\n",
                        links=[(X, "notes"), (Y, "notes")]),
            X: _page("# Notes\n\nnotes notes notes.\n", links=[(X1, "notes")]),
            Y: _page("# Notes\n\nnotes notes notes.\n"),
            X1: _page("leaf.\n"),
        })
        parsed = _result(tb, query="notes")
        assert _fetched_urls(parsed) == [ROOT, X, Y, X1]
        assert [p["depth"] for p in parsed["pages"]] == [0, 1, 1, 2]

    def test_depth_decay_allows_deep_overtake(self, tmp_path):
        """A strongly relevant depth-2 link beats a weak depth-1 one."""
        tb = _toolbox(tmp_path)
        MID = "https://example.com/mid"
        DEEP = "https://example.com/mid/deep"
        WEAK = "https://example.com/weak"
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform deep learning notes.\n",
                        links=[(WEAK, "company"), (MID, "platform notes")]),
            MID: _page("# Mid\n\ndeep learning platform details.\n",
                       links=[(DEEP, "deep learning platform")]),
            DEEP: _page("deep page.\n"),
            WEAK: _page("weak page.\n"),
        })
        parsed = _result(tb, max_pages=3)
        # Root (0), then the better depth-1 (/mid), then the strongly
        # relevant depth-2 (/deep).  The weak candidate scores 0 (its
        # label matches neither the derived query nor the page topic)
        # and is skipped at the min-score floor.
        assert _fetched_urls(parsed) == [ROOT, MID, DEEP]
        assert [p["depth"] for p in parsed["pages"]] == [0, 1, 2]
        # With the weak candidate skipped at the floor the crawl ends by
        # exhausting the frontier, exactly at the max_pages cap.
        assert parsed["count"] == 3
        assert parsed["stop"] == "frontier exhausted"
        assert WEAK not in tb._fetch._fetch_html.state["calls"]

    def test_explicit_query_beats_derived(self, tmp_path):
        tb = _toolbox(tmp_path)
        N = "https://example.com/n"
        X = "https://example.com/x"
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform stuff here.\n",
                        links=[(N, "needle deep"), (X, "platform")]),
            N: _page("needle page.\n"),
            X: _page("platform page.\n"),
        })
        parsed = _result(tb, query="needle deep", max_pages=2)
        assert _fetched_urls(parsed) == [ROOT, N]
        assert X not in tb._fetch._fetch_html.state["calls"]

    def test_query_echo_derived(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nplatform.\n")})
        parsed = _result(tb)
        assert parsed["query"] == "derived from root page"

    def test_min_score_zero_follows_unscored(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform.\n", links=[(B, "About the company")]),
            B: _page("company.\n"),
        })
        parsed = _result(tb, min_score=0.0)
        assert _fetched_urls(parsed) == [ROOT, B]

    def test_min_score_is_clamped_non_negative(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nplatform.\n")})
        parsed = _result(tb, min_score=-1.0)
        assert parsed["min_score"] == 0.0

    def test_min_score_bad_value_falls_back_to_default(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nplatform.\n")})
        parsed = _result(tb, min_score="bogus")
        assert parsed["min_score"] == 0.05


class TestBudgetAndCaps:
    def test_max_pages_is_total_across_depths(self, tmp_path):
        tb = _toolbox(tmp_path)
        pages = {ROOT: _page(
            "# Hub\n\nplatform platform platform.\n",
            links=[(f"https://example.com/p{i}", "platform") for i in range(4)],
        )}
        for i in range(4):
            pages[f"https://example.com/p{i}"] = _page("page.\n")
        tb._fetch._fetch_html = _fake_fetch(pages)
        parsed = _result(tb, max_pages=3)
        assert parsed["count"] == 3
        assert parsed["stop"] == "max_pages reached"
        # Failed fetches do not consume the budget: one bad link among
        # four still leaves room for the good ones within max_pages=3.

    def test_failed_fetch_does_not_consume_budget(self, tmp_path):
        tb = _toolbox(tmp_path)
        OK = "https://example.com/ok"
        BAD = "https://example.com/bad"
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n",
                        links=[(BAD, "platform"), (OK, "platform")]),
            OK: _page("ok.\n"),
        }, fail_on={BAD})
        parsed = _result(tb, max_pages=3)
        assert _fetched_urls(parsed) == [ROOT, OK]
        assert len(parsed["errors"]) == 1
        assert parsed["errors"][0]["url"] == BAD

    def test_max_pages_hard_cap(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nplatform.\n")})
        parsed = _result(tb, max_pages=5000)
        assert parsed["max_pages"] == 50

    def test_max_depth_hard_cap(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nplatform.\n")})
        parsed = _result(tb, max_depth=50)
        assert parsed["max_depth"] == 5

    def test_bad_parameters_fall_back_to_defaults(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nplatform.\n")})
        parsed = _result(tb, max_depth="bogus", max_pages=None)
        assert parsed["max_depth"] == 3
        assert parsed["max_pages"] == 15

    def test_per_page_skim_is_capped(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("x" * 5000 + "\n")})
        parsed = _result(tb)
        assert len(parsed["pages"][0]["markdown"]) == 300

    def test_small_output_budget_still_fits(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=900)
        pages = {ROOT: _page(
            "# Hub\n\nplatform platform platform.\n",
            links=[(f"https://example.com/p{i}", "platform") for i in range(4)],
        )}
        for i in range(4):
            pages[f"https://example.com/p{i}"] = _page("y" * 400 + "\n")
        tb._fetch._fetch_html = _fake_fetch(pages)
        parsed = _result(tb, max_pages=5)
        raw = json.dumps(parsed)
        assert len(raw) <= 900 + 128  # slack for re-serialization
        assert "pages" in parsed


class TestHostAndFilters:
    def test_same_host_skips_external_links(self, tmp_path):
        tb = _toolbox(tmp_path)
        EXT = "https://other.example.net/x"
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n",
                        links=[(A, "platform"), (EXT, "platform")]),
            A: _page("page.\n"),
        })
        parsed = _result(tb, same_host=True)
        assert EXT not in tb._fetch._fetch_html.state["calls"]
        skipped = {s["url"]: s["reason"] for s in parsed["skipped"]}
        assert skipped[EXT] == "external host"
        assert _fetched_urls(parsed) == [ROOT, A]

    def test_same_host_false_follows_external(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        EXT = "https://other.example.net/x"
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n",
                        links=[(EXT, "platform")]),
            EXT: _page("external.\n"),
        })
        monkeypatch.setattr(tb, "_validate_url", lambda url: None)
        parsed = _result(tb, same_host=False)
        assert _fetched_urls(parsed) == [ROOT, EXT]

    def test_same_host_ignores_www(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n",
                        links=[("https://www.example.com/a", "platform")]),
            "https://www.example.com/a": _page("www page.\n"),
        })
        monkeypatch.setattr(tb, "_validate_url", lambda url: None)
        parsed = _result(tb, same_host=True)
        assert _fetched_urls(parsed) == [ROOT, "https://www.example.com/a"]

    def test_boilerplate_paths_and_assets_are_skipped(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n", links=[
                ("https://example.com/login", "platform"),
                ("https://example.com/style.css", "platform"),
                ("https://example.com/tag/ai", "platform"),
                ("https://example.com/cart", "platform"),
                ("https://example.com/app.js", "platform"),
            ]),
        })
        parsed = _result(tb)
        assert parsed["count"] == 1  # root only
        reasons = {s["reason"] for s in parsed["skipped"]}
        assert reasons == {"boilerplate path", "asset"}
        assert parsed["stop"] == "frontier exhausted"

    def test_document_links_collected_not_fetched(self, tmp_path):
        tb = _toolbox(tmp_path)
        DOCX = "https://example.com/spec.docx"
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n", links=[
                (PDF, "platform report"),
                (DOCX, "platform spec"),
                (PDF, "platform report (duplicate)"),
            ]),
        })
        parsed = _result(tb)
        assert [d["url"] for d in parsed["documents"]] == [PDF, DOCX]
        assert parsed["documents_total"] == 2
        assert parsed["documents_below_score"] == 0
        assert tb._fetch._fetch_html.state["calls"] == [ROOT]

    def test_fragment_and_duplicate_links_dedupe(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n", links=[
                (A, "platform"),
                (A + "#section2", "platform"),
                (A, "platform"),
            ]),
            A: _page("page.\n"),
        })
        parsed = _result(tb)
        calls = tb._fetch._fetch_html.state["calls"]
        assert calls.count(A) == 1
        assert parsed["count"] == 2


class TestCacheAndIntegration:
    def test_repeat_crawl_is_cache_backed(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n", links=[(A, "platform")]),
            A: _page("page.\n"),
        })
        _result(tb)
        first = list(tb._fetch._fetch_html.state["calls"])
        _result(tb)
        # Every URL is fetched at most once across both crawls.
        assert tb._fetch._fetch_html.state["calls"] == first
        assert len(first) == 2

    def test_crawl_payload_keeps_full_pages_cached(self, tmp_path):
        """The skim is presentation-only: the full text stays cached so a
        later inspect_html_page of the same URL is a cache hit with the
        complete content (subject to its own read-time budget)."""
        long_md = "# A\n\n" + ("deep learning research platform. " * 200)
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({
            ROOT: _page("# Hub\n\nplatform platform.\n", links=[(A, "platform")]),
            A: _page(long_md),
        })
        parsed = _result(tb)
        assert len(parsed["pages"][1]["markdown"]) == 300
        follow = json.loads(tb.inspect_html_page(url=A))
        assert "error" not in follow
        # Read-time budgeting applies (chunks of 8000 chars by default),
        # but the content served comes from the cache, not a re-fetch.
        assert "markdown" in follow
        assert tb._fetch._fetch_html.state["calls"].count(A) == 1

    def test_execute_tool_dispatch(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb._fetch._fetch_html = _fake_fetch({ROOT: _page("# Hub\n\nplatform.\n")})
        out = tb.execute_tool("focused_discovery", {"root_url": ROOT})
        assert json.loads(out)["root"] == ROOT

    def test_registry_shape(self):
        spec = next(s for s in TOOL_REGISTRY if s.name == "focused_discovery")
        assert spec.method == "focused_discovery"
        params = {p.name: p for p in spec.params}
        assert params["root_url"].required  # no default
        assert params["max_depth"].default == 3
        assert params["max_pages"].default == 15
        assert params["same_host"].default is False
        assert params["min_score"].default == 0.05
        assert params["query"].default is None
