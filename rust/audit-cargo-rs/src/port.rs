//! The Rust port of `scripts/audit_cargo.py`.
//!
//! `load_registry_packages` is the only bridgeable export; `main` stays in the Python CLI. The
//! parsing rules mirror the original regexes exactly: registry packages only, name/version required.

#![forbid(unsafe_code)]

use port_error::{ErrorKind, PortError};
use regex::Regex;
use serde_json::{Value, json};

/// Returns registry packages from a Cargo.lock as `(name, version)` tuple markers.
///
/// # Errors
///
/// Returns [`ErrorKind::Value`] only if one of the fixed regex patterns fails to compile, which
/// indicates a programming error in this file rather than bad lockfile input.
pub fn load_registry_packages(lock_text: String) -> Result<Vec<Value>, PortError> {
    let name = Regex::new(r#"(?m)^name = "([^"]+)""#)
        .map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?;
    let version = Regex::new(r#"(?m)^version = "([^"]+)""#)
        .map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?;
    let source = Regex::new(r#"(?m)^source = "([^"]+)""#)
        .map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?;

    let mut packages = Vec::new();
    for block in package_blocks(&lock_text) {
        let Some(name_match) = name.captures(block) else {
            continue;
        };
        let Some(version_match) = version.captures(block) else {
            continue;
        };
        if let Some(source_match) = source.captures(block) {
            if !source_match[1].contains("registry+") {
                continue;
            }
        } else {
            continue;
        }
        packages.push(json!({
            "$tuple": [name_match[1].to_owned(), version_match[1].to_owned()]
        }));
    }
    Ok(packages)
}

/// Split a Cargo.lock into the bodies between `[[package]]` markers, like the original's lazy
/// regex without needing look-around (which Rust's regex engine does not support).
fn package_blocks(lock_text: &str) -> Vec<&str> {
    const MARKER: &str = "[[package]]";
    let mut blocks = Vec::new();
    let mut rest = lock_text;
    while let Some(start) = rest.find(MARKER) {
        let after = &rest[start + MARKER.len()..];
        let end = after.find(MARKER).unwrap_or(after.len());
        blocks.push(&after[..end]);
        rest = &after[end..];
    }
    blocks
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_registry_packages_only() {
        let lock = concat!(
            "[[package]]\nname = \"serde\"\nversion = \"1.0.0\"\n",
            "source = \"registry+https://github.com/rust-lang/crates.io-index\"\n\n",
            "[[package]]\nname = \"local\"\nversion = \"0.1.0\"\n\n",
            "[[package]]\nname = \"gitdep\"\nversion = \"0.2.0\"\n",
            "source = \"git+https://example.com/repo#abc\"\n",
        );
        assert_eq!(
            load_registry_packages(lock.to_owned()).unwrap(),
            vec![json!({ "$tuple": ["serde", "1.0.0"] })]
        );
    }

    #[test]
    fn empty_lock_has_no_packages() {
        assert!(load_registry_packages(String::new()).unwrap().is_empty());
    }
}
