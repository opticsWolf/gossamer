"""
MCP server exposing gossamer as Model Context Protocol tools.

Runs over stdio so any MCP client (pi, Claude Desktop, Cursor, …) can call
the web-research toolbox directly:

    python -m gossamer.mcp_server

Requires the optional ``mcp`` dependency (v2):

    pip install "gossamer[mcp]"   # or: uv pip install "mcp>=2"

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
fallback (the package was renamed from stitch-web-researcher).
    GOSSAMER_GUARD_ENABLED          (default 0 -- §7 prompt-injection guard off)
    GOSSAMER_GUARD_SCOPES           (default "page_markdown,document_text")
    GOSSAMER_GUARD_MODE             (default "annotate"; annotate|redact|block)
    GOSSAMER_GUARD_THRESHOLD        (default 0.7)
    GOSSAMER_GUARD_MAX_CHUNKS       (default 40)
"""

from __future__ import annotations

import inspect
import json
import logging
import threading
from typing import Optional

from mcp.server.mcpserver import MCPServer

from gossamer.agent_tools import (
    TOOL_REGISTRY,
    ToolboxConfig,
    WebResearcherToolbox,
)
from gossamer.guard import GuardConfig

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Toolbox singleton (lazily constructed from environment)
# ────────────────────────────────────────────────────────────────

_toolbox: Optional[WebResearcherToolbox] = None
_toolbox_lock = threading.Lock()


def _env(name: str, default=None, cast=str, legacy=None):
    # A malformed value warns and falls back to the default instead of
    # crashing server startup on one typo (review A.6).
    from gossamer.env import getenv

    raw = getenv(name, None, legacy=legacy)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "GOSSAMER env %s=%r is not valid; using default %r", name, raw, default
        )
        return default


def _env_bool(name: str, default: bool) -> bool:
    from gossamer.env import getenv_bool

    return getenv_bool(name, default)


def _env_json_dict(name: str) -> dict:
    """Read a JSON object from *name*; return {} when unset or invalid.

    Used for Tier 2.7 transport overrides (custom headers / cookies), which
    are naturally expressed as JSON objects:
    ``GOSSAMER_CUSTOM_HEADERS='{"Authorization": "Bearer ..."}'``.
    """
    from gossamer.env import getenv

    raw = getenv(name, None)
    if raw is None or raw.strip() == "":
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logging.getLogger(__name__).warning(
            "GOSSAMER env %s is not valid JSON; ignoring", name
        )
        return {}
    if not isinstance(value, dict):
        logging.getLogger(__name__).warning(
            "GOSSAMER env %s must be a JSON object; ignoring", name
        )
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _guard_config_from_env():
    """Build a :class:`GuardConfig` from GOSSAMER_GUARD_* env vars.

    The §7 prompt-injection guard is off by default: it only activates when
    ``GOSSAMER_GUARD_ENABLED`` is truthy (which implies the optional
    ``jailguard`` dependency is installed via ``pip install ...[guard]``).
    """
    from gossamer.settings import load_config_file

    try:
        file_guard = load_config_file().get("guard", {}) or {}
    except (OSError, ValueError):
        file_guard = {}
    if not isinstance(file_guard, dict):
        file_guard = {}
    if not _env_bool("GOSSAMER_GUARD_ENABLED", file_guard.get("enabled", False)):
        return None
    return GuardConfig(
        enabled=True,
        mode=_env("GOSSAMER_GUARD_MODE", file_guard.get("mode", "annotate")),
        scopes=_env("GOSSAMER_GUARD_SCOPES", file_guard.get("scopes", "page_markdown,document_text")),
        threshold=_env("GOSSAMER_GUARD_THRESHOLD", file_guard.get("threshold", 0.7), float),
        max_chunks=_env("GOSSAMER_GUARD_MAX_CHUNKS", file_guard.get("max_chunks", 40), int),
    )


