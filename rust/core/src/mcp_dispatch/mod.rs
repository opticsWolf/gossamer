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
