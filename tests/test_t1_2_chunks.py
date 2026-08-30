# tests/test_t1_2_chunks.py
"""Tier 1.2 — chunked/resumable reads: inspect_html_page paging.

A page longer than the output budget used to be a single head-first
truncation with no way to ask for the rest. inspect_html_page now
accepts offset (character offset into the full markdown) and
max_chunks (consecutive budget-sized chunks). Paging happens at read
time on the full cached markdown, so resuming never re-fetches, and
the lossless invariant (chunks concatenate back to the full page)
holds when no token budget is set.
"""

import json

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox


def _toolbox(tmp_path, **config_kwargs):
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


def _paragraph_page(n=30):
    return "\n\n".join(
        f"Paragraph number {i} carries filler words to stretch the "
        "length beyond the budget window."
        for i in range(n)
    )


PAGE = _paragraph_page(30)  # ~2600 chars


class TestSliceMarkdown:
    """Unit tests for the chunk slicer (no network)."""

    def test_fitting_page_is_returned_whole(self, tmp_path):
        tb = _toolbox(tmp_path)
        md = "short page"
        content, next_offset, has_more = tb._fetch._slice_markdown(md, 0, 1, 750, 0)
        assert content == md
        assert next_offset == len(md)
        assert has_more is False

    def test_first_chunk_respects_budget(self, tmp_path):
        tb = _toolbox(tmp_path)
        content, next_offset, has_more = tb._fetch._slice_markdown(PAGE, 0, 1, 750, 0)
        assert 0 < len(content) <= 750
        assert has_more is True
        assert next_offset == len(content)  # no token budget -> raw == delivered

    def test_chunks_concatenate_losslessly(self, tmp_path):
        tb = _toolbox(tmp_path)
        pos = 0
        parts = []
        while True:
            content, next_offset, has_more = tb._fetch._slice_markdown(PAGE, pos, 1, 750, 0)
            parts.append(content)
            pos = next_offset
            if not has_more:
                break
            assert pos > 0 or content == ""  # progress guard
        assert "".join(parts) == PAGE

    def test_max_chunks_returns_consecutive_chunks(self, tmp_path):
        tb = _toolbox(tmp_path)
        content, next_offset, has_more = tb._fetch._slice_markdown(PAGE, 0, 2, 750, 0)
        single, single_next, _ = tb._fetch._slice_markdown(PAGE, 0, 1, 750, 0)
        assert content.startswith(single)
        assert content != single  # a second chunk was appended
        assert next_offset > single_next
        assert has_more is True

    def test_resume_starts_where_the_raw_slice_ended(self, tmp_path):
        tb = _toolbox(tmp_path)
        first, next_offset, _ = tb._fetch._slice_markdown(PAGE, 0, 1, 750, 0)
        second, _, _ = tb._fetch._slice_markdown(PAGE, next_offset, 1, 750, 0)
        # No overlap, no gap: the full page is the concatenation.
        assert first + second == PAGE[: next_offset + len(second)]
        # No paragraph-boundary cut may swallow content on resume.
        assert second.startswith(PAGE[next_offset : next_offset + 30])

    def test_offset_beyond_end_is_clamped(self, tmp_path):
        tb = _toolbox(tmp_path)
        content, next_offset, has_more = tb._fetch._slice_markdown(PAGE, 10**6, 1, 750, 0)
        assert content == ""
        assert next_offset == len(PAGE)
        assert has_more is False

    def test_page_without_paragraphs_hard_cuts(self, tmp_path):
        tb = _toolbox(tmp_path)
        md = "x" * 2000
        content, next_offset, has_more = tb._fetch._slice_markdown(md, 0, 1, 750, 0)
        assert len(content) == 750
        assert next_offset == 750
        assert has_more is True

    def test_tiny_budget_does_not_stall(self, tmp_path):
        tb = _toolbox(tmp_path)
        md = "ab\n\n" * 10
        pos = 0
        parts = []
        while True:
            content, pos, has_more = tb._fetch._slice_markdown(md, pos, 1, 5, 0)
            parts.append(content)
            if not has_more:
                break
        assert "".join(parts) == md


