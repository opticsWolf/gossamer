//! SSRF guard: fail-fast URL/IP validation for the pure core.
//!
//! Policy replicates CPython `ipaddress` predicate semantics exactly (probed, not assumed): the
//! blocked decision is `private-table − exceptions + reserved-table`, with IPv4-mapped IPv6
//! addresses unwrapped to their underlying IPv4. This is the same policy previously implemented in
//! the legacy PyO3 crate; see `docs/RUST_PORT_SSRF_TABLE.md` for the probed matrix.
//!
//! Pinned by `tests/test_rust_parity_ssrf.py` (IP corpus incl. every IANA boundary, message equality,
//! DNS mocked at the resolver seam).

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, ToSocketAddrs};
use std::sync::OnceLock;

use crate::compat::py_repr;
use crate::urlparts::{has_scheme, sanitize_for_split, validate_netloc};

/// Canonical SSRF validation error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct SsrfError {
    message: String,
    backtrace: Backtrace,
}

impl SsrfError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the stable Python-compatible error message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace for diagnostics.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for SsrfError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for SsrfError {}

// --- IANA special-registry tables (probed from CPython 3.13) --------

const V4_PRIVATE: &[(&str, u8)] = &[
    ("0.0.0.0", 8),
    ("10.0.0.0", 8),
    ("127.0.0.0", 8),
    ("169.254.0.0", 16),
    ("172.16.0.0", 12),
    ("192.0.0.0", 24),
    ("192.0.0.170", 31),
    ("192.0.2.0", 24),
    ("192.168.0.0", 16),
    ("198.18.0.0", 15),
    ("198.51.100.0", 24),
    ("203.0.113.0", 24),
    ("240.0.0.0", 4),
    ("255.255.255.255", 32),
];
const V4_PRIVATE_EXCEPTIONS: &[(&str, u8)] = &[("192.0.0.9", 32), ("192.0.0.10", 32)];

const V6_PRIVATE: &[(&str, u8)] = &[
    ("::1", 128),
    ("::", 128),
    ("::ffff:0:0", 96),
    ("64:ff9b:1::", 48),
    ("100::", 64),
    ("2001::", 23),
    ("2001:db8::", 32),
    ("2002::", 16),
    ("3fff::", 20),
    ("fc00::", 7),
    ("fe80::", 10),
];
const V6_PRIVATE_EXCEPTIONS: &[(&str, u8)] = &[
    ("2001:1::1", 128),
    ("2001:1::2", 128),
    ("2001:3::", 32),
    ("2001:4:112::", 48),
    ("2001:20::", 28),
    ("2001:30::", 28),
];
const V6_RESERVED: &[(&str, u8)] = &[
    ("::", 8),
    ("100::", 8),
    ("200::", 7),
    ("400::", 6),
    ("800::", 5),
    ("1000::", 4),
    ("4000::", 3),
    ("6000::", 3),
    ("8000::", 3),
    ("a000::", 3),
    ("c000::", 3),
    ("e000::", 4),
    ("f000::", 5),
    ("f800::", 6),
    ("fe00::", 9),
];

const INTERNAL_SUFFIXES: &[&str] = &[".local", ".internal", ".localhost"];

fn parse_v4(s: &str) -> (u32, u32) {
    let (addr, len) = s
        .split_once('/')
        .unwrap_or_else(|| panic!("const CIDR {s:?} has no '/' separator"));
    let ip: Ipv4Addr = addr
        .parse()
        .unwrap_or_else(|e| panic!("const CIDR {s:?} has invalid IPv4 {addr:?}: {e}"));
    let len: u32 = len
        .parse()
        .unwrap_or_else(|e| panic!("const CIDR {s:?} has invalid prefix {len:?}: {e}"));
    let mask = if len == 0 { 0 } else { u32::MAX << (32 - len) };
    (u32::from(ip), mask)
}

