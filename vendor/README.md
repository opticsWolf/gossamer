# Vendored third-party Rust sources

## `meta_oxide/` — pinned copy of the upstream fork

- **Source:** `https://github.com/opticsWolf/meta_oxide.git` at rev
  `a55c09f1e93892accc029bfb6255949ff5a3e89a`
  (`Fix RDFa infinite recursion on property+typeof elements`, 2026-09-08).
- **Why vendored (0.9.25):** the fork remote became unreachable
  (`Repository not found`), which broke every fresh `cargo` resolution
  (CI included) at the git-fetch step. crates.io `meta_oxide 0.1.1`
  is **not** a substitute: it predates the fork's `#[cfg(feature =
  "python")]` gating (won't compile with `default-features = false`),
  the RDFa recursion fix (process-killing stack overflow, which
  gossamer's `src/metaextract.rs` relies on being fixed), and several
  output-shaping fixes the parity suites pin.
- **Wiring:** root `Cargo.toml` uses `meta_oxide = { path =
  "vendor/meta_oxide", default-features = false }`. The workspace
  `Cargo.lock` is authoritative; the nested `Cargo.lock` inside
  `meta_oxide/` is the upstream file, kept for reference only.
- **Fidelity:** everything except `.git/` is copied verbatim. Verify
  with `diff -r --exclude=.git <fresh-clone> vendor/meta_oxide`
  (modulo the build-generated `include/meta_oxide.h`, which
  `build.rs` regenerates and `.gitignore` excludes).
- **Updating:** replace the tree with the new rev, re-pin, and run
  `cargo update -p meta_oxide` + the metaextract parity suite
  (`tests/test_rust_parity_metaextract.py`, needs the local fork
  build) before committing.
- **Nested-`.gitignore` trap:** the vendored crate's own `.gitignore`
  contains a bare `MANIFEST` line (meant for packaging artifacts).
  On case-insensitive checkouts this also matches
  `src/extractors/manifest/` (and would silently drop it from `git
  add`, breaking fresh clones with `E0583: file not found for module
  'manifest'`). Those files were force-added (`git add -f`) and stay
  tracked thereafter, but any fresh copy of this tree needs the same
  treatment — verify with `git ls-files vendor/ | grep manifest`.
