"""Environment-variable access with rename fallback.

The package was renamed from ``stitch-web-researcher`` to ``gossamer``.
Configuration moved from ``STITCH_*`` to ``GOSSAMER_*``; every reader here
honors the legacy ``STITCH_*`` spelling as a fallback so existing deployments
keep working. New code must use the ``GOSSAMER_*`` spelling.
"""

from __future__ import annotations

import os

from gossamer.settings import load_config_file, lookup_key

__all__ = ["getenv", "LEGACY_PREFIX", "PREFIX"]

PREFIX = "GOSSAMER_"
LEGACY_PREFIX = "STITCH_"


def _legacy_names(new: str, legacy=None) -> list:
    """Explicit legacy spelling(s) for *new*, plus the 1:1 derived one."""
    out: list = []
    if legacy is None:
        pass
    elif isinstance(legacy, str):
        out.append(legacy)
    else:
        out.extend(legacy)
    if new.startswith(PREFIX):
        derived = LEGACY_PREFIX + new[len(PREFIX):]
        if derived not in out:
            out.append(derived)
    return out


def _short_names(new: str, legacy=None) -> list:
    """Candidate keystore/config keys: short, full, then legacies."""
    names: list = []
    if new.startswith(PREFIX):
        names.append(new[len(PREFIX):])
    names.append(new)
    names.extend(_legacy_names(new, legacy))
    return names


def getenv(new: str, default=None, legacy=None):
    """Read *new* from the environment, falling back to legacy spelling(s)
    and then to file configuration.

    Full order: ``GOSSAMER_*`` env > legacy ``STITCH_*`` env > keystore
    file > ``gossamer.json`` ``"keys"`` section > *default*. Empty strings
    count as unset (same convention as the MCP helpers). ``legacy`` is an
    extra legacy name (or list) for variables whose legacy spelling does
    not derive 1:1, e.g. the old allow-private escape hatch.
    """
    raw = os.environ.get(new)
    if raw not in (None, ""):
        return raw
    legacies = _legacy_names(new, legacy)
    for old in legacies:
        raw = os.environ.get(old)
        if raw not in (None, ""):
            return raw
    names = _short_names(new, legacy)
    found = lookup_key(*names)
    if found not in (None, ""):
        return found
    try:
        inline = load_config_file().get("keys", {}) or {}
    except (OSError, ValueError):
        inline = {}
    if isinstance(inline, dict):
        for name in names:
            value = inline.get(name)
            if value not in (None, ""):
                return value if isinstance(value, str) else str(value)
    return default


def getenv_bool(new: str, default: bool, legacy=None) -> bool:
    """Boolean variant of :func:`getenv` (1/true/yes/on)."""
    raw = getenv(new, None, legacy=legacy)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
