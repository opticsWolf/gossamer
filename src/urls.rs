//! URL identity primitives: `normalize_url`, `canonical_url`, `content_hash`.
//!
//! Port of `gossamer.config.normalize_url` / `canonical_url` and
//! `gossamer.dedup.content_hash`. Parsing mirrors CPython's
//! `urllib.parse` operation-for-operation (sanitization, `urlsplit`
//! netloc validation, `_hostinfo` splitting, `parse_qsl` / `urlencode`
//! query handling) because `canonical_url` must agree with it exactly:
//! no dot-segment resolution, no path re-encoding, lowercase-only
//! (no IDNA) host handling.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use std::path::Path;
use crate::pycompat::py_repr;
use unicode_normalization::UnicodeNormalization;

// Mirrors gossamer.structured_parser.DOCUMENT_EXTENSIONS. A Python parity
// test asserts behavioral agreement over every entry, so drift fails loudly.
const DOCUMENT_EXTENSIONS: &[&str] = &[
    ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".json", ".xml",
    ".rss", ".atom",
];

const TRACKING_PARAM_PREFIXES: &[&str] = &["utm_", "fbclid", "gclid", "mc_", "ref_"];


pub(crate) fn has_scheme_pub(s: &str) -> bool {
    has_scheme(s)
}

pub(crate) fn sanitize_pub(s: &str) -> String {
    sanitize_for_split(s)
}

/// `urlsplit` netloc validation (bracket matching, bracketed-host
/// rules, NFKC) for callers that parse URLs themselves (SSRF guard).
/// Raises propagate exactly where `urlparse` would raise them.
pub(crate) fn validate_netloc_pub(netloc: &str) -> Result<(), String> {
    validate_netloc(netloc)
}

/// Python `urlparse` scheme detection: leading ASCII letter, then scheme
/// chars, before the first ':'.
fn has_scheme(s: &str) -> bool {
    match s.find(':') {
        Some(i) if i > 0 => {
            let head = &s[..i];
            let mut chars = head.chars();
            match chars.next() {
                Some(c) if c.is_ascii_alphabetic() => {}
                _ => return false,
            }
            head.chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        }
        _ => false,
    }
}

/// What `urlsplit` sees: leading C0 controls/spaces stripped, TAB/CR/LF
/// removed everywhere (`_WHATWG_C0_CONTROL_OR_SPACE`,
/// `_UNSAFE_URL_BYTES_TO_REMOVE`).
fn sanitize_for_split(s: &str) -> String {
    let t = s.trim_start_matches(|c: char| c == ' ' || (c as u32) <= 0x1f);
    t.replace(['\t', '\r', '\n'], "")
}

/// RFC 3986 reference resolution (what `urllib.parse.urljoin` does).
fn join_url(base: &str, reference: &str) -> String {
    match url::Url::parse(base).and_then(|b| b.join(reference)) {
        Ok(u) => u.to_string(),
        Err(_) => reference.to_string(),
    }
}

/// Netloc validation mirroring `urlsplit`: bracket matching,
/// `_check_bracketed_netloc`, `_checknetloc` (NFKC). Raises propagate
/// exactly where `urlparse` would raise them.
fn validate_netloc(netloc: &str) -> Result<(), String> {
    if netloc.contains('[') != netloc.contains(']') {
        return Err("Invalid IPv6 URL".to_string());
    }
    if netloc.contains('[') {
        check_bracketed_netloc(netloc)?;
    }
    check_nfkc_netloc(netloc)?;
    Ok(())
}

fn check_bracketed_netloc(netloc: &str) -> Result<(), String> {
    // Mirrors _check_bracketed_netloc (which mirrors _hostinfo splitting).
    let hostname_and_port = netloc.rsplit('@').next().unwrap_or("");
    let (before_bracket, have_open, bracketed) = match hostname_and_port.find('[') {
        Some(i) => (&hostname_and_port[..i], true, &hostname_and_port[i + 1..]),
        None => ("", false, hostname_and_port),
    };
    if have_open {
        if !before_bracket.is_empty() {
            return Err("Invalid IPv6 URL".to_string());
        }
        let (hostname, _, port) = match bracketed.find(']') {
            Some(i) => (&bracketed[..i], true, &bracketed[i + 1..]),
            None => (bracketed, false, ""),
        };
        if !port.is_empty() && !port.starts_with(':') {
            return Err("Invalid IPv6 URL".to_string());
        }
        check_bracketed_host(hostname)?;
    } else {
        let (hostname, _, _) = match bracketed.find(':') {
            Some(i) => (&bracketed[..i], true, &bracketed[i + 1..]),
            None => (bracketed, false, ""),
        };
        check_bracketed_host(hostname)?;
    }
    Ok(())
}

