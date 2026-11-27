#!/usr/bin/env bash
# Non-interactive release validation for the pure Rust app.
# Run from the repository root in Git Bash: bash rust/validate_release.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/rust"

cargo build --manifest-path app/Cargo.toml --release
cargo test --manifest-path Cargo.toml --workspace
cargo clippy --manifest-path Cargo.toml --workspace --all-targets -- -D warnings

BIN="$ROOT/rust/app/target/release/gossamer.exe"
[ -x "$BIN" ] || BIN="$ROOT/rust/app/target/release/gossamer"
MCP_BIN="$ROOT/rust/app/target/release/gossamer-mcp.exe"
[ -x "$MCP_BIN" ] || MCP_BIN="$ROOT/rust/app/target/release/gossamer-mcp"

"$BIN" --help >/dev/null
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"validate","version":"1"}}}' \
  | "$MCP_BIN" | grep -q '"serverInfo"'

cd "$ROOT"
rust-anything app replay . --build "$BIN"

echo "release validation passed: $BIN"
