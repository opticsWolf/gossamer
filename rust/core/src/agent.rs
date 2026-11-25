//! Deterministic planning helpers for `gossamer.agent_tools`.
//!
//! Network I/O, SSRF enforcement and locking stay on the binding side.

use serde_json::Value;
use url::Url;

/// Build the recoverable error record for a URL rejected before network I/O.
pub fn url_error(raw: &str, reason: &str, error_type: &str) -> Value {
    serde_json::json!({
        "url": raw,
        "error": format!("URL rejected: {reason}"),
        "error_type": error_type,
    })
}

/// Host with a leading `www.` stripped, for same-host checks.
pub fn crawl_host_key(url: &str) -> String {
    let host = Url::parse(url)
        .ok()
        .and_then(|parsed| parsed.host_str().map(str::to_ascii_lowercase))
        .unwrap_or_default();
    host.strip_prefix("www.").unwrap_or(&host).to_string()
}