fn not_ip_addr(hostname: &str) -> String {
    format!(
        "{} does not appear to be an IPv4 or IPv6 address",
        py_repr(hostname)
    )
}

fn check_bracketed_host(hostname: &str) -> Result<(), String> {
    // Zone IDs: only `addr%zone` with a bare-IPv6 addr part and a
    // non-empty `%`-free zone parses (mirrors `ipaddress`, which fails
    // v4+zone and empty/multi `%` wholes with "does not appear").
    if hostname.contains('%') {
        let i = hostname.find('%').unwrap_or(hostname.len());
        let (addr, zone) = (&hostname[..i], &hostname[i + 1..]);
        if !zone.is_empty()
            && !zone.contains('%')
            && addr.parse::<std::net::Ipv6Addr>().is_ok()
        {
            return Ok(());
        }
        return Err(not_ip_addr(hostname));
    }
    if let Some(rest) = hostname.strip_prefix('v') {
        let valid = match rest.find('.') {
            Some(i) if i > 0 => {
                rest[..i].chars().all(|c| c.is_ascii_hexdigit()) && rest.len() > i + 1
            }
            _ => false,
        };
        return if valid {
            Ok(())
        } else {
            Err("IPvFuture address is invalid".to_string())
        };
    }
    if hostname.parse::<std::net::Ipv4Addr>().is_ok() {
        return Err("An IPv4 address cannot be in brackets".to_string());
    }
    if hostname.parse::<std::net::Ipv6Addr>().is_err() {
        return Err(not_ip_addr(hostname));
    }
    Ok(())
}

fn check_nfkc_netloc(netloc: &str) -> Result<(), String> {
    if netloc.is_empty() || netloc.is_ascii() {
        return Ok(());
    }
    let stripped: String = netloc
        .chars()
        .filter(|c| !matches!(c, '@' | ':' | '#' | '?'))
        .collect();
    let normalized: String = stripped.nfkc().collect();
    if normalized != stripped && normalized.contains(['/', '?', '#', '@', ':']) {
        return Err(format!(
            "netloc '{netloc}' contains invalid characters under NFKC normalization"
        ));
    }
    Ok(())
}

/// Split a sanitized string the way `urlsplit` does: scheme, then netloc
/// (up to the first `/`, `?` or `#`). Validates the netloc on the way.
fn split_validated(san: &str) -> Result<(String, Option<String>), String> {
    let (scheme, rest) = match san.find(':') {
        Some(i) if has_scheme(&san[..i + 1]) => {
            (san[..i].to_ascii_lowercase(), san[i + 1..].to_string())
        }
        _ => (String::new(), san.to_string()),
    };
    if let Some(after) = rest.strip_prefix("//") {
        let cut = after
            .find(|c| c == '/' || c == '?' || c == '#')
            .unwrap_or(after.len());
        let netloc = after[..cut].to_string();
        validate_netloc(&netloc)?;
        return Ok((scheme, Some(netloc)));
    }
    Ok((scheme, None))
}

/// Pure `_hostinfo` mirror: userinfo dropped; brackets stripped; port is
/// the raw string (validated later, exactly like `.port`).
fn hostinfo_parts(authority: &str) -> (String, Option<String>) {
    let hostinfo = authority.rsplit('@').next().unwrap_or("");
    if let Some(br) = hostinfo.find('[') {
        let bracketed = &hostinfo[br + 1..];
        let (hostname, port) = match bracketed.find(']') {
            Some(i) => {
                let after = &bracketed[i + 1..];
                let port = after.strip_prefix(':').unwrap_or(after);
                (&bracketed[..i], Some(port.to_string()))
            }
            None => (bracketed, None),
        };
        let port = port.filter(|p| !p.is_empty());
        return (hostname.to_string(), port);
    }
    match hostinfo.find(':') {
        Some(i) => {
            let port = &hostinfo[i + 1..];
            (
                hostinfo[..i].to_string(),
                if port.is_empty() {
                    None
                } else {
                    Some(port.to_string())
                },
            )
        }
        None => (hostinfo.to_string(), None),
    }
}

