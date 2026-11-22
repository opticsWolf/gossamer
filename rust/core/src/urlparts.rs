//! Minimal `urllib.parse` primitives needed by the SSRF guard.
//!
//! This is a focused copy of the pure helpers in the legacy root `src/urls.rs`. They are kept here
//! so the pure core has no dependency on the PyO3-facing crate.

use unicode_normalization::UnicodeNormalization;

use crate::compat::py_repr;

/// Python `urlparse` scheme detection: scheme chars before the first `:`, with a leading ASCII
/// letter required from 3.11 on.
pub(crate) fn has_scheme(s: &str, runtime_minor: Option<u32>) -> bool {
    match s.find(':') {
        Some(i) if i > 0 => {
            let head = &s[..i];
            if runtime_minor.is_none_or(|m| m >= 11) {
                let mut chars = head.chars();
                match chars.next() {
                    Some(c) if c.is_ascii_alphabetic() => {}
                    _ => return false,
                }
            }
            head.chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        }
        _ => false,
    }
}

/// What `urlsplit` sees: leading C0 controls/spaces stripped, TAB/CR/LF removed everywhere.
pub(crate) fn sanitize_for_split(s: &str) -> String {
    let t = s.trim_start_matches(|c: char| c == ' ' || (c as u32) <= 0x1f);
    t.replace(['\t', '\r', '\n'], "")
}

/// `urlsplit` netloc validation (bracket matching, bracketed-host rules, NFKC).
///
/// # Errors
///
/// Returns the exact CPython-style message where `urlparse` would raise.
pub(crate) fn validate_netloc(netloc: &str) -> Result<(), String> {
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
    let hostname_and_port = netloc.rsplit('@').next().unwrap_or("");
    let (before_bracket, have_open, bracketed) = match hostname_and_port.find('[') {
        Some(i) => (&hostname_and_port[..i], true, &hostname_and_port[i + 1..]),
        None => ("", false, hostname_and_port),
    };
    if have_open {
        if !before_bracket.is_empty() {
            return Err("Invalid IPv6 URL".to_string());
        }
        let (hostname, port) = match bracketed.find(']') {
            Some(i) => (&bracketed[..i], &bracketed[i + 1..]),
            None => (bracketed, ""),
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
