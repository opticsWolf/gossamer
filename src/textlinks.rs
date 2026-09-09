//! Text-level link detection (port of `gossamer.text_links.extract_links`).
//!
//! One compiled regex, one pass, bounded output. The scan itself never
//! fails; input validation (non-string, empty, non-positive cap) stays on
//! the Python side of the boundary.

use pyo3::prelude::*;
use regex::Regex;
use std::collections::HashSet;
use std::sync::OnceLock;

static URL_RE: OnceLock<Regex> = OnceLock::new();

fn url_re() -> &'static Regex {
    URL_RE.get_or_init(|| {
        Regex::new(r#"(?:https?://|www\.)[^\s<>"'\)\]\}，。、；！？（）【】「」『』]+"#)
            .expect("text-links URL regex must compile")
    })
}

const TRAILING_PUNCT: &[char] = &['.', ',', ';', ':', '!', '?', '…', '"', '\''];

pub fn scan_text_links(text: &str, max_links: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for m in url_re().find_iter(text) {
        let mut url = m.as_str().trim_end_matches(TRAILING_PUNCT).to_string();
        if url.is_empty() {
            continue;
        }
        if url.starts_with("www.") {
            url = format!("http://{url}");
        }
        // Dedupe by value while keeping first-occurrence order.
        if !seen.insert(url.clone()) {
            continue;
        }
        out.push(url);
        if out.len() >= max_links {
            break;
        }
    }
    out
}

#[pyfunction]
#[pyo3(signature = (text, max_links = 50))]
pub fn text_links_scan(text: &str, max_links: usize) -> Vec<String> {
    scan_text_links(text, max_links)
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
