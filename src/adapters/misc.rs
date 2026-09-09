//! Adapter kernels: Weather, security, code, space, archive and geo kernels (Open-Meteo, NVD, GitHub, NASA, Software Heritage, Overpass).

use pyo3::prelude::*;
use serde_json::Value;
use crate::pycompat::{char_head};
use crate::cite::py_value_repr;
use regex::Regex;
use std::sync::OnceLock;
use super::common::{attr_error, type_error_not_subscriptable, json_type, apply_limit, is_truthy, to_py_err, type_error_not_iterable, subscript_keyerror, sequence_item_error, subscript_hits, py_str_value};

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

/// Mirror of the CVE-id routing in `NvdAdapter._search_impl` (regex
/// match plus `upper()`); the `(query or "").strip()` pre-step stays
/// Python in the wrapper, so this takes the already-stripped query
/// verbatim.
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


pub(crate) fn nvd_row_impl(cve: &Value, fallback_id: &str) -> Result<Value, String> {
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
#[pyo3(signature = (response_json, fallback_id = ""))]
pub fn nvd_parse_fetch(py: Python, response_json: &str, fallback_id: &str) -> PyResult<String> {
    nvd_parse_fetch_impl(response_json, fallback_id)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn nvd_route_query(py: Python, query: &str) -> PyResult<(String, String)> {
    let _ = py;
    Ok(nvd_route_query_impl(query))
}


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
