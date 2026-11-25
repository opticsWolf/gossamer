#![forbid(unsafe_code)]
use gossamer::discovery::find_feed_links;
use gossamer::sitemap::parse_sitemap;
use serde_json::Value;
pub fn service_find_feed_links(html: String, base_url: String) -> Result<Vec<Value>, String> {
    Ok(find_feed_links(&html, &base_url))
}

pub fn service_parse_sitemap(
    xml_text: String,
    base_url: String,
    max_urls: i64,
) -> Result<Option<Value>, String> {
    Ok(parse_sitemap(
        &xml_text,
        &base_url,
        max_urls.max(0) as usize,
    ))
}
