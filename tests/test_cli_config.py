"""CLI runtime configuration + render-strategy flags.

The CLI and MCP server share ``gossamer.runtime_config.config_from_env`` so
``GOSSAMER_*`` env vars and ``gossamer.json`` affect both. Explicit CLI flags
still win, and every page-fetching subcommand exposes ``--use-smart``.
"""

from __future__ import annotations

import argparse
import json
import os

import gossamer.cli as cli
from gossamer.cli import _build_toolbox, build_parser, main

URL = "https://example.com/cli-config"


def _subparsers(parser):
    return next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )


def test_cli_honors_gossamer_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOSSAMER_CONFIG", "")
    monkeypatch.setenv("GOSSAMER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GOSSAMER_MAX_TOKENS", "1234")
    monkeypatch.setenv("GOSSAMER_FETCH_MODE", "browser")

    tb = _build_toolbox(build_parser().parse_args(["inspect", URL]))
    assert tb.max_tokens == 1234
    assert tb.fetch_mode == "browser"


def test_cli_honors_gossamer_json(monkeypatch, tmp_path):
    monkeypatch.setenv("GOSSAMER_CONFIG", "")
    monkeypatch.delenv("GOSSAMER_FETCH_MODE", raising=False)
    monkeypatch.delenv("GOSSAMER_MAX_TOKENS", raising=False)

    cache = tmp_path / "file-cache"
    cfg = tmp_path / "gossamer.json"
    cfg.write_text(
        json.dumps(
            {
                "fetch_mode": "browser",
                "max_tokens": 4321,
                "cache_dir": str(cache),
            }
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        ["--config", str(cfg), "inspect", URL]
    )
    try:
        tb = _build_toolbox(args)
    finally:
        os.environ.pop("GOSSAMER_CONFIG", None)

    assert tb.fetch_mode == "browser"
    assert tb.max_tokens == 4321
    assert tb.cache.cache_path == cache


def test_explicit_cli_flags_override_env_and_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GOSSAMER_CONFIG", "")
    monkeypatch.setenv("GOSSAMER_FETCH_MODE", "browser")
    monkeypatch.setenv("GOSSAMER_CACHE_DIR", str(tmp_path / "env-cache"))

    args = build_parser().parse_args(
        ["--fetch-mode", "static", "--cache-dir", str(tmp_path / "cli-cache"),
         "inspect", URL]
    )
    tb = _build_toolbox(args)
    assert tb.fetch_mode == "static"
    assert tb.cache.cache_path == tmp_path / "cli-cache"


def test_fetch_mode_flag_works_before_or_after_subcommand():
    parser = build_parser()
    assert parser.parse_args(["--fetch-mode", "browser", "inspect", URL]).fetch_mode == "browser"
    assert parser.parse_args(["inspect", URL, "--fetch-mode", "static"]).fetch_mode == "static"


def test_page_fetching_subcommands_accept_use_smart():
    parser = build_parser()
    for argv in (
        ["search", "q"],
        ["inspect", URL],
        ["batch", URL],
        ["crawl", URL],
    ):
        args = parser.parse_args(argv)
        assert args.use_smart == "auto", argv
        args = parser.parse_args([*argv, "--use-smart", "browser"])
        assert args.use_smart == "browser", argv


def test_inspect_help_explains_use_smart():
    sub = _subparsers(build_parser())
    action = next(
        a for a in sub.choices["inspect"]._actions
        if "--use-smart" in a.option_strings
    )
    assert action.help and "browser" in action.help and "auto" in action.help
    assert action.choices == ("auto", "browser", "static")


def test_search_and_batch_forward_use_smart(monkeypatch, capsys):
    calls = []

    class Stub:
        def web_search(self, query, **kwargs):
            calls.append(("search", kwargs))
            return json.dumps({"results": []})

        def batch_inspect_pages(self, urls, **kwargs):
            calls.append(("batch", kwargs))
            return json.dumps([])

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    assert main(["search", "q", "--search-only", "--use-smart", "browser"]) == 0
    assert main(["batch", URL, "--use-smart", "static"]) == 0
    capsys.readouterr()
    assert calls[0][0] == "search"
    assert calls[0][1]["use_smart"] == "browser"
    assert calls[1][0] == "batch"
    assert calls[1][1]["use_smart"] == "static"


def test_invalid_fetch_mode_env_is_a_clean_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GOSSAMER_CONFIG", "")
    monkeypatch.setenv("GOSSAMER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GOSSAMER_FETCH_MODE", "not-a-mode")
    assert main(["inspect", URL]) == 1
    err = capsys.readouterr().err
    assert "gossamer: error:" in err
    assert "fetch_mode" in err
