//! Small deterministic string kernels from `models.py` / `structured_parser.py`.
//!
//! Pydantic models, oxide orchestration, wall-clock provenance, and markdown link absolutization stay
//! in Python. This module owns only pure functions: host extraction, SHA-256 hex, document/page link
//! routing, and 1-based page-range parsing.

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;

use sha2::{Digest, Sha256};

use crate::compat::{py_repr, py_strip};

/// Canonical page-range error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct PageRangeError {
    message: String,
    backtrace: Backtrace,
}

impl PageRangeError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the Python-compatible error message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace for diagnostics.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for PageRangeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for PageRangeError {}

/// Extracts the URL netloc, falling back to the full input.
///
/// # Examples
///
/// ```
/// use gossamer::miscutils::domain_of;
///
/// assert_eq!(domain_of("https://example.com/x"), "example.com");
/// assert_eq!(domain_of("not a url"), "not a url");
/// ```
pub fn domain_of(url: &str) -> String {
    match url_netloc(url) {
        Some(host) if !host.is_empty() => host.to_string(),
        _ => url.to_string(),
    }
}

fn url_netloc(url: &str) -> Option<&str> {
    let rest = if let Some(stripped) = url.strip_prefix("//") {
        stripped
    } else {
        let colon = url.find(':')?;
        if !valid_scheme(&url[..colon]) {
            return None;
        }
        url[colon + 1..].strip_prefix("//")?
    };
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    Some(&rest[..end])
}

fn valid_scheme(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    s.chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
}

/// Returns the lowercase SHA-256 hex digest of UTF-8 text.
///
/// # Examples
///
/// ```
/// use gossamer::miscutils::sha256_hex;
///
/// assert_eq!(
///     sha256_hex(""),
///     "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
/// );
/// ```
pub fn sha256_hex(text: &str) -> String {
    let mut h = Sha256::new();
    h.update(text.as_bytes());
    hex_encode(&h.finalize())
}

fn hex_encode(bytes: &[u8]) -> String {
    const LUT: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(LUT[(b >> 4) as usize] as char);
        out.push(LUT[(b & 15) as usize] as char);
    }
    out
}

const DOCUMENT_EXTS: [&str; 15] = [
    ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".xlsb", ".csv", ".txt", ".md",
    ".json", ".xml", ".rss", ".atom",
];

/// Routes a URL path to `"document"` or `"page"`.
///
/// # Examples
///
/// ```
/// use gossamer::miscutils::classify_link;
///
/// assert_eq!(classify_link("https://x/a.PDF"), "document");
/// assert_eq!(classify_link("https://x/a"), "page");
/// ```
pub fn classify_link(url: &str) -> &'static str {
    let path = url_path(url).to_lowercase();
    for ext in DOCUMENT_EXTS {
        if path.ends_with(ext) {
            return "document";
        }
    }
    "page"
}

fn url_path(url: &str) -> &str {
    let no_frag = url.split('#').next().unwrap_or(url);
    let no_query = no_frag.split('?').next().unwrap_or(no_frag);
    if let Some(rest) = no_query.strip_prefix("//") {
        match rest.find('/') {
            Some(i) => &rest[i..],
            None => "",
        }
    } else if let Some(colon) = no_query.find(':') {
        if valid_scheme(&no_query[..colon]) {
            let after = &no_query[colon + 1..];
            if let Some(stripped) = after.strip_prefix("//") {
                match stripped.find('/') {
                    Some(i) => &stripped[i..],
                    None => "",
                }
            } else {
                after
            }
        } else {
            no_query
        }
    } else {
        no_query
    }
}

fn py_int(s: &str) -> Option<i128> {
    let t = py_strip(s);
    let (sign, digits) = match t.strip_prefix('+') {
        Some(rest) => (1i128, rest),
        None => match t.strip_prefix('-') {
            Some(rest) => (-1i128, rest),
            None => (1i128, t),
        },
    };
    if digits.is_empty() {
        return None;
    }
    let mut val: i128 = 0;
    let mut prev_underscore = true;
    for c in digits.chars() {
        if c == '_' {
            if prev_underscore {
                return None;
            }
            prev_underscore = true;
            continue;
        }
        let d = c.to_digit(10)?;
        val = val.checked_mul(10)?.checked_add(d as i128)?;
        prev_underscore = false;
    }
    if prev_underscore {
        return None;
    }
    Some(sign * val)
}

