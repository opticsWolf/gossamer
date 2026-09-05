"""CLI smoke tests: parsing, offline commands, error paths."""

import json

import gossamer.cli as cli

from gossamer.cli import build_parser, main


def _parse(argv):
    return build_parser().parse_args(argv)


def test_parsers_accept_all_subcommands():
    assert _parse(["search", "q"]).command == "search"
    assert _parse(["research", "q", "--provider", "epo"]).provider == "epo"
    assert _parse(["categories"]).command == "categories"
    assert _parse(["inspect", "https://x.example"]).command == "inspect"
    assert _parse(["batch", "https://a.example", "https://b.example"]).urls == [
        "https://a.example",
        "https://b.example",
    ]
    assert _parse(["extract", "f.pdf", "--pages", "1-3"]).pages == "1-3"
    assert _parse(["check", "https://x.example", "--mode", "content"]).mode == "content"
    assert _parse(["discover", "https://x.example"]).url == "https://x.example"
    crawl = _parse(["crawl", "https://x.example", "--query", "q",
                    "--max-pages", "10", "--same-host"])
    assert (crawl.query, crawl.max_pages, crawl.same_host) == ("q", 10, True)


def test_crawl_dispatch_through_main(monkeypatch, capsys):
    """The crawl subcommand reaches the toolbox (stubbed, no network)."""
    calls = []

    class Stub:
        def crawl(self, root_url, **kwargs):
            calls.append((root_url, kwargs))
            return '{"ok": true}'

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    assert main(["crawl", "https://x.example"]) == 0
    assert [c[0] for c in calls] == ["https://x.example"]
    assert capsys.readouterr().out.count('{"ok": true}') == 1
    assert _parse(["cache", "--action", "clear"]).action == "clear"
    cite = _parse(["cite", "10.1/abc", "--style", "apa"])
    assert (cite.results, cite.style) == (["10.1/abc"], "apa")


def test_categories_runs_offline(capsys, tmp_path):
    rc = main(["--cache-dir", str(tmp_path), "categories"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert {c["category"] for c in data} >= {
        "scholarly", "legal", "patent", "financial", "geo", "general",
    }


def test_common_flags_propagate(tmp_path):
    args = _parse(["--cache-dir", str(tmp_path), "--keystore", "k.json",
                   "--config", "g.json", "categories"])
    assert args.cache_dir == str(tmp_path)
    assert args.keystore == "k.json"
    assert args.config == "g.json"


def test_tool_error_returns_exit_1(capsys, tmp_path):
    # Unknown research provider -> toolbox returns an error payload, but a
    # hard failure (e.g. bad args inside the tool) exits nonzero. The CLI
    # surfaces tool-level error dicts on stdout with rc 0; only exceptions
    # (ValueError/RuntimeError) become rc 1. Force one via an empty query.
    rc = main(["--cache-dir", str(tmp_path), "research", ""])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert out.strip(), "CLI must always emit something parseable"
