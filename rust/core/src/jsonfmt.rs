//! Python-compatible JSON rendering for URL error records.

use serde_json::Value;

fn field(value: &Value, key: &str) -> String {
    let raw = value.get(key).and_then(Value::as_str).unwrap_or("");
    serde_json::to_string(raw).unwrap_or_else(|_| "\"\"".to_owned())
}

/// Matches `json.dumps(record)` for the fixed three-field URL error shape.
pub fn compact_record(record: &Value) -> String {
    format!(
        "{{\"url\": {}, \"error\": {}, \"error_type\": {}}}",
        field(record, "url"),
        field(record, "error"),
        field(record, "error_type")
    )
}

/// Matches `json.dumps(record, indent=2)` for the fixed three-field URL error shape.
pub fn pretty_record(record: &Value) -> String {
    format!(
        "{{\n  \"url\": {},\n  \"error\": {},\n  \"error_type\": {}\n}}",
        field(record, "url"),
        field(record, "error"),
        field(record, "error_type")
    )
}