fn parse_page_number(raw: &str) -> Result<i128, PageRangeError> {
    match py_int(raw) {
        Some(v) if v >= 1 => Ok(v),
        Some(_) => Err(PageRangeError::new(
            "Invalid page range: page numbers are 1-based.",
        )),
        None => Err(PageRangeError::new(format!(
            "Invalid page range: {} is not a page number.",
            py_repr(raw)
        ))),
    }
}

/// Parses a 1-based inclusive page range into `(start, end)`.
///
/// Empty input or `-` means all pages (`(1, None)`); `N-` is open-ended.
///
/// # Errors
///
/// Returns [`PageRangeError`] with the Python-compatible `ValueError` message for malformed ranges.
///
/// # Examples
///
/// ```
/// use gossamer::miscutils::parse_page_range;
///
/// assert_eq!(parse_page_range("10-20").unwrap(), (10, Some(20)));
/// assert_eq!(parse_page_range("-").unwrap(), (1, None));
/// ```
pub fn parse_page_range(spec: &str) -> Result<(i128, Option<i128>), PageRangeError> {
    let spec = py_strip(spec);
    if spec.is_empty() || spec == "-" {
        return Ok((1, None));
    }
    if spec.contains('-') {
        let (left, right) = match spec.find('-') {
            Some(i) => (py_strip(&spec[..i]), py_strip(&spec[i + 1..])),
            None => unreachable!("contains('-') guarantees find returns an index"),
        };
        if right.contains('-') {
            return Err(PageRangeError::new(format!(
                "Invalid page range: expected N, N-M, N- or -M, got {}.",
                py_repr(spec)
            )));
        }
        let start = if left.is_empty() {
            1
        } else {
            parse_page_number(left)?
        };
        let end = if right.is_empty() {
            None
        } else {
            Some(parse_page_number(right)?)
        };
        if let Some(e) = end
            && e < start
        {
            return Err(PageRangeError::new(format!(
                "Invalid page range: start {start} is after end {e}."
            )));
        }
        Ok((start, end))
    } else {
        let start = parse_page_number(spec)?;
        Ok((start, Some(start)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn domain_shapes() {
        assert_eq!(domain_of("https://example.com/x"), "example.com");
        assert_eq!(domain_of("http://user:pw@host:8080/p"), "user:pw@host:8080");
        assert_eq!(domain_of("//cdn.example/a"), "cdn.example");
        assert_eq!(domain_of("not a url"), "not a url");
        assert_eq!(domain_of(""), "");
        assert_eq!(domain_of("mailto:a@b"), "mailto:a@b");
        assert_eq!(domain_of("https://EXAMPLE.COM/x"), "EXAMPLE.COM");
    }

    #[test]
    fn sha_shapes() {
        assert_eq!(
            sha256_hex(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(sha256_hex("abc").len(), 64);
    }

    #[test]
    fn classify_shapes() {
        assert_eq!(classify_link("https://x/a.PDF"), "document");
        assert_eq!(classify_link("https://x/a.pdf?dl=1#frag"), "document");
        assert_eq!(classify_link("https://x/a"), "page");
        assert_eq!(classify_link("https://x/data.JSON"), "document");
        assert_eq!(classify_link("a/b/csv"), "page");
        assert_eq!(classify_link(""), "page");
    }

    #[test]
    fn pages_shapes() {
        assert_eq!(parse_page_range("10").unwrap(), (10, Some(10)));
        assert_eq!(parse_page_range("10-20").unwrap(), (10, Some(20)));
        assert_eq!(parse_page_range("10-").unwrap(), (10, None));
        assert_eq!(parse_page_range("-20").unwrap(), (1, Some(20)));
        assert_eq!(parse_page_range("").unwrap(), (1, None));
        assert_eq!(parse_page_range("-").unwrap(), (1, None));
        assert_eq!(parse_page_range(" 10 - 20 ").unwrap(), (10, Some(20)));
        assert!(parse_page_range("10-5").is_err());
        assert!(parse_page_range("x").is_err());
        assert!(parse_page_range("0").is_err());
        assert!(parse_page_range("1-2-3").is_err());
        assert_eq!(
            parse_page_range("x").unwrap_err().message(),
            "Invalid page range: 'x' is not a page number."
        );
    }
}
