//! Domain-adapter build/parse kernels: the pure parts of
//! `OpenMeteoAdapter`, `FrankfurterAdapter`, `YahooFinanceAdapter`,
//! `NvdAdapter`, `ZenodoAdapter`, `CourtListenerAdapter`,
//! `GovInfoAdapter`, `HudocAdapter`, `PatentsViewAdapter`,
//! `OldpAdapter`, `FederalRegisterAdapter`, `BioRxivAdapter`,
//! `ChemRxivAdapter`, `EurostatAdapter`, `CoinGeckoAdapter` and
//! `AlphaVantageAdapter`.
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

fn attr_error_attr(t: &str, attr: &str) -> String {
    format!("AttributeError: '{t}' object has no attribute '{attr}'")
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
    fn financial_shapes() {
        // Eurostat `int()` keys: underscores and signs parse, hex does not.
        assert_eq!(py_int("1_0"), Some(10));
        assert_eq!(py_int("  +12  "), Some(12));
        assert_eq!(py_int("0x1A"), None);
        assert_eq!(py_int("_1"), None);
        // Eurostat id joins are strict (non-string codes raise).
        let coords = vec![(Value::String("g".to_string()), Value::from(5), Value::Null)];
        assert_eq!(
            eurostat_row_impl("c", "L", &coords, &Value::Null).unwrap_err(),
            "TypeError: sequence item 0: expected str instance, int found"
        );
        // CoinGecko `.upper()` runs on the raw symbol value.
        let coin: Value =
            serde_json::from_str(r#"{"symbol": 5}"#).unwrap();
        assert_eq!(
            coingecko_search_row_impl(&coin).unwrap_err(),
            "AttributeError: 'int' object has no attribute 'upper'"
        );
        // AlphaVantage note rows take the whole-body raw Python-side;
        // the kernel marks them with a Null meta.
        let (rec, meta) =
            alphavantage_parse_fetch_impl(r#"{"notes": "slow"}"#, "IBM").unwrap();
        assert_eq!(rec["id"], "IBM");
        assert_eq!(rec["title"], "slow");
        assert!(meta.is_null());
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

    #[test]
    fn batch8_xml_shapes() {
        // xmlatom: local names ignore prefixes; tails invisible.
        let root = crate::xmlatom::parse_document(
            "<f xmlns:a='u'><a:x>y<b/>tail</a:x></f>").unwrap();
        let x = root.child("x").unwrap();
        assert_eq!(x.text, "y");
        // arxiv: doi/category fallback chains + append-then-break.
        let out = arxiv_parse_search_impl(
            "<feed><entry><id>http://arxiv.org/abs/1</id>\
             <link title='doi' href='https://doi.org/10.9/y'/>\
             <category term='t' scheme='http://arxiv.org/schemas/atom'/>\
             </entry></feed>", 0).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["doi"], "10.9/y");
        assert_eq!(out[0]["fields"]["arxiv"]["primary_category"], "t");
        // arxiv fetch: error record shape.
        let rec = arxiv_parse_fetch_impl(
            "<feed/>", "9", &Value::String("R".to_string())).unwrap();
        assert_eq!(rec["snippet"], "");
        assert_eq!(rec["url"], "R");
        // pubmed: char-sliced string idlists + direct-index errors.
        let out = pubmed_parse_search_impl(
            r#"{"esearchresult": {"idlist": "ab"}}"#, 5).unwrap();
        assert_eq!(out.len(), 2);
        assert!(pubmed_parse_search_impl("{}", 5).is_err());
        // pubmed fetch: None for article-less payloads.
        assert!(pubmed_parse_fetch_impl("<PubmedArticleSet/>", "1")
            .unwrap()
            .is_none());
    }

    #[test]
    fn batch7_misc_shapes() {
        // WorldBank: indicator fallback + recent join slice.
        let body = r#"[{"page": 1}, [{"indicator": {"id": "X", "value": "V"}, "date": "2023", "value": 5}]]"#;
        let out = worldbank_parse_fetch_impl(body, &Value::String("R".to_string()), "D/R").unwrap();
        assert_eq!(out["id"], "X");
        assert_eq!(out["snippet"], "1 observations; recent: 2023:5");
        // FRED CSV: header skipped, raw window kept.
        let out = fred_parse_csv_impl("DATE,VALUE\n2024-01-01,1.5\n", &Value::String("GDP".to_string())).unwrap();
        assert_eq!(out["id"], "GDP");
        assert_eq!(out["raw"], "DATE,VALUE\n2024-01-01,1.5");
        // GitHub: str() id + url chain.
        let body = r#"{"items": [{"id": 7, "html_url": null, "url": "U"}]}"#;
        let out = github_parse_search_impl(body, 5).unwrap();
        assert_eq!(out[0]["id"], "7");
        assert_eq!(out[0]["url"], "U");
        // Congress: em-dash snippet.
        let body = r#"{"results": [{"cgi_id": "C", "title": "Sen", "party": "D", "state": "CA", "district": "1"}]}"#;
        let out = congress_parse_search_impl(body, 5).unwrap();
        assert_eq!(out[0]["snippet"], "Sen D \u{2014} CA1");
        // NASA: fetch ignores hostile close_approach_data.
        let body = r#"{"a": 1, "close_approach_data": 5, "estimated_diameter": {"meters": {"estimated_diameter_max": 2}}}"#;
        assert!(nasa_parse_fetch_impl(body, &Value::String("R".to_string())).is_ok());
        // SWH: falsy url falls back to visits; sid shape.
        let out = swh_origin_row_impl(&serde_json::from_str(r#"{"visit_types": []}"#).unwrap(), "fb").unwrap();
        assert_eq!(out["url"], "https://archive.softwareheritage.org/browse/origin/?origin_url=fb");
        let out = swh_parse_fetch_sid_impl(r#"{"id": "s:1"}"#, "s:1").unwrap();
        assert_eq!(out["url"], "https://archive.softwareheritage.org/s:1");
        // Overpass: name-or-type:id title + 6-tag cap.
        let body = r#"{"elements": [{"type": "node", "id": 3, "tags": {"a": "1", "b": "2", "c": "3", "d": "4", "e": "5", "f": "6", "g": "7"}}]}"#;
        let out = overpass_parse_search_impl(body, 5).unwrap();
        assert_eq!(out[0]["title"], "node:3");
        assert_eq!(out[0]["snippet"], "a=1, b=2, c=3, d=4, e=5, f=6");
        // Census: header lowering + raw payload shape ("06" sits
        // under `st_ate`, so the state-keyed id folds to "").
        let body = r#"[["ST ATE"], ["06"]]"#;
        let out = census_parse_search_impl(body, "D", 5).unwrap();
        assert_eq!(out[0]["id"], "");
        assert!(out[0].get("raw").is_some());
    }

    #[test]
    fn batch6_scholarly_shapes() {
        // OpenAlex: author folding + url/doi asymmetry.
        let body = r#"{"results": [{"id": "W1", "title": "", "doi": null, "authorships": [{"author": {"display_name": "A."}}, {"author": {}}]}]}"#;
        let out = openalex_parse_search_impl(body, 5).unwrap();
        assert_eq!(out[0]["title"], "");
        assert_eq!(out[0]["url"], "W1");
        assert_eq!(out[0]["authors"], "A.");
        assert_eq!(out[0]["snippet"], "A.");
        // Crossref: title-first-char, date-parts head, abstract head.
        let body = r#"{"message": {"items": [{"DOI": "10.1/x", "title": ["Ab"], "URL": "U", "published": {"date-parts": [[2024]]}, "author": [{"family": "F"}], "abstract": "Abc"}]}}"#;
        let out = crossref_parse_search_impl(body, 5).unwrap();
        assert_eq!(out[0]["title"], "Ab");
        assert_eq!(out[0]["published"], serde_json::json!([2024]));
        assert_eq!(out[0]["snippet"], "Abc");
        // Crossref fetch: direct message indexing + DOI fallback.
        assert!(crossref_parse_fetch_impl("{}", &Value::Null).is_err());
        let out = crossref_parse_fetch_impl(
            r#"{"message": {}}"#, &Value::String("10.1/f".to_string())).unwrap();
        assert_eq!(out["id"], "10.1/f");
        // OpenLibrary: unconditional fetch URL vs guarded search URL.
        let body = r#"{"docs": [{"title": "T", "author": ["A."], "isbn": ["1"]}]}"#;
        let out = openlibrary_parse_search_impl(body, 5, "https://b").unwrap();
        assert_eq!(out[0]["url"], "");
        let out =
            openlibrary_parse_fetch_impl("{}", "/books/K", "https://b").unwrap();
        assert_eq!(out["url"], "https://b/books/K");
        // DOAJ: doi hunt skips non-doi identifiers, missing id reads None.
        let body = r#"{"results": [{"id": "r", "bibjson": {"identifier": [{"type": "issn", "id": "1"}, {"type": "doi"}]}}]}"#;
        let out = doaj_parse_search_impl(body, 5).unwrap();
        assert_eq!(out[0]["doi"], Value::Null);
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

// ── Eurostat ────────────────────────────────────────────────────

/// Mirror `int(s)` for JSON-stat flat keys: surrounding whitespace
/// stripped, optional sign, then digits (Unicode decimal) with single
/// inter-digit underscores. `None` on any other shape (the caller
/// skips the key, like the `except (TypeError, ValueError)`).
fn py_int(s: &str) -> Option<i128> {
    let t = py_strip(s);
    let (sign, digits) = match t.strip_prefix('+') {
        Some(rest) => (1i128, rest),
        None => match t.strip_prefix('-') {
            Some(rest) => (-1i128, rest),
            None => (1i128, t),
        },
    };
    if digits.is_empty() {
        return None;
    }
    let mut val: i128 = 0;
    let mut prev_underscore = true; // first char must be a digit
    for c in digits.chars() {
        if c == '_' {
            if prev_underscore {
                return None;
            }
            prev_underscore = true;
            continue;
        }
        match c.to_digit(10) {
            Some(d) => {
                val = val.checked_mul(10)?.checked_add(d as i128)?;
                prev_underscore = false;
            }
            None => return None,
        }
    }
    if prev_underscore {
        return None;
    }
    Some(sign * val)
}

/// Mirror `sorted(index, key=index.get)` over `{code: order}` pairs.
/// Uniform int/bool orders sort numerically, uniform strings
/// lexicographically; mixed or exotic orders raise TypeError (the
/// reported pair is first-seen order — an approximation for
/// pathological cubes, exact for every uniform one).
fn sorted_index_keys(index: &serde_json::Map<String, Value>) -> Result<Vec<String>, String> {
    let mut keys: Vec<&String> = index.keys().collect();
    let kind_of = |v: &Value| -> &'static str {
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
    };
    // All-bool/int lanes compare numerically (bool == 0/1, like Python).
    let all_num = index.values().all(|v| match v {
        Value::Bool(_) => true,
        Value::Number(n) => n.is_i64() || n.is_u64(),
        _ => false,
    });
    if all_num {
        keys.sort_by_key(|k| match &index[*k] {
            Value::Bool(b) => *b as i128,
            Value::Number(n) => n
                .as_u64()
                .map(|w| w as i128)
                .or(n.as_i64().map(|w| w as i128))
                .unwrap_or(0),
            _ => 0,
        });
        return Ok(keys.into_iter().cloned().collect());
    }
    // Float lanes: NaN never compares less (sorted() won't raise);
    // mixed int/float lanes compare numerically in Python too.
    let all_floaty = index.values().all(|v| {
        matches!(v, Value::Bool(_) | Value::Number(_))
            && !matches!(v, Value::Number(n) if !(n.is_i64() || n.is_u64() || n.is_f64()))
    });
    if all_floaty {
        let num = |v: &Value| -> f64 {
            match v {
                Value::Bool(b) => {
                    if *b {
                        1.0
                    } else {
                        0.0
                    }
                }
                Value::Number(n) => n.as_f64().unwrap_or(f64::NAN),
                _ => f64::NAN,
            }
        };
        keys.sort_by(|a, b| {
            num(&index[a.as_str()])
                .partial_cmp(&num(&index[b.as_str()]))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        return Ok(keys.into_iter().cloned().collect());
    }
    let all_str = index.values().all(|v| matches!(v, Value::String(_)));
    if all_str {
        keys.sort_by_key(|k| match &index[*k] {
            Value::String(s) => s.clone(),
            _ => String::new(),
        });
        return Ok(keys.into_iter().cloned().collect());
    }
    // Mixed lanes: simulate TimSort's binary-insertion comparisons in
    // encounter order (exact for short runs): insert each order value
    // into the sorted prefix, binary-searching the pivot against
    // midpoints; the first failing (pivot, midpoint) pair names the
    // `TypeError`. (Long-run galloping may probe differently —
    // JSON-stat orders are ints in practice; anything uniform above
    // is already exact.)
    let orders: Vec<&Value> = index.values().collect();
    if orders.len() <= 1 {
        // `sorted()` of ≤ 1 element never compares.
        return Ok(index.keys().cloned().collect());
    }
    let kind_of_v = |v: &Value| -> &'static str { kind_of(v) };
    let mut seq: Vec<usize> = vec![0];
    for i in 1..orders.len() {
        let mut lo = 0usize;
        let mut hi = seq.len();
        while lo < hi {
            let mid = (lo + hi) / 2;
            let (pk, mk) = (kind_of_v(orders[i]), kind_of_v(orders[seq[mid]]));
            if pk != mk {
                // Kinds differ: Python raises comparing pivot vs midpoint.
                // (Equal-kind but unorderable values — e.g. dicts — cannot
                // occur here: dict/list values are distinct kinds... except
                // two dicts! `{..} < {..}` raises TypeError too.)
                return Err(format!(
                    "TypeError: '<' not supported between instances of '{pk}' and '{mk}'"
                ));
            }
            // Same kind: probe numerically/textually to steer the search.
            // (Only the search path matters, never the outcome value.)
            let less = match (orders[i], orders[seq[mid]]) {
                (Value::Bool(a), Value::Bool(b)) => a < b,
                (Value::Bool(a), Value::Number(b)) => {
                    (*a as i128) < b.as_i64().unwrap_or(0) as i128
                }
                (Value::Number(a), Value::Bool(b)) => {
                    a.as_i64().unwrap_or(0) as i128 > *b as i128
                }
                (Value::Number(a), Value::Number(b)) => {
                    match (a.as_i64(), b.as_i64()) {
                        (Some(x), Some(y)) => x < y,
                        _ => {
                            a.as_f64().unwrap_or(f64::NAN)
                                < b.as_f64().unwrap_or(f64::NAN)
                        }
                    }
                }
                (Value::String(a), Value::String(b)) => a < b,
                _ => {
                    // Same-kind unorderables (two dicts, two lists):
                    // Python raises comparing them.
                    let k = kind_of_v(orders[i]);
                    return Err(format!(
                        "TypeError: '<' not supported between instances of '{k}' and '{k}'"
                    ));
                }
            };
            if less {
                hi = mid;
            } else {
                lo = mid + 1;
            }
        }
        seq.insert(lo, i);
    }
    // All comparisons passed (uniform enough): emit insertion order.
    // (Unreachable for truly mixed lanes — they always fail above.)
    let keys: Vec<&String> = index.keys().collect();
    Ok(keys.into_iter().cloned().collect())
}

/// One `(code, label)` coordinate for dimension `i`. Empty strides
/// yield `entries[0]` (or empty); over-long `ids` raise `IndexError`
/// on `strides[i]`; otherwise the `idx < len` guard decides between
/// indexing and `("", "")` — float indices raise `TypeError` only
/// when the guard passes.
fn eurostat_cell(entries: &[(Value, Value)], idx: Idx) -> Result<(Value, Value), String> {
    let blank = || {
        (
            Value::String(String::new()),
            Value::String(String::new()),
        )
    };
    match idx {
        Idx::Entries0 => Ok(if entries.is_empty() {
            blank()
        } else {
            entries[0].clone()
        }),
        Idx::Oob => Err("IndexError: list index out of range".to_string()),
        Idx::I(n) => {
            if n < 0 || n >= entries.len() as i128 {
                Ok(blank())
            } else {
                Ok(entries[n as usize].clone())
            }
        }
        Idx::F(f) => {
            if f < entries.len() as f64 {
                Err("TypeError: list indices must be integers or slices, not float"
                    .to_string())
            } else {
                Ok(blank())
            }
        }
    }
}

/// A dimension index: direct entry 0 (empty strides), out-of-bounds,
/// integer, or float.
enum Idx {
    Entries0,
    Oob,
    I(i128),
    F(f64),
}

/// Mirror `EurostatAdapter._unpack`: JSON-stat cube → `[(coords, value)]`,
/// capped *after* appending (a non-positive limit still yields the first
/// cell when values exist). `coords` are `(dim, code, label)` triples
/// with raw JSON values (dims may be non-strings in hostile cubes).
fn eurostat_unpack_impl(
    data: &serde_json::Map<String, Value>,
    limit: i64,
) -> Result<Vec<(Vec<(Value, Value, Value)>, Value)>, String> {
    // `ids` iterates (lists item-wise, dicts key-wise, strings
    // char-wise); anything else raises TypeError.
    let mut dims: Vec<Value> = Vec::new();
    match data.get("id") {
        None => {}
        Some(Value::Array(a)) => dims.extend(a.iter().cloned()),
        Some(Value::Object(m)) => {
            dims.extend(m.keys().map(|k| Value::String(k.clone())))
        }
        Some(Value::String(s)) => {
            dims.extend(s.chars().map(|c| Value::String(c.to_string())))
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    }
    // `sizes` reverses (lists, strings and — reversed-iterator — dicts
    // iterate keys); anything else raises TypeError. Non-empty dicts
    // always raise in the stride loop below (`max(1, key)` on a string
    // key), so materializing keys is exact; `sizes[i]` for them is
    // unreachable and empty dicts take every `else 1` branch.
    let sizes_seq: Vec<Value> = match data.get("size") {
        None => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(st)) => {
            st.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(m)) => {
            m.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => {
            return Err(format!(
                "TypeError: '{}' object is not reversible",
                json_type(other)
            ));
        }
    };
    // `max(1, size)` per element (raises on exotic elements before any
    // cell is read). Float maxima above 1 make their dimension's index
    // a float (`nan`/fractions ≤ 1 collapse back to int 1, like `max`).
    enum Max {
        I(i128),
        F(f64),
    }
    let max_1 = |size: &Value| -> Result<Max, String> {
        match size {
            Value::Bool(b) => Ok(Max::I((*b as i128).max(1))),
            Value::Number(n) => {
                if let Some(w) = n
                    .as_u64()
                    .map(|v| v as i128)
                    .or(n.as_i64().map(|v| v as i128))
                {
                    Ok(Max::I(w.max(1)))
                } else if let Some(f) = n.as_f64() {
                    if f > 1.0 {
                        Ok(Max::F(f))
                    } else {
                        Ok(Max::I(1))
                    }
                } else {
                    Err(format!(
                        "TypeError: '>' not supported between instances of 'str' and 'int'"
                    ))
                }
            }
            other => Err(format!(
                "TypeError: '>' not supported between instances of '{}' and 'int'",
                json_type(other)
            )),
        }
    };
    let maxima: Vec<Max> = sizes_seq
        .iter()
        .map(max_1)
        .collect::<Result<Vec<Max>, String>>()?;
    // Per-dimension index floatness: `idx[i]` is a float iff the stride
    // (any later maximum) or its own maximum is a float.
    // `dimensions`: `.get("dimension", {}) or {}` — missing/falsy →
    // none (empty tables); unhashable dims raise even against folded
    // empties (the `.get` still runs on `{}`); truthy non-dicts raise
    // on `.get(dim)`.
    let dimensions: Option<&Value> = match data.get("dimension") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(v) => Some(v),
    };
    // (dim, entries) with dims compared by JSON equality.
    let mut tables: Vec<(Value, Vec<(Value, Value)>)> = Vec::new();
    for dim in dims.iter() {
        // Hashability first: unhashable dims raise regardless of what
        // `dimensions` folded to.
        match dim {
            Value::Array(_) => {
                return Err("TypeError: unhashable type: 'list'".to_string());
            }
            Value::Object(_) => {
                return Err("TypeError: unhashable type: 'dict'".to_string());
            }
            _ => {}
        }
        // `dimensions.get(dim, {}) or {}` then `.get("category", {}) or {}`.
        let dim_obj: Option<&serde_json::Map<String, Value>> = match dimensions {
            None => None,
            Some(Value::Object(dm)) => match dim {
                Value::String(s) => match dm.get(s.as_str()) {
                    None => None,
                    Some(v) if !is_truthy(v) => None,
                    Some(Value::Object(cm)) => Some(cm),
                    Some(other) => return Err(attr_error(json_type(other))),
                },
                // Hashable scalars miss string-keyed maps.
                _ => None,
            },
            Some(other) => return Err(attr_error(json_type(other))),
        };
        let cat: Option<&serde_json::Map<String, Value>> = match dim_obj {
            None => None,
            Some(cm) => match cm.get("category") {
                None => None,
                Some(v) if !is_truthy(v) => None,
                Some(Value::Object(tm)) => Some(tm),
                Some(other) => return Err(attr_error(json_type(other))),
            },
        };
        // `index` with its `or {}` fold; labels resolve per code below
        // so empty indexes never touch them.
        let index_map: Option<&serde_json::Map<String, Value>> = match cat {
            None => None,
            Some(tm) => match tm.get("index") {
                None => None,
                Some(v) if !is_truthy(v) => None,
                Some(Value::Object(im)) => Some(im),
                Some(other) => return Err(attr_error(json_type(other))),
            },
        };
        let mut entries: Vec<(Value, Value)> = Vec::new();
        if let Some(im) = index_map {
            for code in sorted_index_keys(im)? {
                let label = match cat {
                    None => Value::String(code.clone()),
                    Some(tm) => match tm.get("label") {
                        None => Value::String(code.clone()),
                        Some(v) if !is_truthy(v) => Value::String(code.clone()),
                        Some(Value::Object(lm)) => lm
                            .get(code.as_str())
                            .cloned()
                            .unwrap_or(Value::String(code.clone())),
                        Some(other) => return Err(attr_error(json_type(other))),
                    },
                };
                entries.push((Value::String(code), label));
            }
        }
        tables.push((dim.clone(), entries));
    }
    // `values = data.get("value", {}) or {}`; non-dict truthy values
    // raise on `.items()`. Keys parse via `int()` (failures skip).
    let values: Option<&serde_json::Map<String, Value>> = match data.get("value") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(vm)) => Some(vm),
        // `values.items()`: the attribute is `items`, not `get`.
        Some(other) => return Err(attr_error_attr(json_type(other), "items")),
    };
    // Cells in `values` insertion order, capped after appending (a
    // non-positive limit still yields the first cell when values exist).
    let mut out: Vec<(Vec<(Value, Value, Value)>, Value)> = Vec::new();
    if let Some(vm) = values {
        for (flat, val) in vm.iter() {
            let pos = match py_int(flat.as_str()) {
                Some(p) => p,
                None => continue,
            };
            let mut coords: Vec<(Value, Value, Value)> = Vec::new();
            for (i, dim) in dims.iter().enumerate() {
                let entries: &Vec<(Value, Value)> = &tables[i].1;
                // Empty strides → entry 0; over-long ids → IndexError.
                let idx = if sizes_seq.is_empty() {
                    Idx::Entries0
                } else if i >= sizes_seq.len() {
                    Idx::Oob
                } else {
                    // stride = product of later int maxima; any later
                    // float (or a float own-maximum) makes idx a float.
                    let mut stride_f = 1.0f64;
                    let mut stride_i: i128 = 1;
                    let mut lane_float = false;
                    for m in maxima[i + 1..].iter() {
                        match m {
                            Max::I(w) => {
                                stride_i = stride_i.saturating_mul(*w);
                                stride_f *= *w as f64;
                            }
                            Max::F(f) => {
                                lane_float = true;
                                stride_f *= *f;
                            }
                        }
                    }
                    match &maxima[i] {
                        Max::F(f) => {
                            let q = ((pos as f64) / stride_f).floor();
                            Idx::F(q - (q / *f).floor() * *f)
                        }
                        Max::I(w) => {
                            if lane_float {
                                let q = ((pos as f64) / stride_f).floor();
                                let m_f = *w as f64;
                                Idx::F(q - (q / m_f).floor() * m_f)
                            } else {
                                Idx::I(pos.div_euclid(stride_i).rem_euclid(*w))
                            }
                        }
                    }
                };
                let (code, label) = eurostat_cell(entries, idx)?;
                coords.push((dim.clone(), code, label));
            }
            out.push((coords, val.clone()));
            if out.len() as i64 >= limit {
                break;
            }
        }
    }
    Ok(out)
}


/// One Eurostat record triple: the display `record` (without `fields`
/// dims or `raw` — the wrapper rebuilds both with native JSON types so
/// non-string dims survive exactly), the native `dims` pairs, and the
/// `raw` payload.
fn eurostat_row_impl(
    code: &str,
    label_s: &str,
    coords: &[(Value, Value, Value)],
    val: &Value,
) -> Result<Value, String> {
    // `coord_txt = " · ".join(f"{c[2] or c[1]}" ...)`.
    let coord_txt = coords
        .iter()
        .map(|(_, c, l)| {
            if is_truthy(l) {
                py_value_repr(l)
            } else {
                py_value_repr(c)
            }
        })
        .collect::<Vec<_>>()
        .join(" · ");
    let mut id_parts: Vec<(String, &'static str)> = Vec::new();
    let mut dims_pairs: Vec<Value> = Vec::new();
    for (d, c, _) in coords.iter() {
        dims_pairs.push(Value::Array(vec![d.clone(), c.clone()]));
        // `"/".join(...)` over raw codes: non-strings raise with
        // their position (unlike the f-string fields around it).
        id_parts.push((py_value_repr(c), json_type(c)));
    }
    let mut id_rendered: Vec<String> = Vec::with_capacity(id_parts.len());
    for (i, (text, t)) in id_parts.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        id_rendered.push(text.clone());
    }
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("eurostat".to_string()));
    rec.insert(
        "id".to_string(),
        Value::String(format!("{code}:") + &id_rendered.join("/")),
    );
    rec.insert(
        "title".to_string(),
        Value::String(format!("{label_s}: {coord_txt} = {0}", py_value_repr(val))),
    );
    rec.insert("url".to_string(), Value::String(String::new()));
    rec.insert(
        "snippet".to_string(),
        Value::String(format!("{coord_txt} → {0}", py_value_repr(val))),
    );
    // Placeholder fields (dataset + value only); the wrapper inserts
    // native dims between them in coordinate order.
    let mut fields = serde_json::Map::new();
    fields.insert("dataset".to_string(), Value::String(code.to_string()));
    fields.insert("value".to_string(), val.clone());
    rec.insert("fields".to_string(), Value::Object(fields));
    let mut payload = serde_json::Map::new();
    payload.insert("dataset".to_string(), Value::String(code.to_string()));
    payload.insert(
        "coords".to_string(),
        Value::Array(
            coords
                .iter()
                .map(|(d, c, l)| Value::Array(vec![d.clone(), c.clone(), l.clone()]))
                .collect(),
        ),
    );
    payload.insert("value".to_string(), val.clone());
    let mut triple = serde_json::Map::new();
    triple.insert("record".to_string(), Value::Object(rec));
    triple.insert("dims".to_string(), Value::Array(dims_pairs));
    triple.insert("payload".to_string(), Value::Object(payload));
    Ok(Value::Object(triple))
}

pub fn eurostat_parse_cells_impl(
    response_json: &str,
    code: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `label = data.get("label", code)`.
    let label_s = match obj.get("label") {
        None => code.to_string(),
        Some(v) => py_value_repr(v),
    };
    let mut out = Vec::new();
    for (coords, val) in eurostat_unpack_impl(obj, max_results)? {
        out.push(eurostat_row_impl(code, &label_s, &coords, &val)?);
    }
    Ok(out)
}

// ── CoinGecko ───────────────────────────────────────────────────

fn coingecko_search_row_impl(coin: &Value) -> Result<Value, String> {
    let m = match coin {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(coin))),
    };
    let cid = m
        .get("id")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `coin.get('symbol', '').upper()`: missing → ""; strings upper;
    // anything else raises AttributeError (`.upper` on the raw value).
    let symbol_upper = match m.get("symbol") {
        None => String::new(),
        Some(Value::String(s)) => s.to_uppercase(),
        Some(v) => {
            return Err(format!(
                "AttributeError: '{}' object has no attribute 'upper'",
                json_type(v)
            ));
        }
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("coingecko".to_string()),
    );
    rec.insert("id".to_string(), cid.clone());
    rec.insert(
        "title".to_string(),
        Value::String(format!(
            "{0} ({symbol_upper})",
            py_value_repr(m.get("name").unwrap_or(&Value::String(String::new())))
        )),
    );
    rec.insert(
        "url".to_string(),
        if is_truthy(&cid) {
            Value::String(format!(
                "https://www.coingecko.com/en/coins/{0}",
                py_value_repr(&cid)
            ))
        } else {
            Value::String(String::new())
        },
    );
    // Snippet default `'?'` (fields below default `""` instead).
    rec.insert(
        "snippet".to_string(),
        Value::String(format!(
            "market-cap rank {0}",
            match m.get("market_cap_rank") {
                None => "?".to_string(),
                Some(v) => py_value_repr(v),
            }
        )),
    );
    let mut fields = serde_json::Map::new();
    fields.insert(
        "symbol".to_string(),
        m.get("symbol")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "market_cap_rank".to_string(),
        m.get("market_cap_rank")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn coingecko_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("coins", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("coins") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for coin in hits {
        out.push(coingecko_search_row_impl(coin)?);
    }
    Ok(out)
}

pub fn coingecko_parse_markets_impl(
    response_json: &str,
    cid: &str,
) -> Result<Option<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `rows = resp.json()` — top-level! Falsy → []; truthy non-lists
    // index (`[0]`: str → char (whose `.get` raises), dict →
    // KeyError 0, anything else TypeError).
    if !is_truthy(&body) {
        return Ok(None);
    }
    let first = match &body {
        Value::Array(a) => match a.first() {
            Some(v) => v,
            None => return Ok(None),
        },
        Value::String(s) => match s.chars().next() {
            Some(_) => return Err(attr_error("str")),
            None => return Ok(None),
        },
        Value::Object(_) => return Err("KeyError: 0".to_string()),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let m = match first {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(first))),
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("coingecko".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("id").cloned().unwrap_or(Value::String(cid.to_string())),
    );
    rec.insert(
        "title".to_string(),
        Value::String(format!(
            "{0} ${1}",
            py_value_repr(m.get("name").unwrap_or(&Value::String(cid.to_string()))),
            py_value_repr(
                m.get("current_price")
                    .unwrap_or(&Value::String(String::new()))
            )
        )),
    );
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://www.coingecko.com/en/coins/{0}",
            py_value_repr(m.get("id").unwrap_or(&Value::String(cid.to_string())))
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(format!(
            "${0} (24h {1}%), mcap ${2}",
            py_value_repr(
                m.get("current_price")
                    .unwrap_or(&Value::String(String::new()))
            ),
            py_value_repr(
                m.get("price_change_percentage_24h")
                    .unwrap_or(&Value::String(String::new()))
            ),
            py_value_repr(
                m.get("market_cap").unwrap_or(&Value::String(String::new()))
            )
        )),
    );
    let mut fields = serde_json::Map::new();
    for key in [
        "symbol",
        "current_price",
        "market_cap",
        "price_change_percentage_24h",
    ] {
        let field = match key {
            "symbol" => "symbol",
            "current_price" => "current_price_usd",
            "market_cap" => "market_cap_usd",
            "price_change_percentage_24h" => "change_24h_pct",
            _ => key,
        };
        fields.insert(
            field.to_string(),
            m.get(key)
                .cloned()
                .unwrap_or(Value::String(String::new())),
        );
    }
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Some(Value::Object(rec)))
}

// ── AlphaVantage ────────────────────────────────────────────────

fn alphavantage_note_row(note: String, title: Value) -> Value {
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("alphavantage".to_string()),
    );
    rec.insert("id".to_string(), Value::String(String::new()));
    rec.insert("title".to_string(), title);
    rec.insert("url".to_string(), Value::String(String::new()));
    rec.insert("snippet".to_string(), Value::String(note));
    rec.insert(
        "fields".to_string(),
        Value::Object(serde_json::Map::new()),
    );
    Value::Object(rec)
}

fn alphavantage_search_row_impl(r: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    let symbol = m
        .get("1. symbol")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("alphavantage".to_string()),
    );
    rec.insert("id".to_string(), symbol.clone());
    rec.insert(
        "title".to_string(),
        m.get("2. name").cloned().unwrap_or(symbol.clone()),
    );
    rec.insert("url".to_string(), Value::String(String::new()));
    rec.insert(
        "snippet".to_string(),
        Value::String(format!(
            "{0} — {1} {2}",
            py_value_repr(m.get("2. name").unwrap_or(&Value::String(String::new()))),
            py_value_repr(m.get("3. type").unwrap_or(&Value::String(String::new()))),
            py_value_repr(m.get("4. region").unwrap_or(&Value::String(String::new())))
        )),
    );
    let mut fields = serde_json::Map::new();
    fields.insert(
        "instrument_type".to_string(),
        m.get("3. type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert("ticker".to_string(), symbol);
    fields.insert(
        "currency".to_string(),
        m.get("8. currency")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "match_score".to_string(),
        m.get("9. matchScore")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn alphavantage_parse_search_impl(
    response_json: &str,
    query: &Value,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `rows = body.get("bestMatches", [])`; falsy → the note row
    // (`raw` re-attached Python-side from the whole body).
    let rows = match obj.get("bestMatches") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(v) => Some(v),
    };
    if rows.is_none() {
        let note = match obj.get("Note") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => match obj.get("Information") {
                Some(v) if is_truthy(v) => v.clone(),
                _ => match obj.get("notes") {
                    Some(v) if is_truthy(v) => v.clone(),
                    _ => match obj.get("information") {
                        Some(v) if is_truthy(v) => v.clone(),
                        _ => Value::String(String::new()),
                    },
                },
            },
        };
        // `title: note or query` — raw values on both sides.
        let title = if is_truthy(&note) {
            note.clone()
        } else {
            query.clone()
        };
        return Ok(vec![alphavantage_note_row(py_value_repr(&note), title)]);
    }
    let hits: Vec<&Value> = subscript_hits(rows, max_results)?;
    let mut out = Vec::new();
    for r in hits {
        out.push(alphavantage_search_row_impl(r)?);
    }
    Ok(out)
}

pub fn alphavantage_parse_fetch_impl(
    response_json: &str,
    rid: &str,
) -> Result<(Value, Value), String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `ts = body.get("Time Series (Daily)")` (no default); falsy →
    // the note row; truthy non-dicts raise on `.items()`.
    let ts = match obj.get("Time Series (Daily)") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(v) => Some(v),
    };
    let ts_map: Option<&serde_json::Map<String, Value>> = match ts {
        None => None,
        Some(Value::Object(tm)) => Some(tm),
        // `ts.items()`: the attribute is `items`, not `get`.
        Some(other) => return Err(attr_error_attr(json_type(other), "items")),
    };
    if ts_map.is_none() {
        let note = match obj.get("notes") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => match obj.get("information") {
                Some(v) if is_truthy(v) => v.clone(),
                _ => Value::String(String::new()),
            },
        };
        let note_s = py_value_repr(&note);
        // `title: note or str(record_id)`; `raw` re-attached
        // Python-side from the whole body. No OHLCV payload here:
        // signal with Null meta.
        let title = if is_truthy(&note) {
            note.clone()
        } else {
            Value::String(rid.to_string())
        };
        let mut rec = alphavantage_note_row(note_s, title);
        if let Value::Object(rm) = &mut rec {
            rm.insert("id".to_string(), Value::String(rid.to_string()));
        }
        return Ok((rec, Value::Null));
    }
    let ts_map = ts_map.unwrap();
    // `first_date, ohlcv = next(iter(ts.items()))` — non-empty (only
    // dicts survive the truthiness fold above).
    let (first_date, ohlcv) = match ts_map.iter().next() {
        Some((k, v)) => (k.clone(), v.clone()),
        None => {
            return Err("IndexError: list index out of range".to_string());
        }
    };
    // `meta = body.get("Meta Data", {})` — missing → {}; present
    // values (even falsy) read as-is; non-dicts raise on `.get`.
    let meta: Option<&serde_json::Map<String, Value>> = match obj.get("Meta Data") {
        None => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let meta_get = |key: &str, default: Value| -> Value {
        match meta {
            None => default,
            Some(mm) => mm.get(key).cloned().unwrap_or(default),
        }
    };
    let rid_s = Value::String(rid.to_string());
    let id = meta_get("2. symbol", rid_s.clone());
    let title_sym = meta_get("1. symbol", rid_s.clone());
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("alphavantage".to_string()),
    );
    rec.insert("id".to_string(), id);
    rec.insert(
        "title".to_string(),
        Value::String(format!("{0} daily close", py_value_repr(&title_sym))),
    );
    rec.insert("url".to_string(), Value::String(String::new()));
    // `ohlcv.get(...)` needs a dict (AttributeError otherwise).
    let ohlcv_map = match &ohlcv {
        Value::Object(om) => om,
        _ => return Err(attr_error(json_type(&ohlcv))),
    };
    let ohlc = |key: &str| -> Value {
        ohlcv_map
            .get(key)
            .cloned()
            .unwrap_or(Value::String(String::new()))
    };
    rec.insert(
        "snippet".to_string(),
        Value::String(format!(
            "latest {first_date}: open {0}, close {1}",
            py_value_repr(&ohlc("1. open")),
            py_value_repr(&ohlc("4. close"))
        )),
    );
    let mut fields = serde_json::Map::new();
    fields.insert("symbol".to_string(), meta_get("2. symbol", rid_s.clone()));
    fields.insert(
        "last_refreshed".to_string(),
        meta_get("4. last refreshed", Value::String(String::new())),
    );
    fields.insert("open".to_string(), ohlc("1. open"));
    fields.insert("high".to_string(), ohlc("2. high"));
    fields.insert("low".to_string(), ohlc("3. low"));
    fields.insert("close".to_string(), ohlc("4. close"));
    fields.insert("volume".to_string(), ohlc("5. volume"));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok((Value::Object(rec), ohlcv))
}

#[pyfunction]
#[pyo3(signature = (response_json, code, max_results = 5))]
pub fn eurostat_parse_cells(
    py: Python,
    response_json: &str,
    code: &str,
    max_results: i64,
) -> PyResult<String> {
    eurostat_parse_cells_impl(response_json, code, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn coingecko_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    coingecko_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn coingecko_parse_markets(py: Python, response_json: &str, cid: &str) -> PyResult<String> {
    coingecko_parse_markets_impl(response_json, cid)
        .and_then(|v| match v {
            Some(rec) => serde_json::to_string(&rec).map_err(|e| e.to_string()),
            None => Ok("null".to_string()),
        })
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, query_json = "null", max_results = 5))]
pub fn alphavantage_parse_search(
    py: Python,
    response_json: &str,
    query_json: &str,
    max_results: i64,
) -> PyResult<String> {
    let query: Value =
        serde_json::from_str(query_json).unwrap_or(Value::Null);
    alphavantage_parse_search_impl(response_json, &query, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn alphavantage_parse_fetch(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    alphavantage_parse_fetch_impl(response_json, rid)
        .and_then(|(rec, meta)| {
            let mut both = serde_json::Map::new();
            both.insert("record".to_string(), rec);
            both.insert("meta".to_string(), meta);
            serde_json::to_string(&Value::Object(both)).map_err(|e| e.to_string())
        })
        .map_err(|e| to_py_err(py, e))
}

// ── OpenAlex ────────────────────────────────────────────────────

/// `a.get("author", {}).get("display_name", "")` for one authorship:
/// `a` must be a dict; `author` missing → ""; present values (even
/// falsy) read as-is; non-dicts raise on the inner `.get`.
fn openalex_author_name(a: &Value) -> Result<Value, String> {
    let m = match a {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(a))),
    };
    Ok(match m.get("author") {
        None => Value::String(String::new()),
        Some(Value::Object(am)) => am
            .get("display_name")
            .cloned()
            .unwrap_or(Value::String(String::new())),
        Some(other) => return Err(attr_error(json_type(other))),
    })
}

fn openalex_work_impl(w: &Value, full: bool) -> Result<Value, String> {
    let m = match w {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(w))),
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("openalex".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("id").cloned().unwrap_or(Value::String(String::new())),
    );
    // `w.get("title") or ""`: falsy titles fold to "".
    rec.insert(
        "title".to_string(),
        match m.get("title") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => Value::String(String::new()),
        },
    );
    // `w.get("doi") or w.get("id")`: no defaults — both may be None.
    rec.insert(
        "url".to_string(),
        match m.get("doi") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => m.get("id").cloned().unwrap_or(Value::Null),
        },
    );
    rec.insert(
        "doi".to_string(),
        m.get("doi").cloned().unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "published".to_string(),
        m.get("publication_date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    if full {
        // Authors render with the *filtered* join index on TypeError.
        // `w.get("authorships", [])`: missing → []; lists item-wise;
        // dicts/strings iterate (keys/chars) and fail per item;
        // anything else raises TypeError.
        let raw_authors: Vec<Value> = match m.get("authorships") {
            None => Vec::new(),
            Some(Value::Array(a)) => a
                .iter()
                .map(openalex_author_name)
                .collect::<Result<Vec<Value>, String>>()?,
            Some(Value::String(s)) => s
                .chars()
                .map(|c| openalex_author_name(&Value::String(c.to_string())))
                .collect::<Result<Vec<Value>, String>>()?,
            Some(Value::Object(mm)) => mm
                .keys()
                .map(|k| openalex_author_name(&Value::String(k.clone())))
                .collect::<Result<Vec<Value>, String>>()?,
            Some(other) => return Err(type_error_not_iterable(json_type(other))),
        };
        let mut kept: Vec<(String, &'static str)> = Vec::new();
        for name in raw_authors.iter() {
            if !is_truthy(name) {
                continue;
            }
            kept.push((py_value_repr(name), json_type(name)));
        }
        let mut authors: Vec<String> = Vec::with_capacity(kept.len());
        for (i, (text, t)) in kept.iter().enumerate() {
            if *t != "str" {
                return Err(sequence_item_error(i, t));
            }
            authors.push(text.clone());
        }
        let joined = authors.join(", ");
        rec.insert("authors".to_string(), Value::String(joined.clone()));
        rec.insert(
            "citations".to_string(),
            m.get("cited_by_count").cloned().unwrap_or(Value::from(0)),
        );
        rec.insert("snippet".to_string(), Value::String(joined));
    }
    Ok(Value::Object(rec))
}

pub fn openalex_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for w in hits {
        out.push(openalex_work_impl(w, true)?);
    }
    Ok(out)
}

