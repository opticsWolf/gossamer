# tests/test_t1_sections.py
"""Tier 1.1 — query-relevant section selection.

When a page does not fit the output budget, inspect_html_page used to
truncate head-first and lose relevant content past the cut. With a
research query supplied, the page is now split into heading-anchored
sections, BM25-scored, and the best sections that fit the budget are
returned (in document order) with provenance fields
(sections_available / sections_selected / section_anchors).
"""

import json

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.sections import (
    bm25_scores,
    select_relevant_sections,
    split_sections,
    tokenize_text,
)


def _toolbox(tmp_path, **config_kwargs):
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


PAGE = (
    "# Product Overview\n\n"
    "The platform ships in three editions, each bundling the same core "
    "engine with different support tiers and update windows. Enterprise "
    "customers can negotiate custom SLAs, and all editions include access "
    "to the public status page, the community forum, and the quarterly "
    "roadmap review. See the pricing page for details on volume discounts, "
    "annual billing, and the migration credits available when upgrading "
    "from earlier major versions of the platform.\n\n"
    "## Deployment\n\n"
    "Deployment targets include containers, bare metal, and managed "
    "cloud. The installer verifies checksums and refuses to run on "
    "unsupported kernel versions, and it can operate in air-gapped "
    "environments using the offline bundle. Rolling upgrades are supported "
    "across minor versions; major upgrades require a drain window during "
    "which no new connections are accepted. Operators can pin any release "
    "for the duration of a maintenance window and return to it automatically "
    "if the rollout fails its health checks.\n\n"
    "## Networking\n\n"
    "Traffic is mTLS by default. The gateway terminates external connections "
    "and forwards them over an internal mesh that isolates each service in "
    "its own network namespace. Connection limits scale with the license "
    "tier and can be tuned per service, and the mesh publishes its own "
    "metrics so operators can spot saturation before clients notice. "
    "Private link endpoints are available for on-premises clusters that "
    "must keep all traffic inside their own network fabric.\n\n"
    "## Observability\n\n"
    "Metrics follow the OpenMetrics exposition format and are exposed on a "
    "dedicated port that is never routed through the public gateway. Logs "
    "are emitted as structured JSON with request correlation ids that span "
    "the whole mesh, so a single id can trace a request from ingress to "
    "storage. Distributed tracing is available with any OpenTelemetry-"
    "compatible backend, and the exporter batches spans to keep overhead "
    "below one percent of request latency under normal load.\n\n"
    "## Quantum Lattice Calibration\n\n"
    "Calibration begins by locking the lattice clock to the reference "
    "oscillator and verifying that the phase noise stays within the "
    "tolerance band for at least sixty seconds. The operator then runs the "
    "resonance sweep, which takes about ninety seconds, and records the "
    "drift table in the calibration log. A failed sweep requires a full "
    "cold restart before retrying, and repeated failures should escalate to "
    "a hardware diagnostic before any further data is collected.\n"
)


class TestSplitSections:
    def test_empty_returns_no_sections(self):
        assert split_sections("") == []
        assert split_sections("   \n  ") == []

    def test_no_headings_is_single_intro(self):
        sections = split_sections("just text\n")
        assert len(sections) == 1
        assert sections[0].anchor == "(intro)"
        assert sections[0].text == "just text\n"

    def test_preamble_and_headings(self):
        sections = split_sections("intro line\n\n# Alpha\n\nbody A\n\n## Beta\nbody B")
        assert [s.anchor for s in sections] == ["(intro)", "Alpha", "Beta"]
        # Section bodies include their own heading line.
        assert sections[1].text.startswith("# Alpha")
        assert "body A" in sections[1].text
        assert "body B" in sections[2].text
        assert "body A" not in sections[2].text

    def test_offsets_point_at_headings(self):
        markdown = "intro\n\n# Alpha\nA\n\n# Beta\nB"
        sections = split_sections(markdown)
        for s in sections:
            assert markdown[s.offset : s.offset + len(s.text)] == s.text


class TestTokenize:
    def test_ascii_words_lowercased_and_stopwords_dropped(self):
        assert tokenize_text("The Quick Brown Fox") == ["quick", "brown", "fox"]

    def test_single_ascii_chars_dropped(self):
        assert tokenize_text("a b c xray") == ["xray"]

    def test_cjk_runs_become_bigrams(self):
        tokens = tokenize_text("量子格子")
        assert tokens == ["量子", "子格", "格子"]

    def test_lone_cjk_char_is_singleton(self):
        assert tokenize_text("猫 x") == ["猫"]

    def test_mixed_script(self):
        tokens = tokenize_text("校准 lattice 完了")
        assert "lattice" in tokens
        assert "校准" in tokens
        assert "完了" in tokens


class TestBm25:
    def test_relevant_doc_scores_highest(self):
        docs = [
            "deployment targets containers",
            "quantum lattice calibration drift",
            "metrics logs tracing",
        ]
        scores = bm25_scores(tokenize_text("quantum lattice calibration"), docs)
        assert scores[1] == max(scores)
        assert scores[1] > 0.0

    def test_no_overlap_is_zero(self):
        scores = bm25_scores(tokenize_text("zzz qqq"), ["plain text here"])
        assert scores == [0.0]

    def test_empty_docs_or_query(self):
        assert bm25_scores(["a"], []) == []
        assert bm25_scores([], ["text"]) == [0.0]


