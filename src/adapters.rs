//! Domain-adapter build/parse kernels: the pure parts of
//! `OpenMeteoAdapter`, `FrankfurterAdapter`, `YahooFinanceAdapter`,
//! `NvdAdapter` and `ZenodoAdapter`.
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
use crate::pycompat::{char_head, py_repr, py_strip};

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
        // NB: no slicing here — the `apply_limit` in the loop below is
        // the single `hits[:max_results]` (slicing twice would clip
        // negative limits twice).
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
    use pyo3::exceptions::{
        PyAttributeError, PyIndexError, PyKeyError, PyTypeError, PyValueError,
    };
    if let Some(msg) = e.strip_prefix("AttributeError: ") {
        PyAttributeError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("TypeError: ") {
        PyTypeError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("ValueError: ") {
        PyValueError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("IndexError: ") {
        PyIndexError::new_err(msg.to_string())
    } else if let Some(rest) = e.strip_prefix("KeyError: ") {
        // Integer keys (`dict[0]`) stay ints so `str()` matches CPython
        // (`0`, unquoted); slice keys rebuild a real slice object.
        if let Ok(n) = rest.parse::<i64>() {
            PyKeyError::new_err(n)
        } else if let Some(slice_rest) = rest.strip_prefix("slice(None, ") {
            if let Some(n) = slice_rest.strip_suffix(", None)") {
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
            PyKeyError::new_err(slice_rest.to_string())
        } else {
            PyKeyError::new_err(rest.to_string())
        }
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

    #[test]
    fn nvd_route_shapes() {
        assert_eq!(
            nvd_route_query_impl("CVE-2021-44228"),
            ("cveId".to_string(), "CVE-2021-44228".to_string())
        );
        assert_eq!(
            nvd_route_query_impl("cve-2021-44228"),
            ("cveId".to_string(), "CVE-2021-44228".to_string())
        );
        // Python `$` matches before a trailing newline: routing only,
        // no stripping (the wrapper strips first).
        assert_eq!(
            nvd_route_query_impl("CVE-2021-44228\n").0,
            "cveId".to_string()
        );
        assert_eq!(
            nvd_route_query_impl(" log4shell ").0,
            "keywordSearch".to_string()
        );
    }

    #[test]
    fn nvd_row_keeps_raw_id() {
        let cve: Value =
            serde_json::from_str(r#"{"id": 5, "published": "2021-12-10T00:00Z"}"#)
                .unwrap();
        let row = nvd_row_impl(&cve, "").unwrap();
        assert_eq!(row["id"], 5);
        assert_eq!(row["url"], "https://nvd.nist.gov/vuln/detail/5");
        assert_eq!(row["published"], "2021-12-10");
    }

    #[test]
    fn nvd_fetch_empty() {
        for body in [
            r#"{}"#,
            r#"{"vulnerabilities": []}"#,
            r#"{"vulnerabilities": null}"#,
        ] {
            assert_eq!(nvd_parse_fetch_impl(body, "CVE-1").unwrap().len(), 0);
        }
        assert!(nvd_parse_fetch_impl(r#"{"vulnerabilities": [5]}"#, "").is_err());
    }

    #[test]
    fn zenodo_names_join_error() {
        let people: Value =
            serde_json::from_str(r#"[{"name": 5}]"#).unwrap();
        assert_eq!(
            zenodo_names_impl(Some(&people)).unwrap_err(),
            "TypeError: sequence item 0: expected str instance, int found"
        );
        let people: Value =
            serde_json::from_str(r#"[{"name": "A"}, {"person_or_org": {"name": "B"}}]"#)
                .unwrap();
        assert_eq!(
            zenodo_names_impl(Some(&people)).unwrap(),
            "A, B".to_string()
        );
    }

    #[test]
    fn subscript_key_shapes() {
        let d: Value = serde_json::from_str(r#"{"a": 1}"#).unwrap();
        assert_eq!(
            subscript_hits(Some(&d), 5).unwrap_err(),
            "KeyError: slice(None, 5, None)"
        );
        let n = Value::Null;
        assert!(subscript_hits(Some(&n), 5).is_err());
        assert_eq!(subscript_hits(None, 5).unwrap().len(), 0);
    }
}

fn type_error_not_iterable(t: &str) -> String {
    format!("TypeError: '{t}' object is not iterable")
}

fn subscript_keyerror(max_results: i64) -> String {
    format!("KeyError: slice(None, {max_results}, None)")
}

fn sequence_item_error(index: usize, t: &str) -> String {
    format!("TypeError: sequence item {index}: expected str instance, {t} found")
}

/// Mirror `seq[:max_results]` + iteration for response lists: missing →
/// empty; lists sliced (negatives clip); strings sliced then failed per
/// character (empty-after-slice iterates zero times); dicts raise
/// `KeyError(slice)`; anything else raises TypeError.
fn subscript_hits<'a>(v: Option<&'a Value>, max_results: i64) -> Result<Vec<&'a Value>, String> {
    match v {
        None => Ok(Vec::new()),
        Some(Value::Array(a)) => Ok(slice_refs(a, max_results)),
        Some(Value::String(s)) => {
            let chars: Vec<char> = s.chars().collect();
            let n = chars.len() as i64;
            let end = if max_results < 0 {
                (n + max_results).max(0)
            } else {
                max_results.min(n)
            } as usize;
            if end == 0 {
                Ok(Vec::new())
            } else {
                Err(attr_error("str"))
            }
        }
        Some(Value::Object(_)) => Err(subscript_keyerror(max_results)),
        Some(other) => Err(type_error_not_subscriptable(json_type(other))),
    }
}

fn slice_refs<'a>(items: &'a [Value], max_results: i64) -> Vec<&'a Value> {
    let n = items.len() as i64;
    let end = if max_results < 0 {
        (n + max_results).max(0)
    } else {
        max_results.min(n)
    } as usize;
    items[..end].iter().collect()
}

pub fn yahoo_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `.get("quotes", []) or []`: missing/falsy → empty, truthy kept.
    let hits: Vec<&Value> = match obj.get("quotes") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for q in hits {
        let m = match q {
            Value::Object(m) => m,
            _ => return Err(attr_error(json_type(q))),
        };
        let missing = Value::String(String::new());
        let name_v = m
            .get("shortname")
            .filter(|v| is_truthy(v))
            .or_else(|| m.get("shortName").filter(|v| is_truthy(v)))
            .or_else(|| m.get("symbol"))
            .cloned()
            .unwrap_or_else(|| Value::String(String::new()));
        let name_s = py_value_repr(&name_v);
        let symbol = m.get("symbol").unwrap_or(&missing);
        let url = format!(
            "https://finance.yahoo.com/quote/{}",
            py_value_repr(symbol)
        );
        // `q.get('exchange', '')`: missing → ""; present → rendered as-is.
        let exchange = match m.get("exchange") {
            None => String::new(),
            Some(v) => py_value_repr(v),
        };
        let quote_type = match m.get("quoteType") {
            None => String::new(),
            Some(v) => py_value_repr(v),
        };
        let mut rec = serde_json::Map::new();
        rec.insert("source".to_string(), Value::String("yahoo".to_string()));
        rec.insert("id".to_string(), symbol.clone());
        rec.insert("title".to_string(), name_v);
        rec.insert("url".to_string(), Value::String(url));
        rec.insert(
            "snippet".to_string(),
            Value::String(format!("{name_s} — {exchange} {quote_type}")),
        );
                let mut inner = serde_json::Map::new();
        inner.insert(
            "exchange".to_string(),
            m.get("exchange").cloned().unwrap_or_else(|| Value::String(String::new())),
        );
        inner.insert(
            "quote_type".to_string(),
            m.get("quoteType").cloned().unwrap_or_else(|| Value::String(String::new())),
        );
        inner.insert(
            "market_cap".to_string(),
            m.get("marketCap").cloned().unwrap_or_else(|| Value::String(String::new())),
        );
        let mut fields = serde_json::Map::new();
        fields.insert("yahoo".to_string(), Value::Object(inner));
        rec.insert("fields".to_string(), Value::Object(fields));
        out.push(Value::Object(rec));
    }
    Ok(out)
}


pub fn yahoo_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<(Value, Value), String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `(body.get("chart", {}) or {})`: missing/falsy → {}; truthy kept.
    let chart = match obj.get("chart") {
        None => Value::Object(serde_json::Map::new()),
        Some(v) if !is_truthy(v) => Value::Object(serde_json::Map::new()),
        Some(v) => v.clone(),
    };
    let chart_map = match &chart {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&chart))),
    };
    // `.get("result", [{}])`: missing → [{}]; present (even None) kept.
    let result = chart_map
        .get("result")
        .cloned()
        .unwrap_or(Value::Array(vec![Value::Object(serde_json::Map::new())]));
    // `result[0]`: list (empty → IndexError), str (char), dict
    // (KeyError 0), anything else TypeError.
    let first = match &result {
        Value::Array(a) => match a.first() {
            Some(v) => v.clone(),
            None => {
                return Err("IndexError: list index out of range".to_string());
            }
        },
        Value::String(s) => match s.chars().next() {
            Some(c) => Value::String(c.to_string()),
            None => {
                return Err("IndexError: string index out of range".to_string());
            }
        },
        Value::Object(_) => return Err("KeyError: 0".to_string()),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    // `.get("meta", {})` on the element (must be a dict).
    let meta = match &first {
        Value::Object(m) => match m.get("meta") {
            None => Value::Object(serde_json::Map::new()),
            Some(v) => v.clone(),
        },
        _ => return Err(attr_error(json_type(&first))),
    };
    let meta_map = match &meta {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&meta))),
    };
    let symbol = meta_map.get("symbol").cloned().unwrap_or(fallback.clone());
    let title = meta_map
        .get("longName")
        .filter(|v| is_truthy(v))
        .or_else(|| meta_map.get("shortName").filter(|v| is_truthy(v)))
        .cloned()
        .unwrap_or(fallback.clone());
    let url = format!(
        "https://finance.yahoo.com/quote/{}",
        py_value_repr(meta_map.get("symbol").unwrap_or(&fallback))
    );
    let price = match meta_map.get("regularMarketPrice") {
        None => String::new(),
        Some(v) => py_value_repr(v),
    };
    let currency = match meta_map.get("currency") {
        None => String::new(),
        Some(v) => py_value_repr(v),
    };
    let exchange = match meta_map.get("fullExchangeName") {
        None => String::new(),
        Some(v) => py_value_repr(v),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("yahoo".to_string()));
    rec.insert("id".to_string(), symbol);
    rec.insert("title".to_string(), title);
    rec.insert("url".to_string(), Value::String(url));
    rec.insert(
        "snippet".to_string(),
        Value::String(format!("{price} {currency} ({exchange})")),
    );
    let mut inner = serde_json::Map::new();
    inner.insert(
        "currency".to_string(),
        meta_map.get("currency").cloned().unwrap_or(Value::String(String::new())),
    );
    inner.insert(
        "exchange".to_string(),
        meta_map
            .get("fullExchangeName")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    inner.insert(
        "previous_close".to_string(),
        meta_map
            .get("previousClose")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    let mut fields = serde_json::Map::new();
    fields.insert("yahoo".to_string(), Value::Object(inner));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok((Value::Object(rec), meta))
}

/// Mirror of the CVE-id routing in `NvdAdapter._search_impl` (`re.match`
/// + `upper()`); the `(query or "").strip()` pre-step stays Python in the
/// wrapper, so this takes the already-stripped query verbatim.
pub fn nvd_route_query_impl(query: &str) -> (String, String) {
    static RE: OnceLock<Regex> = OnceLock::new();
    // NB: Python `$` also matches just before a trailing newline, so the
    // end anchor spells that out (`$` alone is end-of-haystack in Rust).
    let re = RE.get_or_init(|| Regex::new(r"(?i)^CVE-\d{4}-\d{4,}(?:\n\z|\z)").expect("cve regex"));
    if re.is_match(query) {
        ("cveId".to_string(), query.to_uppercase())
    } else {
        ("keywordSearch".to_string(), query.to_string())
    }
}

fn py_split(text: &str) -> Vec<&str> {
    text.split(|c: char| {
        c.is_whitespace() || c == '\u{85}' || ('\u{1C}'..='\u{1F}').contains(&c)
    })
    .filter(|w| !w.is_empty())
    .collect()
}

fn first_english_desc(cve: &serde_json::Map<String, Value>) -> Result<String, String> {
    let descs = match cve.get("descriptions") {
        None => return Ok(String::new()),
        Some(v) if !is_truthy(v) => return Ok(String::new()),
        Some(v) => v,
    };
    let items: Vec<&Value> = match descs {
        Value::Array(a) => a.iter().collect(),
        Value::String(s) => {
            // Iterating a string yields chars; `.get` on a char raises.
            if s.is_empty() {
                return Ok(String::new());
            }
            return Err(attr_error("str"));
        }
        Value::Object(_) => return Err(attr_error("str")),
        other => return Err(type_error_not_iterable(json_type(other))),
    };
    for d in items {
        let m = match d {
            Value::Object(m) => m,
            _ => return Err(attr_error(json_type(d))),
        };
        let lang = m.get("lang");
        let english = matches!(lang, Some(Value::String(s)) if s == "en")
            || !lang.map(is_truthy).unwrap_or(false);
        if !english {
            continue;
        }
        // First English (or lang-less) entry RETURNS, even when its
        // value is empty; `(value or "")` keeps truthy non-strings, whose
        // `.split()` then raises AttributeError.
        let text = match m.get("value") {
            None => String::new(),
            Some(Value::String(s)) => s.clone(),
            Some(v) if !is_truthy(v) => String::new(),
            Some(v) => {
                return Err(format!(
                    "AttributeError: '{}' object has no attribute 'split'",
                    json_type(v)
                ));
            }
        };
        let collapsed: String = py_split(&text).join(" ");
        return Ok(char_head(&collapsed, 240).to_string());
    }
    Ok(String::new())
}


fn nvd_row_impl(cve: &Value, fallback_id: &str) -> Result<Value, String> {
    let m = match cve {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(cve))),
    };
    // `cve.get("metrics", {}) or {}`: missing/falsy → none.
    let metrics_obj: Option<&serde_json::Map<String, Value>> = match m.get("metrics") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        // Truthy non-dict: the loop below always reads bucket 0 first,
        // so its `.get` raises here with the payload's own type.
        Some(other) => {
            return Err(attr_error(json_type(other)));
        }
    };
    // First non-skipped bucket wins; `cvss` ends as None ({}) or a value.
    let mut cvss: Option<&Value> = None;
    if let Some(mm) = metrics_obj {
        for bucket in ["cvssMetricV31", "cvssMetricV30", "cvssMetricV2"] {
            let entries = match mm.get(bucket) {
                None => continue,
                Some(v) if !is_truthy(v) => continue,
                Some(v) => v,
            };
            // `entries[0]`: non-empty list → first; non-empty str →
            // first char (whose `.get` then raises); dict → KeyError;
            // anything else TypeError.
            let elem: &Value = match entries {
                Value::Array(a) => a.first().unwrap(),
                // Non-empty (falsy filtered above): the first char's
                // `.get("cvssData", {})` raises AttributeError.
                Value::String(_) => {
                    return Err(attr_error("str"));
                }
                Value::Object(_) => return Err("KeyError: 0".to_string()),
                other => return Err(type_error_not_subscriptable(json_type(other))),
            };
            let data: Option<&Value> = match elem {
                Value::Object(em) => match em.get("cvssData") {
                    None => None,
                    Some(v) if !is_truthy(v) => None,
                    Some(v) => Some(v),
                },
                _ => return Err(attr_error(json_type(elem))),
            };
            cvss = data;
            break;
        }
    }
    // `cvss.get(..., "")`: None ({}) → ""; objects read; anything else
    // (truthy non-dict — falsy already folded to None) raises.
    let cvss_field = |key: &str| -> Result<Value, String> {
        match cvss {
            None => Ok(Value::String(String::new())),
            Some(Value::Object(cm)) => Ok(cm
                .get(key)
                .cloned()
                .unwrap_or(Value::String(String::new()))),
            Some(other) => Err(attr_error(json_type(other))),
        }
    };
    let severity = cvss_field("baseSeverity")?;
    let base_score = cvss_field("baseScore")?;
    let vector = cvss_field("vectorString")?;
    // `cve.get("id", fallback_id)`: missing -> fallback string; present
    // values stay raw in `id`/`title` (only the URL f-string renders).
    let cve_id: Value = match m.get("id") {
        None => Value::String(fallback_id.to_string()),
        Some(v) => v.clone(),
    };
    let published = match m.get("published") {
        None => Value::String(String::new()),
        Some(v) => v.clone(),
    };
    let published_short = match &published {
        Value::String(s) => Value::String(char_head(s, 10).to_string()),
        Value::Array(a) => Value::Array(a.iter().take(10).cloned().collect()),
        // `dict[:10]` raises KeyError (slicing a mapping); anything else
        // is not subscriptable at all.
        Value::Object(_) => return Err(subscript_keyerror(10)),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let snippet = first_english_desc(m)?;
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("nvd".to_string()));
    rec.insert("id".to_string(), cve_id.clone());
    rec.insert("title".to_string(), cve_id.clone());
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://nvd.nist.gov/vuln/detail/{0}",
            py_value_repr(&cve_id)
        )),
    );
    rec.insert("published".to_string(), published_short);
    rec.insert("snippet".to_string(), Value::String(snippet));
    let mut inner = serde_json::Map::new();
    inner.insert("severity".to_string(), severity);
    inner.insert("base_score".to_string(), base_score);
    inner.insert("vector".to_string(), vector);
    let mut fields = serde_json::Map::new();
    fields.insert("nvd".to_string(), Value::Object(inner));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn nvd_parse_vulns_impl(
    response_json: &str,
    fallback_id: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("vulnerabilities", [])`, then `items[:max]`.
    let hits: Vec<&Value> = match obj.get("vulnerabilities") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for item in hits {
        // `item.get("cve", {})`: missing → {}; non-dict items raise in _row.
        let cve = match item {
            Value::Object(m) => m.get("cve").cloned().unwrap_or(Value::Object(serde_json::Map::new())),
            _ => return Err(attr_error(json_type(item))),
        };
        out.push(nvd_row_impl(&cve, fallback_id)?);
    }
    Ok(out)
}

fn strip_tags_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"<[^>]+>").expect("strip-tags regex"))
}

/// Mirror of `_strip_tags`: falsy → `""`, else regex-substitute.
/// Non-string truthy values raise TypeError like `re.sub` does.
fn strip_tags_impl(text: &Value) -> Result<String, String> {
    if !is_truthy(text) {
        return Ok(String::new());
    }
    match text {
        Value::String(s) => Ok(strip_tags_re().replace_all(s, " ").to_string()),
        _ => Err(format!(
            "TypeError: expected string or bytes-like object, got '{}'",
            json_type(text)
        )),
    }
}

/// Mirror of `ZenodoAdapter._names`: falsy input → `""`; dicts via
/// `name` / `person_or_org.name`; anything else truthy stringified;
/// non-string join elements raise TypeError with the join index.
fn zenodo_names_impl(people: Option<&Value>) -> Result<String, String> {
    let people = match people {
        None => return Ok(String::new()),
        Some(v) if !is_truthy(v) => return Ok(String::new()),
        Some(v) => v,
    };
    // Iterate: lists item-wise; strings char-wise; dicts key-wise;
    // anything else raises TypeError (not iterable).
    if !matches!(people, Value::Array(_) | Value::String(_) | Value::Object(_)) {
        return Err(type_error_not_iterable(json_type(people)));
    }
    // (rendered_text, original_type) pairs: only originally-string
    // values survive `", ".join`; anything else raises TypeError.
    let mut out: Vec<(String, &'static str)> = Vec::new();
    if matches!(people, Value::Array(_)) {
        let items: Vec<&Value> = match people {
            Value::Array(a) => a.iter().collect(),
            _ => Vec::new(),
        };
        for a in items {
            match a {
                Value::Object(m) => {
                    // `a.get("name") or person_or_org-name or ""`.
                    let direct = m.get("name").filter(|v| is_truthy(v));
                    let via_poo = || -> Result<Option<(String, &'static str)>, String> {
                        let poo = match m.get("person_or_org") {
                            None => return Ok(None),
                            Some(v) if !is_truthy(v) => return Ok(None),
                            Some(Value::Object(pm)) => pm,
                            Some(other) => return Err(attr_error(json_type(other))),
                        };
                        Ok(match poo.get("name") {
                            None => None,
                            Some(v) if !is_truthy(v) => None,
                            Some(Value::String(s)) => Some((s.clone(), "str")),
                            Some(v) => Some((py_value_repr(v), json_type(v))),
                        })
                    };
                    let chosen: Option<(String, &'static str)> = match direct {
                        Some(Value::String(s)) => Some((s.clone(), "str")),
                        Some(v) => Some((py_value_repr(v), json_type(v))),
                        None => via_poo()?,
                    };
                    if let Some((text, t)) = chosen {
                        if !text.is_empty() {
                            out.push((text, t));
                        }
                    }
                }
                _ => {
                    // Falsy items skipped (`elif a:`); truthy rendered
                    // via `str(a)` (always a string afterwards).
                    if is_truthy(a) {
                        out.push((py_value_repr(a), "str"));
                    }
                }
            }
        }
    } else if let Value::String(st) = people {
        for c in st.chars() {
            out.push((c.to_string(), "str"));
        }
    } else if let Value::Object(m) = people {
        for k in m.keys() {
            // `elif "":` skips empty keys; every other key is truthy.
            if !k.is_empty() {
                out.push((k.clone(), "str"));
            }
        }
    }
    // `", ".join`: first non-string element raises with its index.
    let mut rendered: Vec<String> = Vec::with_capacity(out.len());
    for (i, (text, t)) in out.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        rendered.push(text.clone());
    }
    Ok(rendered.join(", "))
}

fn zenodo_hit_impl(h: &Value, fallback_id: &str) -> Result<Value, String> {
    let hm = match h {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(h))),
    };
    // `h.get("metadata", {}) or {}`: missing/falsy → None (= {}).
    // Truthy non-dicts raise on their first `.get` (resource_type read).
    let m: Option<&serde_json::Map<String, Value>> = match hm.get("metadata") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let links: Option<&serde_json::Map<String, Value>> = match hm.get("links") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let rec_id = match hm.get("id") {
        None => fallback_id.to_string(),
        Some(v) => py_value_repr(v),
    };
    // `m.get("resource_type", {})`, then dict-navigate or `.get("id")`.
    let rtype: Option<&Value> = match m {
        None => None,
        Some(mm) => match mm.get("resource_type") {
            None => None,
            Some(v) => Some(v),
        },
    };
    let rtype_s: Option<Value> = match rtype {
        None => None,
        // Dicts navigate: dict `title` -> its `en` (missing -> "");
        // missing/non-dict `title` -> `id` (missing -> "").
        // Non-dicts are kept as-is for the `or ""` below.
        Some(Value::Object(rm)) => match rm.get("title") {
            Some(Value::Object(tm)) => tm.get("en").cloned(),
            _ => rm.get("id").cloned(),
        },
        Some(v) => Some(v.clone()),
    };
    let rtype_out = match rtype_s {
        None => Value::String(String::new()),
        Some(v) if !is_truthy(&v) => Value::String(String::new()),
        Some(v) => v,
    };
    // Hmm: `rtype or ""` applies to the NAVIGATED value; falsy → "".
    // (Unreachable for missing (None→""), kept for symmetry.)
    let title = match m {
        None => Value::String(String::new()),
        Some(mm) => mm.get("title").cloned().unwrap_or(Value::String(String::new())),
    };
    let url = match links {
        Some(lm) => match lm.get("html") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => match lm.get("self_html") {
                Some(v) if is_truthy(v) => v.clone(),
                _ => {
                    if rec_id.is_empty() {
                        Value::String(String::new())
                    } else {
                        Value::String(format!("https://zenodo.org/records/{rec_id}"))
                    }
                }
            },
        },
        None => {
            if rec_id.is_empty() {
                Value::String(String::new())
            } else {
                Value::String(format!("https://zenodo.org/records/{rec_id}"))
            }
        }
    };
    let published = match m {
        None => Value::String(String::new()),
        Some(mm) => mm
            .get("publication_date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    let creators = match m {
        None => None,
        Some(mm) => mm
            .get("creators")
            .filter(|v| is_truthy(v))
            .or_else(|| mm.get("contributors").filter(|v| is_truthy(v)))
            .or_else(|| mm.get("authors").filter(|v| is_truthy(v))),
    };
    let authors = zenodo_names_impl(creators)?;
    let description = match m {
        None => Value::String(String::new()),
        Some(mm) => mm.get("description").cloned().unwrap_or(Value::String(String::new())),
    };
    let stripped = strip_tags_impl(&description)?;
    let snippet = char_head(&stripped, 240).to_string();
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("zenodo".to_string()));
    rec.insert("id".to_string(), Value::String(rec_id));
    rec.insert("title".to_string(), title);
    rec.insert("url".to_string(), url);
    rec.insert("published".to_string(), published);
    rec.insert("authors".to_string(), Value::String(authors));
    rec.insert("snippet".to_string(), Value::String(snippet));
    let mut inner = serde_json::Map::new();
    inner.insert("resource_type".to_string(), rtype_out);
    let mut fields = serde_json::Map::new();
    fields.insert("zenodo".to_string(), Value::Object(inner));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

/// Mirror of `NvdAdapter.fetch`: empty/missing `vulnerabilities` -> `[]`,
/// else the first item's `cve` row with the (pre-rendered) record id.
pub fn nvd_parse_fetch_impl(
    response_json: &str,
    fallback_id: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `items = ...get("vulnerabilities", [])`; `if not items: return []`.
    let items = match obj.get("vulnerabilities") {
        None => return Ok(Vec::new()),
        Some(v) if !is_truthy(v) => return Ok(Vec::new()),
        Some(v) => v,
    };
    // `items[0]`: list (non-empty — falsy caught above), str (char),
    // dict (KeyError 0), anything else TypeError.
    let first = match items {
        Value::Array(a) => match a.first() {
            Some(v) => v,
            None => return Ok(Vec::new()),
        },
        Value::String(st) => match st.chars().next() {
            Some(_) => {
                // The char's `.get("cve", {})` raises AttributeError.
                return Err(attr_error("str"));
            }
            None => return Ok(Vec::new()),
        },
        Value::Object(_) => return Err("KeyError: 0".to_string()),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let cve = match first {
        Value::Object(m) => m.get("cve").cloned().unwrap_or(Value::Object(serde_json::Map::new())),
        _ => return Err(attr_error(json_type(first))),
    };
    Ok(vec![nvd_row_impl(&cve, fallback_id)?])
}

pub fn zenodo_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("hits", {})` — missing → {}; non-dict body already
    // raised. Present (even falsy) kept; `.get("hits", [])` needs a dict.
    let outer = match obj.get("hits") {
        None => return Ok(Vec::new()),
        Some(v) => v,
    };
    let inner = match outer {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(outer))),
    };
    let hits: Vec<&Value> = match inner.get("hits") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for h in hits {
        out.push(zenodo_hit_impl(h, "")?);
    }
    Ok(out)
}