pub fn openalex_parse_fetch_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    openalex_work_impl(&body, false)
}

// ── Crossref ────────────────────────────────────────────────────

/// `(w.get("title") or [""])[0]`: falsy titles fold to `[""]`;
/// truthy lists index (empty impossible — falsy caught); truthy
/// strings yield their first char; anything else raises TypeError.
fn crossref_title(title: Option<&Value>) -> Result<Value, String> {
    let t = match title {
        None => return Ok(Value::String(String::new())),
        Some(v) if !is_truthy(v) => return Ok(Value::String(String::new())),
        Some(v) => v,
    };
    match t {
        Value::Array(a) => match a.first() {
            Some(v) => Ok(v.clone()),
            None => Ok(Value::String(String::new())),
        },
        Value::String(s) => Ok(Value::String(
            s.chars().next().map(|c| c.to_string()).unwrap_or_default(),
        )),
        other => Err(type_error_not_subscriptable(json_type(other))),
    }
}

/// `(w.get("published", {}) or {}).get("date-parts", [[""]])[0]`:
/// missing/falsy published folds to `{}`; truthy non-dicts raise on
/// `.get`; the date-parts default applies only when missing.
fn crossref_published(published: Option<&Value>) -> Result<Value, String> {
    let m = match published {
        None => return Ok(Value::Array(vec![Value::String(String::new())])),
        Some(v) if !is_truthy(v) => {
            return Ok(Value::Array(vec![Value::String(String::new())]))
        }
        Some(Value::Object(m)) => m,
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let parts = match m.get("date-parts") {
        None => Value::Array(vec![Value::Array(vec![Value::String(String::new())])]),
        Some(v) => v.clone(),
    };
    // `[0]`: lists (empty → IndexError), strings (chars; empty →
    // IndexError), dicts (KeyError 0), anything else TypeError.
    match &parts {
        Value::Array(a) => match a.first() {
            Some(v) => Ok(v.clone()),
            None => Err("IndexError: list index out of range".to_string()),
        },
        Value::String(s) => match s.chars().next() {
            Some(c) => Ok(Value::String(c.to_string())),
            None => Err("IndexError: string index out of range".to_string()),
        },
        Value::Object(_) => Err("KeyError: 0".to_string()),
        other => Err(type_error_not_subscriptable(json_type(other))),
    }
}

/// `[a.get("family", a.get("name", "")) for a in ...]`: `a` must be a
/// dict; the `name` default applies only when `family` is missing.
fn crossref_author_name(a: &Value) -> Result<Value, String> {
    let m = match a {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(a))),
    };
    Ok(match m.get("family") {
        Some(v) => v.clone(),
        None => m
            .get("name")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    })
}