class TestInspectHtmlPageChunked:
    """End-to-end paging through inspect_html_page (fetch spied)."""

    def _install_fetch(self, tb, markdown):
        tb._fetch._fetch_html = lambda url, use_smart=None: (
            markdown,
            [("https://example.com/next", "Next")],
            {},
            "static",
        )

    def test_default_read_is_resumable(self, tmp_path):
        """A plain call on a long page carries resume metadata."""
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        self._install_fetch(tb, PAGE)
        data = json.loads(tb.inspect_html_page("https://example.com/doc"))
        assert data["offset"] == 0
        assert 0 < data["next_offset"] < len(PAGE)
        assert data["has_more"] is True
        assert data["chars_total"] == len(PAGE)
        assert data["truncated"] is True

    def test_fitting_page_with_explicit_paging(self, tmp_path):
        tb = _toolbox(tmp_path)
        self._install_fetch(tb, "small page")
        data = json.loads(
            tb.inspect_html_page("https://example.com/doc", offset=0, max_chunks=1)
        )
        assert data["markdown"] == "small page"
        assert data["truncated"] is False
        assert data["has_more"] is False
        assert data["next_offset"] == len("small page")
        assert data["chars_total"] == len("small page")

    def test_first_chunk_of_long_page(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=1000)  # md_chars = 750
        self._install_fetch(tb, PAGE)
        data = json.loads(tb.inspect_html_page("https://example.com/doc"))
        assert data["chars_total"] == len(PAGE)
        assert data["has_more"] is True
        assert 0 < data["next_offset"] < len(PAGE)
        assert data["truncated"] is True

    def test_resume_covers_the_rest_losslessly(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        self._install_fetch(tb, PAGE)
        url = "https://example.com/doc"
        first = json.loads(tb.inspect_html_page(url))
        parts = [first["markdown"]]
        pos = first["next_offset"]
        while first["has_more"]:
            nxt = json.loads(tb.inspect_html_page(url, offset=pos))
            parts.append(nxt["markdown"])
            pos = nxt["next_offset"]
            first = nxt
        assert "".join(parts) == PAGE
        # All reads after the first were served from cache.
        assert first["cache_hit"] is True

    def test_max_chunks_two(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        self._install_fetch(tb, PAGE)
        url = "https://example.com/doc"
        one = json.loads(tb.inspect_html_page(url))
        data = json.loads(tb.inspect_html_page(url, max_chunks=2))
        # Two chunks: content strictly longer than a single chunk, and the
        # second chunk starts exactly where a single-chunk resume would.
        assert len(data["markdown"]) > len(one["markdown"])
        assert data["markdown"].startswith(one["markdown"])
        assert data["has_more"] is True
        assert data["next_offset"] > one["next_offset"]

    def test_paging_takes_precedence_over_query(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        self._install_fetch(tb, PAGE)
        data = json.loads(
            tb.inspect_html_page(
                "https://example.com/doc",
                query="paragraph number 25",
                offset=100,
                max_chunks=1,
            )
        )
        # Explicit paging wins: paging fields are set, section-selection
        # fields stay at defaults, and the query is not echoed.
        assert data["offset"] == 100
        assert data["has_more"] is True
        assert data["chars_total"] == len(PAGE)
        assert data["query"] is None
        assert data["sections_available"] == 0

    def test_negative_offset_clamps_to_zero(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        self._install_fetch(tb, PAGE)
        data = json.loads(
            tb.inspect_html_page("https://example.com/doc", offset=-5)
        )
        assert data["offset"] == 0
        assert data["has_more"] is True
        assert data["markdown"] == PAGE[: data["next_offset"]]

    def test_zero_max_chunks_clamps_to_one(self, tmp_path):
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        self._install_fetch(tb, PAGE)
        data = json.loads(
            tb.inspect_html_page("https://example.com/doc", max_chunks=0)
        )
        # Behaves like a single chunk: bounded, resumable.
        assert data["markdown"] == PAGE[: data["next_offset"]]
        assert data["has_more"] is True


class TestToolRegistryAdvertisesPaging:
    def test_spec_includes_offset_and_max_chunks(self):
        from stitch_web_researcher.agent_tools import TOOL_REGISTRY

        spec = next(s for s in TOOL_REGISTRY if s.name == "inspect_html_page")
        names = [p.name for p in spec.params]
        assert "offset" in names and "max_chunks" in names
        offset = next(p for p in spec.params if p.name == "offset")
        max_chunks = next(p for p in spec.params if p.name == "max_chunks")
        assert offset.type is int and offset.default == 0
        assert max_chunks.type is int and max_chunks.default == 1

        schema = spec.llm_definition()["function"]["parameters"]
        assert schema["properties"]["offset"]["type"] == "integer"
        assert schema["properties"]["max_chunks"]["type"] == "integer"
        assert "offset" not in schema["required"]
        assert "max_chunks" not in schema["required"]
        assert "url" in schema["required"]
