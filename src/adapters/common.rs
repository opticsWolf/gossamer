//! Adapter kernels: Shared parsing helpers: Python-error spellings, slicing, truthiness, tags.

use pyo3::prelude::*;
use serde_json::Value;
use crate::cite::py_value_repr;
use regex::Regex;
use std::sync::OnceLock;

pub(crate) fn attr_error(t: &str) -> String {
    format!("AttributeError: '{t}' object has no attribute 'get'")
}

pub(crate) fn attr_error_attr(t: &str, attr: &str) -> String {
    format!("AttributeError: '{t}' object has no attribute '{attr}'")
}

pub(crate) fn type_error_not_subscriptable(t: &str) -> String {
    format!("TypeError: '{t}' object is not subscriptable")
}

pub(crate) fn json_type(v: &Value) -> &'static str {
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
pub(crate) fn apply_limit<T: Clone>(items: &[T], max_results: i64) -> Vec<T> {
    let n = items.len() as i64;
    let end = if max_results < 0 {
        (n + max_results).max(0)
    } else {
        max_results.min(n)
    } as usize;
    items[..end].to_vec()
}


pub(crate) fn is_truthy(v: &Value) -> bool {
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

pub(crate) fn to_py_err(py: Python, e: String) -> pyo3::PyErr {
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

pub(crate) fn type_error_not_iterable(t: &str) -> String {
    format!("TypeError: '{t}' object is not iterable")
}

pub(crate) fn subscript_keyerror(max_results: i64) -> String {
    format!("KeyError: slice(None, {max_results}, None)")
}

pub(crate) fn sequence_item_error(index: usize, t: &str) -> String {
    format!("TypeError: sequence item {index}: expected str instance, {t} found")
}

/// Mirror `seq[:max_results]` + iteration for response lists: missing →
/// empty; lists sliced (negatives clip); strings sliced then failed per
/// character (empty-after-slice iterates zero times); dicts raise
/// `KeyError(slice)`; anything else raises TypeError.
pub(crate) fn subscript_hits(v: Option<&Value>, max_results: i64) -> Result<Vec<&Value>, String> {
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

pub(crate) fn slice_refs(items: &[Value], max_results: i64) -> Vec<&Value> {
    let n = items.len() as i64;
    let end = if max_results < 0 {
        (n + max_results).max(0)
    } else {
        max_results.min(n)
    } as usize;
    items[..end].iter().collect()
}

pub(crate) fn strip_tags_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"<[^>]+>").expect("strip-tags regex"))
}

/// Mirror of `_strip_tags`: falsy → `""`, else regex-substitute.
/// Non-string truthy values raise TypeError like `re.sub` does.
pub(crate) fn strip_tags_impl(text: &Value) -> Result<String, String> {
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

/// Python `str()` rendering of a JSON value: strings pass through
/// unquoted; everything else renders exactly as `py_value_repr`
/// (which already matches `str()` for containers, booleans and
/// None) except numbers, which use Python float formatting
/// (`1e+300`, `1.5e-07`, trailing `.0`).
pub(crate) fn py_str_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => py_str_number(n),
        _ => py_value_repr(v),
    }
}

pub(crate) fn py_str_number(n: &serde_json::Number) -> String {
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


/// Slice length for `items[:max_results]` on a fresh list.
pub(crate) fn slice_refs_len(len: usize, max_results: i64) -> usize {
    let n = len as i64;
    (if max_results < 0 {
        (n + max_results).max(0)
    } else {
        max_results.min(n)
    }) as usize
}