fn crossref_work_impl(w: &Value, fallback_id: &Value) -> Result<Value, String> {
    let m = match w {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(w))),
    };
    // Evaluation order matches the original: title, then the author
    // list, then published, then the abstract — an early field's
    // error precedes any later field's.
    let title = crossref_title(m.get("title"))?;
    // `w.get("author", [])`: missing → []; present values iterate
    // (lists item-wise, dicts key-wise, strings char-wise — failing
    // per item); anything else raises TypeError.
    let author_items: Vec<Value> = match m.get("author") {
        None => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut raw_names: Vec<Value> = Vec::with_capacity(author_items.len());
    for a in author_items.iter() {
        raw_names.push(crossref_author_name(a)?);
    }
    // The author *join* runs after `published` in the original dict
    // literal, so it is deferred until then.
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("crossref".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("DOI").cloned().unwrap_or(fallback_id.clone()),
    );
    rec.insert("title".to_string(), title);
    // `w.get("URL")`: no default — missing reads None.
    rec.insert(
        "url".to_string(),
        m.get("URL").cloned().unwrap_or(Value::Null),
    );
    rec.insert(
        "doi".to_string(),
        m.get("DOI")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("published".to_string(), crossref_published(m.get("published"))?);
    // `", ".join(a for a in authors if a)`: falsy dropped, then
    // strict (runs after `published` in the dict literal).
    let mut kept: Vec<(String, &'static str)> = Vec::new();
    for name in raw_names.iter() {
        if !is_truthy(name) {
            continue;
        }
        kept.push((py_value_repr(name), json_type(name)));
    }
    let mut authors: Vec<String> = Vec::with_capacity(kept.len());
    for (i, (text, t)) in kept.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        authors.push(text.clone());
    }
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    // `(w.get("abstract") or "")[:240]`: falsy folds; truthy lists
    // slice (kept as lists!); truthy strings head; else TypeError.
    let abstract_folded = match m.get("abstract") {
        None => Value::String(String::new()),
        Some(v) if !is_truthy(v) => Value::String(String::new()),
        Some(v) => v.clone(),
    };
    rec.insert(
        "snippet".to_string(),
        match &abstract_folded {
            Value::String(s) => Value::String(char_head(s, 240).to_string()),
            Value::Array(a) => {
                Value::Array(a.iter().take(240).cloned().collect())
            }
            Value::Object(_) => return Err(subscript_keyerror(240)),
            other => return Err(type_error_not_subscriptable(json_type(other))),
        },
    );
    Ok(Value::Object(rec))
}

pub fn crossref_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `msg = resp.json().get("message", {})` (missing → {}; present
    // values kept as-is), then `msg.get("items", [])[:max_results]`.
    let msg = match obj.get("message") {
        None => Value::Object(serde_json::Map::new()),
        Some(v) => v.clone(),
    };
    let msg_map = match &msg {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&msg))),
    };
    let hits: Vec<&Value> = match msg_map.get("items") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for w in hits {
        out.push(crossref_work_impl(w, &Value::String(String::new()))?);
    }
    Ok(out)
}

