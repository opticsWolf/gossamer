"""
MCP server exposing gossamer as Model Context Protocol tools.

Runs over stdio so any MCP client (pi, Claude Desktop, Cursor, …) can call
the web-research toolbox directly:

    python -m gossamer.mcp_server

Requires the optional ``mcp`` dependency (v2):

    pip install "gossamer-web[mcp]"   # or: uv pip install "mcp>=2"

Configuration via environment variables (all optional):
    GOSSAMER_CACHE_DIR            (default ".gossamer_cache")
    GOSSAMER_CACHE_TTL_SECONDS    (default 3600)
    GOSSAMER_CACHE_MAX_BYTES      (default 0 = unlimited; disk LRU eviction cap)
    GOSSAMER_CACHE_MEMORY_ENTRIES (default 100; memory-tier LRU entry cap)
    GOSSAMER_MAX_RESPONSE_BYTES   (default 5242880; page + document size cap;
                                   legacy GOSSAMER_WEB_RESEARCHER_MAX_RESPONSE_BYTES honored)
    GOSSAMER_LIVENESS_TIMEOUT     (default 10.0; per-URL check_sources probe timeout)
    GOSSAMER_LOG_LEVEL            (default INFO; MCP server log level)
    GOSSAMER_DDGS_DELAY           (default 1.0; search interval seconds)
    GOSSAMER_DDGS_JITTER          (default 1.0; max random s added to the DDG search gap)
    GOSSAMER_DOMAIN_DELAY         (default 0.5)
    GOSSAMER_FETCH_DELAY          (default: unset — provider default applies)
    GOSSAMER_FETCH_JITTER         (default 1.0; max random s added to the per-domain fetch gap)
    GOSSAMER_MAX_MARKDOWN_CHARS   (default 8000)
    GOSSAMER_MAX_TOKENS           (default 0 = unlimited)
    GOSSAMER_MODEL_NAME           (default "gpt-4o")
    GOSSAMER_MAX_LINKS            (default 20)
    GOSSAMER_FETCH_MODE           (default "auto"; auto|browser|static)
    GOSSAMER_CANDIDATE_CAP        (default 500)
    GOSSAMER_MAX_CONCURRENCY      (default 8)
    GOSSAMER_RESPECT_ROBOTS       (default 1)
    GOSSAMER_CONDITIONAL_REVALIDATE (default 1)
    GOSSAMER_RUST_LOG             (default unset = off; error|warn|info|debug -- bridge Rust log events into Python logging)
    GOSSAMER_HTTP_PROXY           (default unset -- e.g. http://proxy:8080; baked into the shared HTTP client)
    GOSSAMER_USER_AGENT           (default unset -- override the desktop-Chrome User-Agent)
    GOSSAMER_CUSTOM_HEADERS       (default {} -- JSON object, e.g. {"Authorization": "Bearer ..."})
    GOSSAMER_COOKIES              (default {} -- JSON object, e.g. {"session": "abc123"})
    GOSSAMER_SEARCH_MERGE           (default 0 -- 1/true: cross-provider merge for search_web)

Legacy STITCH_* spellings of every variable above remain honored as a
fallback (the package was renamed from stitch-web-researcher). Unset env
vars inherit the matching ``gossamer.json`` value; the CLI resolves the
same options via :mod:`gossamer.runtime_config` (explicit CLI flags win).
    GOSSAMER_GUARD_ENABLED          (default 0 -- §7 prompt-injection guard off)
    GOSSAMER_GUARD_SCOPES           (default "page_markdown,document_text")
    GOSSAMER_GUARD_MODE             (default "annotate"; annotate|redact|block)
    GOSSAMER_GUARD_THRESHOLD        (default 0.7)
    GOSSAMER_GUARD_MAX_CHUNKS       (default 40)
"""

from __future__ import annotations

import inspect
import logging
import threading
from typing import Annotated, Optional

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from gossamer.agent_tools import (
    TOOL_REGISTRY,
    WebResearcherToolbox,
)
from gossamer.runtime_config import (
    config_from_env as _config_from_env,
    env_json_dict as _env_json_dict,  # noqa: F401 -- backwards-compatible re-export
    env_value as _env,
)
from gossamer.skills import skill_description, skill_text

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Toolbox singleton (lazily constructed from environment)
# ────────────────────────────────────────────────────────────────

