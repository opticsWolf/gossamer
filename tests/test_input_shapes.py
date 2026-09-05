"""Input-shape and schema-mapping guards (review A.1/A.3/A.4/A.5/A.9/A.10).

LLMs frequently pass a bare string where a ``list[str]`` is declared, or an
unknown enum value where a closed set is expected. These tests pin the
friendly behavior: wrap-or-explain, never per-character garbage, never an
escaped exception.
"""

import json

import pytest

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.config import TOOL_REGISTRY, ToolParam, ensure_str_list


def _toolbox(tmp_path, **kwargs):
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **kwargs,
        )
    )


class TestToolParamTypeMap:
    @pytest.mark.parametrize(
        "pytype,json_type",
        [
            (str, "string"),
            (int, "integer"),
            (float, "number"),
            (bool, "boolean"),
            (list[str], "array"),
        ],
    )
    def test_every_supported_type_maps(self, pytype, json_type):
        assert ToolParam("x", pytype, None).json_schema["type"] == json_type

    def test_min_score_is_a_number(self):
        for spec in TOOL_REGISTRY:
            for p in spec.params:
                if p.name == "min_score":
                    assert p.type is float
                    assert p.json_schema["type"] == "number"
                    return
        pytest.fail("min_score param missing from registry")

    def test_registry_kwargs_copies_mutable_defaults(self):
        for spec in TOOL_REGISTRY:
            if any(isinstance(p.default, list) for p in spec.params if not p.required):
                first = spec.kwargs({})
                second = spec.kwargs({})
                for p in spec.params:
                    if isinstance(getattr(p, "default", None), list):
                        assert first[p.name] is not second[p.name]
                        first[p.name].append("poison")
                        assert second[p.name] == []


class TestEnsureStrList:
    def test_none_is_empty(self):
        assert ensure_str_list(None, "urls") == []

    def test_bare_string_wraps(self):
        assert ensure_str_list("https://example.com", "urls") == ["https://example.com"]

    def test_blank_string_is_empty(self):
        assert ensure_str_list("   ", "urls") == []

    def test_list_passes_through(self):
        assert ensure_str_list(["a", "b"], "urls") == ["a", "b"]

    def test_other_types_raise(self):
        with pytest.raises(TypeError):
            ensure_str_list({"url": "x"}, "urls")
        with pytest.raises(TypeError):
            ensure_str_list(42, "urls")

    def test_non_string_items_raise(self):
        with pytest.raises(TypeError):
            ensure_str_list(["ok", 42], "urls")


class TestCheckSourcesShapes:
    def test_bare_string_probes_one_url(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.check_sources("notalist"))
        assert payload["count"] == 1
        assert payload["results"][0]["url"] == "notalist"

    def test_non_list_is_an_error(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.check_sources({"url": "https://example.com"}))
        assert "error" in payload
        assert payload["results"] == []

    def test_none_is_empty_envelope(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.check_sources(None))
        assert payload["count"] == 0
        assert payload["results"] == []

    def test_unknown_mode_is_an_error(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.check_sources([], mode="bogus"))
        assert "error" in payload
        assert "bogus" in payload["error"]

    def test_content_mode_reports_its_mode(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.check_sources(["notaurl"], mode="content"))
        assert payload["mode"] == "content"
        assert payload["count"] == 1


class TestBatchShapes:
    def test_bare_string_wraps_to_one_entry(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.batch_inspect_pages("notaurl"))
        assert isinstance(payload, list) and len(payload) == 1
        assert payload[0]["url"] == "notaurl"

    def test_none_returns_error_dict(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.batch_inspect_pages(None))
        assert "error" in payload


class TestExportCitationsShapes:
    def test_bare_doi_string_wraps(self, tmp_path):
        tb = _toolbox(tmp_path)
        out = tb.export_citations("10.1234/abc")
        assert "10.1234/abc" in out

    def test_non_list_returns_error_dict(self, tmp_path):
        tb = _toolbox(tmp_path)
        payload = json.loads(tb.export_citations({"doi": "10.1234/abc"}))
        assert "error" in payload


class TestSearchDropCounterReset:
    def test_cache_hit_resets_dropped_dupes(self, tmp_path):
        class DupeProvider:
            name = "dupe"

            def search(self, query, max_results=5):
                return [
                    {"url": "https://example.com/a", "title": "t", "snippet": "s"},
                    {"url": "https://example.com/a/", "title": "t", "snippet": "s"},
                ]

        tb = _toolbox(tmp_path, search_providers=[DupeProvider()])
        tb.search_web("q", max_results=5)
        assert tb._search._last_search_dropped == 1
        tb.search_web("q", max_results=5)  # cache hit
        assert tb._search._last_search_dropped == 0


class TestCanonicalIdentity:
    def test_tracking_variants_collapse(self):
        from gossamer.config import canonical_url

        assert (
            canonical_url(
                "https://WWW.Example.com:443/docs/?x=1&utm_source=x#top",
                query="drop-tracking",
            )
            == "https://example.com/docs?x=1"
        )

    def test_query_modes(self):
        from gossamer.config import canonical_url

        assert (
            canonical_url("https://example.com/a?page=1", query="keep")
            == "https://example.com/a?page=1"
        )
        assert (
            canonical_url("https://example.com/a?page=1", query="drop")
            == "https://example.com/a"
        )

    def test_www_and_case_collapse(self):
        from gossamer.config import canonical_url

        assert canonical_url("https://www.Example.COM/") == "https://example.com/"
        assert canonical_url("http://example.com:80/a/") == "http://example.com/a"

    def test_dedupe_collapses_www_variants(self):
        from gossamer.dedup import dedupe

        kept, dropped = dedupe(
            [{"url": "https://www.example.com/docs/"}, {"url": "https://example.com/docs"}]
        )
        assert len(kept) == 1 and len(dropped) == 1

    def test_batch_fetches_spelling_variants_once(self, tmp_path):
        from unittest.mock import patch

        tb = _toolbox(tmp_path)
        seen_inputs = []

        def fake_batch(urls, **kwargs):
            seen_inputs.append(list(urls))
            return [
                (
                    urls[0],
                    "<html><body><main><p>hi</p></main></body></html>",
                    "hi",
                    [("https://example.com/x", "x")],
                )
            ]

        with patch("gossamer.fetch.batch_research", side_effect=fake_batch):
            payload = json.loads(
                tb.batch_inspect_pages(
                    ["example.com", "https://example.com/", "https://example.com"]
                )
            )
        assert len(seen_inputs) == 1 and len(seen_inputs[0]) == 1
        assert len(payload) == 1


class TestBatchProvenanceParity:
    def test_batch_entry_carries_provenance_and_resume_fields(self, tmp_path):
        """B.6: batch entries match single-page reads (provenance + Tier 1.2)."""
        from unittest.mock import patch

        tb = _toolbox(tmp_path)

        def fake_batch(urls, **kwargs):
            return [
                (
                    urls[0],
                    "<html><body><main><p>hello world</p></main></body></html>",
                    "hello world, and much more content beyond the budget",
                    [],
                    (200, "https://example.com/final", "text/html; charset=utf-8"),
                )
            ]

        with patch("gossamer.fetch.batch_research", side_effect=fake_batch):
            payload = json.loads(tb.batch_inspect_pages(["https://example.com/p"]))
        assert len(payload) == 1
        entry = payload[0]
        assert entry["http_status"] == 200
        assert entry["final_url"] == "https://example.com/final"
        assert entry["content_type"] == "text/html; charset=utf-8"
        assert entry["fetched_at"]
        assert entry["chars_total"] > 0
        assert entry["content_hash"]  # over the full page, like single reads
