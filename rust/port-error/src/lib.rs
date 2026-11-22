//! Shared error type for the Rust port units.
//!
//! Core logic returns `PortError` instead of Python-formatted strings. The PyO3 layer maps the
//! kind to a Python exception; the front door uses `message()` for its user-facing errors.

use std::error::Error;
use std::fmt;

/// Broad category of a port failure, independent of any target language.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// Bad argument type or shape.
    Type,
    /// A required attribute is missing from an argument.
    Attribute,
    /// Invalid or unsupported value, including malformed input.
    Value,
    /// A lookup failed.
    Lookup,
    /// Filesystem or OS failure.
    Io,
    /// A requested operation is not supported by this port.
    Unsupported,
    /// A named skill does not exist.
    SkillNotFound,
}

/// A canonical error from a ported unit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PortError {
    kind: ErrorKind,
    message: String,
}

impl PortError {
    /// Creates an error with a category and a caller-facing message.
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    /// Returns the broad category.
    pub fn kind(&self) -> ErrorKind {
        self.kind
    }

    /// Returns the message without any language-specific exception prefix.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for PortError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for PortError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_is_the_plain_message() {
        let error = PortError::new(ErrorKind::Value, "bad input");
        assert_eq!(error.to_string(), "bad input");
        assert_eq!(error.kind(), ErrorKind::Value);
    }
}
