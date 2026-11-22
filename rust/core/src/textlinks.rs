//! Text-level link detection (port of `gossamer.text_links.extract_links`).
//!
//! One compiled regex, one pass, bounded output. Validation of non-string input and non-positive
//! caps belongs to the caller; this module only sees valid Rust arguments.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;

// Sentence punctuation stripped after a match; kept identical to the vendored Python scanner in
// tests/test_rust_parity_urls.py so prose glue never leaks into a URL.
const TRAILING_PUNCT: &[char] = &['.', ',', ';', ':', '!', '?', '…', '"', '\''];

const URL_PATTERN: &str =
    r#"(?:https?://|www\.)[^\s<>"')\]\}，。、；！？（）【】「」『』]+"#;

static URL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(URL_PATTERN)
        .unwrap_or_else(|e| panic!("text-links URL regex {URL_PATTERN:?} must compile: {e}"))
});

/// Extracts deduplicated URLs from extracted document text.
///
/// # Examples
///
/// ```
/// use gossamer::textlinks::scan_text_links;
///
/// let links = scan_text_links("See https://example.com/a and www.example.com/b.", 10);
/// assert_eq!(links, ["https://example.com/a", "http://www.example.com/b"]);
/// ```
pub fn scan_text_links(text: &str, max_links: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for found in URL_RE.find_iter(text) {
        let url = found
            .as_str()
            .trim_end_matches(|c| TRAILING_PUNCT.contains(&c));
        if url.is_empty() {
            continue;
        }
        let promoted;
        let url = if url.starts_with("www.") {
            promoted = format!("http://{url}");
            promoted.as_str()
        } else {
            url
        };
        if !seen.insert(url.to_owned()) {
            continue;
        }
        out.push(url.to_owned());
        if out.len() >= max_links {
            break;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_http_and_promotes_www() {
        let links = scan_text_links("See https://example.com/report and www.example.com/docs.", 50);
        assert_eq!(
            links,
            vec![
                "https://example.com/report".to_string(),
                "http://www.example.com/docs".to_string()
            ]
        );
    }

    #[test]
    fn strips_trailing_punctuation_and_dedupes() {
        let links = scan_text_links("Go to https://example.com/a, then https://example.com/a.", 50);
        assert_eq!(links, vec!["https://example.com/a".to_string()]);
    }

    #[test]
    fn bare_domains_ignored_and_cap_respected() {
        assert!(scan_text_links("see example.com/docs for details", 50).is_empty());
        let links = scan_text_links("https://a.example/1 https://a.example/2", 1);
        assert_eq!(links, vec!["https://a.example/1".to_string()]);
    }
}
