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
    gossamer download https://example.com/paper.pdf -o paper.pdf --expect-format pdf
    gossamer locate-pdf 10.1234/abc
    gossamer skills install

Output is the same JSON the MCP tools return (one document per call).
Auth and options resolve exactly like the MCP server: explicit flags >
``GOSSAMER_*`` env > keystore file > ``gossamer.json`` > defaults.
``--config`` / ``--keystore`` set the corresponding env vars for the run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


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
    p.add_argument("query", help="The search query / research topic")
    p.add_argument("--max-results", type=int, default=5,
                   help="Provider results to request (default 5)")
    p.add_argument("--search-only", action="store_true",
                   help="Return provider results only; skip page fetching")
    p.add_argument("--provider", default=None,
                   help="Search engine to prefer (duckduckgo/google/bing/exa/browser)")
    p.add_argument("--depth", type=int, default=5,
                   help="Candidate pages to fetch when not --search-only (default 5)")
    p.add_argument("--max-tokens", type=int, default=0,
                   help="Token budget for the fetched-page corpus")
    _common(p)

    p = sub.add_parser("research", help="Domain-routed research (patents, law, finance, …)")
    p.add_argument("query", help="The research topic")
    p.add_argument("--max-results", type=int, default=5,
                   help="Provider results to request (default 5)")
    p.add_argument("--category", default=None,
                   help="Research category (scholarly/legal/patent/financial/geo); "
                        "default auto-routes the query")
    p.add_argument("--provider", default=None,
                   help="Provider within the category (default: category default)")
    p.add_argument("--filter", default=None,
                   help="OpenAlex-native filter (only with --provider openalex)")
    p.add_argument("--select", default=None,
                   help="OpenAlex-native field projection (only with --provider openalex)")
    p.add_argument("--title", default=None,
                   help="OpenAlex title search via verified title.search filter (only with --provider openalex)")
    p.add_argument("--author", default=None,
                   help="OpenAlex author search via verified raw_author_name.search filter (only with --provider openalex)")
    p.add_argument("--providers", nargs="+", default=None,
                   help="Explicit sequential scholarly providers to merge (mutually exclusive with --provider)")
    _common(p)

    p = sub.add_parser("categories", help="List research categories + providers")
    _common(p)

    p = sub.add_parser("inspect", help="Fetch one page as markdown")
    p.add_argument("url", help="The URL to fetch")
    p.add_argument("--query", default=None)
    p.add_argument("--use-smart", default="auto", choices=("auto", "browser", "static"))
    p.add_argument("--offset", type=int, default=0,
                   help="Skip the first N follow-up links when paging")
    p.add_argument("--max-chunks", type=int, default=1,
                   help="Follow-up link chunks to return after the page")
    p.add_argument("--structured", action="store_true",
                   help="Return structured payload (metadata/tables/links)")
    _common(p)

    p = sub.add_parser("batch", help="Fetch several pages at once")
    p.add_argument("urls", nargs="+", help="URLs to fetch")
    _common(p)

    p = sub.add_parser("download", help="Download a remote file to a local path")
    p.add_argument("source", help="Remote HTTP(S) URL")
    p.add_argument("-o", "--output", dest="output_path", required=True,
                   help="Destination file path")
    p.add_argument("--min-bytes", type=int, default=1)
    p.add_argument("--max-bytes", type=int, default=0,
                   help="Maximum bytes (0 uses configured max_response_bytes)")
    p.add_argument("--expect-format", default="auto", choices=("auto", "pdf"),
                   help="Validate PDF magic when set to pdf (auto infers from .pdf output)")
    p.add_argument("--overwrite", action="store_true",
                   help="Replace an existing destination")
    p.add_argument("--try-mirrors", nargs="+", default=None, metavar="URL",
                   help="Try caller-supplied alternate URLs sequentially after the primary URL fails")
    p.add_argument("--resume", action="store_true",
                   help="Continue an existing partial file with Range/If-Range when the server supports it")
    _common(p)

    p = sub.add_parser("locate-pdf", help="Find OA PDF/landing-page candidates for a DOI")
    p.add_argument("doi", help="DOI (bare, doi: prefix, or resolver URL)")
    _common(p)

    p = sub.add_parser("extract", help="Extract a document (PDF/DOCX/XLSX/…) or feed")
    p.add_argument("source", help="Local file path or remote URL")
    p.add_argument("--pages", default=None, help="PDF page range, e.g. 10-20")
    p.add_argument("--structured", action="store_true",
                   help="Structured payload (metadata/tables/links) where supported")
    p.add_argument("--store", action="store_true",
                   help="Write original + markdown under --store-dir")
    p.add_argument("--store-dir", default=None,
                   help="Directory for the stored original + extracted "
                        "markdown/resources (with --store)")
    p.add_argument("--include-images", action="store_true",
                   help="PDF only, needs --store: also save figures")
    p.add_argument("--tables-as", default="json",
                   choices=("markdown", "csv", "json"),
                   help="Rendering of spreadsheet (XLSX) tables (default: json)")
    _common(p)

    p = sub.add_parser("check", help="Probe URL reachability")
    p.add_argument("urls", nargs="+", help="URLs to probe")
    p.add_argument("--mode", default="status", choices=("status", "content"),
                   help="status = HEAD probe (default); content = GET, for hosts "
                        "that reject HEAD")
    _common(p)

    p = sub.add_parser("discover", help="Find feeds + sitemap pages for a site")
    p.add_argument("url", help="Site URL to probe for feeds and sitemaps")
    _common(p)

    p = sub.add_parser("crawl", help="Relevance-ranked link-graph traversal")
    p.add_argument("root_url", help="Root URL to start the traversal")
    p.add_argument("--query", default=None)
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--max-pages", type=int, default=15)
    p.add_argument("--same-host", action="store_true")
    p.add_argument("--min-score", type=float, default=0.05,
                   help="Minimum relevance score to keep a page")
    p.add_argument("--excerpts", action="store_true",
                   help="Add a keyword-densest 300-char excerpt per page "
                        "(raises the payload; pair with a lower --max-pages)")
    p.add_argument("--search-prior", action="store_true",
                   help="Run one site-scoped web search first and feed its top-5 "
                        "into the frontier, exempt from --min-score")
    p.add_argument("--seed-urls", nargs="*", default=None,
                   help="Extra starting URLs, SSRF-checked and pushed at "
                        "depth 0 (their children are depth 1)")
    p.add_argument("--use-smart", default="auto",
                   choices=("auto", "browser", "static"),
                   help="Render strategy: auto (default), browser (stealth "
                        "engine first), or static (never renders)")
    _common(p)

    p = sub.add_parser("cache", help="Cache maintenance (prune/clear/reset)")
    p.add_argument("--action", default="prune",
                   choices=("prune", "clear", "reset"),
                   help="prune drops expired/over-cap entries (keeps valid ones "
                        "and visited URLs); clear wipes caches plus visited URLs; "
                        "reset forgets visited URLs only")
    _common(p)

    p = sub.add_parser("cite", help="Format DOIs/URLs as citations")
    p.add_argument("results", nargs="+",
                   help="DOIs, URLs, local PDF paths, or JSON result objects")
    p.add_argument("--style", default="bibtex",
                   choices=("bibtex", "csl-json", "apa", "mla"))
    p.add_argument("--enrich", action="store_true",
                   help="One canonical DOI lookup per unique DOI to fill in a "
                        "missing venue/abstract (best-effort)")
    p.add_argument("--no-dedupe", action="store_true",
                   help="Keep records sharing a DOI/URL instead of collapsing them")
    p.add_argument("--from-pdf", action="store_true",
                   help="Treat every input as a local PDF and cite its detected DOI")
    _common(p)

    p = sub.add_parser("skills", help="Bundled agent skill packs (list/show/install)")
    skills_sub = p.add_subparsers(dest="skills_command", required=True)
    skills_sub.add_parser("list",
                          help="List the bundled skills and what triggers them")
    sp = skills_sub.add_parser("show", help="Print one bundled SKILL.md verbatim")
    sp.add_argument("name", help="Bundled skill name (see `gossamer skills list`)")
    sp = skills_sub.add_parser(
        "install", help="Copy the bundled skills into agent discovery directories")
    sp.add_argument("--dest", action="append", default=None, metavar="DIR",
                    help="Skills directory to install into (repeatable). Default: "
                         "~/.claude/skills and ~/.config/opencode/skills")
    sp.add_argument("--project", action="store_true",
                    help="Install into ./.claude/skills and ./.opencode/skills instead")
    sp.add_argument("--skill", action="append", default=None, metavar="NAME",
                    help="Only this skill (repeatable). Default: every bundled skill")
    sp.add_argument("--force", action="store_true",
                    help="Overwrite existing skill directories")

    return parser


