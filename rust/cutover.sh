#!/usr/bin/env bash
# Interactive cutover checklist for replacing the Python CLI with the lifted Rust binary.
# Run from the repository root in Git Bash: bash rust/cutover.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/rust/app/target/release/gossamer.exe"
[ -x "$BIN" ] || BIN="$ROOT/rust/app/target/debug/gossamer.exe"

confirm() {
  echo
  echo "$1"
  read -r -p "Press Enter when done, or Ctrl-C to stop: " _
}

echo "gossamer Rust cutover"
echo "Binary: $BIN"

if [ "${1:-}" = "--check" ]; then
  bash "$ROOT/rust/validate_release.sh"
  exit 0
fi

confirm "1. Rebuild and prove the Rust app:
   cd '$ROOT'
   bash rust/validate_release.sh"

"$BIN" --help >/dev/null
echo "ok: binary answers --help"

confirm "2. Smoke-test the new binary beside the Python reference:
   '$BIN' skills list
   .venv/Scripts/python.exe -m gossamer.cli skills list
   Compare the two outputs."

confirm "3. Point your shell alias / CI job / launcher at:
   '$ROOT/bin/gossamer' (or '$BIN' directly).
   Installed console scripts already delegate to the Rust app via
   gossamer.launcher; the Python modules stay installed as the replay oracle."

confirm "4. Run one real research workflow through the new binary and inspect cache/MCP output."

confirm "5. Only after acceptance, schedule removal of the legacy PyO3 compatibility wrappers in a
   separate reviewed change. The original Python package remains in the tree as reference."

echo
echo "Cutover checklist complete. The lifted Rust app is now the primary CLI."