pub fn crossref_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `w = resp.json()["message"]`: direct indexing (missing key →
    // KeyError; lists/strings index by integer only; anything else
    // is not subscriptable).
    let w = match &body {
        Value::Object(m) => match m.get("message") {
            Some(v) => v.clone(),
            None => return Err("KeyError: message".to_string()),
        },
        Value::Array(_) => {
            return Err(
                "TypeError: list indices must be integers or slices, not str".to_string(),
            );
        }
        Value::String(_) => {
            return Err("TypeError: string indices must be integers, not 'str'".to_string());
        }
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    crossref_work_impl(&w, fallback)
}

// ── OpenLibrary ─────────────────────────────────────────────────

fn openlibrary_search_row_impl(d: &Value, base: &str) -> Result<Value, String> {
    let m = match d {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(d))),
    };
    let key = m
        .get("key")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `", ".join(d.get("author", []) or [])`: falsy folds; truthy
    // values iterate strictly (lists item-wise, dicts key-wise,
    // strings char-wise — non-strings raise at the raw index).
    let author_vals: Vec<Value> = match m.get("author") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut authors: Vec<String> = Vec::with_capacity(author_vals.len());
    for (i, a) in author_vals.iter().enumerate() {
        match a {
            Value::String(s) => authors.push(s.clone()),
            _ => return Err(sequence_item_error(i, json_type(a))),
        }
    }
    // `", ".join(d.get("isbn", []) or [])[:120]`: strict join, then
    // a char slice of the joined string (never raises).
    let isbn_vals: Vec<Value> = match m.get("isbn") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut isbns: Vec<String> = Vec::with_capacity(isbn_vals.len());
    for (i, v) in isbn_vals.iter().enumerate() {
        match v {
            Value::String(s) => isbns.push(s.clone()),
            _ => return Err(sequence_item_error(i, json_type(v))),
        }
    }
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("openlibrary".to_string()),
    );
    rec.insert("id".to_string(), key.clone());
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "url".to_string(),
        if is_truthy(&key) {
            Value::String(format!("{base}{0}", py_value_repr(&key)))
        } else {
            Value::String(String::new())
        },
    );
    rec.insert(
        "published".to_string(),
        m.get("first_publish_year")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(isbns.join(", ").as_str(), 120).to_string()),
    );
    Ok(Value::Object(rec))
}

pub fn openlibrary_parse_search_impl(
    response_json: &str,
    max_results: i64,
    base: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("docs", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("docs") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for d in hits {
        out.push(openlibrary_search_row_impl(d, base)?);
    }
    Ok(out)
}

fn openlibrary_fetch_authors_impl(book: &serde_json::Map<String, Value>) -> Result<Vec<Value>, String> {
    // `for a in book.get("authors", []) or []`: falsy folds; truthy
    // values iterate (lists item-wise, dicts key-wise, strings
    // char-wise); anything else raises TypeError. Strings append
    // as-is; dicts append `name`, else `author.key` when that is a
    // dict (missing → ""); anything else is silently skipped.
    let items: Vec<Value> = match book.get("authors") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut out = Vec::new();
    for a in items.iter() {
        match a {
            Value::String(s) => out.push(Value::String(s.clone())),
            Value::Object(am) => {
                if let Some(name) = am.get("name") {
                    out.push(name.clone());
                } else if let Some(Value::Object(sub)) = am.get("author") {
                    out.push(
                        sub.get("key")
                            .cloned()
                            .unwrap_or(Value::String(String::new())),
                    );
                }
            }
            _ => {}
        }
    }
    Ok(out)
}

pub fn openlibrary_parse_fetch_impl(
    response_json: &str,
    key_s: &str,
    base: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let book = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `key = book.get("key", key)`: missing → the request key.
    let key = book
        .get("key")
        .cloned()
        .unwrap_or(Value::String(key_s.to_string()));
    // `", ".join(a for a in authors if a)`: falsy dropped, then
    // strict at the surviving index.
    let mut kept: Vec<(String, &'static str)> = Vec::new();
    for a in openlibrary_fetch_authors_impl(book)?.iter() {
        if !is_truthy(a) {
            continue;
        }
        kept.push((py_value_repr(a), json_type(a)));
    }
    let mut authors: Vec<String> = Vec::with_capacity(kept.len());
    for (i, (text, t)) in kept.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        authors.push(text.clone());
    }
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("openlibrary".to_string()),
    );
    rec.insert("id".to_string(), key.clone());
    rec.insert(
        "title".to_string(),
        book.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    // Unconditional: `f"{BASE}{key}"` (no truthiness check here,
    // unlike search).
    rec.insert(
        "url".to_string(),
        Value::String(format!("{base}{0}", py_value_repr(&key))),
    );
    // `book.get("first_publish_year") or ... or ""`: no defaults —
    // missing reads propagate as None through the chain.
    let published = match book.get("first_publish_year") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => match book.get("first_publish_date") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => match book.get("publish_date") {
                Some(v) if is_truthy(v) => v.clone(),
                _ => Value::String(String::new()),
            },
        },
    };
    rec.insert("published".to_string(), published);
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    Ok(Value::Object(rec))
}

