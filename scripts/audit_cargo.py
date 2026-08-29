#!/usr/bin/env python3
"""Audit the Rust dependency tree in Cargo.lock against OSV.dev.

Stdlib only. The OSV database mirrors the RustSec advisory DB, so this
covers the same advisories as cargo-audit without a cargo toolchain step
(cargo-audit's crates.io release is stale and its prebuilt binaries are a
moving target — this script is neither).

Only registry (crates.io) packages are audited; path and git dependencies
are first-party or direct refs and are audited in their own repositories.

Usage: python scripts/audit_cargo.py [path-to-Cargo.lock]
Exit code: 0 = clean, 1 = advisories found, 2 = unexpected error.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
TIMEOUT_SECONDS = 60


def load_registry_packages(lock_text: str) -> list[tuple[str, str]]:
    """Return (name, version) for every crates.io package in the lock."""
    packages: list[tuple[str, str]] = []
    blocks = re.findall(r"\[\[package\]\](.*?)(?=\[\[package\]\]|\Z)", lock_text, re.S)
    for block in blocks:
        name = re.search(r'^name = "([^"]+)"', block, re.M)
        version = re.search(r'^version = "([^"]+)"', block, re.M)
        source = re.search(r'^source = "([^"]+)"', block, re.M)
        if not (name and version):
            continue
        if source is None or "registry+" not in source.group(1):
            continue  # path/git dependency — not a crates.io release
        packages.append((name.group(1), version.group(1)))
    return packages


def main(argv: list[str]) -> int:
    lock_path = argv[0] if argv else "Cargo.lock"
    try:
        with open(lock_path, encoding="utf-8") as handle:
            packages = load_registry_packages(handle.read())
    except OSError as exc:
        print(f"cannot read {lock_path}: {exc}", file=sys.stderr)
        return 2

    if not packages:
        print(f"no registry packages found in {lock_path}")
        return 0

    queries = [
        {"package": {"name": name, "ecosystem": "crates.io"}, "version": version}
        for name, version in packages
    ]
    body = json.dumps({"queries": queries}).encode()
    request = urllib.request.Request(
        OSV_BATCH_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            result = json.load(response)
    except Exception as exc:  # network/parse failures are hard errors, not "clean"
        print(f"OSV query failed: {exc}", file=sys.stderr)
        return 2

    vulns_by_index = result.get("vulns") or []
    hits = 0
    for index, (name, version) in enumerate(packages):
        vulns = vulns_by_index[index] if index < len(vulns_by_index) else None
        if not vulns:
            continue
        for vuln in vulns:
            hits += 1
            aliases = ", ".join(vuln.get("aliases") or [])
            summary = (vuln.get("summary") or vuln.get("details") or "").strip()
            print(f"VULNERABLE: {name} {version} — {vuln.get('id')}"
                  + (f" ({aliases})" if aliases else ""))
            if summary:
                print(f"   {summary[:160]}")

    if hits:
        print(f"\n{hits} Rust advisory/advisories found in {lock_path}")
        return 1
    print(f"cargo audit (OSV): {len(packages)} registry packages, no known vulnerabilities")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
