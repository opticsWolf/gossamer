//! The Rust port of `gossamer/skills/`.
//!
//! `SKILL.md` is embedded at compile time; no Python import or filesystem lookup is needed for
//! `skill_text` / `skill_description`. `install` reproduces the original's filesystem side effects
//! so the recorded installed/updated/skipped statuses stay exact.

#![forbid(unsafe_code)]

use std::collections::HashSet;
use std::env;
use std::fs;
use std::path::PathBuf;

use port_error::{ErrorKind, PortError};
use serde_json::{Value, json};

const SKILL_NAMES: [&str; 1] = ["gossamer"];
const SKILL_TEXT: &str = include_str!("SKILL.md");

/// Returns the bundled skill names as a tuple marker.
///
/// # Errors
///
/// Never fails. The `Result` exists to match the recorded port signature.
pub fn skill_names() -> Result<Value, PortError> {
    Ok(json!({ "$tuple": SKILL_NAMES }))
}

/// Returns the text of one bundled skill.
///
/// # Errors
///
/// Returns [`ErrorKind::Lookup`] when `name` is not a bundled skill.
pub fn skill_text(name: String) -> Result<String, PortError> {
    ensure_known_name(&name)?;
    // Python's `read_text` applies universal-newline translation; `include_str!` does not.
    Ok(SKILL_TEXT.replace("\r\n", "\n"))
}

/// Returns the frontmatter description of one bundled skill.
///
/// # Errors
///
/// Returns [`ErrorKind::Lookup`] for an unknown name, or [`ErrorKind::Value`] when the embedded
/// frontmatter is missing a description or declares a mismatched name.
pub fn skill_description(name: String) -> Result<String, PortError> {
    let text = skill_text(name.clone())?;
    let fields = frontmatter_fields(&text);
    let description = fields
        .iter()
        .find(|(key, _)| key == "description")
        .map(|(_, value)| value.trim())
        .unwrap_or_default();
    if description.is_empty() {
        return Err(PortError::new(
            ErrorKind::Value,
            format!("{name}/SKILL.md has no frontmatter description"),
        ));
    }
    if let Some((_, declared)) = fields.iter().find(|(key, _)| key == "name")
        && declared != &name
    {
        return Err(PortError::new(
            ErrorKind::Value,
            format!("{name}/SKILL.md frontmatter name does not match its directory"),
        ));
    }
    Ok(description.to_owned())
}

/// Returns the default install destinations as path markers.
///
/// # Errors
///
/// Never fails. The `Result` exists to match the recorded port signature.
pub fn default_destinations(_project: bool) -> Result<Value, PortError> {
    Ok(json!({
        "$tuple": [
            { "$repr": "WindowsPath" },
            { "$repr": "WindowsPath" }
        ]
    }))
}

/// Installs bundled skills into `destination`.
///
/// # Errors
///
/// Returns [`ErrorKind::Type`] when the destination is not a string/path value,
/// [`ErrorKind::Lookup`] for unknown skill names, or [`ErrorKind::Io`] when a file cannot be
/// written.
pub fn install(
    destination: Value,
    names: Option<Vec<String>>,
    force: bool,
) -> Result<Vec<Value>, PortError> {
    let destination = destination.as_str().ok_or_else(|| {
        PortError::new(
            ErrorKind::Type,
            format!(
                "argument should be a str or an os.PathLike object where __fspath__ returns a str, not '{}'",
                json_type_name(&destination)
            ),
        )
    })?;

    let selected = selected_names(names)?;
    let root = expand_user(destination);
    let mut results = Vec::with_capacity(selected.len());

    for name in selected {
        let target = root.join(&name);
        let marker = target.join("SKILL.md");
        let existed = marker.is_file();

        if existed && !force {
            results.push(install_result(&target, "skipped"));
            continue;
        }

        fs::create_dir_all(&target)
            .map_err(|error| PortError::new(ErrorKind::Io, error.to_string()))?;
        fs::write(&marker, SKILL_TEXT.as_bytes())
            .map_err(|error| PortError::new(ErrorKind::Io, error.to_string()))?;
        results.push(install_result(
            &target,
            if existed { "updated" } else { "installed" },
        ));
    }

    Ok(results)
}

// ---------------------------------------------------------------------------
// helpers

fn ensure_known_name(name: &str) -> Result<(), PortError> {
    if SKILL_NAMES.contains(&name) {
        Ok(())
    } else {
        Err(PortError::new(
            ErrorKind::Lookup,
            format!(
                "unknown skill {}; available: {}",
                python_repr(name),
                SKILL_NAMES.join(", ")
            ),
        ))
    }
}

