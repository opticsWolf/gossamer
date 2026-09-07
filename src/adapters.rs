//! Domain-adapter build/parse kernels: the pure parts of
//! `OpenMeteoAdapter`, `FrankfurterAdapter`, `YahooFinanceAdapter`,
//! `NvdAdapter`, `ZenodoAdapter`, `CourtListenerAdapter`,
//! `GovInfoAdapter`, `HudocAdapter`, `PatentsViewAdapter`,
//! `OldpAdapter`, `FederalRegisterAdapter`, `BioRxivAdapter` and
//! `ChemRxivAdapter`.
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
        PyAttributeError, PyIndexError, PyKeyError, PyRuntimeError, PyTypeError,
        PyValueError,
    };
    if let Some(msg) = e.strip_prefix("AttributeError: ") {
        PyAttributeError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("TypeError: ") {
        PyTypeError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("ValueError: ") {
        PyValueError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("IndexError: ") {
        PyIndexError::new_err(msg.to_string())
    } else if let Some(msg) = e.strip_prefix("RuntimeError: ") {
        PyRuntimeError::new_err(msg.to_string())
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
    fn legal_patent_shapes() {
        // CourtListener strips tags BEFORE slicing the snippet.
        let r: Value = serde_json::from_str(
            r#"{"caseName": 7, "cluster_id": 5}"#,
        )
        .unwrap();
        assert_eq!(
            courtlistener_row_impl(&r).unwrap_err(),
            "TypeError: expected string or bytes-like object, got 'int'"
        );
        // GovInfo download-link preference with package fallback.
        let r: Value = serde_json::from_str(
            r#"{"packageId": "P", "download": {"pdfLink": "https://pdf"}}"#,
        )
        .unwrap();
        let row = govinfo_row_impl(&r).unwrap();
        assert_eq!(row["url"], "https://pdf");
        assert_eq!(row["id"], "P");
        // PatentsView surfaces API errors as RuntimeError.
        assert_eq!(
            patentsview_parse_search_impl(r#"{"error": "bad key"}"#, 5)
                .unwrap_err(),
            "RuntimeError: PatentsView error: bad key"
        );
        // HUDOC search applies no result cap.
        let body = r#"{"results": [{"columns": {}}, {"columns": {}}]}"#;
        assert_eq!(hudoc_parse_search_impl(body).unwrap().len(), 2);
        // PatentsView fetch reads patents[0], then patent_number.
        let body = r#"{"patents": [{"patent_number": "3"}]}"#;
        let out = patentsview_parse_fetch_impl(body).unwrap();
        assert_eq!(out[0]["id"], "3");
        let out = patentsview_parse_fetch_impl(r#"{"other": 1}"#).unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn oldp_fed_preprint_shapes() {
        // OLDP string snippets slice to chars, dicts raise KeyError.
        assert_eq!(
            oldp_snippet(Some(
                &serde_json::from_str(r#""abcdef""#).unwrap()
            ))
            .unwrap(),
            "a … b … c".to_string()
        );
        let d: Value = serde_json::from_str(r#"{"a": 1}"#).unwrap();
        assert_eq!(
            oldp_snippet(Some(&d)).unwrap_err(),
            "KeyError: slice(None, 3, None)"
        );
        // FederalRegister prefers html_url, then text_url; a truthy
        // non-dict agency raises at fields time (after the snippet).
        let d: Value =
            serde_json::from_str(r#"{"text_url": "https://text"}"#).unwrap();
        let row = fed_doc_impl(&d).unwrap();
        assert_eq!(row["url"], "https://text");
        assert_eq!(row["fields"]["agency"], "");
        let d: Value =
            serde_json::from_str(r#"{"agency": "EPA"}"#).unwrap();
        assert_eq!(
            fed_doc_impl(&d).unwrap_err(),
            "AttributeError: 'str' object has no attribute 'get'"
        );
        // ChemRxiv join reports the surviving (filtered) index.
        let items: Value =
            serde_json::from_str(r#"[{"name": ""}, {"name": 5}]"#).unwrap();
        let arr = items.as_array().unwrap();
        assert_eq!(
            chemrxiv_join(arr.iter().map(chemrxiv_part).collect(), true)
                .unwrap_err(),
            "TypeError: sequence item 0: expected str instance, int found"
        );
        // BioRxiv threads the server into flat fields.
        let p: Value =
            serde_json::from_str(r#"{"doi": "10.1/x"}"#).unwrap();
        let row = biorxiv_paper_impl(&p, "medrxiv").unwrap();
        assert_eq!(row["fields"]["server"], "medrxiv");
        assert_eq!(row["url"], "https://www.biorxiv.org/content/10.1/x");
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

// ── CourtListener ───────────────────────────────────────────────

fn courtlistener_row_impl(r: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    // `r.get("caseName") or r.get("caseNameFull", "")`.
    let title = match m.get("caseName") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => m
            .get("caseNameFull")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    // `_strip_tags(r.get("caseNameFull") or r.get("caseName") or "")[:240]`:
    // the or-chain feeds tag-stripping first (falsy → "", truthy
    // non-strings raise TypeError), and the slice applies to the
    // stripped string (chars, never raises).
    let raw_title = match m.get("caseNameFull") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => match m.get("caseName") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => Value::String(String::new()),
        },
    };
    let stripped_raw = strip_tags_impl(&raw_title)?;
    let stripped = char_head(stripped_raw.as_str(), 240).to_string();
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("courtlistener".to_string()),
    );
    rec.insert(
        "id".to_string(),
        Value::String(py_value_repr(
            m.get("cluster_id").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert("title".to_string(), title);
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://www.courtlistener.com{0}",
            py_value_repr(
                m.get("absolute_url")
                    .unwrap_or(&Value::String(String::new()))
            )
        )),
    );
    rec.insert(
        "published".to_string(),
        m.get("dateFiled")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("snippet".to_string(), Value::String(stripped));
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    for (key, field) in [
        ("court", "court"),
        ("court_citation", "court_citation_string"),
        ("docket_number", "docketNumber"),
        ("neutral_cite", "neutralCite"),
        ("cite_count", "citeCount"),
    ] {
        fields.insert(
            key.to_string(),
            m.get(field)
                .cloned()
                .unwrap_or(Value::String(String::new())),
        );
    }
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn courtlistener_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])`, then `results[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for r in hits {
        out.push(courtlistener_row_impl(r)?);
    }
    Ok(out)
}

pub fn courtlistener_parse_fetch_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    courtlistener_row_impl(&body)
}

// ── GovInfo ─────────────────────────────────────────────────────

fn govinfo_detail_url(pkg: &Value) -> Value {
    // `f"...{pkg}" if pkg else ""`: falsy package ids yield no URL.
    if is_truthy(pkg) {
        Value::String(format!(
            "https://www.govinfo.gov/app/details/{0}",
            py_value_repr(pkg)
        ))
    } else {
        Value::String(String::new())
    }
}

fn govinfo_row_impl(r: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    let pkg = m
        .get("packageId")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let granule = m
        .get("granuleId")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `r.get("download", {}) or {}`: missing/falsy → none; truthy
    // non-dicts raise on their first `.get` (txtLink read).
    let dl: Option<&serde_json::Map<String, Value>> = match m.get("download") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let txt = dl.and_then(|mm| mm.get("txtLink").filter(|v| is_truthy(v)));
    let pdf = dl.and_then(|mm| mm.get("pdfLink").filter(|v| is_truthy(v)));
    let url = match txt.or(pdf) {
        Some(v) => v.clone(),
        None => govinfo_detail_url(&pkg),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("govinfo".to_string()));
    // `granule or pkg`: raw values, first truthy wins.
    rec.insert(
        "id".to_string(),
        if is_truthy(&granule) {
            granule.clone()
        } else {
            pkg.clone()
        },
    );
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        Value::String(py_value_repr(
            m.get("dateIssued")
                .unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            py_strip(
                format!(
                    "{0} {1}",
                    py_value_repr(
                        m.get("collectionCode")
                            .unwrap_or(&Value::String(String::new()))
                    ),
                    py_value_repr(&pkg)
                )
                .as_str(),
            )
            .to_string(),
        ),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "collection".to_string(),
        m.get("collectionCode")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert("package_id".to_string(), pkg);
    fields.insert("granule_id".to_string(), granule);
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn govinfo_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])[:max_results]` (no `or []`).
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for r in hits {
        out.push(govinfo_row_impl(r)?);
    }
    Ok(out)
}

pub fn govinfo_parse_fetch_impl(
    response_json: &str,
    rid: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let m = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `body.get("download", {}).get("txtLink", "")`: missing download
    // → ""; present values (even falsy) read as-is; non-dicts raise.
    let txt = match m.get("download") {
        None => Value::String(String::new()),
        Some(Value::Object(dm)) => dm
            .get("txtLink")
            .cloned()
            .unwrap_or(Value::String(String::new())),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let url = if is_truthy(&txt) {
        txt
    } else {
        Value::String(format!("https://www.govinfo.gov/app/details/{rid}"))
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("govinfo".to_string()));
    rec.insert(
        "id".to_string(),
        m.get("packageId").cloned().unwrap_or(Value::String(
            rid.to_string(),
        )),
    );
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(rid.to_string())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        Value::String(py_value_repr(
            m.get("dateIssued")
                .unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(py_value_repr(
            m.get("collectionCode")
                .unwrap_or(&Value::String(String::new())),
        )),
    );
    // NB: flat `fields` here too (search rows carry package/granule).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "collection".to_string(),
        m.get("collectionCode")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

// ── HUDOC ─────────────────────────────────────────────────────

fn hudoc_row_impl(columns: &Value) -> Result<Value, String> {
    let m = match columns {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(columns))),
    };
    let itemid = m
        .get("itemid")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let url = if is_truthy(&itemid) {
        Value::String(format!(
            "https://hudoc.echr.coe.int/eng?i={0}",
            py_value_repr(&itemid)
        ))
    } else {
        Value::String(String::new())
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("hudoc".to_string()));
    rec.insert("id".to_string(), itemid);
    rec.insert(
        "title".to_string(),
        m.get("docname")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        Value::String(
            char_head(
                py_value_repr(
                    m.get("kpdate").unwrap_or(&Value::String(String::new()))
                )
                .as_str(),
                10,
            )
            .to_string(),
        ),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            py_strip(
                format!(
                    "application no. {0}",
                    py_value_repr(
                        m.get("appno").unwrap_or(&Value::String(String::new()))
                    )
                )
                .as_str(),
            )
            .to_string(),
        ),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "appno".to_string(),
        m.get("appno")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "ecli".to_string(),
        m.get("ecli")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn hudoc_parse_search_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // NB: no `[:max_results]` here — every result row is built.
    // `body.get("results", [])`: missing → []; present values iterate
    // (`for r in ...` — no truthiness check, so only ""/[]/{} iterate
    // empty while None/0/False raise TypeError).
    let results = match obj.get("results") {
        None => return Ok(Vec::new()),
        Some(v) => v,
    };
    // Materialize iteration exactly like `for r in results`.
    if !matches!(
        results,
        Value::Array(_) | Value::String(_) | Value::Object(_)
    ) {
        return Err(type_error_not_iterable(json_type(results)));
    }
    let items: Vec<&Value> = match results {
        Value::Array(a) => a.iter().collect(),
        _ => Vec::new(),
    };
    let mut out = Vec::new();
    if matches!(results, Value::Array(_)) {
        for r in items {
            // `r.get("columns", {})`: missing → {}; non-dict rows raise.
            let columns = match r {
                Value::Object(rm) => rm
                    .get("columns")
                    .cloned()
                    .unwrap_or(Value::Object(serde_json::Map::new())),
                _ => return Err(attr_error(json_type(r))),
            };
            out.push(hudoc_row_impl(&columns)?);
        }
    } else if let Value::String(s) = results {
        // Iterating a string yields chars; `.get` on a char raises.
        if !s.is_empty() {
            return Err(attr_error("str"));
        }
    } else if let Value::Object(m) = results {
        // Iterating a dict yields keys; `.get` on a key raises.
        if let Some(k) = m.keys().next() {
            let _ = k;
            return Err(attr_error("str"));
        }
    }
    Ok(out)
}

pub fn hudoc_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `results = ...get("results", [])`; `if not results: return []`.
    let results = match obj.get("results") {
        None => return Ok(Vec::new()),
        Some(v) if !is_truthy(v) => return Ok(Vec::new()),
        Some(v) => v,
    };
    // `results[0]`: list (non-empty — falsy caught above), str (char),
    // dict (KeyError 0), anything else TypeError.
    let first = match results {
        Value::Array(a) => match a.first() {
            Some(v) => v,
            None => return Ok(Vec::new()),
        },
        Value::String(s) => match s.chars().next() {
            Some(_) => return Err(attr_error("str")),
            None => return Ok(Vec::new()),
        },
        Value::Object(_) => return Err("KeyError: 0".to_string()),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    // `.get("columns", {})` on the element (must be a dict).
    let columns = match first {
        Value::Object(m) => m
            .get("columns")
            .cloned()
            .unwrap_or(Value::Object(serde_json::Map::new())),
        _ => return Err(attr_error(json_type(first))),
    };
    Ok(vec![hudoc_row_impl(&columns)?])
}

// ── PatentsView ─────────────────────────────────────────────────

fn patentsview_row_impl(p: &Value) -> Result<Value, String> {
    let m = match p {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(p))),
    };
    // `str(p.get("patent_number", p.get("id", "")))`: the inner default
    // applies only when the outer key is missing.
    let number = match m.get("patent_number") {
        Some(v) => py_value_repr(v),
        None => match m.get("id") {
            Some(v) => py_value_repr(v),
            None => String::new(),
        },
    };
    // `p.get("patent_title", p.get("title", number))`: raw values; the
    // ultimate default is the rendered number string.
    let title = match m.get("patent_title") {
        Some(v) => v.clone(),
        None => match m.get("title") {
            Some(v) => v.clone(),
            None => Value::String(number.clone()),
        },
    };
    // `str(p.get("patent_date", p.get("date", "")))[:10]`.
    let date_raw = match m.get("patent_date") {
        Some(v) => py_value_repr(v),
        None => match m.get("date") {
            Some(v) => py_value_repr(v),
            None => String::new(),
        },
    };
    let date = char_head(date_raw.as_str(), 10).to_string();
    let url = if number.is_empty() {
        Value::String(String::new())
    } else {
        Value::String(format!("https://patents.google.com/patent/US{number}"))
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("patentsview".to_string()),
    );
    rec.insert("id".to_string(), Value::String(number.clone()));
    rec.insert("title".to_string(), title.clone());
    rec.insert("url".to_string(), url);
    rec.insert("published".to_string(), Value::String(date.clone()));
    rec.insert(
        "snippet".to_string(),
        Value::String(format!("{0} ({date})", py_value_repr(&title))),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "assignee".to_string(),
        match m.get("assignee_organization") {
            Some(v) => v.clone(),
            None => m
                .get("assignee")
                .cloned()
                .unwrap_or(Value::String(String::new())),
        },
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn patentsview_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // API-level errors surface before row building.
    if let Some(err) = obj.get("error") {
        if is_truthy(err) {
            return Err(format!(
                "RuntimeError: PatentsView error: {0}",
                py_value_repr(err)
            ));
        }
    }
    // `body.get("patents", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("patents") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for p in hits {
        out.push(patentsview_row_impl(p)?);
    }
    Ok(out)
}

pub fn patentsview_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // Non-dict bodies fall through both `isinstance` checks → [].
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Ok(Vec::new()),
    };
    // `body["patents"][0]`: truthy patents index; str → char (whose
    // `.get` raises), dict → KeyError 0, anything else TypeError.
    if let Some(patents) = obj.get("patents") {
        if is_truthy(patents) {
            let first = match patents {
                Value::Array(a) => match a.first() {
                    Some(v) => v,
                    None => return Ok(Vec::new()),
                },
                Value::String(s) => match s.chars().next() {
                    Some(_) => return Err(attr_error("str")),
                    None => return Ok(Vec::new()),
                },
                Value::Object(_) => return Err("KeyError: 0".to_string()),
                other => return Err(type_error_not_subscriptable(json_type(other))),
            };
            return Ok(vec![patentsview_row_impl(first)?]);
        }
    }
    if let Some(v) = obj.get("patent_number") {
        if is_truthy(v) {
            return Ok(vec![patentsview_row_impl(&body)?]);
        }
    }
    Ok(Vec::new())
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn courtlistener_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    courtlistener_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn courtlistener_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    courtlistener_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn govinfo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    govinfo_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn govinfo_parse_fetch(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    govinfo_parse_fetch_impl(response_json, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json))]
pub fn hudoc_parse_search(py: Python, response_json: &str) -> PyResult<String> {
    hudoc_parse_search_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn hudoc_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    hudoc_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn patentsview_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    patentsview_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn patentsview_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    patentsview_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

// ── OLDP ────────────────────────────────────────────────────────

fn oldp_court_name(court: Option<&Value>) -> String {
    match court {
        // Dicts read `name` (missing → ""); anything else renders
        // `str(court or "")` — falsy → "", truthy → Python-`str()`.
        Some(Value::Object(m)) => match m.get("name") {
            None => String::new(),
            Some(v) => py_value_repr(v),
        },
        Some(v) if is_truthy(v) => py_value_repr(v),
        _ => String::new(),
    }
}

fn oldp_snippet(snippets: Option<&Value>) -> Result<String, String> {
    // `(c.get("snippets") or [])[:3]`, each `str(s)[:200]`, joined with
    // " … ". Unlike the row-list helpers, iterating here cannot fail
    // (`str(s)` accepts chars), so strings slice to chars; dicts raise
    // KeyError on slicing, anything else TypeError.
    let render = |s: &Value| char_head(py_value_repr(s).as_str(), 200).to_string();
    let parts: Vec<String> = match snippets {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => slice_refs(a, 3).iter().map(|s| render(s)).collect(),
        Some(Value::String(s)) => {
            // `str(char)` is the char itself; `[:200]` is a no-op.
            let chars: Vec<char> = s.chars().collect();
            let n = chars.len();
            chars[..3.min(n)].iter().map(|c| c.to_string()).collect()
        }
        Some(Value::Object(_)) => return Err(subscript_keyerror(3)),
        Some(other) => return Err(type_error_not_subscriptable(json_type(other))),
    };
    Ok(parts.join(" … "))
}

fn oldp_case_row_impl(c: &Value) -> Result<Value, String> {
    let m = match c {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(c))),
    };
    let court = oldp_court_name(m.get("court"));
    let file_no = m
        .get("file_number")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `f"{court} {file_no}".strip() or c.get("slug", "")`.
    let headed = py_strip(
        format!("{court} {0}", py_value_repr(&file_no)).as_str(),
    )
    .to_string();
    let title = if headed.is_empty() {
        m.get("slug")
            .cloned()
            .unwrap_or(Value::String(String::new()))
    } else {
        Value::String(headed)
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("oldp".to_string()));
    rec.insert(
        "id".to_string(),
        Value::String(py_value_repr(
            m.get("id").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert("title".to_string(), title);
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://de.openlegaldata.io/case/{0}",
            py_value_repr(m.get("slug").unwrap_or(&Value::String(String::new())))
        )),
    );
    rec.insert(
        "published".to_string(),
        Value::String(py_value_repr(
            m.get("date").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(oldp_snippet(m.get("snippets"))?),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert("court".to_string(), Value::String(court));
    fields.insert("file_number".to_string(), file_no);
    fields.insert(
        "ecli".to_string(),
        m.get("ecli")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "decision_type".to_string(),
        m.get("decision_type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn oldp_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])`, then `hits[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for c in hits {
        out.push(oldp_case_row_impl(c)?);
    }
    Ok(out)
}

pub fn oldp_parse_law_impl(response_json: &str, rid: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let m = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("oldp".to_string()));
    rec.insert("id".to_string(), Value::String(rid.to_string()));
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(rid.to_string())),
    );
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://de.openlegaldata.io/law/{0}",
            py_value_repr(m.get("slug").unwrap_or(&Value::String(String::new())))
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            char_head(
                py_value_repr(m.get("text").unwrap_or(&Value::String(String::new())))
                    .as_str(),
                400,
            )
            .to_string(),
        ),
    );
    let mut fields = serde_json::Map::new();
    fields.insert(
        "book".to_string(),
        m.get("book")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "section".to_string(),
        m.get("section")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

// ── Federal Register ────────────────────────────────────────────

fn fed_doc_impl(d: &Value) -> Result<Value, String> {
    let m = match d {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(d))),
    };
    // `d.get("agency", {}) or {}`: missing/falsy → none. The dict
    // check stays lazy: `agency.get("name", "")` runs at fields time
    // (after the snippet), so a truthy non-dict must not raise here.
    let agency: Option<&Value> = match m.get("agency") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(v) => Some(v),
    };
    // `d.get("html_url", d.get("text_url", ""))`: the inner default
    // applies only when the outer key is missing.
    let url = match m.get("html_url") {
        Some(v) => v.clone(),
        None => m
            .get("text_url")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("federalregister".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("document_number")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        m.get("doc_date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    // `_strip_tags(d.get("abstract", d.get("excerpt", "")))[:240]`.
    let abstract_raw = match m.get("abstract") {
        Some(v) => v.clone(),
        None => m
            .get("excerpt")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    let stripped = strip_tags_impl(&abstract_raw)?;
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(stripped.as_str(), 240).to_string()),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "document_type".to_string(),
        m.get("document_type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "type".to_string(),
        m.get("type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "agency".to_string(),
        match agency {
            None => Value::String(String::new()),
            Some(Value::Object(am)) => am
                .get("name")
                .cloned()
                .unwrap_or(Value::String(String::new())),
            Some(other) => return Err(attr_error(json_type(other))),
        },
    );
    fields.insert(
        "document_number".to_string(),
        m.get("document_number")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn fed_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `body.get("results", body.get("documents", [])) or []`: both
    // `.get`s always run; missing results → documents (missing → []);
    // present values (even falsy) pass through the `or []` fold.
    let raw: Value = match obj.get("results") {
        None => obj
            .get("documents")
            .cloned()
            .unwrap_or(Value::Array(Vec::new())),
        Some(v) => v.clone(),
    };
    let folded: Value = if is_truthy(&raw) {
        raw
    } else {
        Value::Array(Vec::new())
    };
    let hits: Vec<&Value> = subscript_hits(Some(&folded), max_results)?;
    let mut out = Vec::new();
    for d in hits {
        out.push(fed_doc_impl(d)?);
    }
    Ok(out)
}

pub fn fed_parse_fetch_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    fed_doc_impl(&body)
}

// ── BioRxiv ────────────────────────────────────────────────────

fn biorxiv_paper_impl(p: &Value, server: &str) -> Result<Value, String> {
    let m = match p {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(p))),
    };
    let doi = m
        .get("doi")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let url = if is_truthy(&doi) {
        Value::String(format!(
            "https://www.biorxiv.org/content/{0}",
            py_value_repr(&doi)
        ))
    } else {
        Value::String(String::new())
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("biorxiv".to_string()));
    rec.insert("id".to_string(), doi);
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        m.get("date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    let abstract_raw = m
        .get("abstract")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let stripped = strip_tags_impl(&abstract_raw)?;
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(stripped.as_str(), 240).to_string()),
    );
    rec.insert(
        "authors".to_string(),
        m.get("authors")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    // NB: flat `fields`, with the instance's server threaded through.
    let mut fields = serde_json::Map::new();
    fields.insert("server".to_string(), Value::String(server.to_string()));
    for key in ["category", "version", "type", "license"] {
        fields.insert(
            key.to_string(),
            m.get(key)
                .cloned()
                .unwrap_or(Value::String(String::new())),
        );
    }
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn biorxiv_parse_collection_impl(
    response_json: &str,
    max_results: i64,
    server: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("collection", [])`, then `papers[:max_results]`.
    let hits: Vec<&Value> = match obj.get("collection") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for p in hits {
        out.push(biorxiv_paper_impl(p, server)?);
    }
    Ok(out)
}

pub fn biorxiv_parse_fetch_impl(
    response_json: &str,
    server: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `papers = ...get("collection", [])`; `if not papers: return []`.
    let papers = match obj.get("collection") {
        None => return Ok(Vec::new()),
        Some(v) if !is_truthy(v) => return Ok(Vec::new()),
        Some(v) => v,
    };
    // `papers[0]`: list (non-empty — falsy caught above), str (char),
    // dict (KeyError 0), anything else TypeError.
    let first = match papers {
        Value::Array(a) => match a.first() {
            Some(v) => v,
            None => return Ok(Vec::new()),
        },
        Value::String(s) => match s.chars().next() {
            Some(_) => return Err(attr_error("str")),
            None => return Ok(Vec::new()),
        },
        Value::Object(_) => return Err("KeyError: 0".to_string()),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    Ok(vec![biorxiv_paper_impl(first, server)?])
}

// ── ChemRxiv ───────────────────────────────────────────────────

// ── ChemRxiv ───────────────────────────────────────────────────

/// One `", ".join` element: whether it survives an `if n` truthiness
/// filter, its rendered text, and its original type for the join
/// `TypeError` (which reports the *surviving* index).
/// Dicts read raw `name` (missing → ""); anything else renders via
/// `str()` — always a string, whose truthiness is the text's.
fn chemrxiv_part(v: &Value) -> (bool, String, &'static str) {
    match v {
        Value::Object(m) => match m.get("name") {
            None => (false, String::new(), "str"),
            Some(n) => (is_truthy(n), py_value_repr(n), json_type(n)),
        },
        _ => {
            let s = py_value_repr(v);
            (!s.is_empty(), s, "str")
        }
    }
}

fn chemrxiv_join(parts: Vec<(bool, String, &'static str)>, filter: bool) -> Result<String, String> {
    let kept: Vec<(String, &'static str)> = parts
        .into_iter()
        .filter(|(keep, _, _)| !filter || *keep)
        .map(|(_, text, t)| (text, t))
        .collect();
    let mut out: Vec<String> = Vec::with_capacity(kept.len());
    for (i, (text, t)) in kept.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        out.push(text.clone());
    }
    Ok(out.join(", "))
}

/// `", ".join(...)` over authors (truthiness-filtered) or topics
/// (unfiltered) list elements: lists item-wise, dicts key-wise
/// (keys render as themselves), strings char-wise, anything else
/// raises TypeError (not iterable).
fn chemrxiv_names_impl(items: &Value, filter: bool) -> Result<String, String> {
    match items {
        Value::Array(a) => chemrxiv_join(a.iter().map(chemrxiv_part).collect(), filter),
        Value::Object(m) => chemrxiv_join(
            m.keys()
                .map(|k| {
                    let s = k.clone();
                    (!s.is_empty(), s, "str")
                })
                .collect(),
            filter,
        ),
        Value::String(s) => chemrxiv_join(
            s.chars()
                .map(|c| {
                    let t = c.to_string();
                    (true, t, "str")
                })
                .collect(),
            filter,
        ),
        other => Err(type_error_not_iterable(json_type(other))),
    }
}

fn chemrxiv_item_impl(it: &Value) -> Result<Value, String> {
    let m = match it {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(it))),
    };
    // `authors = it.get("authors", [])`: missing → []; lists render
    // per item (dicts read `name`); anything else — even a dict or a
    // falsy value — renders whole as one `str()`.
    let authors = match m.get("authors") {
        None => String::new(),
        Some(Value::Array(a)) => {
            chemrxiv_join(a.iter().map(chemrxiv_part).collect(), true)?
        }
        Some(v) => py_value_repr(v),
    };
    // `it.get("url", f"...{it.get('id', '')}")`.
    let url = match m.get("url") {
        Some(v) => v.clone(),
        None => Value::String(format!(
            "https://chemrxiv.org/engage/chemrxiv/public-article-details/{0}",
            py_value_repr(m.get("id").unwrap_or(&Value::String(String::new())))
        )),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("chemrxiv".to_string()));
    rec.insert(
        "id".to_string(),
        Value::String(py_value_repr(
            m.get("id").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        m.get("published_on")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    let abstract_raw = m
        .get("abstract")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let stripped = strip_tags_impl(&abstract_raw)?;
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(stripped.as_str(), 240).to_string()),
    );
    rec.insert("authors".to_string(), Value::String(authors));
    // `"topics": ", ".join(... for t in it.get("topics", []) or [])`.
    let topics = match m.get("topics") {
        None => String::new(),
        Some(v) if !is_truthy(v) => String::new(),
        Some(v) => chemrxiv_names_impl(v, false)?,
    };
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "doi".to_string(),
        m.get("doi")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert("topics".to_string(), Value::String(topics));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn chemrxiv_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `body.get("data", []) if isinstance(body, dict) else body`,
    // then `items[:max_results]`.
    let hits: Vec<&Value> = match &body {
        Value::Object(m) => match m.get("data") {
            None => Vec::new(),
            Some(v) => subscript_hits(Some(v), max_results)?,
        },
        other => subscript_hits(Some(other), max_results)?,
    };
    let mut out = Vec::new();
    for it in hits {
        out.push(chemrxiv_item_impl(it)?);
    }
    Ok(out)
}

pub fn chemrxiv_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `item = body.get("data", body) if isinstance(body, dict) else
    // body`; falsy → []; lists index [0]; anything else rows as-is.
    let item: &Value = match &body {
        Value::Object(m) => m.get("data").unwrap_or(&body),
        _ => &body,
    };
    if !is_truthy(item) {
        return Ok(Vec::new());
    }
    match item {
        Value::Array(a) => match a.first() {
            Some(v) => Ok(vec![chemrxiv_item_impl(v)?]),
            None => Ok(Vec::new()),
        },
        _ => Ok(vec![chemrxiv_item_impl(item)?]),
    }
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn oldp_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    oldp_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

pub fn oldp_parse_case_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    oldp_case_row_impl(&body)
}

#[pyfunction]
pub fn oldp_parse_case(py: Python, response_json: &str) -> PyResult<String> {
    oldp_parse_case_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn oldp_parse_law(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    oldp_parse_law_impl(response_json, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn fed_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    fed_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn fed_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    fed_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, server = "biorxiv"))]
pub fn biorxiv_parse_collection(
    py: Python,
    response_json: &str,
    max_results: i64,
    server: &str,
) -> PyResult<String> {
    biorxiv_parse_collection_impl(response_json, max_results, server)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, server = "biorxiv"))]
pub fn biorxiv_parse_fetch(py: Python, response_json: &str, server: &str) -> PyResult<String> {
    biorxiv_parse_fetch_impl(response_json, server)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn chemrxiv_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    chemrxiv_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn chemrxiv_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    chemrxiv_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}