def _configure_utf8_stdio() -> None:
    """Prefer UTF-8 CLI text output without breaking embedded/captured streams."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, TypeError, ValueError):
            # Some wrapped/closed streams cannot be reconfigured. Leave those
            # streams alone; normal terminals and redirected Python streams
            # support reconfigure().
            continue


def _run_skills(args) -> int:
    """Handle ``gossamer skills …`` (plain-text output; no toolbox needed)."""
    from gossamer.skills import (
        SkillNotFound,
        default_destinations,
        install as install_skills,
        iter_skills,
        skill_text,
    )

    if args.skills_command == "list":
        for name, description in iter_skills():
            print(f"{name}\n    {description}")
        print("Install with `gossamer skills install`.")
        return 0

    if args.skills_command == "show":
        try:
            text = skill_text(args.name)
        except SkillNotFound as exc:
            print(f"gossamer: error: {exc.args[0]}", file=sys.stderr)
            return 1
        print(text, end="")
        return 0

    if args.skills_command == "install":
        if args.dest and args.project:
            print("gossamer: error: --dest and --project are mutually exclusive",
                  file=sys.stderr)
            return 1
        targets = (
            [Path(d) for d in args.dest] if args.dest
            else list(default_destinations(project=args.project))
        )
        try:
            results = [
                result
                for target in targets
                for result in install_skills(target, names=args.skill, force=args.force)
            ]
        except SkillNotFound as exc:
            print(f"gossamer: error: {exc.args[0]}", file=sys.stderr)
            return 1
        for result in results:
            print(f"{result.status:<9} {result.path}")
        skipped = sum(result.status == "skipped" for result in results)
        if skipped:
            print(f"{skipped} skill(s) already present; use --force to overwrite.")
        return 0

    print(f"gossamer: error: unknown skills command {args.skills_command!r}",
          file=sys.stderr)
    return 1


def main(argv=None) -> int:
    _configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "skills":
        # Skill management needs no toolbox (no cache/config side effects).
        return _run_skills(args)
    toolbox = _build_toolbox(args)
    commands = {
        "search": lambda: toolbox.web_search(
            args.query, search_only=args.search_only,
            max_results=args.max_results, depth=args.depth,
            max_tokens=args.max_tokens, provider=args.provider),
        "research": lambda: toolbox.research_by_category(
            args.query, max_results=args.max_results,
            category=args.category, provider=args.provider,
            filter=args.filter, select=args.select, providers=args.providers,
            title=args.title, author=args.author),
        "categories": lambda: toolbox.research_categories(),
        "inspect": lambda: toolbox.inspect_html_page(
            args.url, use_smart=args.use_smart, query=args.query,
            offset=args.offset, max_chunks=args.max_chunks,
            structured=args.structured),
        "batch": lambda: toolbox.batch_inspect_pages(args.urls),
        "download": lambda: toolbox.download_file(
            args.source, args.output_path, min_bytes=args.min_bytes,
            max_bytes=args.max_bytes, expected_format=args.expect_format,
            overwrite=args.overwrite, fallback_urls=args.try_mirrors, resume=args.resume),
        "locate-pdf": lambda: toolbox.locate_pdf(args.doi),
        "extract": lambda: toolbox.extract_document(
            args.source, pages=args.pages, structured=args.structured,
            store=args.store, store_dir=args.store_dir,
            include_images=args.include_images, tables_as=args.tables_as),
        "check": lambda: toolbox.check_sources(args.urls, mode=args.mode),
        "discover": lambda: toolbox.discover_resources(args.url),
        "crawl": lambda: toolbox.crawl(
            args.root_url, query=args.query, max_depth=args.max_depth,
            max_pages=args.max_pages, same_host=args.same_host,
            min_score=args.min_score,
            excerpts=args.excerpts, search_prior=args.search_prior,
            seed_urls=args.seed_urls, use_smart=args.use_smart),
        "cache": lambda: toolbox.manage_cache(args.action),
        "cite": lambda: toolbox.export_citations(
            args.results, style=args.style, enrich=args.enrich,
            dedupe=not args.no_dedupe, from_pdf=args.from_pdf),
    }
    try:
        output = commands[args.command]()
        print(output)
    except (ValueError, RuntimeError) as exc:
        print(f"gossamer: error: {exc}", file=sys.stderr)
        return 1

    if args.command in {"research", "download", "locate-pdf"}:
        try:
            payload = json.loads(output) if isinstance(output, str) else output
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
