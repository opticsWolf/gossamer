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