fn parse_v6(s: &str) -> (u128, u128) {
    let (addr, len) = s
        .split_once('/')
        .unwrap_or_else(|| panic!("const CIDR {s:?} has no '/' separator"));
    let ip: Ipv6Addr = addr
        .parse()
        .unwrap_or_else(|e| panic!("const CIDR {s:?} has invalid IPv6 {addr:?}: {e}"));
    let len: u32 = len
        .parse()
        .unwrap_or_else(|e| panic!("const CIDR {s:?} has invalid prefix {len:?}: {e}"));
    let mask = if len == 0 {
        0
    } else {
        u128::MAX << (128 - len)
    };
    (u128::from(ip), mask)
}

type V4Tables = (Vec<(u32, u32)>, Vec<(u32, u32)>);

fn v4_tables() -> &'static V4Tables {
    static CELL: OnceLock<V4Tables> = OnceLock::new();
    CELL.get_or_init(|| {
        (
            V4_PRIVATE
                .iter()
                .map(|c| parse_v4(&format!("{}/{}", c.0, c.1)))
                .collect(),
            V4_PRIVATE_EXCEPTIONS
                .iter()
                .map(|c| parse_v4(&format!("{}/{}", c.0, c.1)))
                .collect(),
        )
    })
}

type V6Tables = (Vec<(u128, u128)>, Vec<(u128, u128)>, Vec<(u128, u128)>);

fn v6_tables() -> &'static V6Tables {
    static CELL: OnceLock<V6Tables> = OnceLock::new();
    CELL.get_or_init(|| {
        (
            V6_PRIVATE
                .iter()
                .map(|c| parse_v6(&format!("{}/{}", c.0, c.1)))
                .collect(),
            V6_PRIVATE_EXCEPTIONS
                .iter()
                .map(|c| parse_v6(&format!("{}/{}", c.0, c.1)))
                .collect(),
            V6_RESERVED
                .iter()
                .map(|c| parse_v6(&format!("{}/{}", c.0, c.1)))
                .collect(),
        )
    })
}

fn in_table(addr: u128, table: &[(u128, u128)]) -> bool {
    table.iter().any(|(net, mask)| addr & mask == *net)
}

fn v4_blocked(ip: u32) -> bool {
    let (priv_tbl, exc_tbl) = v4_tables();
    priv_tbl.iter().any(|(net, mask)| ip & mask == *net)
        && !exc_tbl.iter().any(|(net, mask)| ip & mask == *net)
}

fn v6_blocked(ip: u128) -> bool {
    if (ip >> 32) == 0xFFFF {
        return v4_blocked((ip & 0xFFFF_FFFF) as u32);
    }
    let (priv_tbl, exc_tbl, res_tbl) = v6_tables();
    (in_table(ip, priv_tbl) && !in_table(ip, exc_tbl)) || in_table(ip, res_tbl)
}

fn render_ip(addr: &IpAddr) -> String {
    match addr {
        IpAddr::V4(v) => v.to_string(),
        IpAddr::V6(v) => {
            let n = u128::from(*v);
            if (n >> 32) == 0xFFFF {
                let v4 = Ipv4Addr::from((n & 0xFFFF_FFFF) as u32);
                return format!("::ffff:{v4}");
            }
            v.to_string()
        }
    }
}

fn split_host_port(hostport: &str) -> (&str, Option<&str>) {
    if let Some(stripped) = hostport.strip_prefix('[') {
        return match stripped.find(']') {
            Some(i) => {
                let after = &stripped[i + 1..];
                (&stripped[..i], after.strip_prefix(':'))
            }
            None => (hostport, None),
        };
    }
    match hostport.rsplit_once(':') {
        Some((h, p)) => (h, Some(p)),
        None => (hostport, None),
    }
}

/// DNS resolver seam. Tests inject a deterministic implementation so literals never hit DNS.
pub type Resolver = dyn Fn(&str, u16) -> Result<Vec<IpAddr>, String>;