_toolbox: Optional[WebResearcherToolbox] = None
_toolbox_lock = threading.Lock()


def get_toolbox() -> WebResearcherToolbox:
    """Return the process-wide toolbox, constructing it on first use."""
    global _toolbox
    if _toolbox is None:
        with _toolbox_lock:
            if _toolbox is None:
                config = _config_from_env()
                logger.info(
                    "Starting gossamer MCP server "
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
    "(PDF, DOCX, XLSX, PPTX) and text formats (TXT, MD, CSV, JSON, XML, "
    "RSS/Atom). Results are cached, rate-limited per domain, and "
    "token-budgeted.\n\n"
    "## Rendering (JS pages / anti-bot)\n\n"
    "Page fetches use a render strategy: `auto` (static first; stealth "
    "browser on failure/non-text), `browser` (stealth first, static "
    "fallback), or `static` (never renders). Select it per call with "
    "`use_smart` on `web_search` (when not search_only), "
    "`inspect_html_page`, `batch_inspect_pages`, and `crawl`, or set the "
    "process default with the `GOSSAMER_FETCH_MODE` environment variable. "
    "Browser rendering needs the optional `gossamer-web[browser]` extra; "
    "`provider='browser'` is the corresponding stealth search engine.\n\n"
    "## Skill pack\n\n"
    "The `gossamer` agent skill ships inside this package and is readable "
    "as the resource `skills://gossamer` (routing, budgets, auth, cache "
    "behavior, and the literature-collection recipe). Run "
    "`gossamer skills show gossamer` to print it, or `gossamer skills "
    "install` to copy it into Claude Code / OpenCode discovery directories."
)


def _mcp_tool_for(spec):
    """Build an MCP tool wrapper for one registry entry (P8).

    The wrapper's name, docstring, and signature are generated from the
    registry, so the MCP input schema can never drift from the LLM
    definitions. Every call dispatches through ``execute_tool``."""
    parameters = []
    arg_lines = []
    annotations = {}
    for p in spec.params:
        # Preserve the registry's per-parameter description and enum in the
        # MCP input schema. A bare annotation would drop both, leaving an
        # agent with only the prose docstring instead of a machine-readable
        # schema (the LLM function definitions already carry both).
        annotation = p.type
        field_kwargs = {}
        if p.description:
            field_kwargs["description"] = p.description
        if p.enum:
            field_kwargs["json_schema_extra"] = {"enum": list(p.enum)}
        if field_kwargs:
            annotation = Annotated[p.type, Field(**field_kwargs)]
        default = p.default if not p.required else inspect.Parameter.empty
        parameters.append(
            inspect.Parameter(
                p.name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=annotation,
            )
        )
        annotations[p.name] = annotation
        arg_lines.append(f"    {p.name}: {p.description}".rstrip())

    def wrapper(**kwargs):
        return get_toolbox().execute_tool(spec.name, kwargs)

    wrapper.__name__ = spec.name
    doc = spec.description
    if arg_lines:
        doc += "\n\nArgs:\n" + "\n".join(arg_lines)
    wrapper.__doc__ = doc
    wrapper.__signature__ = inspect.Signature(parameters=parameters)
    annotations["return"] = str
    wrapper.__annotations__ = annotations
    return wrapper


def build_server() -> MCPServer:
    """Build a fully-wired MCPServer instance (no I/O).

    P8: tools are registered from ``TOOL_REGISTRY`` (the single source of
    truth) instead of hand-written per-tool functions, so the MCP surface
    and the LLM definitions cannot drift."""
    server: MCPServer = MCPServer(
        "gossamer",
        instructions=INSTRUCTIONS,
    )
    for spec in TOOL_REGISTRY:
        server.tool()(_mcp_tool_for(spec))

    # Agent-facing document that ships in the wheel. A resource (not a
    # tool) keeps the tool surface intact while letting an MCP-only host
    # read the skill pack without a repo checkout.
    @server.resource(
        "skills://gossamer",
        name="gossamer",
        description=skill_description("gossamer"),
        mime_type="text/markdown",
    )
    def _skill_gossamer() -> str:
        """The gossamer agent skill pack."""
        return skill_text("gossamer")

    return server


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    import asyncio

    logging.basicConfig(level=_env("GOSSAMER_LOG_LEVEL", "INFO"))
    server = build_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
