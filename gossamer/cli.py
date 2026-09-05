"""Direct shell access to every toolbox method (no MCP hop).

For harnesses that prefer Bash over MCP (Codex, Claude Code without an MCP
entry, pi shell commands, cron jobs)::

    python -m gossamer.cli search "quantum error correction" --max-results 5
    gossamer research "ECB euro exchange rate" --provider frankfurter
    gossamer inspect https://example.com/paper --query "methods"
    gossamer batch URL1 URL2
    gossamer extract report.pdf --pages 1-3
    gossamer check URL1 URL2 --mode content
    gossamer categories
    gossamer discover https://example.com
    gossamer crawl https://example.com --query "pricing" --max-pages 10
    gossamer cache --action prune
    gossamer cite 10.1234/abc https://example.com/paper --style apa

Output is the same JSON the MCP tools return (one document per call).
Auth and options resolve exactly like the MCP server: explicit flags >
``GOSSAMER_*`` env > keystore file > ``gossamer.json`` > defaults.
``--config`` / ``--keystore`` set the corresponding env vars for the run.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def _build_toolbox(args) -> object:
    from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox

    if getattr(args, "config", None):
        os.environ["GOSSAMER_CONFIG"] = args.config
    if getattr(args, "keystore", None):
        os.environ["GOSSAMER_KEYSTORE"] = args.keystore
    if getattr(args, "log_level", None):
        logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    kwargs = {}
    if getattr(args, "cache_dir", None):
        kwargs["cache_dir"] = args.cache_dir
    return WebResearcherToolbox(ToolboxConfig(**kwargs))


def _common(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS so a flag given before the subcommand is not clobbered by the
    # subparser default; the top-level parser sets real defaults (see below).
    parser.add_argument("--cache-dir", default=argparse.SUPPRESS,
                        help="Cache directory (default: ./.gossamer_cache)")
    parser.add_argument("--config", default=argparse.SUPPRESS,
                        help="Explicit gossamer.json path")
    parser.add_argument("--keystore", default=argparse.SUPPRESS,
                        help="Explicit keys.json path")
    parser.add_argument("--log-level", default=argparse.SUPPRESS,
                        help="DEBUG/INFO/WARNING/ERROR (default WARNING)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gossamer", description="Web research toolkit (direct CLI)")
    # Global flags work before the subcommand too (also accepted after it).
    _common(parser)
    parser.set_defaults(cache_dir=None, config=None, keystore=None,
                         log_level=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="Web search (+ optional fetch)")
    p.add_argument("query")
    p.add_argument("--max-results", type=int, default=5)
    p.add_argument("--search-only", action="store_true")
    p.add_argument("--provider", default=None)
    p.add_argument("--depth", type=int, default=5)
    _common(p)

    p = sub.add_parser("research", help="Domain-routed research (patents, law, finance, …)")
    p.add_argument("query")
    p.add_argument("--max-results", type=int, default=5)
    p.add_argument("--category", default=None)
    p.add_argument("--provider", default=None)
    _common(p)

    p = sub.add_parser("categories", help="List research categories + providers")
    _common(p)

    p = sub.add_parser("inspect", help="Fetch one page as markdown")
    p.add_argument("url")
    p.add_argument("--query", default=None)
    p.add_argument("--use-smart", default="auto", choices=("auto", "browser", "static"))
    _common(p)

    p = sub.add_parser("batch", help="Fetch several pages at once")
    p.add_argument("urls", nargs="+")
    _common(p)

    p = sub.add_parser("extract", help="Extract a document (PDF/DOCX/XLSX/…) or feed")
    p.add_argument("source")
    p.add_argument("--pages", default=None, help="PDF page range, e.g. 10-20")
    p.add_argument("--structured", action="store_true")
    _common(p)

    p = sub.add_parser("check", help="Probe URL reachability")
    p.add_argument("urls", nargs="+")
    p.add_argument("--mode", default="status", choices=("status", "content"))
    _common(p)

    p = sub.add_parser("discover", help="Find feeds + sitemap pages for a site")
    p.add_argument("url")
    _common(p)

    p = sub.add_parser("crawl", help="Relevance-ranked link-graph traversal")
    p.add_argument("root_url")
    p.add_argument("--query", default=None)
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--max-pages", type=int, default=15)
    p.add_argument("--same-host", action="store_true")
    p.add_argument("--excerpts", action="store_true")
    p.add_argument("--search-prior", action="store_true")
    p.add_argument("--seed-urls", nargs="*", default=None)
    p.add_argument("--use-smart", default="auto",
                   choices=("auto", "browser", "static"))
    _common(p)

    p = sub.add_parser("cache", help="Cache maintenance (prune/clear/reset)")
    p.add_argument("--action", default="prune",
                   choices=("prune", "clear", "reset"))
    _common(p)

    p = sub.add_parser("cite", help="Format DOIs/URLs as citations")
    p.add_argument("results", nargs="+",
                   help="DOIs, URLs, or JSON result objects")
    p.add_argument("--style", default="bibtex",
                   choices=("bibtex", "csl-json", "apa", "mla"))
    p.add_argument("--enrich", action="store_true")
    p.add_argument("--no-dedupe", action="store_true")
    _common(p)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    toolbox = _build_toolbox(args)
    commands = {
        "search": lambda: toolbox.web_search(
            args.query, search_only=args.search_only,
            max_results=args.max_results, depth=args.depth,
            provider=args.provider),
        "research": lambda: toolbox.research_by_category(
            args.query, max_results=args.max_results,
            category=args.category, provider=args.provider),
        "categories": lambda: toolbox.research_categories(),
        "inspect": lambda: toolbox.inspect_html_page(
            args.url, use_smart=args.use_smart, query=args.query),
        "batch": lambda: toolbox.batch_inspect_pages(args.urls),
        "extract": lambda: toolbox.extract_document(
            args.source, pages=args.pages, structured=args.structured),
        "check": lambda: toolbox.check_sources(args.urls, mode=args.mode),
        "discover": lambda: toolbox.discover_resources(args.url),
        "crawl": lambda: toolbox.crawl(
            args.root_url, query=args.query, max_depth=args.max_depth,
            max_pages=args.max_pages, same_host=args.same_host,
            excerpts=args.excerpts, search_prior=args.search_prior,
            seed_urls=args.seed_urls, use_smart=args.use_smart),
        "cache": lambda: toolbox.manage_cache(args.action),
        "cite": lambda: toolbox.export_citations(
            args.results, style=args.style, enrich=args.enrich,
            dedupe=not args.no_dedupe),
    }
    try:
        print(commands[args.command]())
    except (ValueError, RuntimeError) as exc:
        print(f"gossamer: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