class TestSelectRelevantSections:
    def test_returns_none_when_content_fits(self):
        assert select_relevant_sections(PAGE, "quantum lattice", len(PAGE) + 10) is None

    def test_returns_none_for_tokenless_query(self):
        assert select_relevant_sections(PAGE, "the of and", 100) is None

    def test_returns_none_when_nothing_matches(self):
        assert select_relevant_sections(PAGE, "zzz qqq www", 1000) is None

    def test_returns_none_for_single_section_document(self):
        assert select_relevant_sections("no headings at all, " * 100, "headings", 100) is None

    def test_selects_relevant_tail_section(self):
        sel = select_relevant_sections(PAGE, "quantum lattice calibration drift", 1500)
        assert sel is not None
        assert "Quantum Lattice Calibration" in sel.markdown
        assert "cold restart" in sel.markdown
        # Irrelevant sections must not be included.
        assert "mTLS" not in sel.markdown
        assert "OpenMetrics" not in sel.markdown
        assert sel.total_sections == 5
        assert sel.selected_count == 1
        assert sel.anchors == ("Quantum Lattice Calibration",)
        assert len(sel.markdown) <= 1500

    def test_document_order_preserved_for_multiple_picks(self):
        # Two relevant sections with the lower one scoring highest must
        # still come out in original order.
        doc = (
            "# One\n\nquantum lattice notes\n\n"
            "## Fill\n\n" + ("unrelated filler text. " * 60) + "\n\n"
            "## Two\n\nmore quantum lattice details\n"
        )
        sel = select_relevant_sections(doc, "quantum lattice", 1000)
        assert sel is not None
        assert sel.selected_count == 2
        one_pos = sel.markdown.find("# One")
        two_pos = sel.markdown.find("## Two")
        assert 0 <= one_pos < two_pos
        assert list(sel.anchors) == ["One", "Two"]

    def test_oversized_top_section_is_head_truncated_not_dropped(self):
        doc = (
            "# Big\n\n" + ("quantum lattice " * 300) + "\n\n"
            "## Other\n\nplain\n"
        )
        sel = select_relevant_sections(doc, "quantum lattice", 400)
        assert sel is not None
        assert sel.selected_count == 1
        assert len(sel.markdown) <= 400
        assert "quantum lattice" in sel.markdown


class TestInspectHtmlPageWithQuery:
    def _install_fetch(self, tb, markdown):
        tb._fetch._fetch_html = lambda url, use_smart=None: (
            markdown,
            [("https://example.com/next", "Next")],
            {},
            "static",
        )

    def test_legacy_head_first_without_query(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=2000)
        self._install_fetch(tb, PAGE)
        data = json.loads(tb.inspect_html_page("https://example.com/doc"))
        # Budget is 2000 * (1 - 0.25) = 1500 chars; the tail section is
        # well past the cut, so the legacy path loses it.
        assert "Quantum Lattice Calibration" not in data["markdown"]
        assert data["sections_available"] == 0
        assert data["query"] is None

    def test_query_keeps_relevant_tail_section(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=2000)
        self._install_fetch(tb, PAGE)
        data = json.loads(
            tb.inspect_html_page(
                "https://example.com/doc",
                query="quantum lattice calibration drift",
            )
        )
        assert "Quantum Lattice Calibration" in data["markdown"]
        assert "cold restart" in data["markdown"]
        assert "mTLS" not in data["markdown"]
        assert data["query"] == "quantum lattice calibration drift"
        assert data["sections_available"] == 5
        assert data["sections_selected"] == 1
        assert "Quantum Lattice Calibration" in data["section_anchors"]
        assert data["truncated"] is True
        # The char budget is still enforced on the selected content.
        assert len(data["markdown"]) <= 2000

    def test_query_that_matches_nothing_falls_back_to_head_first(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=2000)
        self._install_fetch(tb, PAGE)
        data = json.loads(
            tb.inspect_html_page(
                "https://example.com/doc",
                query="zzz qqq www",
            )
        )
        assert data["sections_available"] == 0
        assert "Quantum Lattice Calibration" not in data["markdown"]

    def test_query_is_honored_from_cache_on_second_call(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=2000)
        self._install_fetch(tb, PAGE)
        first = json.loads(
            tb.inspect_html_page(
                "https://example.com/doc",
                query="quantum lattice calibration",
            )
        )
        # Same URL again, different query: no re-fetch (cache), different
        # section outcome is still allowed.
        second = json.loads(
            tb.inspect_html_page(
                "https://example.com/doc",
                query="deployment containers kernel",
            )
        )
        assert second["cache_hit"] is True
        assert "Quantum Lattice Calibration" not in second["markdown"]
        assert "Deployment" in second["markdown"]
        assert second["sections_available"] == first["sections_available"]


class TestToolRegistryAdvertisesQuery:
    def test_inspect_html_page_spec_includes_query_param(self):
        from stitch_web_researcher.agent_tools import TOOL_REGISTRY

        spec = next(s for s in TOOL_REGISTRY if s.name == "inspect_html_page")
        names = [p.name for p in spec.params]
        assert "query" in names
        param = next(p for p in spec.params if p.name == "query")
        assert param.required is False
        schema = spec.llm_definition()["function"]["parameters"]
        assert "query" in schema["properties"]
        assert "query" not in schema["required"]