// ── DOAJ ────────────────────────────────────────────────────────
fn doaj_row_impl(r: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    // `bib = r.get("bibjson", {}) or {}`: falsy folds; truthy
    // non-dicts raise on the first `.get` (identifier read below —
    // nothing raisable precedes it, so eager is exact).
    let bib: Option<&serde_json::Map<String, Value>> = match m.get("bibjson") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(bm)) => Some(bm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    // DOI hunt: non-dict identifier items are silently skipped;
    // `id` reads raw (missing → None!). Present non-list identifiers
    // iterate harmlessly (keys/chars never match the dict filter).
    let mut dois: Vec<Value> = Vec::new();
    if let Some(bm) = bib {
        match bm.get("identifier") {
            None => {}
            Some(Value::Array(a)) => {
                for i in a.iter() {
                    if let Value::Object(im) = i {
                        if matches!(im.get("type"), Some(Value::String(t)) if t == "doi")
                        {
                            dois.push(im.get("id").cloned().unwrap_or(Value::Null));
                        }
                    }
                }
            }
            Some(Value::Object(_)) | Some(Value::String(_)) => {}
            Some(other) => return Err(type_error_not_iterable(json_type(other))),
        }
    }
    // `bib.get("author", [])` only when it is a list (double `.get`,
    // same result — no side effects either way).
    let author_items: Vec<&Value> = match bib {
        None => Vec::new(),
        Some(bm) => match bm.get("author") {
            Some(Value::Array(a)) => a.iter().collect(),
            _ => Vec::new(),
        },
    };
    // Strict join at the raw index (no truthiness filter here).
    let mut parts: Vec<(String, &'static str)> = Vec::new();
    for a in author_items.iter() {
        match a {
            Value::Object(am) => match am.get("name") {
                None => parts.push((String::new(), "str")),
                Some(Value::String(s)) => parts.push((s.clone(), "str")),
                Some(v) => parts.push((py_value_repr(v), json_type(v))),
            },
            _ => parts.push((py_value_repr(a), "str")),
        }
    }
    let mut author_names: Vec<String> = Vec::with_capacity(parts.len());
    for (i, (text, t)) in parts.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        author_names.push(text.clone());
    }
    let title = match bib {
        None => Value::String(String::new()),
        Some(bm) => match bm.get("title") {
            None => Value::String(String::new()),
            Some(v) if !is_truthy(v) => Value::String(String::new()),
            Some(v) => v.clone(),
        },
    };
    let abstract_folded = match bib {
        None => Value::String(String::new()),
        Some(bm) => match bm.get("abstract") {
            None => Value::String(String::new()),
            Some(v) if !is_truthy(v) => Value::String(String::new()),
            Some(v) => v.clone(),
        },
    };
    let snippet = match &abstract_folded {
        Value::String(s) => Value::String(char_head(s, 240).to_string()),
        Value::Array(a) => Value::Array(a.iter().take(240).cloned().collect()),
        Value::Object(_) => return Err(subscript_keyerror(240)),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("doaj".to_string()));
    rec.insert(
        "id".to_string(),
        m.get("id").cloned().unwrap_or(Value::String(String::new())),
    );
    rec.insert("title".to_string(), title);
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://doaj.org/article/{0}",
            py_value_repr(m.get("id").unwrap_or(&Value::String(String::new())))
        )),
    );
    // `dois[0] if dois else ""`: the first hit stays raw (even None).
    rec.insert(
        "doi".to_string(),
        dois.into_iter().next().unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(author_names.join(", ")),
    );
    rec.insert("snippet".to_string(), snippet);
    Ok(Value::Object(rec))
}

pub fn doaj_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for r in hits {
        out.push(doaj_row_impl(r)?);
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn openalex_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    openalex_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn openalex_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    openalex_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn crossref_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    crossref_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn crossref_parse_fetch(py: Python, response_json: &str, fallback_json: &str) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    crossref_parse_fetch_impl(response_json, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, base = "https://openlibrary.org"))]
pub fn openlibrary_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
    base: &str,
) -> PyResult<String> {
    openlibrary_parse_search_impl(response_json, max_results, base)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, key = "", base = "https://openlibrary.org"))]
pub fn openlibrary_parse_fetch(
    py: Python,
    response_json: &str,
    key: &str,
    base: &str,
) -> PyResult<String> {
    openlibrary_parse_fetch_impl(response_json, key, base)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn doaj_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    doaj_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

// ── M17 helpers ─────────────────────────────────────────────────

/// Python `str()` rendering of a JSON value: strings pass through
/// unquoted; everything else renders exactly as `py_value_repr`
/// (which already matches `str()` for containers, booleans and
/// None) except numbers, which use Python float formatting
/// (`1e+300`, `1.5e-07`, trailing `.0`).
fn py_str_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => py_str_number(n),
        _ => py_value_repr(v),
    }
}

fn py_str_number(n: &serde_json::Number) -> String {
    if let Some(i) = n.as_i64() {
        return i.to_string();
    }
    if let Some(u) = n.as_u64() {
        return u.to_string();
    }
    // Shortest round-trip digits, then Python exponent restyling.
    let f = n.as_f64().unwrap_or(f64::NAN);
    let s = format!("{f:?}");
    if !f.is_finite() {
        return s;
    }
    if let Some(pos) = s.find(['e', 'E']) {
        let (mant, exp) = s.split_at(pos);
        let exp = &exp[1..];
        let (sign, digits) = match exp.strip_prefix('-') {
            Some(d) => ('-', d),
            None => ('+', exp.strip_prefix('+').unwrap_or(exp)),
        };
        let width = digits.len().max(2);
        return format!("{mant}e{sign}{digits:0>width$}", width = width);
    }
    if s.contains('.') {
        s
    } else {
        format!("{s}.0")
    }
}

// ── WorldBank ───────────────────────────────────────────────────

/// The static retired-search note (minus `raw`, which the wrapper
/// re-attaches with the original `json.dumps` expression verbatim).
pub fn worldbank_note_impl() -> Result<Value, String> {
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("worldbank".to_string()),
    );
    rec.insert("id".to_string(), Value::String(String::new()));
    rec.insert(
        "title".to_string(),
        Value::String("World Bank keyword search unavailable".to_string()),
    );
    rec.insert("url".to_string(), Value::String(String::new()));
    rec.insert(
        "snippet".to_string(),
        Value::String(
            "The World Bank /v2/search endpoint was retired. Use \
             fetch(series_code) for time-series data (e.g. SP.POP.TOTL)."
                .to_string(),
        ),
    );
    Ok(Value::Object(rec))
}