/// `.hostname`: empty → None; zone IDs (`%…`) keep their case.
fn hostname_value(hostname: &str) -> Option<String> {
    if hostname.is_empty() {
        return None;
    }
    let (head, percent, zone) = match hostname.find('%') {
        Some(i) => (
            hostname[..i].to_string(),
            "%".to_string(),
            hostname[i + 1..].to_string(),
        ),
        None => (hostname.to_string(), String::new(), String::new()),
    };
    Some(head.to_lowercase() + &percent + &zone)
}

/// `.port`: empty/absent → None; ASCII digits → value (range-checked);
/// anything else → `ValueError` with CPython's exact message.
fn port_value(port_raw: Option<&str>) -> Result<Option<u16>, String> {
    match port_raw {
        None => Ok(None),
        Some(p) => {
            if !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()) {
                match p.parse::<u32>() {
                    Ok(n) if n <= 65535 => return Ok(Some(n as u16)),
                    _ => return Err("Port out of range 0-65535".to_string()),
                }
            }
            Err(format!(
                "Port could not be cast to integer value as {}",
                py_repr(p)
            ))
        }
    }
}

fn authority_end(s: &str) -> usize {
    s.find(|c| c == '/' || c == '?' || c == '#')
        .unwrap_or(s.len())
}

pub fn normalize_url_impl(raw: Option<&str>, base: Option<&str>) -> Result<String, String> {
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
    // Mirrors `urlparse(s).scheme` in the base-join condition (raises
    // propagate before any join is attempted).
    let (pre_scheme, _) = split_validated(&sanitize_for_split(&s))?;
    if let Some(b) = base {
        if pre_scheme.is_empty() && !s.starts_with("//") {
            s = join_url(b, &s);
        }
    }
    if s.starts_with("//") {
        s = format!("https:{s}");
    }
    // Mirrors `urlparse(s)` — validation raises propagate here.
    let (mut scheme, _) = split_validated(&sanitize_for_split(&s))?;
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
        // Candidate host from the parsed path (sanitized), like
        // `urlparse(s).path.split("/")[0]`.
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
        // Mirrors the second `urlparse(s)` — re-validates the final string.
        (scheme, _) = split_validated(&sanitize_for_split(&s))?;
    }
    if scheme != "http" && scheme != "https" {
        return Err(format!("Unsupported URL scheme in {}", py_repr(raw_str)));
    }
    // Host check on the final string (mirrors `.hostname` access).
    let san = sanitize_for_split(&s);
    let authority = san
        .split_once("://")
        .map(|(_, after)| &after[..authority_end(after)])
        .unwrap_or("");
    let (hostname, _) = hostinfo_parts(authority);
    if hostname_value(&hostname).is_none() {
        return Err(format!(
            "Cannot parse {} as a URL (no host)",
            py_repr(raw_str)
        ));
    }
    Ok(s)
}

/// `unquote(s.replace('+', ' '))` with errors='replace'.
fn unquote_plus(s: &str) -> String {
    let b = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' => match b.get(i + 1..i + 3) {
                Some(hex) if hex.iter().all(|c| c.is_ascii_hexdigit()) => {
                    out.push(u8::from_str_radix(std::str::from_utf8(hex).unwrap(), 16).unwrap());
                    i += 3;
                }
                _ => {
                    out.push(b'%');
                    i += 1;
                }
            },
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `parse_qsl(qs, keep_blank_values=True)` with the 3.10+ `&`-only separator.
fn parse_qsl_keep_blank(qs: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for seg in qs.split('&') {
        if seg.is_empty() {
            continue;
        }
        let (name, value) = match seg.split_once('=') {
            Some((n, v)) => (n, v),
            None => (seg, ""),
        };
        out.push((unquote_plus(name), unquote_plus(value)));
    }
    out
}

