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
    let mut buffer = ryu::Buffer::new();
    let rendered = buffer.format_finite(value);
    if let Some((mantissa, exponent)) = rendered.split_once('e') {
        let Ok(number) = exponent.parse::<i32>() else {
            return rendered.to_string();
        };
        let sign = if number < 0 { '-' } else { '+' };
        return format!("{mantissa}e{sign}{:02}", number.unsigned_abs());
    }
    if value != 0.0 && value.abs() < 1e-4 {
        let sign = if value < 0.0 { "-" } else { "" };
        let body = rendered.trim_start_matches('-');
        let Some((_, fraction)) = body.split_once('.') else {
            return rendered.to_string();
        };
        let zeros = fraction.chars().take_while(|ch| *ch == '0').count();
        let digits = &fraction[zeros..];
        if digits.is_empty() {
            return format!("{sign}0.0");
        }
        let exponent = -(zeros as i32) - 1;
        let mut mantissa = String::new();
        let mut chars = digits.chars();
        if let Some(first) = chars.next() {
            mantissa.push(first);
        }
        let rest: String = chars.collect();
        if !rest.is_empty() {
            mantissa.push('.');
            mantissa.push_str(&rest);
        }
        let exp_sign = if exponent < 0 { '-' } else { '+' };
        return format!("{sign}{mantissa}e{exp_sign}{:02}", exponent.unsigned_abs());
    }
    rendered.to_string()
}

/// Quote a string the way Python's `json.dumps` does with `ensure_ascii=True`.
fn py_string(text: &str) -> String {
    use std::fmt::Write;

    let quoted = serde_json::to_string(text).unwrap_or_else(|_| "\"\"".to_owned());
    let mut out = String::with_capacity(quoted.len());
    for ch in quoted.chars() {
        if ch.is_ascii() {
            out.push(ch);
            continue;
        }
        let code = ch as u32;
        if code <= 0xffff {
            let _ = write!(out, "\\u{code:04x}");
        } else {
            let scalar = code - 0x1_0000;
            let high = 0xd800 + (scalar >> 10);
            let low = 0xdc00 + (scalar & 0x3ff);
            let _ = write!(out, "\\u{high:04x}\\u{low:04x}");
        }
    }
    out
}

fn quote(text: &str, ascii: bool) -> String {
    if ascii {
        return py_string(text);
    }
    serde_json::to_string(text).unwrap_or_else(|_| "\"".to_owned())
}

/// Encode a float for Python's `json.dumps`, which emits non-finite values
/// as the bare tokens `NaN`/`Infinity`/`-Infinity` (invalid strict JSON, but
/// observable CLI output). Finite values stay ordinary JSON numbers.
pub fn py_float_value(value: f64) -> Value {
    if value.is_nan() {
        Value::String("\u{1f}NaN".to_string())
    } else if value == f64::INFINITY {
        Value::String("\u{1f}Infinity".to_string())
    } else if value == f64::NEG_INFINITY {
        Value::String("\u{1f}-Infinity".to_string())
    } else {
        serde_json::Number::from_f64(value)
            .map(Value::Number)
            .unwrap_or(Value::Null)
    }
}

fn write_json(value: &Value, level: Option<usize>, ascii: bool, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => {
            if number.is_f64() {
                out.push_str(&py_float(number.as_f64().unwrap_or(0.0)));
            } else {
                out.push_str(&number.to_string());
            }
        }
        Value::String(text) => match text.as_str() {
            "\u{1f}NaN" => out.push_str("NaN"),
            "\u{1f}Infinity" => out.push_str("Infinity"),
            "\u{1f}-Infinity" => out.push_str("-Infinity"),
            _ => out.push_str(&quote(text, ascii)),
        },
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                match level {
                    Some(level) => {
                        out.push('\n');
                        out.push_str(&"  ".repeat(level + 1));
                    }
                    None => {
                        if index > 0 {
                            out.push(' ');
                        }
                    }
                }
                write_json(item, level.map(|level| level + 1), ascii, out);
            }
            if let Some(level) = level {
                out.push('\n');
                out.push_str(&"  ".repeat(level));
            }
            out.push(']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push('{');
            for (index, (key, item)) in map.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                match level {
                    Some(level) => {
                        out.push('\n');
                        out.push_str(&"  ".repeat(level + 1));
                    }
                    None => {
                        if index > 0 {
                            out.push(' ');
                        }
                    }
                }
                out.push_str(&quote(key, ascii));
                out.push_str(": ");
                write_json(item, level.map(|level| level + 1), ascii, out);
            }
            if let Some(level) = level {
                out.push('\n');
                out.push_str(&"  ".repeat(level));
            }
            out.push('}');
        }
    }
}

/// Serialize JSON with Python `json.dumps` default separators (`, ` and `: `).
pub fn py_dumps(value: &Value) -> String {
    let mut out = String::new();
    write_json(value, None, true, &mut out);
    out
}

/// Serialize JSON like `json.dumps(value, ensure_ascii=False)`.
pub fn py_dumps_utf8(value: &Value) -> String {
    let mut out = String::new();
    write_json(value, None, false, &mut out);
    out
}

/// Serialize JSON with Python `json.dumps(value, indent=2)` formatting.
pub fn py_dumps_pretty(value: &Value) -> String {
    let mut out = String::new();
    write_json(value, Some(0), true, &mut out);
    out
}

/// Serialize JSON like `json.dumps(value, indent=2, ensure_ascii=False)`.
pub fn py_dumps_pretty_utf8(value: &Value) -> String {
    let mut out = String::new();
    write_json(value, Some(0), false, &mut out);
    out
}

/// Serialize like Pydantic's `model_dump_json()`: compact and UTF-8.
pub fn pydantic_json(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "null".to_string())
}

#[cfg(test)]
mod py_float_tests {
    use super::{py_dumps, py_dumps_pretty};
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

    #[test]
    fn pretty_output_matches_python_indent_and_escaping() {
        let value = json!({
            "sheet": "Résultats",
            "headers": ["name"],
            "rows": [["café"]],
        });
        assert_eq!(
            py_dumps_pretty(&value),
            concat!(
                "{\n",
                "  \"sheet\": \"R\\u00e9sultats\",\n",
                "  \"headers\": [\n",
                "    \"name\"\n",
                "  ],\n",
                "  \"rows\": [\n",
                "    [\n",
                "      \"caf\\u00e9\"\n",
                "    ]\n",
                "  ]\n",
                "}"
            )
        );
    }
}
