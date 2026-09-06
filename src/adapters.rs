//! Domain-adapter build/parse kernels, pilot batch (port of the pure
//! parts of `OpenMeteoAdapter` and `FrankfurterAdapter`).
//!
//! Deliberate split: URL/param building, HTTP, keys, rate limiting and
//! retry stay Python (the existing httpx-mock tests keep working
//! unchanged); response *parsing* — where the historical bugs lived —
//! moves here. Records cross the boundary as JSON **minus `raw`**
//! (Python re-attaches `json.dumps` of the source object, exact by
//! construction). Values keep their JSON types except where the
//! original f-strings them (ids, titles), rendered via Python-`str()`
//! spellings.
//!
//! Pinned by `tests/test_rust_parity_adapters.py`.

use pyo3::prelude::*;
use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

use crate::cite::py_value_repr;
use crate::pycompat::{py_repr, py_strip};

fn attr_error(t: &str) -> String {
    format!("AttributeError: '{t}' object has no attribute 'get'")
}

fn type_error_not_subscriptable(t: &str) -> String {
    format!("TypeError: '{t}' object is not subscriptable")
}

fn json_type(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_i64() || n.is_u64() {
                "int"
            } else {
                "float"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Slice `hits[:max_results]` with Python semantics (negatives clip).
fn apply_limit<T: Clone>(items: &[T], max_results: i64) -> Vec<T> {
    let n = items.len() as i64;
    let end = if max_results < 0 {
        (n + max_results).max(0)
    } else {
        max_results.min(n)
    } as usize;
    items[..end].to_vec()
}

// ── Open-Meteo ───────────────────────────────────────────────

/// Geocoding response → records (minus `raw`).
pub fn openmeteo_parse_search_impl(
    response_json: &str,
    max_results: i64,
    base_url: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(Value::Array(a)) => a.iter().collect(),
        // `hits[:max_results]` subscripts first: dicts raise KeyError
        // with the slice as key (even when empty); other non-lists
        // raise TypeError. Strings slice fine and then fail per
        // character (empty-after-slice iterates zero times).
        Some(Value::Object(_)) => {
            return Err(format!("KeyError: slice(None, {max_results}, None)"));
        }
        Some(Value::String(s)) => {
            let chars: Vec<char> = s.chars().collect();
            let n = chars.len() as i64;
            let end = if max_results < 0 {
                (n + max_results).max(0)
            } else {
                max_results.min(n)
            } as usize;
            if end == 0 {
                Vec::new()
            } else {
                return Err(attr_error("str"));
            }
        }
        Some(v) => return Err(type_error_not_subscriptable(json_type(v))),
    };
    let mut out = Vec::new();
    for h in apply_limit(&hits, max_results) {
        let m = match h {
            Value::Object(m) => m,
            _ => return Err(attr_error(json_type(h))),
        };
        // `h.get('latitude', 0)`: missing → 0, present → rendered
        // as-is (None stays "None"). The fallback URL instead uses
        // `h.get('latitude')` (no default): missing → "None".
        let lat_id = match m.get("latitude") {
            None => "0".to_string(),
            Some(v) => py_value_repr(v),
        };
        let lon_id = match m.get("longitude") {
            None => "0".to_string(),
            Some(v) => py_value_repr(v),
        };
        let lat_url = match m.get("latitude") {
            None => "None".to_string(),
            Some(v) => py_value_repr(v),
        };
        let lon_url = match m.get("longitude") {
            None => "None".to_string(),
            Some(v) => py_value_repr(v),
        };
        // Title parts: falsy skipped; truthy non-strings raise TypeError
        // with the *filtered* index (mirrors `", ".join`).
        let mut parts: Vec<&str> = Vec::new();
        for key in ["name", "admin1", "country"] {
            if let Some(v) = m.get(key) {
                if is_truthy(v) {
                    match v {
                        Value::String(s) => parts.push(s),
                        _ => {
                            return Err(format!(
                                "TypeError: sequence item {}: expected str instance, {} found",
                                parts.len(),
                                json_type(v)
                            ));
                        }
                    }
                }
            }
        }
        let title = parts.join(", ");
        let url = match m.get("url") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => Value::String(format!("{base_url}?latitude={lat_url}&longitude={lon_url}")),
        };
        let snippet = m.get("country").cloned().unwrap_or(Value::String(String::new()));
        let mut rec = serde_json::Map::new();
        rec.insert("source".to_string(), Value::String("open-meteo".to_string()));
        rec.insert("id".to_string(), Value::String(format!("{lat_id},{lon_id}")));
        rec.insert("title".to_string(), Value::String(title));
        rec.insert("url".to_string(), url);
        rec.insert("snippet".to_string(), snippet);
        out.push(Value::Object(rec));
    }
    Ok(out)
}

fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i != 0
            } else if let Some(u) = n.as_u64() {
                u != 0
            } else {
                n.as_f64().map(|f| f != 0.0).unwrap_or(false)
            }
        }
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Forecast response → single record (minus `raw`). `lat_s`/`lon_s` are
/// pre-rendered by Python (exact float spellings by construction).
pub fn openmeteo_parse_forecast_impl(
    data_json: &str,
    lat_s: &str,
    lon_s: &str,
    base_url: &str,
) -> Result<Value, String> {
    let data: Value = serde_json::from_str(data_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &data {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&data))),
    };
    // `.get("current", {})`: missing key defaults to empty (no error).
    let current = obj.get("current").cloned().unwrap_or(Value::Object(serde_json::Map::new()));
    let snippet = match &current {
        Value::Object(m) => m
            .iter()
            .map(|(k, v)| format!("{k}={}", py_value_repr(v)))
            .collect::<Vec<_>>()
            .join(", "),
        _ => {
            // `current.items()` — attribute is `items`, not `get`.
            let t = match &current {
                Value::Null => "NoneType",
                v => json_type(v),
            };
            return Err(format!(
                "AttributeError: '{t}' object has no attribute 'items'"
            ));
        }
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("open-meteo".to_string()));
    rec.insert("id".to_string(), Value::String(format!("{lat_s},{lon_s}")));
    rec.insert(
        "title".to_string(),
        Value::String("Open-Meteo forecast".to_string()),
    );
    rec.insert(
        "url".to_string(),
        Value::String(format!("{base_url}?latitude={lat_s}&longitude={lon_s}")),
    );
    rec.insert("snippet".to_string(), Value::String(snippet));
    Ok(Value::Object(rec))
}

// ── Frankfurter ──────────────────────────────────────────────

fn split_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[\s/]+").expect("split regex"))
}

fn code_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Z]{3}$").expect("code regex"))
}

/// `"USD/EUR"` / `"USD EUR"` → `(base, quote?)` with exact messages.
pub fn frankfurter_split_pair_impl(spec: Option<&str>) -> Result<(String, Option<String>), String> {
    let text = py_strip(spec.unwrap_or("")).to_uppercase();
    let parts: Vec<&str> = split_re().split(&text).filter(|p| !p.is_empty()).collect();
    if parts.is_empty() {
        return Err(
            "ValueError: FrankfurterAdapter needs a currency (USD) or pair (USD/EUR)."
                .to_string(),
        );
    }
    if !code_re().is_match(parts[0]) {
        return Err(format!(
            "ValueError: Not a currency code: {}",
            py_repr(parts[0])
        ));
    }
    let base = parts[0].to_string();
    let quote = if parts.len() > 1 {
        if !code_re().is_match(parts[1]) {
            return Err(format!(
                "ValueError: Not a currency code: {}",
                py_repr(parts[1])
            ));
        }
        Some(parts[1].to_string())
    } else {
        None
    };
    Ok((base, quote))
}

fn render_cell(v: &Value) -> String {
    py_value_repr(v)
}

/// Rates response → rows (minus `raw`). Accepts the v2 list shape and
/// the `{base, quotes: {...}}` map shape; `max_results` caps with the
/// original early-return semantics (runs at least once per pairs loop).
pub fn frankfurter_parse_rates_impl(
    body_json: &str,
    base: &str,
    date_fallback: Option<&str>,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(body_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let rows: Vec<&Value> = match &body {
        Value::Array(a) => a.iter().collect(),
        single => vec![single],
    };
    let mut out = Vec::new();
    for row in rows {
        let m = match row {
            Value::Object(m) => m,
            _ => continue,
        };
        let day = match m.get("date") {
            None => date_fallback.unwrap_or("").to_string(),
            Some(v) => render_cell(v),
        };
        let b = match m.get("base") {
            None => Value::String(base.to_string()),
            Some(v) => v.clone(),
        };
        let mut pairs: Vec<(Value, &Value)> = Vec::new();
        if let Some(q) = m.get("quote") {
            pairs.push((q.clone(), m.get("rate").unwrap_or(&Value::Null)));
        }
        match m.get("quotes") {
            None => {}
            Some(Value::Object(qm)) => {
                for (q, rate) in qm {
                    pairs.push((Value::String(q.clone()), rate));
                }
            }
            Some(v) if !is_truthy(v) => {}
            Some(v) => {
                let t = match v {
                    Value::String(_) => "str",
                    Value::Null => "NoneType",
                    Value::Bool(_) => "bool",
                    Value::Number(n) => {
                        if n.is_i64() || n.is_u64() {
                            "int"
                        } else {
                            "float"
                        }
                    }
                    Value::Array(_) => "list",
                    Value::Object(_) => "dict",
                };
                return Err(format!(
                    "AttributeError: '{t}' object has no attribute 'items'"
                ));
            }
        }
        for (q, rate) in &pairs {
            if !is_truthy(q) {
                continue;
            }
            let b_s = render_cell(&b);
            let q_s = render_cell(q);
            let r_s = render_cell(rate);
            let mut rec = serde_json::Map::new();
            rec.insert("source".to_string(), Value::String("frankfurter".to_string()));
            rec.insert("id".to_string(), Value::String(format!("{b_s}/{q_s}")));
            rec.insert(
                "title".to_string(),
                Value::String(format!("{b_s}/{q_s} = {r_s} ({day})")),
            );
            rec.insert("url".to_string(), Value::String(String::new()));
            rec.insert("published".to_string(), Value::String(day.clone()));
            rec.insert(
                "snippet".to_string(),
                Value::String(format!(
                    "1 {b_s} = {r_s} {q_s} on {day} (central-bank reference rates)"
                )),
            );
            let mut fields = serde_json::Map::new();
            fields.insert("base".to_string(), b.clone());
            fields.insert("quote".to_string(), q.clone());
            fields.insert("rate".to_string(), (*rate).clone());
            fields.insert("date".to_string(), Value::String(day.clone()));
            rec.insert("fields".to_string(), Value::Object(fields));
            out.push(Value::Object(rec));
            if out.len() as i64 >= max_results {
                return Ok(out);
            }
        }
    }
    Ok(out)
}

// ── PyO3 wrappers ────────────────────────────────────────────────

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, base_url = "https://api.open-meteo.com/v1/forecast"))]
pub fn openmeteo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
    base_url: &str,
) -> PyResult<String> {
    // Records cross as a JSON array (minus `raw`, re-attached by Python).
    openmeteo_parse_search_impl(response_json, max_results, base_url)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn openmeteo_parse_forecast(
    py: Python,
    data_json: &str,
    lat_s: &str,
    lon_s: &str,
    base_url: &str,
) -> PyResult<String> {
    openmeteo_parse_forecast_impl(data_json, lat_s, lon_s, base_url)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (spec = None))]