pub fn worldbank_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
    record_url: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `payload[1]` only when the body is a long-enough list whose
    // second element is itself a list; anything else folds to [].
    let points: Vec<Value> = match &body {
        Value::Array(p) if p.len() > 1 => match &p[1] {
            Value::Array(pts) => pts.clone(),
            _ => Vec::new(),
        },
        _ => Vec::new(),
    };
    let first: Option<&Value> = points.first();
    // `(first or {}).get("indicator", {}) if isinstance(first, dict)
    // else {}`: non-dict firsts fold to {}; falsy dicts fold; the
    // indicator read itself stays raw (even falsy/None).
    let indicator: Value = match first {
        Some(Value::Object(fm)) if !fm.is_empty() => match fm.get("indicator") {
            None => Value::Object(serde_json::Map::new()),
            Some(v) => v.clone(),
        },
        _ => Value::Object(serde_json::Map::new()),
    };
    // `indicator.get("id") or record_id`: a non-dict indicator raises
    // here (AttributeError), matching the original.
    let indicator_map = match &indicator {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&indicator))),
    };
    let series_id = match indicator_map.get("id") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => fallback.clone(),
    };
    let title = match indicator_map.get("value") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => fallback.clone(),
    };
    // Dated non-null points render `date:value`, joined and sliced.
    // (Missing dates render as ""; the count covers all points.)
    let n_points = points.len();
    let mut pairs: Vec<String> = Vec::new();
    for pt in points.iter() {
        if let Value::Object(pm) = pt {
            match pm.get("value") {
                Some(Value::Null) | None => {}
                Some(val) => {
                    let date = match pm.get("date") {
                        None => String::new(),
                        Some(v) => py_str_value(v),
                    };
                    pairs.push(format!("{date}:{0}", py_str_value(val)));
                }
            }
        }
    }
    // `payload[0] if isinstance(payload, list) and payload else {}`.
    let pagination: Value = match &body {
        Value::Array(p) if !p.is_empty() => p[0].clone(),
        _ => Value::Object(serde_json::Map::new()),
    };
    let mut wb = serde_json::Map::new();
    wb.insert("observations".to_string(), Value::Array(points));
    wb.insert("pagination".to_string(), pagination);
    let mut fields = serde_json::Map::new();
    fields.insert("worldbank".to_string(), Value::Object(wb));
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("worldbank".to_string()),
    );
    rec.insert("id".to_string(), series_id);
    rec.insert("title".to_string(), title);
    rec.insert("url".to_string(), Value::String(record_url.to_string()));
    rec.insert(
        "snippet".to_string(),
        Value::String(format!(
            "{n_points} observations; recent: {0}",
            char_head(pairs.join(", ").as_str(), 200)
        )),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

// ── FRED ────────────────────────────────────────────────────────

/// Shared `_record` builder: `obs` items are kernel-built
/// `{date, value}` dicts; the id stays raw while title/url render it.
/// `raw` crosses separately for the official path (`None` here — the
/// wrapper re-attaches `json.dumps(data)`); the CSV path computes its
/// own `raw` before calling.
fn fred_record_impl(obs: &[Value], rid: &Value, raw: Option<String>) -> Value {
    let tail: Vec<&Value> = if obs.len() > 10 {
        obs[obs.len() - 10..].iter().collect()
    } else {
        obs.iter().collect()
    };
    let mut points: Vec<String> = Vec::with_capacity(tail.len());
    for o in tail.iter() {
        // Items are kernel-built `{date, value}` dicts (missing keys
        // read "", matching the original `.get` defaults).
        let om = match o {
            Value::Object(om) => om,
            _ => {
                points.push("=".to_string());
                continue;
            }
        };
        points.push(format!(
            "{0}={1}",
            om.get("date").map(py_str_value).unwrap_or_default(),
            om.get("value").map(py_str_value).unwrap_or_default()
        ));
    }
    let last = points.last().cloned().unwrap_or("n/a".to_string());
    let kept: Vec<Value> = if obs.len() > 50 {
        obs[obs.len() - 50..].to_vec()
    } else {
        obs.to_vec()
    };
    let mut fred = serde_json::Map::new();
    fred.insert("observations".to_string(), Value::Array(kept));
    let mut fields = serde_json::Map::new();
    fields.insert("fred".to_string(), Value::Object(fred));
    let rid_s = py_str_value(rid);
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("fred".to_string()));
    rec.insert("id".to_string(), rid.clone());
    rec.insert(
        "title".to_string(),
        Value::String(format!("FRED series {rid_s}")),
    );
    rec.insert(
        "url".to_string(),
        Value::String(format!("https://fred.stlouisfed.org/series/{rid_s}")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(format!("{0} observations; last: {last}", obs.len())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    if let Some(raw) = raw {
        rec.insert("raw".to_string(), Value::String(raw));
    }
    Value::Object(rec)
}

pub fn fred_parse_official_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `data.get("observations", [])`: the body must be a dict; lists
    // build per item (non-dict items raise on `.get`); strings/dicts
    // iterate and raise on their first member; anything else raises
    // TypeError. (The official `raw` is `json.dumps(data)`, re-attached
    // by the wrapper.)
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    let obs: Vec<Value> = match obj.get("observations") {
        None => Vec::new(),
        Some(Value::Array(a)) => {
            let mut out = Vec::with_capacity(a.len());
            for o in a.iter() {
                let om = match o {
                    Value::Object(m) => m,
                    _ => return Err(attr_error(json_type(o))),
                };
                let mut point = serde_json::Map::new();
                point.insert(
                    "date".to_string(),
                    om.get("date").cloned().unwrap_or(Value::String(String::new())),
                );
                point.insert(
                    "value".to_string(),
                    om.get("value").cloned().unwrap_or(Value::String(String::new())),
                );
                out.push(Value::Object(point));
            }
            out
        }
        // Non-empty strings/dicts fail on their first member's `.get`.
        Some(Value::String(s)) => {
            if s.chars().next().is_some() {
                return Err(attr_error("str"));
            }
            Vec::new()
        }
        Some(Value::Object(mm)) => {
            if mm.keys().next().is_some() {
                return Err(attr_error("str"));
            }
            Vec::new()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    Ok(fred_record_impl(&obs, fallback, None))
}

pub fn fred_parse_csv_impl(response_text: &str, fallback: &Value) -> Result<Value, String> {
    use crate::pycompat::{py_splitlines, py_strip};
    // `resp.text.strip().splitlines()`; the header row is skipped and
    // each remaining line splits on the first comma.
    let lines: Vec<&str> = py_splitlines(py_strip(response_text));
    let mut obs: Vec<Value> = Vec::new();
    for line in lines.iter().skip(1) {
        let (before, after) = match line.split_once(',') {
            Some((b, a)) => (b, a),
            None => (*line, ""),
        };
        let date = py_strip(before);
        let value = py_strip(after);
        if !date.is_empty() && !value.is_empty() {
            let mut point = serde_json::Map::new();
            point.insert("date".to_string(), Value::String(date.to_string()));
            point.insert("value".to_string(), Value::String(value.to_string()));
            obs.push(Value::Object(point));
        }
    }
    let raw = lines
        .iter()
        .take(51)
        .copied()
        .collect::<Vec<&str>>()
        .join("\n");
    Ok(fred_record_impl(&obs, fallback, Some(raw)))
}

// ── GitHub ──────────────────────────────────────────────────────

/// `is_fetch` selects the fetch defaults (`record_id` for a missing
/// id/title, `""` for a missing url) over the search ones (`""`,
/// `""`, `r.get("url")`).
fn github_row_impl(r: &Value, fallback: &Value, is_fetch: bool) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    // Search: `r.get("html_url") or r.get("url")` (no defaults —
    // both may read None). Fetch: `repo.get("html_url") or ""`.
    let url = match m.get("html_url") {
        Some(v) if is_truthy(v) => v.clone(),
        _ if is_fetch => Value::String(String::new()),
        _ => m.get("url").cloned().unwrap_or(Value::Null),
    };
    let mut gh = serde_json::Map::new();
    gh.insert(
        "language".to_string(),
        m.get("language").cloned().unwrap_or(Value::Null),
    );
    gh.insert(
        "stars".to_string(),
        m.get("stargazers_count").cloned().unwrap_or(Value::Null),
    );
    let mut fields = serde_json::Map::new();
    fields.insert("github".to_string(), Value::Object(gh));
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("github".to_string()));
    rec.insert(
        "id".to_string(),
        // Search `str(r.get("id", ""))`, fetch
        // `str(repo.get("id", record_id))`: strings pass through;
        // anything else renders with Python `str()`.
        Value::String(py_str_value(m.get("id").unwrap_or(fallback))),
    );
    rec.insert(
        "title".to_string(),
        m.get("full_name")
            .cloned()
            .unwrap_or(fallback.clone()),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "snippet".to_string(),
        match m.get("description") {
            None => Value::String(String::new()),
            Some(v) if !is_truthy(v) => Value::String(String::new()),
            Some(Value::String(s)) => Value::String(char_head(s, 240).to_string()),
            Some(Value::Array(a)) => {
                Value::Array(a.iter().take(240).cloned().collect())
            }
            Some(Value::Object(_)) => return Err(subscript_keyerror(240)),
            Some(other) => return Err(type_error_not_subscriptable(json_type(other))),
        },
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn github_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("items", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("items") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let empty = Value::String(String::new());
    let mut out = Vec::new();
    for r in hits {
        out.push(github_row_impl(r, &empty, false)?);
    }
    Ok(out)
}

pub fn github_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    github_row_impl(&body, fallback, true)
}

// ── Congress ────────────────────────────────────────────────────

/// The dead `loc` computation (`r.get("state", "")` dance) is skipped:
/// it never reaches the record and cannot raise anything the first
/// field read does not already raise identically.
fn congress_row_impl(r: &Value, fallback: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    let mut cg = serde_json::Map::new();
    cg.insert(
        "chamber".to_string(),
        m.get("chamber")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    cg.insert(
        "party".to_string(),
        m.get("party").cloned().unwrap_or(Value::String(String::new())),
    );
    cg.insert(
        "state".to_string(),
        m.get("state").cloned().unwrap_or(Value::String(String::new())),
    );
    let mut fields = serde_json::Map::new();
    fields.insert("congress".to_string(), Value::Object(cg));
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("congress".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("cgi_id").cloned().unwrap_or(fallback.clone()),
    );
    rec.insert(
        "title".to_string(),
        m.get("display_name").cloned().unwrap_or(fallback.clone()),
    );
    rec.insert(
        "url".to_string(),
        m.get("url").cloned().unwrap_or(Value::String(String::new())),
    );
    // `f"{title} {party} — {state}{district}"`: every slot renders
    // raw (missing → "").
    let empty = Value::String(String::new());
    rec.insert(
        "snippet".to_string(),
        Value::String(format!(
            "{0} {1} \u{2014} {2}{3}",
            py_str_value(m.get("title").unwrap_or(&empty)),
            py_str_value(m.get("party").unwrap_or(&empty)),
            py_str_value(m.get("state").unwrap_or(&empty)),
            py_str_value(m.get("district").unwrap_or(&empty))
        )),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn congress_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let empty = Value::String(String::new());
    let mut out = Vec::new();
    for r in hits {
        out.push(congress_row_impl(r, &empty)?);
    }
    Ok(out)
}

pub fn congress_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    congress_row_impl(&body, fallback)
}

// ── NASA NeoWs ──────────────────────────────────────────────────

/// `(neo.get("estimated_diameter", {}) or {}).get("meters", {}) or {}`:
/// missing/falsy outer folds; truthy non-dicts raise; the meters read
/// stays raw (falsy folds, truthy kept even when not a dict).
fn nasa_diameter(neo: &serde_json::Map<String, Value>) -> Result<Value, String> {
    // Falsy outers fold; truthy non-dicts raise on `.get`.
    let est = match neo.get("estimated_diameter") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(v) => Some(v),
    };
    let meters: Value = match est {
        None => Value::Object(serde_json::Map::new()),
        Some(Value::Object(em)) => em
            .get("meters")
            .cloned()
            .unwrap_or(Value::Object(serde_json::Map::new())),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    // `.get("meters", {}) or {}`: falsy folds, truthy kept as-is.
    Ok(if is_truthy(&meters) {
        meters
    } else {
        Value::Object(serde_json::Map::new())
    })
}

/// `(neo.get("close_approach_data") or [{}])[0]`: missing/falsy folds
/// to `[{}]`; truthy lists index (empty impossible — falsy caught);
/// truthy strings yield their first char; truthy dicts raise KeyError;
/// anything else raises TypeError.
fn nasa_first_ca(neo: &serde_json::Map<String, Value>) -> Result<Value, String> {
    let cad = match neo.get("close_approach_data") {
        None => Value::Array(vec![Value::Object(serde_json::Map::new())]),
        Some(v) if !is_truthy(v) => {
            Value::Array(vec![Value::Object(serde_json::Map::new())])
        }
        Some(v) => v.clone(),
    };
    match &cad {
        Value::Array(a) => match a.first() {
            Some(v) => Ok(v.clone()),
            None => Ok(Value::Object(serde_json::Map::new())),
        },
        Value::String(s) => Ok(Value::String(
            s.chars().next().map(|c| c.to_string()).unwrap_or_default(),
        )),
        Value::Object(_) => Err("KeyError: 0".to_string()),
        other => Err(type_error_not_subscriptable(json_type(other))),
    }
}

fn nasa_row_impl(neo: &Value, fallback: &Value, is_fetch: bool) -> Result<Value, String> {
    let m = match neo {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(neo))),
    };
    // Evaluation order matches the original: search builds `ca`
    // before `diam`; fetch never touches `close_approach_data`.
    let ca: Option<Value> = if is_fetch {
        None
    } else {
        Some(nasa_first_ca(m)?)
    };
    let diam = nasa_diameter(m)?;
    let (published, km) = if is_fetch {
        (Value::String(String::new()), Value::String(String::new()))
    } else {
        let ca = ca.unwrap();
        let ca_map = match &ca {
            Value::Object(m) => m,
            _ => return Err(attr_error(json_type(&ca))),
        };
        // `published` reads `ca` before the snippet touches the
        // distance (original dict order).
        let published = ca_map
            .get("close_approach_date")
            .cloned()
            .unwrap_or(Value::String(String::new()));
        // `ca.get('closest_approach_distance', {}).get('kilometers',
        // '')`: the inner read stays raw (falsy/None/non-dict raises).
        let dist = match ca_map.get("closest_approach_distance") {
            None => Value::Object(serde_json::Map::new()),
            Some(v) => v.clone(),
        };
        let km = match &dist {
            Value::Object(dm) => dm
                .get("kilometers")
                .cloned()
                .unwrap_or(Value::String(String::new())),
            _ => return Err(attr_error(json_type(&dist))),
        };
        (published, km)
    };
    let diam_map = match &diam {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&diam))),
    };
    let diam_max = diam_map
        .get("estimated_diameter_max")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let empty = Value::String(String::new());
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("nasa".to_string()));
    rec.insert(
        "id".to_string(),
        m.get("neo_reference_id").cloned().unwrap_or(fallback.clone()),
    );
    rec.insert(
        "title".to_string(),
        m.get("object_name").cloned().unwrap_or(fallback.clone()),
    );
    rec.insert(
        "url".to_string(),
        m.get("nasa_jpl_url")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    if !is_fetch {
        rec.insert("published".to_string(), published);
    }
    // Search: `f"~{max} m diameter; {km} km closest"`. Fetch:
    // `f"~{max} m diameter"`.
    let snippet = if is_fetch {
        format!("~{0} m diameter", py_str_value(&diam_max))
    } else {
        format!(
            "~{0} m diameter; {1} km closest",
            py_str_value(&diam_max),
            py_str_value(&km)
        )
    };
    rec.insert("snippet".to_string(), Value::String(snippet));
    let mut nasa = serde_json::Map::new();
    nasa.insert(
        "object_type".to_string(),
        m.get("object_type").cloned().unwrap_or(empty.clone()),
    );
    nasa.insert(
        "is_hazardous".to_string(),
        m.get("is_hazardous").cloned().unwrap_or(empty.clone()),
    );
    if !is_fetch {
        nasa.insert(
            "absolute_magnitude".to_string(),
            m.get("absolute_magnitude").cloned().unwrap_or(empty.clone()),
        );
    }
    let mut fields = serde_json::Map::new();
    fields.insert("nasa".to_string(), Value::Object(nasa));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn nasa_parse_search_impl(
    response_json: &str,
    start: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `.get("near_earth_objects", {}).get(start, [])`: a missing outer
    // folds; falsy/non-dict outers raise on the inner `.get`.
    let objects: Vec<&Value> = match obj.get("near_earth_objects") {
        None => Vec::new(),
        Some(Value::Object(nm)) => match nm.get(start) {
            None => Vec::new(),
            Some(v) => subscript_hits(Some(v), max_results)?,
        },
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let empty = Value::String(String::new());
    let mut out = Vec::new();
    for neo in objects {
        out.push(nasa_row_impl(neo, &empty, false)?);
    }
    Ok(out)
}

pub fn nasa_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    nasa_row_impl(&body, fallback, true)
}

// ── Software Heritage ───────────────────────────────────────────

pub fn swh_origin_row_impl(origin: &Value, fallback: &str) -> Result<Value, String> {
    let m = match origin {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(origin))),
    };
    // `\", \".join(origin.get(\"visit_types\", []) or [])`: falsy folds;
    // truthy values iterate strictly (dicts key-wise, strings
    // char-wise — non-strings raise at the raw index).
    let vt_vals: Vec<Value> = match m.get("visit_types") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut vts: Vec<String> = Vec::with_capacity(vt_vals.len());
    for (i, v) in vt_vals.iter().enumerate() {
        match v {
            Value::String(s) => vts.push(s.clone()),
            _ => return Err(sequence_item_error(i, json_type(v))),
        }
    }
    let types = vts.join(", ");
    let url = m
        .get("url")
        .cloned()
        .unwrap_or(Value::String(fallback.to_string()));
    let visits = m
        .get("origin_visits_url")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let mut swh = serde_json::Map::new();
    swh.insert("visit_types".to_string(), Value::String(types.clone()));
    let mut fields = serde_json::Map::new();
    fields.insert("softwareheritage".to_string(), Value::Object(swh));
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("softwareheritage".to_string()),
    );
    rec.insert("id".to_string(), url.clone());
    rec.insert("title".to_string(), url.clone());
    // `f"...{url}" if url else visits`: url truthiness decides.
    rec.insert(
        "url".to_string(),
        if is_truthy(&url) {
            Value::String(format!(
                "https://archive.softwareheritage.org/browse/origin/?origin_url={0}",
                py_str_value(&url)
            ))
        } else {
            visits
        },
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(if types.is_empty() {
            "archived origin".to_string()
        } else {
            format!("archived origin; visit types: {types}")
        }),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn swh_parse_search_impl(
    response_json: &str,
    fallback: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `[self._origin_row(resp.json(), q)][:max_results]`: the single
    // row builds first (raising on hostile shapes), then slices (a
    // 1-list keeps its row only for `max_results >= 1`).
    let row = swh_origin_row_impl(&body, fallback)?;
    Ok(if max_results >= 1 { vec![row] } else { Vec::new() })
}

pub fn swh_parse_fetch_origin_impl(
    response_json: &str,
    fallback: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    swh_origin_row_impl(&body, fallback)
}

pub fn swh_parse_fetch_sid_impl(
    response_json: &str,
    sid: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let src = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `meta = src.get("meta", {}) or {}`: falsy folds; truthy
    // non-dicts raise on their first `.get` below (nothing raisable
    // precedes it past the dict check, so eager is exact).
    let meta: Option<&serde_json::Map<String, Value>> = match src.get("meta") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    // Evaluation order matches the dict build: id, then title (whose
    // eager `src.get("id", sid)` default re-reads the id), then url.
    let id = src
        .get("id")
        .cloned()
        .unwrap_or(Value::String(sid.to_string()));
    let title = match meta {
        None => id.clone(),
        Some(mm) => match mm.get("name") {
            // Eager default: `src.get("id", sid)` runs regardless.
            None => src
                .get("id")
                .cloned()
                .unwrap_or(Value::String(sid.to_string())),
            Some(v) => v.clone(),
        },
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("softwareheritage".to_string()),
    );
    rec.insert("id".to_string(), id.clone());
    rec.insert("title".to_string(), title);
    let sid_val = Value::String(sid.to_string());
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://archive.softwareheritage.org/{0}",
            py_str_value(src.get("id").unwrap_or(&sid_val))
        )),
    );
    let published = match meta {
        None => Value::String(String::new()),
        Some(mm) => mm
            .get("date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    rec.insert("published".to_string(), published);
    // `(meta.get("description", "") or "")[:240]`: meta folds to {}
    // when missing/falsy, so the read is always safe here.
    let desc_folded = match meta {
        None => Value::String(String::new()),
        Some(mm) => match mm.get("description") {
            None => Value::String(String::new()),
            Some(v) if !is_truthy(v) => Value::String(String::new()),
            Some(v) => v.clone(),
        },
    };
    rec.insert(
        "snippet".to_string(),
        match &desc_folded {
            Value::String(s) => Value::String(char_head(s, 240).to_string()),
            Value::Array(a) => Value::Array(a.iter().take(240).cloned().collect()),
            Value::Object(_) => return Err(subscript_keyerror(240)),
            other => return Err(type_error_not_subscriptable(json_type(other))),
        },
    );
    let mut swh = serde_json::Map::new();
    swh.insert(
        "type".to_string(),
        src.get("type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    let mut fields = serde_json::Map::new();
    fields.insert("softwareheritage".to_string(), Value::Object(swh));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

// ── Overpass ────────────────────────────────────────────────────

fn overpass_row_impl(el: &Value) -> Result<Value, String> {
    let m = match el {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(el))),
    };
    // `tags = el.get("tags", {}) or {}`: falsy folds; truthy non-dicts
    // raise on the first `.get`/`.items` (the name read comes first).
    let tags: Option<&serde_json::Map<String, Value>> = match m.get("tags") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(tm)) => Some(tm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let name = match tags {
        None => Value::String(String::new()),
        Some(tm) => tm
            .get("name")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    let empty = Value::String(String::new());
    let el_type = m.get("type").unwrap_or(&empty);
    let el_id = m.get("id").unwrap_or(&empty);
    // `name or f"{type}:{id}"`: name truthiness decides (kept raw).
    let title = if is_truthy(&name) {
        name.clone()
    } else {
        Value::String(format!(
            "{0}:{1}",
            py_str_value(el_type),
            py_str_value(el_id)
        ))
    };
    // `", ".join(f"{k}={v}" for k, v in list(tags.items())[:6])`:
    // all slots render, so joining never raises past the tags check.
    let mut pairs: Vec<String> = Vec::new();
    if let Some(tm) = tags {
        for (k, v) in tm.iter().take(6) {
            pairs.push(format!("{k}={0}", py_str_value(v)));
        }
    }
    let mut op = serde_json::Map::new();
    op.insert(
        "type".to_string(),
        m.get("type").cloned().unwrap_or(Value::String(String::new())),
    );
    op.insert(
        "lat".to_string(),
        m.get("lat").cloned().unwrap_or(Value::String(String::new())),
    );
    op.insert(
        "lon".to_string(),
        m.get("lon").cloned().unwrap_or(Value::String(String::new())),
    );
    let mut fields = serde_json::Map::new();
    fields.insert("overpass".to_string(), Value::Object(op));
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("overpass".to_string()),
    );
    rec.insert(
        "id".to_string(),
        Value::String(py_str_value(m.get("id").unwrap_or(&empty))),
    );
    rec.insert("title".to_string(), title);
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://www.openstreetmap.org/{0}/{1}",
            py_str_value(el_type),
            py_str_value(el_id)
        )),
    );
    rec.insert("snippet".to_string(), Value::String(pairs.join(", ")));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn overpass_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("elements", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("elements") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for el in hits {
        out.push(overpass_row_impl(el)?);
    }
    Ok(out)
}

