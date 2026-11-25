//! Pure URL preparation shared by document-facing tools.
//!
//! The Python layer turns rejected URLs into recoverable JSON records. This module owns the
//! validation decision and error classification; the binding/front door owns presentation.

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;

use serde_json::{Map, Value, json};

use crate::ssrf::check_url;
use crate::urls::normalize_url;

/// Error class to report to callers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UrlPrepErrorKind {
    /// `normalize_url` rejected the raw input.
    Value,
    /// SSRF/public-host validation rejected the normalized URL.
    SsrfBlocked,
}

/// URL preparation failure with a stable Python-facing class name.
#[derive(Debug)]
pub struct UrlPrepError {
    kind: UrlPrepErrorKind,
    raw: String,
    message: String,
    backtrace: Backtrace,
}

impl UrlPrepError {
    /// Creates a preparation error.
    pub fn new(kind: UrlPrepErrorKind, raw: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind,
            raw: raw.into(),
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the Python-facing error class name.
    pub fn error_type(&self) -> &'static str {
        match self.kind {
            UrlPrepErrorKind::Value => "ValueError",
            UrlPrepErrorKind::SsrfBlocked => "SsrfBlockedError",
        }
    }

    /// Returns the raw caller-supplied URL.
    pub fn raw(&self) -> &str {
        &self.raw
    }

    /// Returns the plain rejection message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }

    /// Renders the same error record the Python tools return.
    pub fn to_record(&self) -> Value {
        let mut map = Map::new();
        map.insert("url".to_owned(), json!(self.raw));
        map.insert(
            "error".to_owned(),
            json!(format!("URL rejected: {}", self.message)),
        );
        map.insert("error_type".to_owned(), json!(self.error_type()));
        Value::Object(map)
    }
}

impl fmt::Display for UrlPrepError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for UrlPrepError {}

/// Normalizes and SSRF-validates a caller-supplied URL.
///
/// # Errors
///
/// Returns [`UrlPrepError`] when the URL cannot be interpreted or points at a non-public host.
pub fn prepare_url(raw: &str, runtime_minor: Option<u32>) -> Result<String, UrlPrepError> {
    let normalized = normalize_url(Some(raw), None, runtime_minor)
        .map_err(|e| UrlPrepError::new(UrlPrepErrorKind::Value, raw, e.message().to_owned()))?;
    check_url(&normalized, false, runtime_minor).map_err(|e| {
        UrlPrepError::new(UrlPrepErrorKind::SsrfBlocked, raw, e.message().to_owned())
    })?;
    Ok(normalized)
}

/// Collapse a name to one safe basename, matching `Path(...).stem` on Windows inputs.
pub fn sanitize_filename(name: &str, fallback: &str) -> String {
    let last = name.replace('\\', "/").rsplit('/').next().unwrap_or("").to_string();
    let stem = match last.rfind('.') {
        Some(index) if index > 0 && index + 1 < last.len() => &last[..index],
        _ => &last[..],
    };
    let replaced = stem
        .replace("..", "_")
        .trim()
        .chars()
        .map(|ch| if ch.is_alphanumeric() || matches!(ch, '-' | '_' | ' ') { ch } else { '_' })
        .collect::<String>();
    let cleaned = replaced.trim_matches(|ch| matches!(ch, '.' | '_' | '-'));
    if cleaned.is_empty() { fallback.to_string() } else { cleaned.to_string() }
}


/// Whether the JATS route may handle a source under the requested table format.
pub fn jats_tables_allows(source: &str, tables_as: &str) -> bool {
    if tables_as == "markdown" { return true; }
    let whole = source.rsplit('/').next().unwrap_or(source);
    let direct = whole.rfind('.').map(|index| whole[index..].to_ascii_lowercase()).unwrap_or_default();
    if !direct.is_empty() {
        return !matches!(direct.as_str(), ".xlsx" | ".xls" | ".xlsb" | ".csv");
    }
    let without_fragment = source.split('#').next().unwrap_or(source);
    let without_query = without_fragment.split('?').next().unwrap_or(without_fragment);
    let last = without_query.rsplit('/').next().unwrap_or(without_query);
    let suffix = last.rfind('.').map(|index| last[index..].to_ascii_lowercase()).unwrap_or_default();
    !matches!(suffix.as_str(), ".xlsx" | ".xls" | ".xlsb" | ".csv")
}

/// Append the JATS route marker to an already-canonical cache base.
pub fn jats_cache_key(base: &str) -> String {
    format!("{base}#jats")
}

