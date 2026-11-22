//! URL identity primitives: normalization, canonicalization, content hashing.
//!
//! Pure port of the legacy root `src/urls.rs`. Parsing mirrors CPython `urllib.parse`
//! operation-for-operation because canonical URLs must agree exactly with the Python original.

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;
use std::path::Path;

use sha2::{Digest, Sha256};

use crate::compat::py_repr;
use crate::urlparts::{
    authority_end, hostinfo_parts, hostname_value, join_url, parse_qsl_keep_blank,
    port_value, quote_plus, sanitize_for_split, split_validated,
};

// Mirrors gossamer.structured_parser.DOCUMENT_EXTENSIONS. A Python parity test asserts behavioral
// agreement over every entry, so drift fails loudly.
const DOCUMENT_EXTENSIONS: &[&str] = &[
    ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".xlsb", ".csv", ".txt", ".md",
    ".json", ".xml", ".rss", ".atom",
];

const TRACKING_PARAM_PREFIXES: &[&str] = &["utm_", "fbclid", "gclid", "mc_", "ref_"];

/// Python `urlparse` scheme detection, exposed for the legacy robots wrapper.
pub fn has_scheme(s: &str, runtime_minor: Option<u32>) -> bool {
    crate::urlparts::has_scheme(s, runtime_minor)
}

/// Canonical URL validation error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct UrlError {
    message: String,
    backtrace: Backtrace,
}