// ── Census ──────────────────────────────────────────────────────

/// `[h.lower().replace(" ", "_") for h in rows[0]]`: `rows[0]`
/// indexes directly (anything but a list/dict/str raises TypeError);
/// the row itself then iterates (non-iterables raise TypeError);
/// dicts iterate their keys; members must be strings (`.lower`
/// raises otherwise — chars/keys included).
fn census_header(first: &Value) -> Result<Vec<String>, String> {
    match first {
        Value::Array(a) => {
            let owned: Vec<Value> = a.iter().cloned().collect();
            census_header_owned(&owned)
        }
        // Dicts iterate keys (always strings in JSON).
        Value::Object(mm) => {
            let owned: Vec<Value> = mm
                .keys()
                .map(|k| Value::String(k.clone()))
                .collect();
            census_header_owned(&owned)
        }
        Value::String(s) => {
            // Chars carry no owned storage; rebuild as owned Values.
            let chars: Vec<Value> = s
                .chars()
                .map(|c| Value::String(c.to_string()))
                .collect();
            census_header_owned(&chars)
        }
        // Iterating the header row (not indexing it).
        other => return Err(type_error_not_iterable(json_type(other))),
    }
}

fn census_header_owned(items: &[Value]) -> Result<Vec<String>, String> {
    let mut out = Vec::with_capacity(items.len());
    for h in items.iter() {
        match h {
            Value::String(s) => out.push(s.to_lowercase().replace(' ', "_")),
            // `.lower` names the method (not `.get`).
            _ => return Err(attr_error_attr(json_type(h), "lower")),
        }
    }
    Ok(out)
}

/// `{header[i]: row[i] for i in range(len(header)) if i < len(row)}`:
/// `len(row)` raises first for unsized rows; dict rows raise KeyError
/// on `row[0]`; anything else indexes.
fn census_record(header: &[String], row: &Value) -> Result<serde_json::Map<String, Value>, String> {
    // An empty header never touches the row (neither `len` nor `[i]`).
    if header.is_empty() {
        return Ok(serde_json::Map::new());
    }
    match row {
        Value::Array(a) => {
            let mut rec = serde_json::Map::new();
            for (i, h) in header.iter().enumerate() {
                if i < a.len() {
                    rec.insert(h.clone(), a[i].clone());
                }
            }
            Ok(rec)
        }
        Value::String(s) => {
            let chars: Vec<Value> =
                s.chars().map(|c| Value::String(c.to_string())).collect();
            let mut rec = serde_json::Map::new();
            for (i, h) in header.iter().enumerate() {
                if i < chars.len() {
                    rec.insert(h.clone(), chars[i].clone());
                }
            }
            Ok(rec)
        }
        // Dict rows raise KeyError on `row[0]` — but only when a slot
        // passes the `i < len(row)` filter (non-empty row); empty dicts
        // yield no pairs without ever indexing.
        Value::Object(rm) => {
            if rm.is_empty() {
                Ok(serde_json::Map::new())
            } else {
                Err("KeyError: 0".to_string())
            }
        }
        other => Err(format!(
            "TypeError: object of type '{0}' has no len()",
            json_type(other)
        )),
    }
}

fn census_search_row_impl(
    header: &[String],
    row: &Value,
    dataset: &str,
) -> Result<Value, String> {
    let rec = census_record(header, row)?;
    // `key = rec.get("state", rec.get("geographic_unit", ""))`:
    // eager default, always dict-safe here.
    let geo = rec
        .get("geographic_unit")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let key = rec.get("state").cloned().unwrap_or(geo);
    // Title renders the first three pairs; the snippet renders slots
    // 1.. (both raw-rendered, so joining never raises).
    let keys: Vec<&String> = rec.keys().collect();
    let title_pairs: Vec<String> = keys
        .iter()
        .take(3)
        .map(|k| format!("{k}={0}", py_str_value(&rec[*k])))
        .collect();
    // `f"{header[i]}={row[i]}" for i in range(1, len(header)) if i <
    // len(row)`: header names pair with the RAW row slots.
    let row_len: usize = match row {
        Value::Array(a) => a.len(),
        Value::String(s) => s.chars().count(),
        _ => 0,
    };
    let mut snip: Vec<String> = Vec::new();
    for (i, h) in header.iter().enumerate().skip(1) {
        if i < row_len {
            let v = match row {
                Value::Array(a) => a[i].clone(),
                Value::String(s) => Value::String(
                    s.chars().nth(i).map(|c| c.to_string()).unwrap_or_default(),
                ),
                _ => Value::Null,
            };
            snip.push(format!("{h}={0}", py_str_value(&v)));
        }
    }
    let mut cf = serde_json::Map::new();
    cf.insert("dataset".to_string(), Value::String(dataset.to_string()));
    let mut fields = serde_json::Map::new();
    fields.insert("census".to_string(), Value::Object(cf));
    let mut out = serde_json::Map::new();
    out.insert("source".to_string(), Value::String("census".to_string()));
    out.insert("id".to_string(), Value::String(py_str_value(&key)));
    out.insert(
        "title".to_string(),
        Value::String(title_pairs.join(", ")),
    );
    out.insert(
        "url".to_string(),
        Value::String(format!("https://data.census.gov/?g={dataset}")),
    );
    out.insert("snippet".to_string(), Value::String(snip.join(", ")));
    out.insert("fields".to_string(), Value::Object(fields));
    // `raw` is `json.dumps(rec)`: the rebuilt record crosses here and
    // the wrapper attaches it verbatim (see below).
    out.insert("raw".to_string(), Value::Object(rec));
    Ok(Value::Object(out))
}

/// Shared response prologue: falsy bodies fold to []; truthy bodies
/// index `[0]` (dicts → KeyError 0) and slice `[1:]` (strings slice
/// by char; anything else raises TypeError).
fn census_rows_impl(body: &Value) -> Result<(Vec<String>, Vec<Value>), String> {
    if !is_truthy(body) {
        return Ok((Vec::new(), Vec::new()));
    }
    let first: Value = match body {
        Value::Array(a) => match a.first() {
            Some(v) => v.clone(),
            None => return Ok((Vec::new(), Vec::new())),
        },
        Value::String(s) => Value::String(
            s.chars().next().map(|c| c.to_string()).unwrap_or_default(),
        ),
        Value::Object(_) => return Err("KeyError: 0".to_string()),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    // `"".rows[0]` on an empty string: `"a"[0]` of `""`… unreachable
    // (empty strings are falsy and folded above).
    let header = census_header(&first)?;
    let tail: Vec<Value> = match body {
        Value::Array(a) => a.iter().skip(1).cloned().collect(),
        Value::String(s) => s
            .chars()
            .skip(1)
            .map(|c| Value::String(c.to_string()))
            .collect(),
        _ => Vec::new(),
    };
    Ok((header, tail))
}

pub fn census_parse_search_impl(
    response_json: &str,
    dataset: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `if not rows: return []` folds falsy bodies before any indexing.
    if !is_truthy(&body) {
        return Ok(Vec::new());
    }
    let (header, tail) = census_rows_impl(&body)?;
    // `rows[1:][:max_results]`: slice, then slice again.
    let mut out = Vec::new();
    for row in slice_refs(&tail, max_results) {
        out.push(census_search_row_impl(&header, row, dataset)?);
    }
    Ok(out)
}

pub fn census_parse_fetch_impl(
    response_json: &str,
    dataset: &str,
    rid_s: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    if !is_truthy(&body) {
        return Ok(Value::Array(Vec::new()));
    }
    let (header, tail) = census_rows_impl(&body)?;
    for row in tail.iter() {
        let rec = census_record(&header, row)?;
        // `str(rec.get("state", rec.get("geographic_unit", ""))) ==
        // str(record_id)`: both sides Python-str rendered.
        let geo = rec
            .get("geographic_unit")
            .cloned()
            .unwrap_or(Value::String(String::new()));
        let key = rec.get("state").cloned().unwrap_or(geo);
        if py_str_value(&key) == rid_s {
            // Title renders slots 1..; snippet reads `state` raw.
            let row_len: usize = match row {
                Value::Array(a) => a.len(),
                Value::String(s) => s.chars().count(),
                _ => 0,
            };
            let mut title: Vec<String> = Vec::new();
            for (i, h) in header.iter().enumerate().skip(1) {
                if i < row_len {
                    let v = match row {
                        Value::Array(a) => a[i].clone(),
                        Value::String(s) => Value::String(
                            s.chars().nth(i).map(|c| c.to_string()).unwrap_or_default(),
                        ),
                        _ => Value::Null,
                    };
                    title.push(format!("{h}={0}", py_str_value(&v)));
                }
            }
            let mut cf = serde_json::Map::new();
            cf.insert("dataset".to_string(), Value::String(dataset.to_string()));
            let mut fields = serde_json::Map::new();
            fields.insert("census".to_string(), Value::Object(cf));
            let mut out = serde_json::Map::new();
            out.insert("source".to_string(), Value::String("census".to_string()));
            out.insert("id".to_string(), Value::String(rid_s.to_string()));
            out.insert("title".to_string(), Value::String(title.join(", ")));
            out.insert(
                "url".to_string(),
                Value::String(format!("https://data.census.gov/?g={dataset}")),
            );
            out.insert(
                "snippet".to_string(),
                rec.get("state")
                    .cloned()
                    .unwrap_or(Value::String(String::new())),
            );
            out.insert("fields".to_string(), Value::Object(fields));
            out.insert("raw".to_string(), Value::Object(rec));
            return Ok(Value::Array(vec![Value::Object(out)]));
        }
    }
    Ok(Value::Array(Vec::new()))
}

#[pyfunction]
pub fn worldbank_note(py: Python) -> PyResult<String> {
    worldbank_note_impl()
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null", record_url = ""))]
pub fn worldbank_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
    record_url: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    worldbank_parse_fetch_impl(response_json, &fallback, record_url)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn fred_parse_official(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    fred_parse_official_impl(response_json, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_text, fallback_json = "null"))]
pub fn fred_parse_csv(
    py: Python,
    response_text: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    fred_parse_csv_impl(response_text, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn github_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    github_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn github_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    github_parse_fetch_impl(response_json, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn congress_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    congress_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn congress_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    congress_parse_fetch_impl(response_json, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, start = "", max_results = 5))]
pub fn nasa_parse_search(
    py: Python,
    response_json: &str,
    start: &str,
    max_results: i64,
) -> PyResult<String> {
    nasa_parse_search_impl(response_json, start, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn nasa_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    nasa_parse_fetch_impl(response_json, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback = "", max_results = 5))]
pub fn swh_parse_search(
    py: Python,
    response_json: &str,
    fallback: &str,
    max_results: i64,
) -> PyResult<String> {
    swh_parse_search_impl(response_json, fallback, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback = ""))]
pub fn swh_parse_fetch_origin(
    py: Python,
    response_json: &str,
    fallback: &str,
) -> PyResult<String> {
    swh_parse_fetch_origin_impl(response_json, fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, sid = ""))]
