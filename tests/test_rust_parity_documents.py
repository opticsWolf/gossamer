"""Replayable parity pins for the deterministic documents payload helpers.

Pydantic model classes and binary PDF/Office parsing stay in Python; this unit
owns only the pure JSON-shaping helpers that define the shared payload schema.
"""

from gossamer import structured_parser
from gossamer.models import _absolutize_markdown_links


def test_office_cell_text_flattens_paragraph_runs():
    cell = {
        "content": [
            {"type": "paragraph", "content": [{"text": " a "}, {"text": "b"}]},
            {"type": "image", "content": [{"text": "ignored"}]},
        ]
    }
    assert structured_parser._office_cell_text(cell) == "a b"


def test_office_ir_tables_keeps_titles_and_rows():
    ir = {
        "sections": [
            {
                "title": "Sheet1",
                "elements": [
                    {
                        "type": "table",
                        "rows": [
                            {"cells": [{"content": [{"type": "paragraph", "content": [{"text": "A"}]}]}]},
                            {"cells": [{"content": [{"type": "paragraph", "content": [{"text": "1"}]}]}]},
                        ],
                    }
                ],
            },
            {"title": "Empty", "elements": []},
        ]
    }
    assert structured_parser.office_ir_tables(ir) == [("Sheet1", [["A"], ["1"]])]


def test_office_ir_tables_ignores_malformed_sections():
    assert structured_parser.office_ir_tables({"sections": [None, {"elements": []}]}) == []


def test_follow_up_candidates_dedupe_type_and_cap():
    links = [
        ("https://x.test/a", " A "),
        ("https://x.test/a", "again"),
        ("https://x.test/report.pdf", ""),
        ("https://x.test/c", "C"),
    ]
    out = structured_parser.build_follow_up_candidates(links, max_links=3)
    assert [(c.url, c.title, c.type) for c in out] == [
        ("https://x.test/a", "A", "page"),
        ("https://x.test/report.pdf", "(untitled)", "document"),
        ("https://x.test/c", "C", "page"),
    ]


def test_follow_up_candidates_accept_bare_items():
    out = structured_parser.build_follow_up_candidates(["https://x.test/a", ""], max_links=0)
    assert [(c.url, c.title) for c in out] == [("https://x.test/a", "(untitled)")]


def test_markdown_links_are_absolutized_and_idempotent():
    once = _absolutize_markdown_links(
        "[A](/a) and [B](child?x=1) and [C](https://other.test/z)",
        "https://x.test/dir/page",
    )
    assert once == (
        "[A](https://x.test/a) and [B](https://x.test/dir/child?x=1) "
        "and [C](https://other.test/z)"
    )
    assert _absolutize_markdown_links(once, "https://x.test/dir/page") == once


def test_markdown_special_targets_pass_through():
    text = "![img](/i.png) [F](#frag) [M](mailto:a@b.c) [D](//cdn.test/x.js)"
    assert _absolutize_markdown_links(text, "https://x.test/p") == text