pub fn frankfurter_split_pair(
    py: Python,
    spec: Option<&str>,
) -> PyResult<(String, Option<String>)> {
    frankfurter_split_pair_impl(spec).map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (body_json, base, date_fallback = None, max_results = 5))]
pub fn frankfurter_parse_rates(
    py: Python,
    body_json: &str,
    base: &str,
    date_fallback: Option<&str>,
    max_results: i64,
) -> PyResult<String> {
    frankfurter_parse_rates_impl(body_json, base, date_fallback, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

fn to_py_err(py: Python, e: String) -> pyo3::PyErr {
    use pyo3::exceptions::{PyAttributeError, PyKeyError, PyTypeError, PyValueError};
    if let Some(msg) = e.strip_prefix("AttributeError: ") {
        PyAttributeError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("TypeError: ") {
        PyTypeError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("ValueError: ") {
        PyValueError::new_err(msg.to_string())
    } else if let Some(rest) = e.strip_prefix("KeyError: slice(None, ") {
        // Rebuild a real `slice(None, N, None)` so `str()` renders
        // exactly like CPython (`slice(None, 5, None)`, unquoted).
        if let Some(n) = rest.strip_suffix(", None)") {
            if n.trim().parse::<i64>().is_ok() {
                if let Ok(code) = std::ffi::CString::new(format!("slice(None, {n}, None)")) {
                    if let Ok(slice) = py.eval(&code, None, None) {
                        use pyo3::types::PyAnyMethods;
                        if let Ok(inst) = py.get_type::<PyKeyError>().call1((slice,)) {
                            return pyo3::PyErr::from_value(inst);
                        }
                    }
                }
            }
        }
        PyKeyError::new_err(rest.to_string())
    } else {
        PyValueError::new_err(e)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_pair_shapes() {
        assert_eq!(
            frankfurter_split_pair_impl(Some("USD/EUR")).unwrap(),
            ("USD".to_string(), Some("EUR".to_string()))
        );
        assert_eq!(
            frankfurter_split_pair_impl(Some("usd eur")).unwrap(),
            ("USD".to_string(), Some("EUR".to_string()))
        );
        assert_eq!(
            frankfurter_split_pair_impl(Some("USD")).unwrap(),
            ("USD".to_string(), None)
        );
        assert!(frankfurter_split_pair_impl(None).is_err());
        assert!(frankfurter_split_pair_impl(Some("USDD")).is_err());
        assert!(frankfurter_split_pair_impl(Some("USD/EURO")).is_err());
    }

    #[test]
    fn openmeteo_search_parses() {
        let body = r#"{"results": [{"name": "Berlin", "admin1": "Berlin", "country": "Germany", "latitude": 52.52, "longitude": 13.41}]}"#;
        let out = openmeteo_parse_search_impl(body, 5, "https://b").unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["id"], "52.52,13.41");
        assert_eq!(out[0]["title"], "Berlin, Berlin, Germany");
    }

    #[test]
    fn frankfurter_shapes() {
        let v2 = r#"[{"base": "USD", "quote": "EUR", "rate": 0.92, "date": "2024-01-01"}]"#;
        let out = frankfurter_parse_rates_impl(v2, "USD", None, 5).unwrap();
        assert_eq!(out[0]["id"], "USD/EUR");
        assert_eq!(out[0]["fields"]["rate"], 0.92);
        let map = r#"{"base": "USD", "quotes": {"EUR": 0.92, "JPY": 150.0}}"#;
        let out = frankfurter_parse_rates_impl(map, "USD", None, 5).unwrap();
        assert_eq!(out.len(), 2);
    }
}
