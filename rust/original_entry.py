"""Original entry point used by rust-anything app replay.

Dispatches the front door's fallback to one of the original Python entry points:
gossamer CLI, scripts/audit_cargo.py, or scripts/verify_bundled_assets.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

command = sys.argv[1] if len(sys.argv) > 1 else ""

if command == "benchmarks":
    import benchmarks

    raise SystemExit(benchmarks.main(sys.argv[2:]))

if command == "audit-cargo":
    sys.path.insert(0, str(ROOT / "scripts"))
    from audit_cargo import main

    raise SystemExit(main(sys.argv[2:]))

if command == "verify-bundled-assets":
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_bundled_assets import main

    # verify_bundled_assets.main expects argv[0] to be the program name.
    raise SystemExit(main(sys.argv[1:]))

from gossamer.cli import main

raise SystemExit(main(sys.argv[1:]))
