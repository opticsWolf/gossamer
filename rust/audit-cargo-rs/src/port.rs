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

/// Build the OSV `/v1/querybatch` request body for registry packages.
///
/// Package tuples come from [`load_registry_packages`] in the shared
/// `{"$tuple": [name, version]}` shape used by the Python bridge.
pub fn osv_request_body(packages: &[Value]) -> Value {
    let queries: Vec<Value> = packages
        .iter()
        .filter_map(package_pair)
        .map(|(name, version)| {
            json!({
                "package": {
                    "name": name,
                    "ecosystem": "crates.io",
                },
                "version": version,
            })
        })
        .collect();
    json!({"queries": queries})
}

/// Format an OSV batch response using the original script's output contract.
///
/// Returns `(text, exit_code)`: `0` when clean, `1` when at least one
/// advisory was found. The caller owns HTTP/read failures and exit code `2`.
///
/// The original script read a top-level `vulns` field, but OSV returns
/// `{"results": [{"vulns": [...]}, ...]}`. This uses the actual API shape and
/// keeps each result aligned with its package index.
pub fn format_osv_report(result: &Value, packages: &[Value], lock_path: &str) -> (String, u8) {
    let results = result.get("results").and_then(Value::as_array);
    let mut output = String::new();
    let mut hits = 0usize;

    for (index, package) in packages.iter().enumerate() {
        let Some((name, version)) = package_pair(package) else {
            continue;
        };
        let vulns = results
            .and_then(|items| items.get(index))
            .and_then(|entry| entry.get("vulns"))
            .and_then(Value::as_array);
        let Some(vulns) = vulns else {
            continue;
        };
        for vuln in vulns {
            hits += 1;
            let id = vuln.get("id").and_then(Value::as_str).unwrap_or("None");
            let aliases = vuln
                .get("aliases")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default();
            let summary = vuln
                .get("summary")
                .and_then(Value::as_str)
                .filter(|text| !text.is_empty())
                .or_else(|| vuln.get("details").and_then(Value::as_str))
                .unwrap_or("")
                .trim();
            output.push_str(&format!(
                "VULNERABLE: {name} {version} \u{2014} {id}{}\n",
                if aliases.is_empty() {
                    String::new()
                } else {
                    format!(" ({aliases})")
                }
            ));
            if !summary.is_empty() {
                output.push_str(&format!(
                    "   {}\n",
                    summary.chars().take(160).collect::<String>()
                ));
            }
        }
    }

    if hits > 0 {
        output.push_str(&format!(
            "\n{hits} Rust advisory/advisories found in {lock_path}\n"
        ));
        (output, 1)
    } else {
        (
            format!(
                "cargo audit (OSV): {} registry packages, no known vulnerabilities\n",
                packages.len()
            ),
            0,
        )
    }
}

fn package_pair(package: &Value) -> Option<(&str, &str)> {
    let tuple = package.get("$tuple")?.as_array()?;
    Some((tuple.first()?.as_str()?, tuple.get(1)?.as_str()?))
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

    #[test]
    fn builds_osv_queries_in_package_order() {
        let packages = vec![
            json!({"$tuple": ["serde", "1.0.0"]}),
            json!({"$tuple": ["smallvec", "1.6.0"]}),
        ];
        assert_eq!(
            osv_request_body(&packages),
            json!({
                "queries": [
                    {"package": {"name": "serde", "ecosystem": "crates.io"}, "version": "1.0.0"},
                    {"package": {"name": "smallvec", "ecosystem": "crates.io"}, "version": "1.6.0"},
                ]
            })
        );
    }

    #[test]
    fn formats_clean_osv_report() {
        let packages = vec![json!({"$tuple": ["serde", "1.0.0"]})];
        let (text, code) = format_osv_report(&json!({"results": [{}]}), &packages, "Cargo.lock");
        assert_eq!(
            text,
            "cargo audit (OSV): 1 registry packages, no known vulnerabilities\n"
        );
        assert_eq!(code, 0);
    }

    #[test]
    fn formats_vulnerable_osv_report() {
        let packages = vec![json!({"$tuple": ["smallvec", "1.6.0"]})];
        let response = json!({
            "results": [{
                "vulns": [{
                    "id": "RUSTSEC-2021-0003",
                    "aliases": ["GHSA-43w2-9j62-hq99"],
                    "summary": "Buffer overflow in SmallVec",
                }]
            }]
        });
        let (text, code) = format_osv_report(&response, &packages, "Cargo.lock");
        assert_eq!(
            text,
            "VULNERABLE: smallvec 1.6.0 \u{2014} RUSTSEC-2021-0003 (GHSA-43w2-9j62-hq99)\n   Buffer overflow in SmallVec\n\n1 Rust advisory/advisories found in Cargo.lock\n"
        );
        assert_eq!(code, 1);
    }

    #[test]
    fn ignores_top_level_vulns_like_the_old_bug() {
        let packages = vec![json!({"$tuple": ["smallvec", "1.6.0"]})];
        let response = json!({"vulns": [{"id": "RUSTSEC-2021-0003"}]});
        let (text, code) = format_osv_report(&response, &packages, "Cargo.lock");
        assert!(text.contains("no known vulnerabilities"));
        assert_eq!(code, 0);
    }
}
