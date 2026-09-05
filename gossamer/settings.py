"""File-based configuration and secret storage for gossamer.

Two files, both optional, both JSON:

* ``gossamer.json`` — toolbox options (see :meth:`ToolboxConfig.from_dict`).
  Discovery: explicit path > ``$GOSSAMER_CONFIG`` > ``./gossamer.json`` >
  ``~/.gossamer/config.json``.
* keystore (default ``~/.gossamer/keys.json``) — API keys and tokens as a flat
  ``{"NAME": "secret"}`` object. Discovery: explicit path >
  ``$GOSSAMER_KEYSTORE`` > ``keystore`` field of ``gossamer.json`` >
  ``~/.gossamer/keys.json``.

Resolution order everywhere (see :mod:`gossamer.env`): explicit constructor
argument > ``GOSSAMER_*`` env > legacy ``STITCH_*`` env > keystore file >
``gossamer.json`` ``"keys"`` section > built-in default.

Key names in files may be written short (``OPENALEX_KEY``) or fully prefixed
(``GOSSAMER_OPENALEX_KEY``); all spellings are recognized. Third-party
conventional names (``GOOGLE_API_KEY``, …) are used verbatim.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_ENV_VAR = "GOSSAMER_CONFIG"
KEYSTORE_ENV_VAR = "GOSSAMER_KEYSTORE"

CONFIG_FILENAME = "gossamer.json"
KEYSTORE_FILENAME = "keys.json"


def _home_dir() -> Path:
    """``~/.gossamer`` (created on demand by writers, never by readers)."""
    return Path.home() / ".gossamer"


def find_config_file(explicit: Optional[str] = None) -> Optional[Path]:
    """Locate ``gossamer.json``: explicit path > ``$GOSSAMER_CONFIG`` >
    ``./gossamer.json`` > ``~/.gossamer/config.json``. ``None`` when absent."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / CONFIG_FILENAME)
    candidates.append(_home_dir() / "config.json")
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_keystore_file(explicit: Optional[str] = None) -> Optional[Path]:
    """Locate the keys file: explicit > ``$GOSSAMER_KEYSTORE`` > the
    ``keystore`` field of the discovered ``gossamer.json`` >
    ``~/.gossamer/keys.json``. ``None`` when absent."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get(KEYSTORE_ENV_VAR)
    if env:
        candidates.append(Path(env))
    try:
        cfg_path = find_config_file()
        if cfg_path is not None:
            cfg = load_json_file(cfg_path)
            if isinstance(cfg.get("keystore"), str) and cfg["keystore"].strip():
                candidates.append(Path(cfg["keystore"].strip()).expanduser())
    except (OSError, ValueError):
        logger.debug("ignoring unreadable gossamer.json during keystore lookup")
    candidates.append(_home_dir() / KEYSTORE_FILENAME)
    for path in candidates:
        if path.is_file():
            return path
    return None


# mtime-checked cache: files are tiny, but getenv() falls through here on
# every miss, so avoid re-parsing on every adapter construction.
_file_cache: Dict[str, tuple] = {}
_warned_perms: set = set()


def clear_caches() -> None:
    """Drop parsed-file caches (tests that rewrite temp files)."""
    _file_cache.clear()


def load_json_file(path) -> Any:
    """Parse *path* as JSON (cached by mtime). Raises on invalid content."""
    key = str(path)
    try:
        mtime = os.path.getmtime(key)
    except OSError as exc:
        raise FileNotFoundError(f"config file not found: {key}") from exc
    cached = _file_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with open(key, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {key}: {exc}") from exc
    _file_cache[key] = (mtime, data)
    return data


def load_config_file(explicit: Optional[str] = None) -> Dict[str, Any]:
    """Load ``gossamer.json`` as a dict; ``{}`` when no file is discovered.

    Raises ``FileNotFoundError`` when *explicit* (or ``$GOSSAMER_CONFIG``)
    names a missing file, and ``ValueError`` on invalid JSON or a
    non-object top level.
    """
    wanted = explicit or os.environ.get(CONFIG_ENV_VAR)
    if wanted:
        data = load_json_file(wanted)
        if not isinstance(data, dict):
            raise ValueError(f"gossamer.json must hold an object: {wanted}")
        return data
    found = find_config_file()
    if found is None:
        return {}
    data = load_json_file(str(found))
    if not isinstance(data, dict):
        raise ValueError(f"gossamer.json must hold an object: {found}")
    return data


def load_keystore(explicit: Optional[str] = None) -> Dict[str, str]:
    """Load the keys file as a flat dict; ``{}`` when none is discovered.

    Same missing/invalid semantics as :func:`load_config_file`. Non-string
    values are stringified; warns (once per path) when the file is readable
    by group/other — secrets should be ``0600``.
    """
    wanted = explicit or os.environ.get(KEYSTORE_ENV_VAR)
    if wanted:
        path = Path(wanted)
        if not path.is_file():
            raise FileNotFoundError(f"keystore file not found: {wanted}")
        data = load_json_file(str(path))
    else:
        found = find_keystore_file()
        if found is None:
            return {}
        path, data = found, load_json_file(str(found))
    if not isinstance(data, dict):
        raise ValueError(f"keystore must hold an object: {path}")
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o600
    if mode & 0o077 and str(path) not in _warned_perms:
        _warned_perms.add(str(path))
        logger.warning(
            "keystore %s is readable by group/other (mode %o); "
            "consider chmod 600",
            path,
            mode,
        )
    return {str(k): ("" if v is None else str(v)) for k, v in data.items()}


def lookup_key(*names: str, keystore: Optional[str] = None) -> Optional[str]:
    """First non-empty keystore value for *names* (tried in order)."""
    try:
        store = load_keystore(keystore)
    except (OSError, ValueError) as exc:
        logger.debug("keystore lookup failed: %s", exc)
        return None
    for name in names:
        value = store.get(name)
        if value not in (None, ""):
            return value
    return None


#: Secret slots recognized by ``gossamer keystore --init`` (short names).
KNOWN_KEYS: List[str] = [
    "OPENALEX_KEY",
    "OPENALEX_EMAIL",
    "GOOGLE_API_KEY",
    "GOOGLE_CX",
    "BING_API_KEY",
    "EXA_API_KEY",
    "GITHUB_TOKEN",
    "NCBI_KEY",
    "NASA_KEY",
    "NVD_API_KEY",
    "ZENODO_TOKEN",
    "CONGRESS_KEY",
    "CENSUS_KEY",
    "CHEMXIV_TOKEN",
    "ALPHA_VANTAGE_KEY",
    "COURTLISTENER_KEY",
    "GOVINFO_KEY",
    "EPO_KEY",
    "EPO_SECRET",
    "KIPRIS_KEY",
    "PATENTSVIEW_API_KEY",
    "JPO_USERNAME",
    "JPO_PASSWORD",
]


def keystore_template() -> Dict[str, str]:
    """Empty ``{name: \"\"}`` template covering every known secret slot."""
    return {name: "" for name in KNOWN_KEYS}


def config_template() -> Dict[str, Any]:
    """Documented ``gossamer.json`` template (safe defaults, no secrets)."""
    return {
        "$comment": "gossamer configuration (docs: README, gossamer/mcp_server.py). "
        "Environment variables override every value here.",
        "cache_dir": ".gossamer_cache",
        "cache_ttl_seconds": 3600,
        "cache_max_bytes": 0,
        "cache_memory_entries": 100,
        "ddgs_delay": 1.0,
        "ddgs_jitter": 1.0,
        "domain_delay": 0.5,
        "fetch_jitter": 1.0,
        "fetch_mode": "auto",
        "max_markdown_chars": 8000,
        "max_tokens": 0,
        "model_name": "gpt-4o",
        "max_links": 20,
        "candidate_cap": 500,
        "max_concurrency": 8,
        "max_response_bytes": 5242880,
        "liveness_timeout": 10.0,
        "link_budget_ratio": 0.25,
        "respect_robots": True,
        "conditional_revalidation": True,
        "search_merge": False,
        "keystore": "~/.gossamer/keys.json",
        "keys": {},
        "guard": {
            "enabled": False,
            "mode": "annotate",
            "scopes": "page_markdown,document_text",
            "threshold": 0.7,
            "max_chunks": 40,
        },
    }