/// `quote_plus` with default safe set (alphanumerics + `_.-~`; space → `+`).
fn quote_plus(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for &b in s.as_bytes() {
        match b {
            b'a'..=b'z' | b'A'..=b'Z' | b'0'..=b'9' | b'_' | b'-' | b'.' | b'~' => {
                out.push(b as char)
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

pub fn canonical_url_impl(url: &str, query_mode: &str) -> Result<String, String> {
    let normalized = normalize_url_impl(Some(url), None)?;
    // The fresh `urlparse(normalized)` sanitizes first (TAB/CR/LF removed,
    // C0/space left-stripped), so all parts below come from the sanitized
    // form even though normalize() returned the raw string.
    let san = sanitize_for_split(&normalized);
    let no_frag = san.split('#').next().unwrap_or("");
    let (scheme_raw, rest) = no_frag
        .split_once(':')
        .ok_or_else(|| format!("Cannot parse {url} as a URL (no host)"))?;
    let scheme = scheme_raw.to_ascii_lowercase();
    let after = rest.strip_prefix("//").unwrap_or(rest);
    let cut = after.find(|c| c == '/' || c == '?').unwrap_or(after.len());
    let authority = &after[..cut];
    let remainder = &after[cut..];
    let (path, query) = match remainder.find('?') {
        Some(i) => (&remainder[..i], &remainder[i + 1..]),
        None => (remainder, ""),
    };
    // `;params` are dropped: canonical_url passes params="" to urlunparse.
    let path = path.split(';').next().unwrap_or("");
    // Validated by normalize_url; re-split for the parts (mirrors the
    // fresh `urlparse(normalized)` — including `.port`, which raises).
    let (hostname_raw, port_raw) = hostinfo_parts(authority);
    let host = hostname_value(&hostname_raw).unwrap_or_default();
    let host = if host.starts_with("www.") {
        host["www.".len()..].to_string()
    } else {
        host
    };
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
        // Key names lowercased, stable-sorted by (key, value).
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
    // Unknown query modes behave like "keep" (matches the Python fall-through).
    Ok(if query_str.is_empty() {
        format!("{scheme}://{netloc}{path_out}")
    } else {
        format!("{scheme}://{netloc}{path_out}?{query_str}")
    })
}

pub fn content_hash_impl(text: Option<&str>) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.unwrap_or("").as_bytes());
    format!("{:x}", hasher.finalize())
}

// ── PyO3 wrappers ────────────────────────────────────────────────

#[pyfunction]
#[pyo3(signature = (raw, base = None))]
pub fn normalize_url(raw: Option<&str>, base: Option<&str>) -> PyResult<String> {
    normalize_url_impl(raw, base).map_err(PyValueError::new_err)
}

#[pyfunction]
#[pyo3(signature = (url, query = "keep"))]
pub fn canonical_url(url: &str, query: &str) -> PyResult<String> {
    canonical_url_impl(url, query).map_err(PyValueError::new_err)
}

#[pyfunction]
#[pyo3(signature = (text = None))]
pub fn content_hash(text: Option<&str>) -> String {
    content_hash_impl(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_domains_gain_https() {
        assert_eq!(
            normalize_url_impl(Some("example.com/a"), None).unwrap(),
            "https://example.com/a"
        );
    }

    #[test]
    fn local_paths_rejected() {
        assert!(normalize_url_impl(Some("./report.pdf"), None).is_err());
        assert!(normalize_url_impl(Some("report.pdf"), None).is_err());
        assert!(normalize_url_impl(Some("justaword"), None).is_err());
        assert!(normalize_url_impl(Some(""), None).is_err());
        assert!(normalize_url_impl(None, None).is_err());
    }

    #[test]
    fn canonical_modes() {
        assert_eq!(
            canonical_url_impl("https://www.Example.COM/", "keep").unwrap(),
            "https://example.com/"
        );
        assert_eq!(
            canonical_url_impl("http://example.com:80/a/", "keep").unwrap(),
            "http://example.com/a"
        );
        assert_eq!(
            canonical_url_impl("https://example.com/a?page=1", "drop").unwrap(),
            "https://example.com/a"
        );
        assert_eq!(
            canonical_url_impl("https://example.com/a?b=2&a=1", "keep").unwrap(),
            "https://example.com/a?a=1&b=2"
        );
        assert_eq!(
            canonical_url_impl(
                "https://example.com/a?utm_source=x&id=7",
                "drop-tracking"
            )
            .unwrap(),
            "https://example.com/a?id=7"
        );
    }

    #[test]
    fn malformed_brackets_rejected_like_urlsplit() {
        assert!(normalize_url_impl(Some("http://[::1/a"), None).is_err());
        assert!(normalize_url_impl(Some("http://x]/a"), None).is_err());
        assert_eq!(
            normalize_url_impl(Some("http://[::1]:8080/p"), None).unwrap(),
            "http://[::1]:8080/p"
        );
    }

    #[test]
    fn hash_is_sha256() {
        assert_eq!(
            content_hash_impl(Some("")),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(content_hash_impl(None), content_hash_impl(Some("")));
    }
}
