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
    STITCH_CACHE_MAX_BYTES      (default 0 = unlimited; disk LRU eviction cap)
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
    STITCH_RESPECT_ROBOTS       (default 1)
    STITCH_CONDITIONAL_REVALIDATE (default 1)
    STITCH_RUST_LOG             (default unset = off; error|warn|info|debug -- bridge Rust log events into Python logging)
    STITCH_HTTP_PROXY           (default unset -- e.g. http://proxy:8080; baked into the shared HTTP client)
    STITCH_USER_AGENT           (default unset -- override the desktop-Chrome User-Agent)
    STITCH_CUSTOM_HEADERS       (default {} -- JSON object, e.g. {"Authorization": "Bearer ..."})
    STITCH_COOKIES              (default {} -- JSON object, e.g. {"session": "abc123"})
    STITCH_SEARCH_MERGE           (default 0 -- 1/true: cross-provider merge for search_web)
    STITCH_GUARD_ENABLED          (default 0 -- §7 prompt-injection guard off)
    STITCH_GUARD_SCOPES           (default "page_markdown,document_text")
    STITCH_GUARD_MODE             (default "annotate"; annotate|redact|block)
    STITCH_GUARD_THRESHOLD        (default 0.7)
    STITCH_GUARD_MAX_CHUNKS       (default 40)
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
from typing import Optional

from mcp.server.mcpserver import MCPServer

from stitch_web_researcher.agent_tools import (
    TOOL_REGISTRY,
    ToolboxConfig,
    WebResearcherToolbox,
)
from stitch_web_researcher.guard import GuardConfig

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


def _env_json_dict(name: str) -> dict:
    """Read a JSON object from *name*; return {} when unset or invalid.

    Used for Tier 2.7 transport overrides (custom headers / cookies), which
    are naturally expressed as JSON objects:
    ``STITCH_CUSTOM_HEADERS='{"Authorization": "Bearer ..."}'``.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logging.getLogger(__name__).warning(
            "STITCH env %s is not valid JSON; ignoring", name
        )
        return {}
    if not isinstance(value, dict):
        logging.getLogger(__name__).warning(
            "STITCH env %s must be a JSON object; ignoring", name
        )
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _guard_config_from_env():
    """Build a :class:`GuardConfig` from STITCH_GUARD_* env vars.

    The §7 prompt-injection guard is off by default: it only activates when
    ``STITCH_GUARD_ENABLED`` is truthy (which implies the optional
    ``jailguard`` dependency is installed via ``pip install ...[guard]``).
    """
    if not _env_bool("STITCH_GUARD_ENABLED", False):
        return None
    return GuardConfig(
        enabled=True,
        mode=_env("STITCH_GUARD_MODE", "annotate"),
        scopes=_env("STITCH_GUARD_SCOPES", "page_markdown,document_text"),
        threshold=_env("STITCH_GUARD_THRESHOLD", 0.7, float),
        max_chunks=_env("STITCH_GUARD_MAX_CHUNKS", 40, int),
    )


def _config_from_env() -> ToolboxConfig:
    fetch_delay = _env("STITCH_FETCH_DELAY", None, float)
    return ToolboxConfig(
        cache_dir=_env("STITCH_CACHE_DIR", ".web_research_cache"),
        cache_ttl_seconds=_env("STITCH_CACHE_TTL_SECONDS", 3600, int),
        cache_max_bytes=_env("STITCH_CACHE_MAX_BYTES", 0, int),
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
        # Tier 1.4: revalidate expired cached pages with ETag / Last-Modified
        # before re-downloading; operators can opt out explicitly.
        conditional_revalidation=_env_bool(
            "STITCH_CONDITIONAL_REVALIDATE", True
        ),
        # §7: optional prompt-injection guard, off by default.
        guard=_guard_config_from_env(),
        # Tier 2.7: HTTP transport overrides (proxy / User-Agent / headers /
        # cookies) for authenticated sources. Headers and cookies are JSON
        # objects; proxy and User-Agent are plain strings.
        http_proxy=_env("STITCH_HTTP_PROXY", None),
        user_agent=_env("STITCH_USER_AGENT", None),
        custom_headers=_env_json_dict("STITCH_CUSTOM_HEADERS"),
        cookies=_env_json_dict("STITCH_COOKIES"),
        search_merge=_env_bool("STITCH_SEARCH_MERGE", False),
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


def _mcp_tool_for(spec):
    """Build an MCP tool wrapper for one registry entry (P8).

    The wrapper's name, docstring, and signature are generated from the
    registry, so the MCP input schema can never drift from the LLM
    definitions. Every call dispatches through ``execute_tool``."""
    parameters = []
    arg_lines = []
    for p in spec.params:
        default = p.default if not p.required else inspect.Parameter.empty
        parameters.append(
            inspect.Parameter(
                p.name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=p.type,
            )
        )
        arg_lines.append(f"    {p.name}: {p.description}".rstrip())

    def wrapper(**kwargs):
        return get_toolbox().execute_tool(spec.name, kwargs)

    wrapper.__name__ = spec.name
    doc = spec.description
    if arg_lines:
        doc += "\n\nArgs:\n" + "\n".join(arg_lines)
    wrapper.__doc__ = doc
    wrapper.__signature__ = inspect.Signature(parameters=parameters)
    wrapper.__annotations__ = {p.name: p.type for p in spec.params}
    wrapper.__annotations__["return"] = str
    return wrapper


def build_server() -> MCPServer:
    """Build a fully-wired MCPServer instance (no I/O).

    P8: tools are registered from ``TOOL_REGISTRY`` (the single source of
    truth) instead of hand-written per-tool functions, so the MCP surface
    and the LLM definitions cannot drift."""
    server: MCPServer = MCPServer(
        "stitch-web-researcher",
        instructions=INSTRUCTIONS,
    )
    for spec in TOOL_REGISTRY:
        server.tool()(_mcp_tool_for(spec))
    return server


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    import asyncio

    logging.basicConfig(level=_env("STITCH_LOG_LEVEL", "INFO"))
    server = build_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
