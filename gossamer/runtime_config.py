"""Runtime configuration shared by every gossamer surface.

The MCP server and the direct CLI must resolve options identically:
explicit flags > ``GOSSAMER_*`` env > legacy ``STITCH_*`` env >
``gossamer.json`` > built-in defaults. Historically that mapping lived in
``gossamer.mcp_server``; keeping a second copy in the CLI is how the two
surfaces drifted. This module is the single source of truth.

Only *toolbox options* live here. API keys are resolved lazily by
:mod:`gossamer.env` / the keystore, exactly as before.
"""

from __future__ import annotations

import json
import logging

from gossamer.config import ToolboxConfig
from gossamer.guard import GuardConfig
from gossamer.settings import load_config_file

logger = logging.getLogger(__name__)


def env_value(name: str, default=None, cast=str, legacy=None):
    """Read one ``GOSSAMER_*`` value with legacy fallback and type coercion.

    A malformed value warns and falls back to *default* instead of crashing
    startup on one typo (review A.6). Empty strings count as unset, matching
    :func:`gossamer.env.getenv`.
    """
    from gossamer.env import getenv

    raw = getenv(name, None, legacy=legacy)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logger.warning(
            "GOSSAMER env %s=%r is not valid; using default %r",
            name,
            raw,
            default,
        )
        return default


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean ``GOSSAMER_*`` value (legacy spellings honored)."""
    from gossamer.env import getenv_bool

    return getenv_bool(name, default)


def env_json_dict(name: str) -> dict:
    """Read a JSON object from *name*; return ``{}`` when unset or invalid.

    Used for transport overrides (custom headers / cookies), which are
    naturally expressed as JSON objects::

        GOSSAMER_CUSTOM_HEADERS='{"Authorization": "Bearer ..."}'
    """
    from gossamer.env import getenv

    raw = getenv(name, None)
    if raw is None or raw.strip() == "":
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("GOSSAMER env %s is not valid JSON; ignoring", name)
        return {}
    if not isinstance(value, dict):
        logger.warning("GOSSAMER env %s must be a JSON object; ignoring", name)
        return {}
    return {str(k): str(v) for k, v in value.items()}


def guard_config_from_env():
    """Build a :class:`GuardConfig` from ``GOSSAMER_GUARD_*`` env vars.

    The prompt-injection guard is off by default: it only activates when
    ``GOSSAMER_GUARD_ENABLED`` is truthy (which implies the optional
    ``jailguard`` dependency is installed via ``pip install ...[guard]``).
    """
    try:
        file_guard = load_config_file().get("guard", {}) or {}
    except (OSError, ValueError):
        file_guard = {}
    if not isinstance(file_guard, dict):
        file_guard = {}
    if not env_bool("GOSSAMER_GUARD_ENABLED", file_guard.get("enabled", False)):
        return None
    return GuardConfig(
        enabled=True,
        mode=env_value(
            "GOSSAMER_GUARD_MODE", file_guard.get("mode", "annotate")
        ),
        scopes=env_value(
            "GOSSAMER_GUARD_SCOPES",
            file_guard.get("scopes", "page_markdown,document_text"),
        ),
        threshold=env_value(
            "GOSSAMER_GUARD_THRESHOLD", file_guard.get("threshold", 0.7), float
        ),
        max_chunks=env_value(
            "GOSSAMER_GUARD_MAX_CHUNKS", file_guard.get("max_chunks", 40), int
        ),
    )


def config_from_env() -> ToolboxConfig:
    """Build the toolbox configuration shared by the CLI and MCP server.

    Precedence: explicit env > ``gossamer.json`` > built-in defaults. Each
    :func:`env_value` call falls back to the file value, so an unset variable
    inherits the file and a malformed one warns + falls back (A.6).
    """
    try:
        file_cfg = load_config_file()
    except (OSError, ValueError) as exc:
        logger.warning("ignoring gossamer.json: %s", exc)
        file_cfg = {}

    def _f(key, default):
        return file_cfg.get(key, default)

    fetch_delay = env_value(
        "GOSSAMER_FETCH_DELAY", _f("fetch_delay", None), float
    )
    return ToolboxConfig(
        cache_dir=env_value(
            "GOSSAMER_CACHE_DIR", _f("cache_dir", ".gossamer_cache")
        ),
        cache_ttl_seconds=env_value(
            "GOSSAMER_CACHE_TTL_SECONDS", _f("cache_ttl_seconds", 3600), int
        ),
        cache_max_bytes=env_value(
            "GOSSAMER_CACHE_MAX_BYTES", _f("cache_max_bytes", 0), int
        ),
        cache_memory_entries=env_value(
            "GOSSAMER_CACHE_MEMORY_ENTRIES",
            _f("cache_memory_entries", 100),
            int,
        ),
        max_response_bytes=env_value(
            "GOSSAMER_MAX_RESPONSE_BYTES",
            _f("max_response_bytes", 5 * 1024 * 1024),
            int,
            legacy="STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES",
        ),
        liveness_timeout=env_value(
            "GOSSAMER_LIVENESS_TIMEOUT", _f("liveness_timeout", 10.0), float
        ),
        ddgs_delay=env_value(
            "GOSSAMER_DDGS_DELAY", _f("ddgs_delay", 1.0), float
        ),
        ddgs_jitter=env_value(
            "GOSSAMER_DDGS_JITTER", _f("ddgs_jitter", 1.0), float
        ),
        domain_delay=env_value(
            "GOSSAMER_DOMAIN_DELAY", _f("domain_delay", 0.5), float
        ),
        fetch_delay=fetch_delay,
        fetch_jitter=env_value(
            "GOSSAMER_FETCH_JITTER", _f("fetch_jitter", 1.0), float
        ),
        max_markdown_chars=env_value(
            "GOSSAMER_MAX_MARKDOWN_CHARS", _f("max_markdown_chars", 8000), int
        ),
        max_tokens=env_value(
            "GOSSAMER_MAX_TOKENS", _f("max_tokens", 0), int
        ),
        model_name=env_value(
            "GOSSAMER_MODEL_NAME", _f("model_name", "gpt-4o")
        ),
        max_links=env_value(
            "GOSSAMER_MAX_LINKS", _f("max_links", 20), int
        ),
        fetch_mode=env_value(
            "GOSSAMER_FETCH_MODE", _f("fetch_mode", "auto")
        ),
        candidate_cap=env_value(
            "GOSSAMER_CANDIDATE_CAP", _f("candidate_cap", 500), int
        ),
        max_concurrency=env_value(
            "GOSSAMER_MAX_CONCURRENCY", _f("max_concurrency", 8), int
        ),
        # S4: robots.txt compliance; operators can opt out explicitly.
        respect_robots=env_bool(
            "GOSSAMER_RESPECT_ROBOTS", _f("respect_robots", True)
        ),
        # Tier 1.4: revalidate expired cached pages with ETag / Last-Modified
        # before re-downloading; operators can opt out explicitly.
        conditional_revalidation=env_bool(
            "GOSSAMER_CONDITIONAL_REVALIDATE",
            _f("conditional_revalidation", True),
        ),
        # Prompt-injection guard, off by default.
        guard=guard_config_from_env(),
        # Tier 2.7: HTTP transport overrides for authenticated sources.
        # Headers and cookies are JSON objects; proxy and UA are strings.
        http_proxy=env_value("GOSSAMER_HTTP_PROXY", _f("http_proxy", None)),
        user_agent=env_value("GOSSAMER_USER_AGENT", _f("user_agent", None)),
        custom_headers=(
            env_json_dict("GOSSAMER_CUSTOM_HEADERS")
            or _f("custom_headers", {})
        ),
        cookies=env_json_dict("GOSSAMER_COOKIES") or _f("cookies", {}),
        search_merge=env_bool(
            "GOSSAMER_SEARCH_MERGE", _f("search_merge", False)
        ),
    )
