"""API-key storage for gossamer (see :mod:`gossamer.settings` for the full story).

Keys are read, in order, from explicit constructor arguments,
``GOSSAMER_*`` / legacy ``STITCH_*`` environment variables, the keystore
file, and the ``"keys"`` section of ``gossamer.json``.

Command line::

    python -m gossamer.keystore --init            # ~/.gossamer/keys.json template (0600)
    python -m gossamer.keystore --init ./keys.json
    python -m gossamer.keystore --init-config     # ./gossamer.json template
    python -m gossamer.keystore --check           # validate files (never prints secrets)

Fill in the values with any editor afterwards. The template only contains
empty strings, so it is safe to create anywhere.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from gossamer.settings import (
    KNOWN_KEYS,
    clear_caches,
    config_template,
    find_config_file,
    find_keystore_file,
    keystore_template,
    load_config_file,
    load_keystore,
    lookup_key,
)

__all__ = [
    "KNOWN_KEYS",
    "clear_caches",
    "config_template",
    "find_config_file",
    "find_keystore_file",
    "keystore_template",
    "load_config_file",
    "load_keystore",
    "lookup_key",
]


def _write_json(path: Path, payload: dict, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    return path


def init_keystore(path: Optional[str] = None) -> Path:
    """Write an empty keystore template (0600); refuses to overwrite.

    Defaults to ``~/.gossamer/keys.json`` when *path* is omitted.
    """
    target = Path(path).expanduser() if path else Path.home() / ".gossamer" / "keys.json"
    return _write_json(target, keystore_template())


def init_config(path: Optional[str] = None) -> Path:
    """Write a documented ``gossamer.json`` template; refuses to overwrite.

    Defaults to ``./gossamer.json`` (current working directory) when *path*
    is omitted.
    """
    target = Path(path).expanduser() if path else Path.cwd() / "gossamer.json"
    return _write_json(target, config_template(), mode=0o644)


def check_files() -> int:
    """Validate discovered config/keystore files. Returns nonzero on error."""
    errors = 0
    cfg = find_config_file()
    if cfg is None:
        print("gossamer.json: none found")
    else:
        try:
            data = load_config_file(str(cfg))
            print(f"gossamer.json: OK ({cfg}, {len(data)} top-level keys)")
        except (OSError, ValueError) as exc:
            print(f"gossamer.json: ERROR {exc}")
            errors += 1
    store = find_keystore_file()
    if store is None:
        print("keystore: none found")
    else:
        try:
            data = load_keystore(str(store))
            filled = sum(1 for v in data.values() if v)
            print(f"keystore: OK ({store}, {filled}/{len(data)} filled)")
        except (OSError, ValueError) as exc:
            print(f"keystore: ERROR {exc}")
            errors += 1
    return errors


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gossamer.keystore", description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", nargs="?", const=None, metavar="PATH",
                       help="write an empty keystore template (default ~/.gossamer/keys.json)")
    group.add_argument("--init-config", nargs="?", const=None, metavar="PATH",
                       help="write a gossamer.json template (default ./gossamer.json)")
    group.add_argument("--check", action="store_true",
                       help="validate discovered files without printing secrets")
    args = parser.parse_args(argv)
    try:
        if args.check:
            return check_files()
        if args.init_config is not None:
            print(f"wrote {init_config(args.init_config)}")
            return 0
        print(f"wrote {init_keystore(args.init)}")
        print("fill in your keys with any editor; the file is mode 0600.")
        return 0
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
