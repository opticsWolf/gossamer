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
