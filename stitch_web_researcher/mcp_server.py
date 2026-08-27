"""
MCP server exposing stitch-web-researcher as Model Context Protocol tools.

Runs over stdio so any MCP client (pi, Claude Desktop, Cursor, …) can call
the web-research toolbox directly:

    python -m stitch_web_researcher.mcp_server

Requires the optional ``mcp`` dependency (v2):

    pip install "stitch-web-researcher[mcp]"   # or: uv pip install "mcp>=2"

Configuration via environment variables (all optional):
    STITCH_CACHE_DIR            (default ".web_research_cache")
    STITCH_CACHE_TTL_SECONDS    (default 3600)
    STITCH_DDGS_DELAY           (default 1.0)
    STITCH_DOMAIN_DELAY         (default 0.5)
    STITCH_FETCH_DELAY          (default: unset — provider default applies)
    STITCH_MAX_MARKDOWN_CHARS   (default 8000)
    STITCH_MAX_TOKENS           (default 0 = unlimited)
    STITCH_MODEL_NAME           (default "gpt-4o")
    STITCH_MAX_LINKS            (default 20)
    STITCH_FETCH_MODE           (default "auto"; auto|browser|static)
    STITCH_CANDIDATE_CAP        (default 500)
    STITCH_MAX_CONCURRENCY      (default 8)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Toolbox singleton (lazily constructed from environment)
# ────────────────────────────────────────────────────────────────

_toolbox: Optional[WebResearcherToolbox] = None
_toolbox_lock = threading.Lock()


def _env(name: str, default=None, cast=str):
    raw = os.environ.get(name)
    return cast(raw) if raw not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _config_from_env() -> ToolboxConfig:
    fetch_delay = _env("STITCH_FETCH_DELAY", None, float)
    return ToolboxConfig(
        cache_dir=_env("STITCH_CACHE_DIR", ".web_research_cache"),
        cache_ttl_seconds=_env("STITCH_CACHE_TTL_SECONDS", 3600, int),
        ddgs_delay=_env("STITCH_DDGS_DELAY", 1.0, float),
        domain_delay=_env("STITCH_DOMAIN_DELAY", 0.5, float),
        fetch_delay=fetch_delay,
        max_markdown_chars=_env("STITCH_MAX_MARKDOWN_CHARS", 8000, int),
        max_tokens=_env("STITCH_MAX_TOKENS", 0, int),
        model_name=_env("STITCH_MODEL_NAME", "gpt-4o"),
        max_links=_env("STITCH_MAX_LINKS", 20, int),
        fetch_mode=_env("STITCH_FETCH_MODE", "auto"),
        candidate_cap=_env("STITCH_CANDIDATE_CAP", 500, int),
        max_concurrency=_env("STITCH_MAX_CONCURRENCY", 8, int),
        # S4: robots.txt compliance; operators can opt out explicitly.
        respect_robots=_env_bool("STITCH_RESPECT_ROBOTS", True),
    )


def get_toolbox() -> WebResearcherToolbox:
    """Return the process-wide toolbox, constructing it on first use."""
    global _toolbox
    if _toolbox is None:
        with _toolbox_lock:
            if _toolbox is None:
                config = _config_from_env()
                logger.info(
                    "Starting stitch-web-researcher MCP server "
                    "(max_tokens=%s, model=%s, fetch_mode=%s)",
                    config.max_tokens,
                    config.model_name,
                    config.fetch_mode,
                )
                _toolbox = WebResearcherToolbox(config)
    return _toolbox


def reset_toolbox() -> None:
    """Drop the singleton (used by tests)."""
    global _toolbox
    with _toolbox_lock:
        _toolbox = None


# ────────────────────────────────────────────────────────────────
# Server definition
# ────────────────────────────────────────────────────────────────

INSTRUCTIONS = (
    "Web research toolkit: search the web, fetch pages as LLM-friendly "
    "markdown with follow-up links, and extract text/tables from documents "
    "(PDF, DOCX, XLSX, PPTX). Results are cached, rate-limited per domain, "
    "and token-budgeted."
)


def build_server() -> MCPServer:
    """Build a fully-wired MCPServer instance (no I/O)."""
    server: MCPServer = MCPServer(
        "stitch-web-researcher",
        instructions=INSTRUCTIONS,
    )

    @server.tool()
    def search_web(query: str, max_results: int = 5, provider: str = "duckduckgo") -> str:
        """Search the web and return JSON results (title, url, snippet).

        Args:
            query: The search query.
            max_results: Maximum number of results (1-20).
            provider: Preferred engine: duckduckgo | google | bing | exa.
                Falls back through the others on failure.
        """
        return get_toolbox().search_web(
            query, max_results=max(max_results, 1), provider=provider
        )

    @server.tool()
    def inspect_html_page(url: str, use_smart: bool = False) -> str:
        """Fetch a web page and return JSON: markdown content, follow-up
        links (typed page/document), metadata, and truncation flags.

        Args:
            url: Absolute http(s) URL of the page.
            use_smart: Use headless JS rendering for SPA/anti-bot pages.
        """
        return get_toolbox().inspect_html_page(url, use_smart=use_smart)

    @server.tool()
    def batch_inspect_pages(urls: List[str]) -> str:
        """Fetch multiple pages concurrently; same-domain requests are
        staggered automatically. Returns a JSON array with one entry per URL.

        Args:
            urls: Absolute http(s) URLs to inspect.
        """
        return get_toolbox().batch_inspect_pages(urls)

    @server.tool()
    def extract_document(source: str) -> str:
        """Extract plain-text/markdown content from a document (PDF, DOCX,
        XLSX, PPTX) given a URL or local file path. Results are cached.

        Args:
            source: Document URL or local path.
        """
        return get_toolbox().extract_document(source)

    @server.tool()
    def extract_document_structured(source: str) -> str:
        """Extract structured content (metadata, per-page text/markdown,
        tables) from PDF, DOCX, XLSX, or PPTX as validated JSON.

        Args:
            source: Document URL or local path.
        """
        return get_toolbox().extract_document_structured(source)

    @server.tool()
    def inspect_html_structured(url: str, use_smart: bool = False) -> str:
        """Fetch a web page as a unified structured payload: metadata
        (Open Graph, Twitter, JSON-LD), markdown content, and links.

        Args:
            url: Absolute http(s) URL of the page.
            use_smart: Use headless JS rendering for JS-heavy pages.
        """
        return get_toolbox().inspect_html_structured(url, use_smart=use_smart)

    @server.tool()
    def clear_cache() -> str:
        """Clear both in-memory and disk research caches and the visited-URL
        set (forces fresh fetches on subsequent calls)."""
        return get_toolbox().clear_cache()

    @server.tool()
    def reset_visited() -> str:
        """Forget all previously visited URLs so they can be fetched again.

        Unlike clear_cache, the caches are NOT touched — a visited URL whose
        result is still cached keeps being served from the cache. Use this
        after a fetch failure you want to retry, or when starting a new
        research session on the same pages.
        """
        get_toolbox().reset_visited()
        return json.dumps({"visited_cleared": True, "visited_urls_count": 0}, indent=2)

    @server.tool()
    def get_stats() -> str:
        """Return toolbox statistics: visited URLs, cache hit rate and size,
        token budget settings."""
        return get_toolbox().get_stats()

    return server


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    import asyncio

    logging.basicConfig(level=_env("STITCH_LOG_LEVEL", "INFO"))
    server = build_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
