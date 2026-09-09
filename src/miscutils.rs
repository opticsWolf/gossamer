//! Small pure kernels from `models.py` / `structured_parser.py` (M21).
//!
//! The pydantic models, the oxide orchestration, the wall-clock
//! provenance (`_utc_now_iso`), and the markdown absolutizer
//! (`urljoin` semantics) stay Python by design — what ports here are
//! the four deterministic string kernels: host extraction for
//! per-domain stats, SHA-256 provenance hashes, document/page link
//! routing, and 1-based page-range parsing with byte-identical
//! `ValueError` messages.

use crate::pycompat::{py_repr, py_strip};
use sha2::{Digest, Sha256};

/// `models._domain_of`: `urlparse(url).netloc or url`, with any
/// exception returning `url`. The wrapper runs the non-`str` gate in
/// Python; here `url` is always `str` (on which `urlparse` never
/// raises), so only the netloc extraction is replicated: a netloc
/// exists after a leading `//` or a valid `scheme://`; it runs to the
/// next `/`, `?`, or `#` with case and userinfo/port preserved. Empty
/// netloc falls back to the full input (`or url`).
pub fn domain_of_impl(url: &str) -> String {
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
    let end = rest
        .find(['/', '?', '#'])
        .unwrap_or(rest.len());
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

/// `models._sha256_hex`: SHA-256 of the UTF-8 bytes, hex-encoded.
/// (The wrapper's `encode` call doubles as the type gate.)
pub fn sha256_hex_impl(text: &str) -> String {
    let mut h = Sha256::new();
    h.update(text.as_bytes());
    let out = h.finalize();
    hex_encode(&out)
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

const DOCUMENT_EXTS: [&str; 11] = [
    ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".json",
    ".xml", ".rss", ".atom",
];

/// `structured_parser.classify_link`: document iff the URL path
/// (before `?`/`#`, after any netloc) lowercases to one of
/// `DOCUMENT_EXTENSIONS`. The wrapper returns `"page"` for every
/// non-`str` input (the original's `except` arm); here `url` is `str`.
pub fn classify_link_impl(url: &str) -> &'static str {
    let path = url_path(url).to_lowercase();
    for ext in DOCUMENT_EXTS {
        if path.ends_with(ext) {
            return "document";
        }
    }
    "page"
}

/// Path component of a URL: strip `#fragment`, then `?query`, then —
/// when a netloc is present — everything through the authority.
/// (Query/fragment are stripped first, so `?`/`#` inside the path
/// search never leak into the extension check.)
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

/// Mirror `int(s)`: surrounding `strip()`, optional sign, ASCII and
/// Unicode decimal digits with single inter-digit underscores.
/// (Same shape as `adapters::py_int`; duplicated so this module stays
/// independent.)
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
    let mut prev_underscore = true; // first char must be a digit
    for c in digits.chars() {
        if c == '_' {
            if prev_underscore {
                return None;
            }
            prev_underscore = true;
            continue;
        }
        {
            let d = c.to_digit(10)?;
            val = val.checked_mul(10)?.checked_add(d as i128)?;
            prev_underscore = false;
        }
    }
    if prev_underscore {
        return None;
    }
    Some(sign * val)
}

fn parse_page_number(raw: &str) -> Result<i128, String> {
    match py_int(raw) {
        Some(v) if v >= 1 => Ok(v),
        Some(_) => Err(
            "Invalid page range: page numbers are 1-based.".to_string()
        ),
        None => Err(format!(
            "Invalid page range: {} is not a page number.",
            py_repr(raw)
        )),
    }
}

/// `structured_parser.parse_page_range`: 1-based inclusive
/// `(start, end)` with `None` for open-ended. `ValueError` texts are
/// byte-identical (the `#[pyfunction]` maps them via `PyValueError`).
/// Inputs above `i128` range report "not a page number" instead of
/// Python's unbounded int — page numbers never reach 39 digits.
pub fn parse_page_range_impl(spec: &str) -> Result<(i128, Option<i128>), String> {
    let spec = py_strip(spec);
    if spec.is_empty() || spec == "-" {
        return Ok((1, None));
    }
    if spec.contains('-') {
        let (left, right) = match spec.find('-') {
            // `partition("-")`: split at the FIRST dash.
            Some(i) => (py_strip(&spec[..i]), py_strip(&spec[i + 1..])),
            None => unreachable!(),
        };
        if right.contains('-') {
            return Err(format!(
                "Invalid page range: expected N, N-M, N- or -M, got {}.",
                py_repr(spec)
            ));
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
        if let Some(e) = end {
            if e < start {
                return Err(format!(
                    "Invalid page range: start {start} is after end {e}."
                ));
            }
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
        assert_eq!(
            domain_of_impl("https://example.com/x"),
            "example.com"
        );
        assert_eq!(
            domain_of_impl("http://user:pw@host:8080/p"),
            "user:pw@host:8080"
        );
        assert_eq!(domain_of_impl("//cdn.example/a"), "cdn.example");
        assert_eq!(domain_of_impl("not a url"), "not a url");
        assert_eq!(domain_of_impl(""), "");
        assert_eq!(domain_of_impl("mailto:a@b"), "mailto:a@b");
        assert_eq!(
            domain_of_impl("https://EXAMPLE.COM/x"),
            "EXAMPLE.COM"
        );
    }

    #[test]
    fn sha_shapes() {
        assert_eq!(
            sha256_hex_impl(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(sha256_hex_impl("abc").len(), 64);
    }

    #[test]
    fn classify_shapes() {
        assert_eq!(classify_link_impl("https://x/a.PDF"), "document");
        assert_eq!(
            classify_link_impl("https://x/a.pdf?dl=1#frag"),
            "document"
        );
        assert_eq!(classify_link_impl("https://x/a"), "page");
        assert_eq!(classify_link_impl("https://x/data.JSON"), "document");
        assert_eq!(classify_link_impl("a/b/csv"), "page");
        assert_eq!(classify_link_impl(""), "page");
    }

    #[test]
    fn pages_shapes() {
        assert_eq!(parse_page_range_impl("10"), Ok((10, Some(10))));
        assert_eq!(parse_page_range_impl("10-20"), Ok((10, Some(20))));
        assert_eq!(parse_page_range_impl("10-"), Ok((10, None)));
        assert_eq!(parse_page_range_impl("-20"), Ok((1, Some(20))));
        assert_eq!(parse_page_range_impl(""), Ok((1, None)));
        assert_eq!(parse_page_range_impl("-"), Ok((1, None)));
        assert_eq!(
            parse_page_range_impl(" 10 - 20 "),
            Ok((10, Some(20)))
        );
        assert!(parse_page_range_impl("10-5").is_err());
        assert!(parse_page_range_impl("x").is_err());
        assert!(parse_page_range_impl("0").is_err());
        assert!(parse_page_range_impl("1-2-3").is_err());
        assert_eq!(
            parse_page_range_impl("x").unwrap_err(),
            "Invalid page range: 'x' is not a page number."
        );
    }
}

use pyo3::prelude::*;

#[pyfunction]
pub fn domain_of(url: &str) -> String {
    domain_of_impl(url)
}

#[pyfunction]
pub fn sha256_hex(text: &str) -> String {
    sha256_hex_impl(text)
}

#[pyfunction]
pub fn classify_link(url: &str) -> String {
    classify_link_impl(url).to_string()
}

#[pyfunction]
pub fn parse_page_range(spec: &str) -> PyResult<(i128, Option<i128>)> {
    parse_page_range_impl(spec)
        .map_err(pyo3::exceptions::PyValueError::new_err)
}
