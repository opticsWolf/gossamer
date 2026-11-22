//! ADR-012 guard: the pure core must remain independent of Python bindings.

use std::fs;
use std::path::{Path, PathBuf};

const FORBIDDEN_SOURCE_TOKENS: &[&str] = &[
    "pyo3::",
    "use pyo3",
    "PyResult<",
    "Bound<",
    "Python<'",
    "py.detach",
];

fn source_files(dir: &Path, out: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(dir).expect("core/src must be readable") {
        let path = entry.expect("directory entry must be readable").path();
        if path.is_dir() {
            source_files(&path, out);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            out.push(path);
        }
    }
}

#[test]
fn core_has_no_pyo3_dependency() {
    let manifest = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml"))
        .expect("core Cargo.toml must be readable");
    assert!(
        !manifest.contains("pyo3"),
        "rust/core/Cargo.toml must not declare pyo3"
    );
}

#[test]
fn core_source_has_no_pyo3_types() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    source_files(&src, &mut files);

    for path in files {
        let text = fs::read_to_string(&path).expect("core source must be UTF-8");
        for token in FORBIDDEN_SOURCE_TOKENS {
            assert!(
                !text.contains(token),
                "{} contains forbidden PyO3 token {token:?}",
                path.display()
            );
        }
    }
}