def _config_from_env() -> ToolboxConfig:
    # Precedence: explicit env > gossamer.json > built-in defaults. Each
    # _env() call falls back to the file value, so an unset variable
    # inherits the file and a malformed one warns + falls back (A.6).
    from gossamer.settings import load_config_file

    try:
        file_cfg = load_config_file()
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("ignoring gossamer.json: %s", exc)
        file_cfg = {}

    def _f(key, default):
        return file_cfg.get(key, default)

    fetch_delay = _env("GOSSAMER_FETCH_DELAY", _f("fetch_delay", None), float)
    return ToolboxConfig(
        cache_dir=_env("GOSSAMER_CACHE_DIR", _f("cache_dir", ".gossamer_cache")),
        cache_ttl_seconds=_env("GOSSAMER_CACHE_TTL_SECONDS", _f("cache_ttl_seconds", 3600), int),
        cache_max_bytes=_env("GOSSAMER_CACHE_MAX_BYTES", _f("cache_max_bytes", 0), int),
        cache_memory_entries=_env("GOSSAMER_CACHE_MEMORY_ENTRIES", _f("cache_memory_entries", 100), int),
        max_response_bytes=_env(
            "GOSSAMER_MAX_RESPONSE_BYTES", _f("max_response_bytes", 5 * 1024 * 1024), int,
            legacy="STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES",
        ),
        liveness_timeout=_env("GOSSAMER_LIVENESS_TIMEOUT", _f("liveness_timeout", 10.0), float),
        ddgs_delay=_env("GOSSAMER_DDGS_DELAY", _f("ddgs_delay", 1.0), float),
        ddgs_jitter=_env("GOSSAMER_DDGS_JITTER", _f("ddgs_jitter", 1.0), float),
        domain_delay=_env("GOSSAMER_DOMAIN_DELAY", _f("domain_delay", 0.5), float),
        fetch_delay=fetch_delay,
        fetch_jitter=_env("GOSSAMER_FETCH_JITTER", _f("fetch_jitter", 1.0), float),
        max_markdown_chars=_env("GOSSAMER_MAX_MARKDOWN_CHARS", _f("max_markdown_chars", 8000), int),
        max_tokens=_env("GOSSAMER_MAX_TOKENS", _f("max_tokens", 0), int),
        model_name=_env("GOSSAMER_MODEL_NAME", _f("model_name", "gpt-4o")),
        max_links=_env("GOSSAMER_MAX_LINKS", _f("max_links", 20), int),
        fetch_mode=_env("GOSSAMER_FETCH_MODE", _f("fetch_mode", "auto")),
        candidate_cap=_env("GOSSAMER_CANDIDATE_CAP", _f("candidate_cap", 500), int),
        max_concurrency=_env("GOSSAMER_MAX_CONCURRENCY", _f("max_concurrency", 8), int),
        # S4: robots.txt compliance; operators can opt out explicitly.
        respect_robots=_env_bool("GOSSAMER_RESPECT_ROBOTS", _f("respect_robots", True)),
        # Tier 1.4: revalidate expired cached pages with ETag / Last-Modified
        # before re-downloading; operators can opt out explicitly.
        conditional_revalidation=_env_bool(
            "GOSSAMER_CONDITIONAL_REVALIDATE", _f("conditional_revalidation", True)
        ),
        # §7: optional prompt-injection guard, off by default.
        guard=_guard_config_from_env(),
        # Tier 2.7: HTTP transport overrides (proxy / User-Agent / headers /
        # cookies) for authenticated sources. Headers and cookies are JSON
        # objects; proxy and User-Agent are plain strings.
        http_proxy=_env("GOSSAMER_HTTP_PROXY", _f("http_proxy", None)),
        user_agent=_env("GOSSAMER_USER_AGENT", _f("user_agent", None)),
        custom_headers=_env_json_dict("GOSSAMER_CUSTOM_HEADERS") or _f("custom_headers", {}),
        cookies=_env_json_dict("GOSSAMER_COOKIES") or _f("cookies", {}),
        search_merge=_env_bool("GOSSAMER_SEARCH_MERGE", _f("search_merge", False)),
    )


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
    "token-budgeted."
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
        "gossamer",
        instructions=INSTRUCTIONS,
    )
    for spec in TOOL_REGISTRY:
        server.tool()(_mcp_tool_for(spec))
    return server


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    import asyncio

    logging.basicConfig(level=_env("GOSSAMER_LOG_LEVEL", "INFO"))
    server = build_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
