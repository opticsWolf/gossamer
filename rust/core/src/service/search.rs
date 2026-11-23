//! Deterministic search/provider kernels.

use serde_json::{Map, Value, json};

use crate::urls::canonical_url;

/// Deduplicate results by canonical URL, preserving first occurrence.
pub fn dedup_results(results: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let Some(items) = results.as_array() else {
        return out;
    };
    for item in items {
        if let Some(url) = item.get("url").and_then(Value::as_str)
            && let Ok(key) = canonical_url(url, "keep", None)
        {
            if seen.iter().any(|value| value == &key) {
                continue;
            }
            seen.push(key);
        }
        out.push(item.clone());
    }
    out
}

/// Note describing a recognized-but-unregistered provider substitution.
pub fn fallback_note(provider_name: Option<&str>, registered: &Value) -> Option<Value> {
    let name = provider_name.filter(|value| !value.is_empty())?;
    let canonical = crate::search::resolve_provider_name(name)?;
    let names: Vec<String> = registered
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect();
    if names.iter().any(|value| value == canonical) {
        return None;
    }
    Some(json!({
        "requested": canonical,
        "reason": "provider not registered",
        "used": names,
    }))
}

/// Ordered provider names: requested provider first when registered.
pub fn resolve_order(provider_name: Option<&str>, registered: &Value) -> Vec<String> {
    let names: Vec<String> = registered
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect();
    let Some(name) = provider_name.filter(|value| !value.is_empty()) else {
        return names;
    };
    let Some(canonical) = crate::search::resolve_provider_name(name) else {
        return names;
    };
    let mut out: Vec<String> = names
        .iter()
        .filter(|value| value.as_str() == canonical)
        .cloned()
        .collect();
    out.extend(names.into_iter().filter(|value| value != canonical));
    out
}

/// Compact server rate-limit headers into a snapshot dict.
pub fn parse_rate_headers(status: i64, headers: &Value) -> Value {
    let mut out = Map::new();
    out.insert("rps_limit".into(), Value::Null);
    out.insert("remaining".into(), Value::Null);
    out.insert("reset_seconds".into(), Value::Null);
    out.insert("retry_after".into(), Value::Null);
    out.insert("exhausted".into(), Value::Bool(status == 429));
    if let Some(object) = headers.as_object() {
        for (key, value) in object {
            let lowered = key.to_lowercase();
            if lowered == "retry-after"
                && let Some(text) = value.as_str()
                && let Ok(seconds) = text.parse::<f64>()
            {
                out.insert("retry_after".into(), json!(seconds));
            }
        }
    }
    Value::Object(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dedup_preserves_first_and_keeps_invalid() {
        let input = json!([
            {"url": "https://WWW.Example.com/a?x=1"},
            {"url": "https://example.com/a?x=1"},
            {"url": "not a url"}
        ]);
        let out = dedup_results(&input);
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn provider_aliases_order_first() {
        let names = json!(["duckduckgo", "google"]);
        assert_eq!(resolve_order(Some("ddg"), &names), ["duckduckgo", "google"]);
        assert!(fallback_note(Some("bing"), &names).is_some());
    }
}
