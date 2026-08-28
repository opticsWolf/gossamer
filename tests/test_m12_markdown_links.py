# tests/test_m12_markdown_links.py
"""M12 — relative markdown hrefs must reach the model as absolute URLs.

The Rust core's markdown conversion keeps hrefs as written (e.g.
``[A](/a)``); only the separate ``follow_up_links`` list is absolute.
A model copying a markdown link gets an unresolvable URL, so the body
is made self-contained via urljoin on the Python side.
"""

import json
from unittest import mock

from stitch_web_researcher.agent_tools import (
    WebResearcherToolbox,
    _absolutize_markdown_links,
)


class TestAbsolutizeMarkdownLinks:
    def test_root_relative_becomes_absolute(self):
        out = _absolutize_markdown_links(
            "[A](/a)", "https://example.com/dir/page"
        )
        assert out == "[A](https://example.com/a)"

    def test_path_relative_resolves_against_base_path(self):
        out = _absolutize_markdown_links(
            "[A](child)", "https://example.com/dir/page"
        )
        assert out == "[A](https://example.com/dir/child)"

    def test_query_string_preserved(self):
        out = _absolutize_markdown_links(
            "[A](child?x=1)", "https://example.com/dir/page"
        )
        assert out == "[A](https://example.com/dir/child?x=1)"

    def test_fragment_only_untouched(self):
        assert _absolutize_markdown_links("[A](#sec)", "https://example.com/p") == \
            "[A](#sec)"

    def test_protocol_relative_untouched(self):
        assert _absolutize_markdown_links(
            "[A](//cdn.example.com/x.js)", "https://example.com/p"
        ) == "[A](//cdn.example.com/x.js)"

    def test_mailto_untouched(self):
        assert _absolutize_markdown_links(
            "[M](mailto:a@b.c)", "https://example.com/p"
        ) == "[M](mailto:a@b.c)"

    def test_absolute_http_untouched(self):
        assert _absolutize_markdown_links(
            "[A](https://other.com/a)", "https://example.com/p"
        ) == "[A](https://other.com/a)"

    def test_data_and_javascript_untouched(self):
        for target in ("data:text/plain,x", "javascript:void(0)"):
            assert _absolutize_markdown_links(
                f"[A]({target})", "https://example.com/p"
            ) == f"[A]({target})"

    def test_title_preserved(self):
        out = _absolutize_markdown_links(
            '[A](/a "T")', "https://example.com/p"
        )
        assert out == '[A](https://example.com/a "T")'

    def test_image_link_untouched(self):
        assert _absolutize_markdown_links(
            "![img](/i.png)", "https://example.com/p"
        ) == "![img](/i.png)"

    def test_no_links_unchanged(self):
        text = "plain text, no links here."
        assert _absolutize_markdown_links(text, "https://example.com/p") == text

    def test_multiple_links(self):
        out = _absolutize_markdown_links(
            "[A](/a) and [B](b)", "https://example.com/dir/p"
        )
        assert out == "[A](https://example.com/a) and [B](https://example.com/dir/b)"

    def test_idempotent(self):
        once = _absolutize_markdown_links(
            "[A](/a) [B](b)", "https://example.com/dir/p"
        )
        twice = _absolutize_markdown_links(once, "https://example.com/dir/p")
        assert once == twice


def _toolbox(tmp_path, **kw):
    kw.setdefault("cache_dir", str(tmp_path / "cache"))
    kw.setdefault("respect_robots", False)
    return WebResearcherToolbox(**kw)


class TestAbsolutizationInTools:
    def test_inspect_html_page_delivers_absolute_links(self, tmp_path):
        tb = _toolbox(tmp_path)
        md = "# T\n[Home](/)\n[Sub](sub/child?x=1)\n[M](mailto:a@b.c)\n"
        with mock.patch.object(
            tb, "_static_fetch", return_value=(md, [], {}, "static")
        ):
            raw = tb.inspect_html_page("https://example.com/dir/page")

        data = json.loads(raw)
        assert "[Home](https://example.com/)" in data["markdown"]
        assert "[Sub](https://example.com/dir/sub/child?x=1)" in data["markdown"]
        assert "[M](mailto:a@b.c)" in data["markdown"]

    def test_batch_inspect_pages_delivers_absolute_links(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/dir/page"
        fake = [(url, "# T\n[A](/a)\n", [])]
        with mock.patch(
            "stitch_web_researcher.agent_tools.batch_research", return_value=fake
        ):
            raw = tb.batch_inspect_pages([url])

        data = json.loads(raw)
        assert "[A](https://example.com/a)" in data[0]["markdown"]

    def test_inspect_html_structured_delivers_absolute_links(self, tmp_path):
        tb = _toolbox(tmp_path)
        md = "# T\n[A](/a)\n"
        # Tier 3.11: the structured path fetches with keep_html=True, so
        # the _static_fetch fake returns the 5-tuple (raw HTML None).
        with mock.patch.object(
            tb,
            "_static_fetch",
            return_value=(md, ["/a"], {}, "static", None),
        ):
            raw = tb.inspect_html_structured("https://example.com/dir/page")

        data = json.loads(raw)
        assert "[A](https://example.com/a)" in data["pages"][0]["markdown"]