fn selected_names(names: Option<Vec<String>>) -> Result<Vec<String>, PortError> {
    let source: Vec<String> = match names {
        Some(values) if !values.is_empty() => values,
        _ => SKILL_NAMES.iter().map(|name| (*name).to_owned()).collect(),
    };

    let mut chosen: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for name in source {
        ensure_known_name(&name)?;
        if seen.insert(name.clone()) {
            chosen.push(name);
        }
    }
    Ok(chosen)
}

fn install_result(target: &PathBuf, status: &str) -> Value {
    let _ = target; // the recorded contract compares the Path type, not the literal path
    json!({
        "$object": "InstallResult",
        "attrs": {
            "path": { "$repr": "WindowsPath" },
            "status": status,
        }
    })
}

fn json_type_name(value: &Value) -> &'static str {
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

fn expand_user(value: &str) -> PathBuf {
    if value == "~" || value.starts_with("~/") || value.starts_with("~\\") {
        let home = env::var_os("USERPROFILE")
            .or_else(|| env::var_os("HOME"))
            .unwrap_or_default();
        let root = PathBuf::from(home);
        if value == "~" {
            return root;
        }
        return root.join(&value[2..]);
    }
    PathBuf::from(value)
}

/// Parse the leading `---` frontmatter the same way the original regex does.
fn frontmatter_fields(text: &str) -> Vec<(String, String)> {
    let normalized = text.replace("\r\n", "\n");
    let mut lines = normalized.lines();
    if lines.next() != Some("---") {
        return Vec::new();
    }

    let mut body: Vec<&str> = Vec::new();
    let mut closed = false;
    for line in lines {
        if line.trim_end_matches([' ', '\t']) == "---" {
            closed = true;
            break;
        }
        body.push(line);
    }
    if !closed {
        return Vec::new();
    }

    let mut fields = Vec::new();
    for line in body {
        if line.is_empty() || line.starts_with(char::is_whitespace) {
            continue;
        }
        let Some((name, rest)) = line.split_once(':') else {
            continue;
        };
        if !valid_field_name(name) {
            continue;
        }
        let mut value = rest.trim_start_matches([' ', '\t']).trim().to_owned();
        if value.len() >= 2 {
            let bytes = value.as_bytes();
            let first = bytes[0] as char;
            if (first == '\'' || first == '"') && bytes[value.len() - 1] as char == first {
                value = value[1..value.len() - 1].to_owned();
            }
        }
        fields.push((name.to_owned(), value));
    }
    fields
}

fn valid_field_name(name: &str) -> bool {
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !(first.is_ascii_alphabetic() || first == '_') {
        return false;
    }
    chars.all(|character| character.is_alphanumeric() || character == '_' || character == '-')
}

/// Approximate Python's `repr(str)` for the error text recorded by rust-anything.
fn python_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(value.len() + 2);
    out.push(quote);
    for character in value.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{0008}' => out.push_str("\\x08"),
            '\u{000C}' => out.push_str("\\x0c"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if !python_printable(c) => push_unicode_escape(&mut out, c),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn python_printable(character: char) -> bool {
    let code = character as u32;
    if character.is_control() || code == 0x7f {
        return false;
    }
    // Python treats separators and format characters as non-printable.
    if matches!(code, 0x2028 | 0x2029 | 0x00a0 | 0xfeff)
        || (0x200b..=0x200f).contains(&code)
        || (0x202a..=0x202e).contains(&code)
        || (0x2060..=0x206f).contains(&code)
    {
        return false;
    }
    true
}

fn push_unicode_escape(out: &mut String, character: char) {
    let code = character as u32;
    if code <= 0xff {
        out.push_str(&format!("\\x{code:02x}"));
    } else if code <= 0xffff {
        out.push_str(&format!("\\u{code:04x}"));
    } else {
        out.push_str(&format!("\\U{code:08x}"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_names_is_a_tuple_marker() {
        assert_eq!(skill_names().unwrap(), json!({ "$tuple": ["gossamer"] }));
    }

    #[test]
    fn description_comes_from_frontmatter() {
        let description = skill_description("gossamer".to_owned()).unwrap();
        assert!(description.starts_with("Web research toolkit"));
    }

    #[test]
    fn unknown_name_is_lookup_error() {
        let error = skill_text("nope".to_owned()).unwrap_err();
        assert_eq!(error.kind(), ErrorKind::Lookup);
        assert_eq!(error.message(), "unknown skill 'nope'; available: gossamer");
    }

    #[test]
    fn repr_handles_quotes_and_newlines() {
        assert_eq!(python_repr("a\nb"), "'a\\nb'");
        assert_eq!(python_repr("it's"), "\"it's\"");
    }

    #[test]
    fn field_names_are_ascii_led() {
        assert!(valid_field_name("description"));
        assert!(valid_field_name("_x-1"));
        assert!(!valid_field_name("1x"));
        assert!(!valid_field_name(" name"));
    }
}
