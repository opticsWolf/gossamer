"""Launch the pure Rust gossamer binaries from Python packaging entry points.

The Python package stays in the tree as the replay oracle. Installed ``gossamer``
and ``gossamer-mcp`` commands, however, run the lifted Rust app. Set
``GOSSAMER_RUST_BIN`` to override binary discovery in custom installations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _binary(name: str) -> Path:
    override = os.environ.get("GOSSAMER_RUST_BIN")
    if override:
        return Path(override)
    root = Path(__file__).resolve().parent.parent
    suffixes = (".exe", "") if os.name == "nt" else ("",)
    for profile in ("release", "debug"):
        for suffix in suffixes:
            candidate = root / "rust" / "app" / "target" / profile / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
    raise SystemExit(f"gossamer Rust binary not found (set GOSSAMER_RUST_BIN): {name}")


def run(name: str) -> int:
    """Execute one Rust binary with the caller's argv and return its exit code."""
    return subprocess.call([str(_binary(name)), *sys.argv[1:]])


def main() -> int:
    """``gossamer`` console-script entry point."""
    return run("gossamer")


def mcp_main() -> int:
    """``gossamer-mcp`` console-script entry point."""
    return run("gossamer-mcp")


if __name__ == "__main__":  # pragma: no cover - manual smoke path
    raise SystemExit(main())