impl UrlError {
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

impl fmt::Display for UrlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for UrlError {}

fn normalize_inner(
    raw: Option<&str>,
    base: Option<&str>,
    runtime_minor: Option<u32>,
) -> Result<String, String> {
    let raw_str = raw.unwrap_or("");
    let mut s: String = raw_str
        .trim()
        .trim_matches(|c| c == '"' || c == '\'')
        .trim_matches(|c| c == '<' || c == '>')
        .trim()
        .to_string();
    if s.is_empty() {
        return Err("Empty URL".to_string());
    }
    let (pre_scheme, _) = split_validated(&sanitize_for_split(&s), runtime_minor)?;
    if let Some(b) = base
        && pre_scheme.is_empty()
        && !s.starts_with("//")
    {
        s = join_url(b, &s);
    }
    if s.starts_with("//") {
        s = format!("https:{s}");
    }
    let (mut scheme, _) = split_validated(&sanitize_for_split(&s), runtime_minor)?;
    if s.contains(' ') {
        return Err(format!(
            "Cannot interpret {} as a URL (contains spaces)",
            py_repr(raw_str)
        ));
    }
    if scheme.is_empty() {
        if s.starts_with("./")
            || s.starts_with("../")
            || s.starts_with(".\\")
            || s.starts_with("..\\")
            || Path::new(&s).exists()
        {
            return Err(format!(
                "{} looks like a local file path, not a URL",
                py_repr(raw_str)
            ));
        }
        let san = sanitize_for_split(&s);
        let no_query = san.split('?').next().unwrap_or("");
        let candidate_host = no_query.split('/').next().unwrap_or("");
        if !candidate_host.contains('.') && candidate_host != "localhost" {
            return Err(format!("{} does not look like a URL", py_repr(raw_str)));
        }
        if !s.contains('/')
            && DOCUMENT_EXTENSIONS
                .iter()
                .any(|e| candidate_host.to_lowercase().ends_with(e))
        {
            return Err(format!(
                "{} looks like a local file path, not a URL",
                py_repr(raw_str)
            ));
        }
        s = format!("https://{s}");
        (scheme, _) = split_validated(&sanitize_for_split(&s), runtime_minor)?;
    }
    if scheme != "http" && scheme != "https" {
        return Err(format!("Unsupported URL scheme in {}", py_repr(raw_str)));
    }
    let san = sanitize_for_split(&s);
    let authority = san
        .split_once("://")
        .map(|(_, after)| &after[..authority_end(after)])
        .unwrap_or("");
    let (hostname, _) = hostinfo_parts(authority);
    if hostname_value(&hostname).is_none() {
        return Err(format!("Cannot parse {} as a URL (no host)", py_repr(raw_str)));
    }
    Ok(s)
}

/// Normalizes a raw URL the way `gossamer.config.normalize_url` does.
///
/// `runtime_minor` selects CPython error spellings; `None` uses the newest supported spelling.
///
/// # Errors
///
/// Returns [`UrlError`] for empty, malformed, local, or unsupported-scheme URLs.
pub fn normalize_url(
    raw: Option<&str>,
    base: Option<&str>,
    runtime_minor: Option<u32>,
) -> Result<String, UrlError> {
    normalize_inner(raw, base, runtime_minor).map_err(UrlError::new)
}

fn canonical_inner(
    url: &str,
    query_mode: &str,
    runtime_minor: Option<u32>,
) -> Result<String, String> {
    let normalized = normalize_inner(Some(url), None, runtime_minor)?;
    let san = sanitize_for_split(&normalized);
    let no_frag = san.split('#').next().unwrap_or("");
    let (scheme_raw, rest) = no_frag
        .split_once(':')
        .ok_or_else(|| format!("Cannot parse {url} as a URL (no host)"))?;
    let scheme = scheme_raw.to_ascii_lowercase();
    let after = rest.strip_prefix("//").unwrap_or(rest);
    let cut = after.find(['/', '?']).unwrap_or(after.len());
    let authority = &after[..cut];
    let remainder = &after[cut..];
    let (path, query) = match remainder.find('?') {
        Some(i) => (&remainder[..i], &remainder[i + 1..]),
        None => (remainder, ""),
    };
    let path = path.split(';').next().unwrap_or("");
    let (hostname_raw, port_raw) = hostinfo_parts(authority);
    let host = hostname_value(&hostname_raw).unwrap_or_default();
    let host = host.strip_prefix("www.").unwrap_or(&host).to_string();
    let port = port_value(port_raw.as_deref())?;
    let default_port = if scheme == "http" { 80 } else { 443 };
    let netloc = match port {
        Some(p) if p != default_port => format!("{host}:{p}"),
        _ => host.clone(),
    };
    let trimmed = path.trim_end_matches('/');
    let path_out = if trimmed.is_empty() { "/" } else { trimmed };
    let query_str = if query_mode == "drop" {
        String::new()
    } else {
        let mut items = parse_qsl_keep_blank(query);
        if query_mode == "drop-tracking" {
            items.retain(|(k, _)| {
                let lower = k.to_lowercase();
                !TRACKING_PARAM_PREFIXES
                    .iter()
                    .any(|p| lower.starts_with(p))
            });
        }
        let mut keyed: Vec<(String, String)> = items
            .into_iter()
            .map(|(k, v)| (k.to_lowercase(), v))
            .collect();
        keyed.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
        keyed
            .iter()
            .map(|(k, v)| format!("{}={}", quote_plus(k), quote_plus(v)))
            .collect::<Vec<_>>()
            .join("&")
    };
    Ok(if query_str.is_empty() {
        format!("{scheme}://{netloc}{path_out}")
    } else {
        format!("{scheme}://{netloc}{path_out}?{query_str}")
    })
}

/// Canonicalizes a URL for deduplication.
///
/// `query_mode` is `keep`, `drop`, or `drop-tracking`; unknown modes behave like `keep`.
///
/// # Errors
///
/// Returns [`UrlError`] when the input cannot be normalized or its port is invalid.
pub fn canonical_url(
    url: &str,
    query_mode: &str,
    runtime_minor: Option<u32>,
) -> Result<String, UrlError> {
    canonical_inner(url, query_mode, runtime_minor).map_err(UrlError::new)
}

/// Returns the SHA-256 hex digest of optional text.
pub fn content_hash(text: Option<&str>) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.unwrap_or("").as_bytes());
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_domains_gain_https() {
        assert_eq!(
            normalize_url(Some("example.com/a"), None, None).unwrap(),
            "https://example.com/a"
        );
    }

    #[test]
    fn canonical_modes() {
        let url = "https://www.Example.com/a/?utm_x=1&b=2";
        assert_eq!(
            canonical_url(url, "drop", None).unwrap(),
            "https://example.com/a"
        );
        assert_eq!(
            canonical_url(url, "drop-tracking", None).unwrap(),
            "https://example.com/a?b=2"
        );
    }

    #[test]
    fn content_hash_is_sha256() {
        assert_eq!(content_hash(Some("")), format!("{:x}", Sha256::digest("")));
    }
}
