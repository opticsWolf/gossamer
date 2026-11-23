//! The Rust port of `scripts/verify_bundled_assets.py`.
//!
//! The PyO3 binding receives JSON values, so `check` on a non-`Path` reproduces the original's
//! `AttributeError`. The real archive logic lives in `check_path` and is covered by Rust tests
//! against the generated fixtures in `rust/fixtures/`.

#![forbid(unsafe_code)]

use std::collections::HashMap;
use std::fs::File;
use std::io::Read;
use std::path::Path;

use flate2::read::GzDecoder;
use port_error::{ErrorKind, PortError};
use serde_json::Value;
use zip::ZipArchive;

const REQUIRED: [(&str, &[u8]); 1] = [("gossamer/skills/gossamer/SKILL.md", b"description:")];

/// Reproduces the original `check` contract for JSON values.
///
/// # Errors
///
/// Always returns [`ErrorKind::Attribute`], matching the original's failure on non-`Path` inputs.
pub fn check(artifact: Value) -> Result<Vec<String>, PortError> {
    Err(PortError::new(
        ErrorKind::Attribute,
        format!(
            "'{}' object has no attribute 'suffix'",
            python_type_name(&artifact)
        ),
    ))
}

/// Checks a real wheel or sdist for the required bundled assets.
///
/// # Errors
///
/// Returns [`ErrorKind::Io`] for unreadable files, [`ErrorKind::Value`] for malformed or
/// unsupported archives, and [`ErrorKind::Unsupported`] for unknown artifact types.
pub fn check_path(artifact: &Path) -> Result<Vec<String>, PortError> {
    let found = payloads(artifact)?;
    let mut problems = Vec::new();
    for (name, marker) in REQUIRED {
        match found.get(name) {
            None => problems.push(format!("missing {name}")),
            Some(payload) if !payload.windows(marker.len()).any(|window| window == marker) => {
                problems.push(format!(
                    "{name} is missing expected content {}",
                    bytes_repr(marker)
                ));
            }
            Some(_) => {}
        }
    }
    Ok(problems)
}

fn python_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(number) if number.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

fn bytes_repr(marker: &[u8]) -> String {
    let mut out = String::from("b'");
    for byte in marker {
        match byte {
            b'\\' => out.push_str("\\\\"),
            b'\'' => out.push_str("\\'"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b'\t' => out.push_str("\\t"),
            0x20..=0x7e => out.push(*byte as char),
            other => out.push_str(&format!("\\x{other:02x}")),
        }
    }
    out.push('\'');
    out
}

fn required_name(archive_name: &str) -> Option<&'static str> {
    REQUIRED
        .iter()
        .find(|(required, _)| archive_name.ends_with(required))
        .map(|(required, _)| *required)
}

fn payloads(artifact: &Path) -> Result<HashMap<String, Vec<u8>>, PortError> {
    let mut found = HashMap::new();
    let filename = artifact
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();

    if artifact.extension().and_then(|ext| ext.to_str()) == Some("whl") {
        let file = File::open(artifact)
            .map_err(|error| PortError::new(ErrorKind::Io, error.to_string()))?;
        let mut archive = ZipArchive::new(file)
            .map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?;
        for index in 0..archive.len() {
            let mut entry = archive
                .by_index(index)
                .map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?;
            let Some(required) = required_name(entry.name()) else {
                continue;
            };
            let mut payload = Vec::new();
            entry
                .read_to_end(&mut payload)
                .map_err(|error| PortError::new(ErrorKind::Io, error.to_string()))?;
            found.insert(required.to_owned(), payload);
        }
    } else if filename.ends_with(".tar.gz") {
        let file = File::open(artifact)
            .map_err(|error| PortError::new(ErrorKind::Io, error.to_string()))?;
        let mut archive = tar::Archive::new(GzDecoder::new(file));
        let entries = archive
            .entries()
            .map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?;
        for entry in entries {
            let mut entry =
                entry.map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?;
            if !entry.header().entry_type().is_file() {
                continue;
            }
            let name = entry
                .path()
                .map_err(|error| PortError::new(ErrorKind::Value, error.to_string()))?
                .to_string_lossy()
                .into_owned();
            let Some(required) = required_name(&name) else {
                continue;
            };
            let mut payload = Vec::new();
            entry
                .read_to_end(&mut payload)
                .map_err(|error| PortError::new(ErrorKind::Io, error.to_string()))?;
            found.insert(required.to_owned(), payload);
        }
    } else {
        return Err(PortError::new(
            ErrorKind::Unsupported,
            format!("unsupported artifact: {}", artifact.display()),
        ));
    }

    Ok(found)
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;

    fn fixture(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("fixtures")
            .join(name)
    }

    #[test]
    fn good_wheel_has_no_problems() {
        assert!(check_path(&fixture("good.whl")).unwrap().is_empty());
    }

    #[test]
    fn bad_marker_is_reported() {
        assert_eq!(
            check_path(&fixture("bad-marker.whl")).unwrap(),
            vec![format!(
                "{} is missing expected content {}",
                REQUIRED[0].0,
                bytes_repr(REQUIRED[0].1)
            )]
        );
    }

    #[test]
    fn missing_entry_is_reported() {
        assert_eq!(
            check_path(&fixture("missing.whl")).unwrap(),
            vec![format!("missing {}", REQUIRED[0].0)]
        );
    }

    #[test]
    fn good_sdist_has_no_problems() {
        assert!(check_path(&fixture("good.tar.gz")).unwrap().is_empty());
    }

    #[test]
    fn json_values_raise_attribute_error() {
        let error = check(Value::Null).unwrap_err();
        assert_eq!(error.kind(), ErrorKind::Attribute);
        assert!(
            error
                .message()
                .starts_with("'NoneType' object has no attribute")
        );
    }
}
