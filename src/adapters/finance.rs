//! Adapter kernels: Market, stats and rates kernels (Yahoo, Frankfurter, Eurostat, CoinGecko, AlphaVantage, World Bank, FRED, Bundesbank, BIS).

use pyo3::prelude::*;
use serde_json::Value;
use crate::pycompat::{char_head, py_repr, py_strip};
use crate::cite::py_value_repr;
use regex::Regex;
use std::sync::OnceLock;
use super::common::{attr_error, attr_error_attr, type_error_not_subscriptable, json_type, is_truthy, to_py_err, type_error_not_iterable, sequence_item_error, subscript_hits, py_str_value};

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
        py_value_repr(meta_map.get("symbol").unwrap_or(fallback))
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

/// Mirror `int(s)` for JSON-stat flat keys: surrounding whitespace
/// stripped, optional sign, then digits (Unicode decimal) with single
/// inter-digit underscores. `None` on any other shape (the caller
/// skips the key, like the `except (TypeError, ValueError)`).
pub(crate) fn py_int(s: &str) -> Option<i128> {
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
        {
            let d = c.to_digit(10)?;
            val = val.checked_mul(10)?.checked_add(d as i128)?;
            prev_underscore = false;
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

/// One Eurostat cell: `(dim, code, label)` triples (raw JSON values;
/// dims may be non-strings in hostile cubes) plus the cell value.
type EurostatRow = (Vec<(Value, Value, Value)>, Value);

/// Mirror `EurostatAdapter._unpack`: JSON-stat cube → `[(coords, value)]`,
/// capped *after* appending (a non-positive limit still yields the first
/// cell when values exist).
fn eurostat_unpack_impl(
    data: &serde_json::Map<String, Value>,
    limit: i64,
) -> Result<Vec<EurostatRow>, String> {
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
                    Err("TypeError: '>' not supported between instances of 'str' and 'int'"
                        .to_string())
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
    let mut out: Vec<EurostatRow> = Vec::new();
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
pub(crate) fn eurostat_row_impl(
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


pub(crate) fn coingecko_search_row_impl(coin: &Value) -> Result<Value, String> {
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

pub fn bundesbank_parse_impl(
    response_xml: &str,
    flow: &str,
    key: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // Namespace-agnostic `Obs` extraction over all descendants, in
    // document order; append-then-break (any hits yield at least one
    // record, even for `max_results <= 0`).
    let mut out = Vec::new();
    // `root.iter()` visits the root itself first, then descendants
    // in pre-order — replicated with an explicit stack.
    let mut stack: Vec<&crate::xmlatom::Node> = vec![&root];
    while let Some(el) = stack.pop() {
        if el.local == "Obs" {
            // `period = attrib.get("value", period)`: keep-previous
            // default (not "").
            let mut period = String::new();
            let mut value = String::new();
            for child in el.children.iter() {
                if child.local == "ObsDimension" || child.local == "TimeDimension" {
                    if let Some(v) = child.attr_opt("value") {
                        period = v.to_string();
                    }
                } else if child.local == "ObsValue" {
                    if let Some(v) = child.attr_opt("value") {
                        value = v.to_string();
                    }
                }
            }
            if !period.is_empty() || !value.is_empty() {
                let mut fields = serde_json::Map::new();
                fields.insert("flow".to_string(), Value::String(flow.to_string()));
                fields.insert("key".to_string(), Value::String(key.to_string()));
                fields.insert("date".to_string(), Value::String(period.clone()));
                fields.insert("value".to_string(), Value::String(value.clone()));
                let mut rec = serde_json::Map::new();
                rec.insert(
                    "source".to_string(),
                    Value::String("bundesbank".to_string()),
                );
                rec.insert(
                    "id".to_string(),
                    Value::String(format!("{flow}/{key}/{period}")),
                );
                rec.insert(
                    "title".to_string(),
                    Value::String(format!("{flow} {key} {period} = {value}")),
                );
                rec.insert("url".to_string(), Value::String(String::new()));
                rec.insert(
                    "snippet".to_string(),
                    Value::String(format!("{period}: {value}")),
                );
                rec.insert("fields".to_string(), Value::Object(fields));
                // `raw` is `json.dumps({flow, key, date, value})`:
                // re-attached by the wrapper from the fields above.
                out.push(Value::Object(rec));
            }
            // Outside the nonempty check: any Obs trips the limit
            // once reached (even leading empty ones for limits <= 0).
            if out.len() as i64 >= max_results {
                break;
            }
        }
        stack.extend(el.children.iter().rev());
    }
    Ok(out)
}


/// Ordered attribute pop: removes and returns the first value for
/// `name` (ElementTree attribs cannot hold duplicates, so first is all).
fn pop_attr(attrs: &mut Vec<(String, String)>, name: &str) -> Option<String> {
    attrs
        .iter()
        .position(|(k, _)| k == name)
        .map(|pos| attrs.remove(pos).1)
}

pub fn bis_parse_impl(
    response_xml: &str,
    flow: &str,
    key: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // `Series` descendants in pre-order (root itself included, as with
    // `root.iter()`); each `Obs` child maps with the eager double-pop
    // convention (`TIME_PERIOD` over `TIME`, `OBS_VALUE` over `OBS` —
    // the inner pop always runs first and wins only when the outer is
    // absent). The limit returns immediately once reached.
    let mut out = Vec::new();
    let mut stack: Vec<&crate::xmlatom::Node> = vec![&root];
    while let Some(el) = stack.pop() {
        if el.local == "Series" {
            let series_key: Vec<(String, String)> = el.attrs.to_vec();
            for obs in el.children.iter() {
                if obs.local != "Obs" {
                    continue;
                }
                let mut attrs: Vec<(String, String)> = obs.attrs.to_vec();
                // Eager inner pops first (both sides always evaluated).
                let time_fallback = pop_attr(&mut attrs, "TIME");
                let period = pop_attr(&mut attrs, "TIME_PERIOD")
                    .or(time_fallback)
                    .unwrap_or_default();
                let obs_fallback = pop_attr(&mut attrs, "OBS");
                let value = pop_attr(&mut attrs, "OBS_VALUE")
                    .or(obs_fallback)
                    .unwrap_or_default();
                let key_txt = series_key
                    .iter()
                    .map(|(k, v)| format!("{k}={v}"))
                    .collect::<Vec<String>>()
                    .join(".");
                let mut fields = serde_json::Map::new();
                fields.insert("flow".to_string(), Value::String(flow.to_string()));
                fields.insert("key".to_string(), Value::String(key.to_string()));
                fields
                    .insert("date".to_string(), Value::String(period.clone()));
                fields
                    .insert("value".to_string(), Value::String(value.clone()));
                for (k, v) in series_key.iter() {
                    fields.insert(format!("dim_{k}"), Value::String(v.clone()));
                }
                let mut rec = serde_json::Map::new();
                rec.insert("source".to_string(), Value::String("bis".to_string()));
                rec.insert(
                    "id".to_string(),
                    Value::String(format!("{flow}/{key}/{period}")),
                );
                rec.insert(
                    "title".to_string(),
                    Value::String(format!("{flow} {key_txt} {period} = {value}")),
                );
                rec.insert("url".to_string(), Value::String(String::new()));
                rec.insert(
                    "snippet".to_string(),
                    Value::String(format!("{key_txt} \u{2014} {period}: {value}")),
                );
                rec.insert("fields".to_string(), Value::Object(fields));
                // `raw` is `json.dumps({flow, series, date, value,
                // extra})`: the series/extra maps cross here and the
                // wrapper re-dumps them (see below).
                let mut series_map = serde_json::Map::new();
                for (k, v) in series_key.iter() {
                    series_map.insert(k.clone(), Value::String(v.clone()));
                }
                let mut extra_map = serde_json::Map::new();
                for (k, v) in attrs.iter() {
                    extra_map.insert(k.clone(), Value::String(v.clone()));
                }
                let mut raw = serde_json::Map::new();
                raw.insert("flow".to_string(), Value::String(flow.to_string()));
                raw.insert("series".to_string(), Value::Object(series_map));
                raw.insert("date".to_string(), Value::String(period));
                raw.insert("value".to_string(), Value::String(value));
                raw.insert("extra".to_string(), Value::Object(extra_map));
                rec.insert("raw".to_string(), Value::Object(raw));
                out.push(Value::Object(rec));
                if out.len() as i64 >= max_results {
                    return Ok(out);
                }
            }
        }
        stack.extend(el.children.iter().rev());
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (response_xml, flow = "", key = "", max_results = 5))]
pub fn bundesbank_parse(
    py: Python,
    response_xml: &str,
    flow: &str,
    key: &str,
    max_results: i64,
) -> PyResult<String> {
    bundesbank_parse_impl(response_xml, flow, key, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, flow = "", key = "", max_results = 5))]
pub fn bis_parse(
    py: Python,
    response_xml: &str,
    flow: &str,
    key: &str,
    max_results: i64,
) -> PyResult<String> {
    bis_parse_impl(response_xml, flow, key, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}
