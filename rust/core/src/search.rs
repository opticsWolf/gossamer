//! Pure search-provider naming and error-shape kernels.

use serde_json::{Value, json};

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
