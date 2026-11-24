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

/// Decide the two-value record-id fallback payload.
pub fn json_fallback(rid: &str, probe: Option<&str>, is_tuple: bool) -> Vec<Option<String>> {
    let fallback = if is_tuple { None } else { probe.map(str::to_string) };
    vec![Some(rid.to_string()), fallback]
}

/// Python-compatible rounding to a fixed number of decimals.
pub fn py_round(value: f64, digits: u32) -> f64 {
    match format!("{:.*}", digits as usize, value).parse::<f64>() {
        Ok(rounded) => rounded,
        Err(_) => value,
    }
}