pub fn zenodo_parse_fetch_impl(
    response_json: &str,
    record_id_s: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    zenodo_hit_impl(&body, record_id_s)
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_id = "", max_results = 5))]
pub fn nvd_parse_vulns(
    py: Python,
    response_json: &str,
    fallback_id: &str,
    max_results: i64,
) -> PyResult<String> {
    nvd_parse_vulns_impl(response_json, fallback_id, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, record_id = None, fallback_json = None))]
pub fn yahoo_parse_fetch(
    py: Python,
    response_json: &str,
    record_id: Option<&str>,
    fallback_json: Option<&str>,
) -> PyResult<String> {
    // Exact fallback for every `record_id` spelling: None -> null,
    // JSON-native values (int/list/dict/...) via the Python-rendered
    // `fallback_json`, anything else (tuples, sets, objects) via the
    // pre-rendered `str(record_id)` string. Returns
    // `{"record": ..., "meta": ...}`; Python attaches raw.
    let fallback = match (record_id, fallback_json) {
        (None, _) => Value::Null,
        (Some(st), Some(j)) => {
            serde_json::from_str(j).unwrap_or(Value::String(st.to_string()))
        }
        (Some(st), None) => Value::String(st.to_string()),
    };
    yahoo_parse_fetch_impl(response_json, &fallback)
        .and_then(|(rec, meta)| {
            let mut both = serde_json::Map::new();
            both.insert("record".to_string(), rec);
            both.insert("meta".to_string(), meta);
            serde_json::to_string(&Value::Object(both)).map_err(|e| e.to_string())
        })
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_id = ""))]
pub fn nvd_parse_fetch(py: Python, response_json: &str, fallback_id: &str) -> PyResult<String> {
    nvd_parse_fetch_impl(response_json, fallback_id)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn zenodo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    zenodo_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn zenodo_parse_fetch(py: Python, response_json: &str, record_id_s: &str) -> PyResult<String> {
    zenodo_parse_fetch_impl(response_json, record_id_s)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn yahoo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    yahoo_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn nvd_route_query(py: Python, query: &str) -> PyResult<(String, String)> {
    let _ = py;
    Ok(nvd_route_query_impl(query))
}
