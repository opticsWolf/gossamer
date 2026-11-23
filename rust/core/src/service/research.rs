//! Deterministic research-result kernels.

use serde_json::Value;
use crate::urls::canonical_url;

/// Deduplicate research records by canonical URL, preserving first occurrence.
pub fn normalize_records(records: &Value, max_results: i64) -> Vec<Value> {
    let mut out = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let Some(items) = records.as_array() else { return out };
    for item in items {
        if !item.is_object() { continue; }
        if let Some(url) = item.get("url").and_then(Value::as_str)
            && let Ok(key) = canonical_url(url, "keep", None)
        {
            if seen.iter().any(|value| value == &key) { continue; }
            seen.push(key);
        }
        out.push(item.clone());
        if max_results > 0 && out.len() as i64 >= max_results { break; }
    }
    out
}

/// Order provider names with the preferred canonical provider first.
pub fn provider_order(providers: &Value, preferred: Option<&str>) -> Vec<String> {
    let names: Vec<String> = providers.as_array().into_iter().flatten()
        .filter_map(Value::as_str).map(str::to_string).collect();
    let Some(name) = preferred.filter(|v| !v.is_empty()) else { return names };
    let canonical = crate::search::resolve_provider_name(name).unwrap_or(name).to_lowercase();
    let mut out: Vec<String> = names.iter().filter(|n| n.to_lowercase() == canonical).cloned().collect();
    out.extend(names.into_iter().filter(|n| n.to_lowercase() != canonical));
    out
}
