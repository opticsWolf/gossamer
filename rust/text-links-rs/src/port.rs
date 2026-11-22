//! The Rust port of `gossamer/text_links.py`.
//!
//! The Python wrapper delegates to `_core.text_links_scan`; the scanner itself lives in the pure
//! `gossamer` core crate (ADR-012). This module keeps the generated port API and its Python-bound
//! validation policy: empty text or a non-positive cap yields no links.

#![forbid(unsafe_code)]

use port_error::PortError;

/// Extracts deduplicated URLs from extracted document text.
///
/// Empty text and non-positive caps return an empty vector. Positive caps are delegated to
/// [`gossamer::textlinks::scan_text_links`], which owns scanning, deduplication, `www.` promotion,
/// and trailing-punctuation trimming.
///
/// # Errors
///
/// Never fails. The `Result` mirrors the generated PyO3 signature; the core scanner is total for
/// valid Rust arguments.
pub fn extract_links(text: String, max_links: i64) -> Result<Vec<String>, PortError> {
    if text.is_empty() || max_links <= 0 {
        return Ok(Vec::new());
    }
    let cap = usize::try_from(max_links).unwrap_or(usize::MAX);
    Ok(gossamer::textlinks::scan_text_links(&text, cap))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_http_and_promotes_www() {
        assert_eq!(
            extract_links(
                "See https://example.com/report and www.example.com/docs.".to_owned(),
                50
            )
            .unwrap(),
            vec![
                "https://example.com/report".to_owned(),
                "http://www.example.com/docs".to_owned(),
            ]
        );
    }

    #[test]
    fn dedupes_and_respects_cap() {
        assert_eq!(
            extract_links(
                "Go to https://example.com/a, then https://example.com/a.".to_owned(),
                2
            )
            .unwrap(),
            vec!["https://example.com/a".to_owned()]
        );
    }

    #[test]
    fn bare_domains_are_not_links() {
        assert!(
            extract_links("see example.com/docs for details".to_owned(), 50)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn zero_cap_and_empty_text_return_empty() {
        assert!(extract_links(String::new(), 50).unwrap().is_empty());
        assert!(
            extract_links("https://example.com".to_owned(), 0)
                .unwrap()
                .is_empty()
        );
        assert!(
            extract_links("https://example.com".to_owned(), -1)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn stops_at_cjk_punctuation() {
        assert_eq!(
            extract_links(
                "CJK：https://example.com/中，next www.example.com/文。".to_owned(),
                50
            )
            .unwrap(),
            vec![
                "https://example.com/中".to_owned(),
                "http://www.example.com/文".to_owned(),
            ]
        );
    }
}
