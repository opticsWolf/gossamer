//! Deterministic resource-discovery kernels.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

const FEED_LINK_PATTERN: &str = r#"(?is)<link\b[^>]*rel\s*=\s*["']?alternate["']?[^>]*>"#;
static FEED_LINK_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(FEED_LINK_PATTERN)
        .unwrap_or_else(|error| panic!("invalid feed link regex {FEED_LINK_PATTERN:?}: {error}"))
});
const LINK_ATTR_PATTERN: &str = r##"(?i)(?P<attr>type|href)\s*=\s*["'](?P<value>[^"']*)["']"##;
static LINK_ATTR_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(LINK_ATTR_PATTERN).unwrap_or_else(|error| {
        panic!("invalid link attribute regex {LINK_ATTR_PATTERN:?}: {error}")
    })
});
const FEED_TYPE_PREFIXES: [&str; 3] = [
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
];

/// Extract feed alternates from arbitrary HTML.
pub fn find_feed_links(html: &str, base_url: &str) -> Vec<Value> {
    let mut out = Vec::new();
    for tag in FEED_LINK_RE.find_iter(html) {
        let mut attrs = Map::new();
        for captures in LINK_ATTR_RE.captures_iter(tag.as_str()) {
            let name = captures.name("attr").map(|m| m.as_str().to_lowercase());
            let value = captures.name("value").map(|m| m.as_str().to_string());
            if let (Some(name), Some(value)) = (name, value) {
                attrs.insert(name, Value::String(value));
            }
        }
        let link_type = attrs
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("")
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .to_lowercase();
        if !FEED_TYPE_PREFIXES
            .iter()
            .any(|prefix| link_type.starts_with(prefix))
        {
            continue;
        }
        let href = attrs
            .get("href")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if href.is_empty() {
            continue;
        }
        out.push(json!({
            "url": crate::urlparts::join_url(base_url, href),
            "type": link_type,
        }));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_feed_alternates() {
        let html = r#"<link rel="alternate" type="application/rss+xml" href="/feed.xml"><link rel="alternate" type="text/html" href="/x">"#;
        let out = find_feed_links(html, "https://x.test/page");
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["url"], json!("https://x.test/feed.xml"));
    }
}

/// Fetch a text payload for resource discovery, returning the final URL.
pub fn fetch_text(url: &str, timeout_seconds: f64) -> Result<(String, String), String> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| format!("RuntimeError: {error}"))?;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::limited(10))
        .timeout(std::time::Duration::from_secs_f64(timeout_seconds.max(0.0)))
        .build()
        .map_err(|error| format!("RuntimeError: {error}"))?;
    runtime.block_on(async {
        let response = client
            .get(url)
            .send()
            .await
            .map_err(|error| format!("HTTPError: {error}"))?;
        let final_url = response.url().to_string();
        let body = response
            .text()
            .await
            .map_err(|error| format!("HTTPError: {error}"))?;
        Ok((body, final_url))
    })
}
