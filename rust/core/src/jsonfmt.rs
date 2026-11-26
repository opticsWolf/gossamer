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
    let fallback = if is_tuple {
        None
    } else {
        probe.map(str::to_string)
    };
    vec![Some(rid.to_string()), fallback]
}

/// Python-compatible rounding to a fixed number of decimals.
pub fn py_round(value: f64, digits: u32) -> f64 {
    match format!("{:.*}", digits as usize, value).parse::<f64>() {
        Ok(rounded) => rounded,
        Err(_) => value,
    }
}

/// Python `repr` spelling for a finite float. Rust and Python both use the
/// shortest round-tripping mantissa, but Python pads the exponent to at least
/// two digits and always includes its sign.
fn py_float(value: f64) -> String {
    let rendered = format!("{value:?}");
    let Some((mantissa, exponent)) = rendered.split_once('e') else {
        return rendered;
    };
    let Ok(number) = exponent.parse::<i32>() else {
        return rendered;
    };
    let sign = if number < 0 { '-' } else { '+' };
    format!("{mantissa}e{sign}{:02}", number.unsigned_abs())
}

/// Serialize JSON with Python `json.dumps` default separators (`, ` and `: `).
pub fn py_dumps(value: &Value) -> String {
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(flag) => flag.to_string(),
        Value::Number(number) => match number.as_f64() {
            Some(value) if number.is_f64() => py_float(value),
            _ => number.to_string(),
        },
        Value::String(text) => serde_json::to_string(text).unwrap_or_else(|_| "\"\"".to_string()),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(py_dumps).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(key, value)| format!(
                    "{}: {}",
                    serde_json::to_string(key).unwrap_or_else(|_| "\"\"".to_string()),
                    py_dumps(value)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

#[cfg(test)]
mod py_float_tests {
    use super::py_dumps;
    use serde_json::json;

    #[test]
    fn formats_small_floats_like_python() {
        assert_eq!(
            py_dumps(&json!({"score": 9.999999747378752e-5})),
            "{\"score\": 9.999999747378752e-05}"
        );
        assert_eq!(py_dumps(&json!({"score": 1e16})), "{\"score\": 1e+16}");
        assert_eq!(py_dumps(&json!({"score": 1})), "{\"score\": 1}");
    }
}