pub fn swh_parse_fetch_sid(
    py: Python,
    response_json: &str,
    sid: &str,
) -> PyResult<String> {
    swh_parse_fetch_sid_impl(response_json, sid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn overpass_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    overpass_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, dataset = "", max_results = 5))]
pub fn census_parse_search(
    py: Python,
    response_json: &str,
    dataset: &str,
    max_results: i64,
) -> PyResult<String> {
    census_parse_search_impl(response_json, dataset, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, dataset = "", rid = ""))]
pub fn census_parse_fetch(
    py: Python,
    response_json: &str,
    dataset: &str,
    rid: &str,
) -> PyResult<String> {
    census_parse_fetch_impl(response_json, dataset, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

// ── arXiv (Atom via src/xmlatom.rs) ─────────────────────────────

/// `_bare_arxiv_id`: reduce an id or abs URL to the bare identifier.
fn arxiv_bare_id(value: &str) -> String {
    let mut ident = value.to_string();
    for sep in ["arxiv.org/abs/", "arxiv.org/abs", "arxiv.org/"] {
        if let Some((_, rest)) = ident.split_once(sep) {
            ident = rest.to_string();
        }
    }
    ident.trim_matches('/').to_string()
}

/// `_entry_field`: collapsed text of the first direct child with this
/// local name ("" when absent).
fn arxiv_field(entry: &crate::xmlatom::Node, name: &str) -> String {
    match entry.child(name) {
        None => String::new(),
        Some(c) => crate::pycompat::py_collapse_ws(&c.text),
    }
}

fn arxiv_entry_impl(entry: &crate::xmlatom::Node) -> Value {
    let entry_id = arxiv_field(entry, "id");
    let bare_id = arxiv_bare_id(&entry_id);
    // DOI: the extension field, else the rel=related title=doi link.
    let mut doi = arxiv_field(entry, "doi");
    if doi.is_empty() {
        for link in entry.children_named("link") {
            if link.attr("title") == "doi" {
                let href = link.attr("href").trim().to_string();
                doi = match href.split_once("doi.org/") {
                    Some((_, rest)) => rest.trim().to_string(),
                    None => href,
                };
                break;
            }
        }
    }
    // Primary category: the extension, else the arxiv-scheme category.
    let mut primary = String::new();
    for pc in entry.children_named("primary_category") {
        primary = pc.attr("term").to_string();
        if !primary.is_empty() {
            break;
        }
    }
    if primary.is_empty() {
        for cat in entry.children_named("category") {
            if cat.attr("scheme").ends_with("schemas/atom") {
                primary = cat.attr("term").to_string();
                break;
            }
        }
    }
    let mut authors: Vec<String> = Vec::new();
    for a in entry.children_named("author") {
        let name = arxiv_field(a, "name");
        if !name.is_empty() {
            authors.push(name);
        }
    }
    let mut fields = serde_json::Map::new();
    fields.insert(
        "primary_category".to_string(),
        Value::String(primary),
    );
    let mut arxiv = serde_json::Map::new();
    arxiv.insert("arxiv".to_string(), Value::Object(fields));
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("arxiv".to_string()));
    rec.insert("id".to_string(), Value::String(bare_id.clone()));
    rec.insert(
        "title".to_string(),
        Value::String(arxiv_field(entry, "title")),
    );
    rec.insert(
        "url".to_string(),
        Value::String(if entry_id.is_empty() {
            bare_id
        } else {
            entry_id
        }),
    );
    rec.insert("doi".to_string(), Value::String(doi));
    rec.insert(
        "published".to_string(),
        Value::String(arxiv_field(entry, "published")),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            crate::pycompat::char_head(
                crate::pycompat::py_collapse_ws(&arxiv_field(entry, "summary")).as_str(),
                240,
            )
            .to_string(),
        ),
    );
    // `raw` is `ET.tostring(entry)`: re-attached by the wrapper from
    // its own ElementTree parse (see below).
    rec.insert("fields".to_string(), Value::Object(arxiv));
    Value::Object(rec)
}

pub fn arxiv_parse_search_impl(
    response_xml: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // Append-then-break: any entries at all yield at least one record,
    // even for `max_results <= 0`.
    let mut out = Vec::new();
    for entry in root.children_named("entry") {
        out.push(arxiv_entry_impl(entry));
        if out.len() as i64 >= max_results {
            break;
        }
    }
    Ok(out)
}

pub fn arxiv_parse_fetch_impl(
    response_xml: &str,
    ident: &str,
    url_fallback: &Value,
) -> Result<Value, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    let entries = root.children_named("entry");
    // A valid entry's <id> contains 'abs/'; otherwise (or when empty)
    // answer the error record with the entry summary ("" when empty).
    let entry = entries.first().copied();
    let valid = match entry {
        Some(e) => arxiv_field(e, "id").contains("abs/"),
        None => false,
    };
    if !valid {
        let summary = match entry {
            Some(e) => arxiv_field(e, "summary"),
            None => String::new(),
        };
        let mut fields = serde_json::Map::new();
        fields.insert(
            "primary_category".to_string(),
            Value::String(String::new()),
        );
        let mut arxiv = serde_json::Map::new();
        arxiv.insert("arxiv".to_string(), Value::Object(fields));
        let mut rec = serde_json::Map::new();
        rec.insert("source".to_string(), Value::String("arxiv".to_string()));
        rec.insert("id".to_string(), Value::String(ident.to_string()));
        rec.insert("title".to_string(), Value::String(String::new()));
        rec.insert("url".to_string(), url_fallback.clone());
        rec.insert("doi".to_string(), Value::String(String::new()));
        rec.insert("published".to_string(), Value::String(String::new()));
        rec.insert("authors".to_string(), Value::String(String::new()));
        rec.insert("snippet".to_string(), Value::String(summary));
        rec.insert("fields".to_string(), Value::Object(arxiv));
        // `raw` is "": attached by the wrapper.
        return Ok(Value::Object(rec));
    }
    Ok(arxiv_entry_impl(entry.unwrap()))
}

// ── PubMed ──────────────────────────────────────────────────────

pub fn pubmed_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `resp.json()["esearchresult"]`: direct indexing (missing key →
    // KeyError; lists/strings index by integer only; anything else is
    // not subscriptable).
    let esearch = match &body {
        Value::Object(m) => match m.get("esearchresult") {
            Some(v) => v.clone(),
            None => return Err("KeyError: esearchresult".to_string()),
        },
        Value::Array(_) => {
            return Err(
                "TypeError: list indices must be integers or slices, not str".to_string(),
            );
        }
        Value::String(_) => {
            return Err("TypeError: string indices must be integers, not 'str'".to_string());
        }
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let emap = match &esearch {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&esearch))),
    };
    // `.get("idlist", [])[:max_results]`: missing → []; lists slice;
    // strings slice per char (uids render, never `.get`); dicts raise
    // `KeyError(slice)`; anything else raises TypeError.
    let hits: Vec<Value> = match emap.get("idlist") {
        None => Vec::new(),
        Some(Value::Array(a)) => slice_refs(a, max_results)
            .into_iter()
            .cloned()
            .collect(),
        Some(Value::String(s)) => {
            let chars: Vec<char> = s.chars().collect();
            let n = chars.len() as i64;
            let end = (if max_results < 0 {
                (n + max_results).max(0)
            } else {
                max_results.min(n)
            }) as usize;
            chars[..end]
                .iter()
                .map(|c| Value::String(c.to_string()))
                .collect()
        }
        Some(Value::Object(_)) => return Err(subscript_keyerror(max_results)),
        Some(other) => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let mut out = Vec::new();
    for uid in hits.iter() {
        let uid_s = py_str_value(uid);
        let mut rec = serde_json::Map::new();
        rec.insert("source".to_string(), Value::String("pubmed".to_string()));
        rec.insert("id".to_string(), uid.clone());
        rec.insert("title".to_string(), Value::String(String::new()));
        rec.insert(
            "url".to_string(),
            Value::String(format!("https://pubmed.ncbi.nlm.nih.gov/{uid_s}/")),
        );
        rec.insert(
            "snippet".to_string(),
            Value::String(format!("PMID {uid_s}")),
        );
        out.push(Value::Object(rec));
    }
    Ok(out)
}

pub fn pubmed_parse_fetch_impl(
    response_xml: &str,
    rid_s: &str,
) -> Result<Option<Value>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // `root.find("PubmedArticle")`: None answers the bare record in
    // the wrapper (which owns the ET lookup); the kernel returns None.
    let entry = match root.child("PubmedArticle") {
        Some(e) => e,
        None => return Ok(None),
    };
    // `entry.find("MedlineCitation")`: None raises AttributeError on
    // the first `.findtext` below.
    let mc = entry.child("MedlineCitation");
    let pmid = match mc {
        None => return Err(attr_error("NoneType")),
        Some(m) => match m.child("PMID") {
            None => None,
            Some(p) => Some(p.text.clone()),
        },
    };
    // `article.findtext("ArticleTitle")`: article None raises here.
    let article = mc.unwrap().child("Article");
    let title = match article {
        None => return Err(attr_error("NoneType")),
        Some(a) => match a.child("ArticleTitle") {
            None => String::new(),
            Some(t) => crate::pycompat::py_collapse_ws(&t.text),
        },
    };
    let mut authors: Vec<String> = Vec::new();
    if let Some(alist) = article.unwrap().child("AuthorList") {
        for a in alist.children_named("Author") {
            let last = a.child("LastName").map(|n| n.text.as_str());
            let fore = a.child("ForeName").map(|n| n.text.as_str());
            // `" ".join(x for x in (ln, fn) if x).strip()`: raw parts
            // (uncollapsed), single-space join, ends stripped.
            let mut parts: Vec<&str> = Vec::new();
            if let Some(x) = last {
                if !x.is_empty() {
                    parts.push(x);
                }
            }
            if let Some(x) = fore {
                if !x.is_empty() {
                    parts.push(x);
                }
            }
            let name = parts.join(" ").trim().to_string();
            if !name.is_empty() {
                authors.push(name);
            }
        }
    }
    // `mc.findtext("PMID") or str(record_id)`: falsy PMIDs fold.
    let uid = match pmid {
        Some(t) if !t.is_empty() => t,
        _ => rid_s.to_string(),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("pubmed".to_string()));
    rec.insert("id".to_string(), Value::String(uid.clone()));
    rec.insert("title".to_string(), Value::String(title.clone()));
    rec.insert(
        "url".to_string(),
        Value::String(format!("https://pubmed.ncbi.nlm.nih.gov/{uid}/")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(crate::pycompat::char_head(title.as_str(), 240).to_string()),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    // `raw` is `resp.text` verbatim: attached by the wrapper.
    Ok(Some(Value::Object(rec)))
}

#[pyfunction]
#[pyo3(signature = (response_xml, max_results = 5))]
pub fn arxiv_parse_search(
    py: Python,
    response_xml: &str,
    max_results: i64,
) -> PyResult<String> {
    arxiv_parse_search_impl(response_xml, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, ident = "", url_fallback_json = "null"))]
pub fn arxiv_parse_fetch(
    py: Python,
    response_xml: &str,
    ident: &str,
    url_fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(url_fallback_json).unwrap_or(Value::Null);
    arxiv_parse_fetch_impl(response_xml, ident, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn pubmed_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    pubmed_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, rid = ""))]
pub fn pubmed_parse_fetch(
    py: Python,
    response_xml: &str,
    rid: &str,
) -> PyResult<String> {
    pubmed_parse_fetch_impl(response_xml, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}
