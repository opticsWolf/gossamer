//! Pure search-provider naming and error-shape kernels.

use scraper::{Html, Selector};
use serde_json::{Value, json};
use url::form_urlencoded;

use crate::compat::py_repr;

/// Case-insensitive provider alias resolution, matching `PROVIDER_NAME_MAP`.
pub fn resolve_provider_name(name: &str) -> Option<&'static str> {
    match name.trim().to_ascii_lowercase().as_str() {
        "duckduckgo" | "ddg" => Some("duckduckgo"),
        "google" => Some("google"),
        "bing" => Some("bing"),
        "exa" => Some("exa"),
        "browser" | "browser_oxide" | "browseroxide" => Some("browser"),
        _ => None,
    }
}

/// Returns the JSON error payload for an unrecognized provider, if any.
pub fn unknown_provider_error(provider: &str, available: &[&str]) -> Option<Value> {
    if resolve_provider_name(provider).is_some() {
        return None;
    }
    Some(json!({
        "error": format!("Unknown search provider: {}", py_repr(provider)),
        "available_providers": available,
    }))
}

/// Build the DuckDuckGo HTML search URL for a query.
pub fn duckduckgo_search_url(query: &str) -> String {
    let encoded: String = form_urlencoded::byte_serialize(query.as_bytes()).collect();
    format!("https://html.duckduckgo.com/html/?q={encoded}")
}

fn unwrap_ddg_href(href: &str) -> String {
    if let Some(rest) = href.strip_prefix("//duckduckgo.com/l/?") {
        for pair in rest.split('&') {
            if pair.starts_with("uddg=")
                && let Some((_, decoded)) = form_urlencoded::parse(pair.as_bytes()).next()
            {
                return decoded.to_string();
            }
        }
    }
    if href.starts_with("//") {
        format!("https:{href}")
    } else {
        href.to_string()
    }
}

/// Parse DuckDuckGo HTML results into the shared result-dict shape.
pub fn duckduckgo_results(html: &str, max_results: usize) -> Vec<Value> {
    let document = Html::parse_document(html);
    let result = Selector::parse("div.result").expect("result selector must compile");
    let link = Selector::parse("a.result__a").expect("result link selector must compile");
    let snippet = Selector::parse(".result__snippet").expect("snippet selector must compile");
    let mut out = Vec::new();
    let mut seen = Vec::new();
    for container in document.select(&result) {
        if out.len() >= max_results {
            break;
        }
        let Some(anchor) = container.select(&link).next() else {
            continue;
        };
        let Some(href) = anchor.value().attr("href") else {
            continue;
        };
        let url = unwrap_ddg_href(href);
        if url.is_empty() || seen.contains(&url) {
            continue;
        }
        let title = anchor
            .text()
            .collect::<Vec<_>>()
            .join("")
            .trim()
            .to_string();
        let body = container
            .select(&snippet)
            .next()
            .map(|element| element.text().collect::<Vec<_>>().join(""))
            .unwrap_or_default()
            .trim()
            .to_string();
        seen.push(url.clone());
        out.push(json!({"title": title, "url": url, "snippet": body}));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::duckduckgo_results;

    const STANDARD: &str = include_str!("../../fixtures/ddg-standard.html");
    const EMPTY: &str = include_str!("../../fixtures/ddg-empty.html");

    #[test]
    fn parses_captured_duckduckgo_results() {
        let results = duckduckgo_results(STANDARD, 5);
        assert_eq!(results.len(), 5);
        assert_eq!(results[0]["url"], "https://rust-lang.org/");
        assert_eq!(results[0]["title"], "Rust Programming Language");
        assert!(!results[0]["snippet"].as_str().unwrap().is_empty());
        assert!(results.iter().all(|item| {
            item["title"].as_str().is_some_and(|text| !text.is_empty())
                && item["url"]
                    .as_str()
                    .is_some_and(|url| url.starts_with("http"))
                && item["snippet"]
                    .as_str()
                    .is_some_and(|text| !text.is_empty())
        }));
    }

    #[test]
    fn decodes_duckduckgo_redirect_links() {
        let results = duckduckgo_results(STANDARD, 5);
        assert!(
            results
                .iter()
                .all(|item| !item["url"].as_str().unwrap().contains("duckduckgo.com/l/"))
        );
    }

    #[test]
    fn empty_duckduckgo_page_yields_no_results() {
        assert!(duckduckgo_results(EMPTY, 5).is_empty());
    }

    #[test]
    fn duckduckgo_result_cap_is_enforced() {
        assert_eq!(duckduckgo_results(STANDARD, 2).len(), 2);
        assert_eq!(duckduckgo_results(STANDARD, 0).len(), 0);
    }
}