/// Validates a URL against the SSRF policy with an injected resolver.
///
/// `runtime_minor` selects CPython error spellings; `None` uses the newest supported spelling.
/// It is passed explicitly instead of stored in a static (`M-AVOID-STATICS`).
///
/// # Errors
///
/// Returns [`SsrfError`] when the scheme, host, port, or resolved address is rejected. The
/// `allow_private` flag is resolved by the caller (env layer stays outside core).
pub fn check_url_impl(
    url: &str,
    allow_private: bool,
    resolve: &Resolver,
    runtime_minor: Option<u32>,
) -> Result<(), SsrfError> {
    if allow_private {
        return Ok(());
    }
    let san = sanitize_for_split(url);
    let (scheme, rest) = match san.find(':') {
        Some(i) if has_scheme(&san[..i + 1], runtime_minor) => {
            (san[..i].to_ascii_lowercase(), san[i + 1..].to_string())
        }
        _ => (String::new(), san.clone()),
    };
    if let Some(after_slashes) = rest.strip_prefix("//") {
        let cut = after_slashes
            .find(['/', '?', '#'])
            .unwrap_or(after_slashes.len());
        validate_netloc(&after_slashes[..cut]).map_err(SsrfError::new)?;
    }
    if scheme != "http" && scheme != "https" {
        return Err(SsrfError::new(format!(
            "URL scheme {} is not allowed for fetching",
            py_repr(&scheme)
        )));
    }
    let after = match rest.strip_prefix("//") {
        Some(a) => a,
        None => return Err(SsrfError::new(format!("URL has no host: {url}"))),
    };
    let auth_end = after.find(['/', '?', '#']).unwrap_or(after.len());
    let authority = &after[..auth_end];
    let hostport = authority.rsplit('@').next().unwrap_or("");
    let (host_raw, port_raw) = split_host_port(hostport);
    if host_raw.is_empty() {
        return Err(SsrfError::new(format!("URL has no host: {url}")));
    }
    let (addr_part, zone) = match host_raw.find('%') {
        Some(i) => (&host_raw[..i], Some(&host_raw[i + 1..])),
        None => (host_raw, None),
    };
    let host = match zone {
        Some(z) => format!("{}%{z}", addr_part.to_lowercase()),
        None => host_raw.to_lowercase(),
    };
    let host_l = host.to_lowercase();
    let host_l = host_l.trim_end_matches('.');
    if host_l == "localhost" || INTERNAL_SUFFIXES.iter().any(|s| host_l.ends_with(s)) {
        return Err(SsrfError::new(format!(
            "Host {} is an internal name",
            py_repr(&host)
        )));
    }
    let default_port = if scheme == "https" { 443 } else { 80 };
    let port: u16 = match port_raw {
        None | Some("") => default_port,
        Some(p) => match p.parse::<u32>() {
            Ok(n) if n <= 65535 => {
                if n == 0 {
                    default_port
                } else {
                    n as u16
                }
            }
            _ => return Err(SsrfError::new(format!("URL has an invalid port: {url}"))),
        },
    };
    if let Some(z) = zone {
        if !z.is_empty()
            && !z.contains('%')
            && let Ok(v6) = addr_part.parse::<Ipv6Addr>()
        {
            return check_ip_zone(&host, &IpAddr::V6(v6), z);
        }
    } else if let Ok(ip) = host.parse::<IpAddr>() {
        return check_ip(&host, &ip);
    }
    let addrs = resolve(&host, port).map_err(|e| {
        SsrfError::new(format!("DNS resolution failed for {}: {e}", py_repr(&host)))
    })?;
    if addrs.is_empty() {
        return Err(SsrfError::new(format!(
            "DNS returned no addresses for {}",
            py_repr(&host)
        )));
    }
    for ip in addrs {
        check_ip(&host, &ip)?;
    }
    Ok(())
}

fn check_ip(host: &str, ip: &IpAddr) -> Result<(), SsrfError> {
    check_ip_zone(host, ip, "")
}

