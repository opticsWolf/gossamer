#![forbid(unsafe_code)]
use gossamer::discovery::find_feed_links;
use serde_json::Value;
pub fn service_find_feed_links(html: String, base_url: String) -> Result<Vec<Value>, String> {
    Ok(find_feed_links(&html, &base_url))
}
