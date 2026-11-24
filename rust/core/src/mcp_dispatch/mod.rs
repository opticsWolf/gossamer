//! MCP tool-call envelope helpers.

use serde_json::{Map, Value};

/// Wrap a tool result with the standard MCP envelope.
pub fn tool_envelope(name: &str, args: &Value, result: Value) -> Value {
    let mut envelope = Map::new();
    envelope.insert("tool".into(), Value::String(name.to_string()));
    envelope.insert("arguments".into(), args.clone());
    envelope.insert("result".into(), result);
    Value::Object(envelope)
}

/// Route an HTML inspection call to the correct implementation.
pub fn inspect_route(structured: bool, store_dir: Option<&str>) -> &'static str {
    if structured {
        "structured"
    } else if store_dir.is_some() {
        "store"
    } else {
        "markdown"
    }
}

/// Exact error payload for an unsupported `tables_as` value.
pub fn tables_as_error() -> Value {
    let mut out = Map::new();
    out.insert("error".into(), Value::String("tables_as must be 'markdown', 'csv' or 'json'".into()));
    Value::Object(out)
}

/// Registry defaults plus caller arguments for one registered tool.
pub fn tool_arguments(name: &str, arguments: &Value) -> Option<Value> {
    let tools: Value = serde_json::from_str(crate::mcp::TOOL_SCHEMA_JSON).ok()?;
    let tool = tools.as_array()?.iter().find(|tool| {
        tool.get("name").and_then(Value::as_str) == Some(name)
    })?;
    let schema = tool.get("inputSchema")?;
    let required: Vec<&str> = schema.get("required")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    let mut out = Map::new();
    if let Some(properties) = schema.get("properties").and_then(Value::as_object) {
        for (key, prop) in properties {
            if required.contains(&key.as_str()) {
                continue;
            }
            if let Some(default) = prop.get("default") {
                out.insert(key.clone(), default.clone());
            }
        }
    }
    if let Some(extra) = arguments.as_object() {
        for (key, value) in extra {
            out.insert(key.clone(), value.clone());
        }
    }
    Some(Value::Object(out))
}

pub fn inspect_request(
    url: &str,
    use_smart: &str,
    query: Option<&str>,
    offset: i64,
    max_chunks: i64,
    structured: bool,
    store_dir: Option<&str>,
) -> Value {
    serde_json::json!({
        "url": url,
        "use_smart": use_smart,
        "query": query,
        "offset": offset.max(0),
        "max_chunks": max_chunks.max(1),
        "route": inspect_route(structured, store_dir),
        "store_dir": store_dir,
    })
}

/// Deterministic validation errors for `extract_document`.
pub fn extract_validation(
    tables_as: &str,
    include_images: bool,
    structured: bool,
    store: bool,
) -> Option<Value> {
    if !matches!(tables_as, "markdown" | "csv" | "json") {
        return Some(tables_as_error());
    }
    if include_images && !store {
        return Some(serde_json::json!({
            "error": "include_images=True requires store=True; images need a directory to live in"
        }));
    }
    if include_images && structured {
        return Some(serde_json::json!({
            "error": "include_images cannot be combined with structured=True; use store=True instead"
        }));
    }
    None
}

/// Deterministic batch plan: canonical pending keys, cached keys and rejected inputs.
pub fn batch_plan(urls: Option<&Value>, cached_keys: &Value, visited_keys: &Value, strategy: &str) -> Value {
    let items: Vec<Value> = match urls {
        Some(Value::Array(values)) => values.clone(),
        Some(Value::String(value)) => vec![Value::String(value.clone())],
        _ => return serde_json::json!({"error": "Batch inspection failed: urls is required"}),
    };
    let mut pending: Vec<String> = Vec::new();
    let mut cached: Vec<String> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let cached_set: Vec<&str> = cached_keys.as_array().into_iter().flatten().filter_map(Value::as_str).collect();
    let visited_set: Vec<&str> = visited_keys.as_array().into_iter().flatten().filter_map(Value::as_str).collect();
    for item in items {
        let Some(raw) = item.as_str() else { continue };
        let Ok(key) = crate::urls::canonical_url(raw, "drop-tracking", None) else { continue };
        if seen.iter().any(|value| value == &key) { continue; }
        seen.push(key.clone());
        if cached_set.contains(&key.as_str()) {
            cached.push(key);
            continue;
        }
        if strategy != "auto" && visited_set.contains(&key.as_str()) {
            continue;
        }
        pending.push(key);
    }
    serde_json::json!({"pending": pending, "cached": cached, "strategy": strategy})
}

/// Normalize and validate `check_sources` inputs.
pub fn check_sources_plan(mode: Option<&str>, urls: Option<&Value>, type_name: &str) -> Value {
    let Some(mode) = mode else {
        return serde_json::json!({
            "error": "Unknown check_sources mode: None; expected \"status\" or \"content\"",
            "results": [],
        });
    };
    if !matches!(mode, "status" | "content") {
        return serde_json::json!({
            "error": format!(
                "Unknown check_sources mode: {}; expected \"status\" or \"content\"",
                crate::compat::py_repr(mode)
            ),
            "results": [],
        });
    }
    let Some(value) = urls else {
        return serde_json::json!({"urls": [], "probe": if mode == "status" { "head" } else { "get" }});
    };
    let items: Vec<&Value> = match value {
        Value::Array(items) => items.iter().collect(),
        Value::String(_text) => vec![value],
        _ => return serde_json::json!({
            "error": format!(
                "urls must be a list of URLs (or {{url}} dicts), got {type_name}"
            ),
            "results": [],
        }),
    };
    let mut out: Vec<String> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for item in items {
        let candidate = if let Some(object) = item.as_object() {
            object.get("url").or_else(|| object.get("href")).or_else(|| object.get("link"))
        } else {
            Some(item)
        };
        let Some(raw) = candidate.and_then(Value::as_str) else { continue };
        let raw = raw.trim();
        if raw.is_empty() { continue; }
        let key = crate::urls::canonical_url(raw, "keep", None).unwrap_or_else(|_| raw.to_string());
        if seen.contains(&key) { continue; }
        seen.push(key);
        out.push(raw.to_string());
    }
    serde_json::json!({"urls": out, "probe": if mode == "status" { "head" } else { "get" }})
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn inspection_routes() {
        assert_eq!(inspect_route(true, Some("dir")), "structured");
        assert_eq!(inspect_route(false, Some("dir")), "store");
        assert_eq!(inspect_route(false, None), "markdown");
    }

    #[test]
    fn tool_arguments_merge_defaults() {
        let out = tool_arguments("web_search", &json!({"query": "rust"})).expect("known tool");
        assert_eq!(out["query"], json!("rust"));
        assert_eq!(out["max_results"], json!(5));
    }
}

