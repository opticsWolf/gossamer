//! Minimal `urllib.parse` primitives needed by the SSRF guard.
//!
//! This is a focused copy of the pure helpers in the legacy root `src/urls.rs`. They are kept here
//! so the pure core has no dependency on the PyO3-facing crate.

use unicode_normalization::UnicodeNormalization;

use crate::compat::py_repr;

/// Python `urlparse` scheme detection: scheme chars before the first `:`, with a leading ASCII
/// letter required from 3.11 on.
pub fn has_scheme(s: &str, runtime_minor: Option<u32>) -> bool {
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
pub fn sanitize_for_split(s: &str) -> String {
    let t = s.trim_start_matches(|c: char| c == ' ' || (c as u32) <= 0x1f);
    t.replace(['\t', '\r', '\n'], "")
}

/// `urlsplit` netloc validation (bracket matching, bracketed-host rules, NFKC).
///
/// # Errors
///
/// Returns the exact CPython-style message where `urlparse` would raise.
pub fn validate_netloc(netloc: &str) -> Result<(), String> {
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

/// Splits a sanitized string the way `urlsplit` does, validating the netloc.
///
/// # Errors
///
/// Returns CPython-style netloc validation messages.
pub fn split_validated(san: &str, runtime_minor: Option<u32>) -> Result<(String, Option<String>), String> {
    let (scheme, rest) = match san.find(':') {
        Some(i) if has_scheme(&san[..i + 1], runtime_minor) => {
            (san[..i].to_ascii_lowercase(), san[i + 1..].to_string())
        }
        _ => (String::new(), san.to_string()),
    };
    if let Some(after) = rest.strip_prefix("//") {
        let cut = after.find(['/', '?', '#']).unwrap_or(after.len());
        let netloc = after[..cut].to_string();
        validate_netloc(&netloc)?;
        return Ok((scheme, Some(netloc)));
    }
    Ok((scheme, None))
}

/// RFC 3986 reference resolution, matching `urllib.parse.urljoin`.
pub fn join_url(base: &str, reference: &str) -> String {
    match url::Url::parse(base).and_then(|b| b.join(reference)) {
        Ok(u) => u.to_string(),
        Err(_) => reference.to_string(),
    }
}

/// Pure `_hostinfo` mirror: userinfo dropped, brackets stripped, raw port retained.
pub fn hostinfo_parts(authority: &str) -> (String, Option<String>) {
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
                if port.is_empty() { None } else { Some(port.to_string()) },
            )
        }
        None => (hostinfo.to_string(), None),
    }
}

/// `.hostname`: empty → None; zone IDs keep their case.
pub fn hostname_value(hostname: &str) -> Option<String> {
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

/// `.port`: empty/absent → None; ASCII digits → value; else CPython message.
///
/// # Errors
///
/// Returns CPython's exact port error messages.
pub fn port_value(port_raw: Option<&str>) -> Result<Option<u16>, String> {
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

/// Index of the first `/`, `?`, or `#` in an authority tail.
pub fn authority_end(s: &str) -> usize {
    s.find(['/', '?', '#']).unwrap_or(s.len())
}

/// `unquote(s.replace('+', ' '))` with errors='replace'.
pub fn unquote_plus(s: &str) -> String {
    let b = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(b.len());
    let mut i = 0usize;
    while i < b.len() {
        match b[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' => match b.get(i + 1..i + 3) {
                Some(hex) if hex.iter().all(|c| c.is_ascii_hexdigit()) => {
                    let text = std::str::from_utf8(hex).expect("ASCII hex must be UTF-8");
                    out.push(u8::from_str_radix(text, 16).expect("two ASCII hex digits"));
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

/// `parse_qsl(qs, keep_blank_values=True)` with the `&`-only separator.
pub fn parse_qsl_keep_blank(qs: &str) -> Vec<(String, String)> {
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

/// `quote_plus` with the default safe set.
pub fn quote_plus(s: &str) -> String {
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
