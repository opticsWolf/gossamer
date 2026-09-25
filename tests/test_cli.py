"""CLI smoke tests: parsing, offline commands, error paths."""

import json
import os
import subprocess
import sys
from pathlib import Path

import gossamer.cli as cli

from gossamer.cli import build_parser, main


def _parse(argv):
    return build_parser().parse_args(argv)


def test_parsers_accept_all_subcommands():
    assert _parse(["search", "q"]).command == "search"
    assert _parse(["research", "q", "--provider", "epo"]).provider == "epo"
    advanced = _parse([
        "research", "q", "--provider", "openalex",
        "--filter", "type:article", "--select", "id,title",
        "--title", "gradient index", "--author", "Smith",
    ])
    assert (advanced.filter, advanced.select) == ("type:article", "id,title")
    assert (advanced.title, advanced.author) == ("gradient index", "Smith")
    assert _parse([
        "research", "q", "--providers", "openalex", "arxiv",
    ]).providers == ["openalex", "arxiv"]
    assert _parse(["categories"]).command == "categories"
    assert _parse(["inspect", "https://x.example"]).command == "inspect"
    assert _parse(["batch", "https://a.example", "https://b.example"]).urls == [
        "https://a.example",
        "https://b.example",
    ]
    assert _parse(["extract", "f.pdf", "--pages", "1-3"]).pages == "1-3"
    download = _parse([
        "download", "https://example.org/paper.pdf", "-o", "paper.pdf",
        "--min-bytes", "100", "--max-bytes", "1000000",
        "--expect-format", "pdf", "--try-mirrors", "https://mirror.example/paper.pdf",
    ])
    assert (download.output_path, download.min_bytes, download.max_bytes) == (
        "paper.pdf", 100, 1000000,
    )
    assert download.try_mirrors == ["https://mirror.example/paper.pdf"]
    assert _parse(["check", "https://x.example", "--mode", "content"]).mode == "content"
    assert _parse(["discover", "https://x.example"]).url == "https://x.example"
    assert _parse(["locate-pdf", "10.1234/example"]).doi == "10.1234/example"
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


def test_cli_emits_unicode_json_under_legacy_windows_encoding():
    script = r'''
import json
import sys
import gossamer.cli as cli
assert sys.stdout.encoding.lower().replace("-", "") == "cp1252"
class Stub:
    def web_search(self, *args, **kwargs):
        return json.dumps({"results": [{"snippet": "gradient index μ"}]}, ensure_ascii=False)
cli._build_toolbox = lambda args: Stub()
raise SystemExit(cli.main(["search", "q", "--search-only"]))
'''
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252:strict"
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    payload = json.loads(proc.stdout.decode("utf-8"))
    assert payload["results"][0]["snippet"] == "gradient index μ"


def test_research_provider_error_payload_is_json_and_returns_exit_1(
    monkeypatch, capsys,
):
    class Stub:
        def research_by_category(self, query, **kwargs):
            return json.dumps({
                "query": query,
                "results": [],
                "error": "arxiv search failed: temporary edge limit",
            })

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    rc = main(["research", "quantum", "--provider", "arxiv"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"] == []
    assert payload["error"] == "arxiv search failed: temporary edge limit"


def test_research_native_options_dispatch(monkeypatch, capsys):
    calls = {}

    class Stub:
        def research_by_category(self, query, **kwargs):
            calls.update(query=query, **kwargs)
            return json.dumps({"query": query, "results": []})

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    assert main([
        "research", "q", "--provider", "openalex",
        "--filter", "type:article", "--select", "id,title",
        "--title", "gradient index", "--author", "Smith",
    ]) == 0
    capsys.readouterr()
    assert calls["filter"] == "type:article"
    assert calls["select"] == "id,title"
    assert calls["title"] == "gradient index"
    assert calls["author"] == "Smith"


def test_research_multi_provider_dispatch(monkeypatch, capsys):
    calls = {}

    class Stub:
        def research_by_category(self, query, **kwargs):
            calls.update(query=query, **kwargs)
            return json.dumps({"query": query, "results": []})

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    assert main([
        "research", "graph papers", "--providers", "openalex", "arxiv",
    ]) == 0
    capsys.readouterr()
    assert calls["providers"] == ["openalex", "arxiv"]
    assert calls["provider"] is None


def test_download_dispatch_and_error_exit_status(monkeypatch, capsys):
    class Stub:
        def download_file(self, source, output_path, **kwargs):
            return json.dumps({
                "source": source,
                "output_path": output_path,
                "status": "error",
                "error": {"code": "not_found", "message": "missing"},
            })

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    rc = main(["download", "https://example.org/a.pdf", "-o", "a.pdf"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["error"]["code"] == "not_found"


def test_locate_pdf_dispatch_and_error_exit_status(monkeypatch, capsys):
    class Stub:
        def locate_pdf(self, doi):
            return json.dumps({
                "doi": doi,
                "status": "error",
                "error": {"code": "provider_error", "message": "offline"},
            })

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    rc = main(["locate-pdf", "10.1234/example"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["error"]["code"] == "provider_error"


def test_research_success_payload_returns_exit_0(monkeypatch, capsys):
    class Stub:
        def research_by_category(self, query, **kwargs):
            return json.dumps({"query": query, "results": [{"title": "paper"}]})

    monkeypatch.setattr(cli, "_build_toolbox", lambda args: Stub())
    assert main(["research", "quantum"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"] == [{"title": "paper"}]
