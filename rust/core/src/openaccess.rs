//! DOI normalization kernels shared by open-access lookup and citation paths.

use std::sync::LazyLock;

use regex::Regex;

use crate::compat::py_repr;

/// Regex source mirroring the Python DOI shape check.
const DOI_PATTERN: &str = r"(?i)^10\.\d{4,9}/\S+$";

/// Compiled once for speed; this immutable cache carries no process state.
static DOI_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(DOI_PATTERN).unwrap_or_else(|e| panic!("invalid DOI regex {DOI_PATTERN:?}: {e}"))
});

/// Decode `%XX` escapes without treating `+` as a space.
pub fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = &value[index + 1..index + 3];
            if let Ok(byte) = u8::from_str_radix(hex, 16) {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Normalize a bare DOI, `doi:` value or DOI-resolver URL.
///
/// # Errors
///
/// Returns a `ValueError`-shaped message when a resolver URL carries query or
/// fragment components, or when the decoded DOI does not match the expected
/// `10.NNNN/suffix` shape.
pub fn normalize_doi(value: &str) -> Result<String, String> {
    let trimmed = value.trim();
    let mut doi = if trimmed.len() >= 4 && trimmed[..4].eq_ignore_ascii_case("doi:") {
        trimmed[4..].trim().to_string()
    } else {
        let lower = trimmed.to_ascii_lowercase();
        let scheme_end = lower.find("://");
        let is_http = scheme_end.is_some_and(|at| {
            let scheme = &lower[..at];
            scheme == "http" || scheme == "https"
        });
        if is_http {
            let rest = &trimmed[scheme_end.unwrap_or(0) + 3..];
            let authority_end = rest
                .find(['/', '?', '#'])
                .unwrap_or(rest.len());
            let host = rest[..authority_end]
                .rsplit('@')
                .next()
                .unwrap_or("")
                .split(':')
                .next()
                .unwrap_or("")
                .to_ascii_lowercase();
            if matches!(host.as_str(), "doi.org" | "www.doi.org" | "dx.doi.org") {
                let tail = &rest[authority_end..];
                if tail.contains('?') || tail.contains('#') {
                    return Err("ValueError: DOI resolver URLs must not include a query or fragment".to_string());
                }
                percent_decode(tail.trim_start_matches('/'))
            } else {
                trimmed.to_string()
            }
        } else {
            trimmed.to_string()
        }
    };
    if !DOI_RE.is_match(&doi) {
        return Err(format!(
            "ValueError: expected a DOI such as {}",
            py_repr("10.1234/example")
        ));
    }
    doi.make_ascii_lowercase();
    Ok(doi)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn doi_forms() {
        assert_eq!(normalize_doi(" DOI:10.1234/Example ").ok().as_deref(), Some("10.1234/example"));
        assert_eq!(normalize_doi("https://doi.org/10.1234/Ab%2FC").ok().as_deref(), Some("10.1234/ab/c"));
        assert!(normalize_doi("https://doi.org/10.1234/x?q=1").is_err());
    }
}
