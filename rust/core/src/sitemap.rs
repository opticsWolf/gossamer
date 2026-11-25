//! Deterministic sitemap XML parsing for resource discovery.

use serde_json::Value;

use crate::urlparts::join_url;

/// Parse a sitemap payload into its kind and absolutized locations.
pub fn parse_sitemap(xml: &str, base: &str, max_urls: usize) -> Option<Value> {
    let document = roxmltree::Document::parse(xml.trim_start()).ok()?;
    let root = document.root_element();
    let kind_tag = root.tag_name().name();
    if !matches!(kind_tag, "urlset" | "sitemapindex") {
        return None;
    }
    let mut locs: Vec<String> = root
        .descendants()
        .filter(|node| node.is_element() && node.tag_name().name() == "loc")
        .filter_map(|node| node.text())
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(|text| join_url(base, text))
        .collect();
    if locs.is_empty() {
        return None;
    }
    let truncated = locs.len() > max_urls;
    locs.truncate(max_urls);
    Some(serde_json::json!({
        "kind": if kind_tag == "sitemapindex" { "index" } else { "urlset" },
        "locs": locs,
        "truncated": truncated,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parse_urlset() {
        let xml = r#"<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/a</loc></url></urlset>"#;
        let parsed = parse_sitemap(xml, "https://x/sitemap.xml", 10).unwrap();
        assert_eq!(parsed["kind"], "urlset");
        assert_eq!(parsed["locs"][0], "https://x/a");
    }
}