fn check_ip_zone(host: &str, ip: &IpAddr, zone: &str) -> Result<(), SsrfError> {
    let blocked = match ip {
        IpAddr::V4(v) => v4_blocked(u32::from(*v)),
        IpAddr::V6(v) => v6_blocked(u128::from(*v)),
    };
    if blocked {
        let rendered = if zone.is_empty() {
            render_ip(ip)
        } else {
            format!("{}%{}", render_ip(ip).to_lowercase(), zone.to_lowercase())
        };
        return Err(SsrfError::new(format!(
            "Host {} ({}) is not a public address",
            py_repr(host),
            rendered
        )));
    }
    Ok(())
}

/// System DNS resolver used by [`check_url`].
///
/// # Errors
///
/// Returns the resolver's error string when resolution fails.
pub fn system_resolve(host: &str, port: u16) -> Result<Vec<IpAddr>, String> {
    ToSocketAddrs::to_socket_addrs(&(host, port))
        .map(|it| it.map(|sa| sa.ip()).collect())
        .map_err(|e| e.to_string())
}

/// Validates a URL using system DNS and the requested CPython spelling version.
///
/// `runtime_minor` of `None` uses the newest supported spelling.
///
/// # Examples
///
/// ```
/// use gossamer::ssrf::check_url;
///
/// let err = check_url("http://127.0.0.1/", false, None).unwrap_err();
/// assert!(err.message().contains("not a public address"));
/// ```
///
/// # Errors
///
/// Returns the same [`SsrfError`] messages as [`check_url_impl`].
pub fn check_url(
    url: &str,
    allow_private: bool,
    runtime_minor: Option<u32>,
) -> Result<(), SsrfError> {
    check_url_impl(url, allow_private, &system_resolve, runtime_minor)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn no_dns(_host: &str, _port: u16) -> Result<Vec<IpAddr>, String> {
        panic!("DNS must not be consulted for literals")
    }

    fn blocked(url: &str) -> Option<String> {
        check_url_impl(url, false, &no_dns, None)
            .err()
            .map(|e| e.message().to_owned())
    }

    #[test]
    fn metadata_and_loopback_blocked() {
        assert!(
            blocked("http://169.254.169.254/latest")
                .unwrap()
                .contains("not a public address")
        );
        assert!(blocked("http://127.0.0.1/admin").is_some());
        assert!(blocked("http://[::1]/").is_some());
        assert!(blocked("http://[fd00:ec2::254]/").is_some());
        assert!(blocked("http://10.1.2.3/").is_some());
    }

    #[test]
    fn public_passes_and_exceptions_hold() {
        assert!(check_url_impl("https://8.8.8.8/", false, &no_dns, None).is_ok());
        assert!(check_url_impl("http://100.64.0.1/", false, &no_dns, None).is_ok());
        assert!(check_url_impl("http://[ff02::1]/", false, &no_dns, None).is_ok());
        assert!(check_url_impl("http://[2001:1::1]/", false, &no_dns, None).is_ok());
        assert!(check_url_impl("http://192.0.0.9/", false, &no_dns, None).is_ok());
    }

    #[test]
    fn scheme_host_port_and_names() {
        assert!(
            blocked("ftp://example.com/")
                .unwrap()
                .contains("not allowed for fetching")
        );
        assert!(blocked("http:///").unwrap().contains("no host"));
        assert!(
            blocked("http://example.com:abc/")
                .unwrap()
                .contains("invalid port")
        );
        assert!(
            blocked("http://localhost:8000/")
                .unwrap()
                .contains("internal name")
        );
        assert!(
            blocked("http://svc.internal/")
                .unwrap()
                .contains("internal name")
        );
    }

    #[test]
    fn allow_private_bypasses_everything() {
        assert!(check_url_impl("http://127.0.0.1/", true, &no_dns, None).is_ok());
    }
}
