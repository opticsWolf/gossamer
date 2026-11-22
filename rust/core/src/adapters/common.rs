//! Shared adapter parsing helpers: error spellings, slicing, truthiness, and tag stripping.
//!
//! Interpreter-version behavior is passed explicitly as `runtime_minor` (ADR-012). The compiled
//! tag regex is an immutable performance cache, not mutable state (`M-AVOID-STATICS`).

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;
use std::sync::OnceLock;

use regex::Regex;
use serde_json::Value;

use crate::cite::py_value_repr;

/// Canonical adapter-kernel error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct AdapterError {
    message: String,
    backtrace: Backtrace,
}

impl AdapterError {
    /// Creates a canonical adapter error with a legacy-compatible message.
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the legacy-compatible error message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace for diagnostics.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for AdapterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for AdapterError {}

type Result<T> = std::result::Result<T, AdapterError>;

/// Builds an `AttributeError` message for a missing `.get` method.
pub fn attr_error(t: &str) -> String {
    format!("AttributeError: '{t}' object has no attribute 'get'")
}

/// Builds an `AttributeError` message for a missing named attribute.
pub fn attr_error_attr(t: &str, attr: &str) -> String {
    format!("AttributeError: '{t}' object has no attribute '{attr}'")
}

/// Builds a `TypeError` message for non-subscriptable values.
pub fn type_error_not_subscriptable(t: &str) -> String {
    format!("TypeError: '{t}' object is not subscriptable")
}

/// Returns the Python type name used in adapter error messages.
pub fn json_type(v: &Value) -> &'static str {
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

/// Slices a list with Python semantics; negative bounds clip to zero.
pub fn apply_limit<T: Clone>(items: &[T], max_results: i64) -> Vec<T> {
    let end = slice_len(items.len(), max_results);
    items[..end].to_vec()
}

/// Slice length for `items[:max_results]` on a fresh list.
pub fn slice_len(len: usize, max_results: i64) -> usize {
    let n = len as i64;
    (if max_results < 0 {
        (n + max_results).max(0)
    } else {
        max_results.min(n)
    }) as usize
}

/// Returns Python truthiness for a JSON value.
pub fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i != 0
            } else if let Some(u) = n.as_u64() {
                u != 0
            } else {
                n.as_f64().is_some_and(|f| f != 0.0)
            }
        }
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Builds a `TypeError` message for non-iterable values.
pub fn type_error_not_iterable(t: &str) -> String {
    format!("TypeError: '{t}' object is not iterable")
}

/// Builds the `d[slice]` error for the supplied interpreter minor version.
pub fn dict_slice_error(max_results: i64, minor: Option<u32>) -> String {
    if minor.is_none_or(|m| m >= 12) {
        format!("KeyError: slice(None, {max_results}, None)")
    } else {
        "TypeError: unhashable type: 'slice'".to_string()
    }
}

/// Builds a `KeyError(slice)` message using explicit interpreter state.
pub fn subscript_keyerror(max_results: i64, minor: Option<u32>) -> String {
    dict_slice_error(max_results, minor)
}

/// Builds a sequence-item type error message.
pub fn sequence_item_error(index: usize, t: &str) -> String {
    format!("TypeError: sequence item {index}: expected str instance, {t} found")
}

/// Slices an array of JSON values with Python semantics.
pub fn slice_refs(items: &[Value], max_results: i64) -> Vec<&Value> {
    items[..slice_len(items.len(), max_results)]
        .iter()
        .collect()
}

/// Mirrors `seq[:max_results]` + iteration for response lists.
///
/// Missing values become empty lists. Strings are sliced by character; a non-empty string slice
/// raises the legacy `AttributeError` because iterating a string and calling `.get` fails. Dicts
/// raise the version-dependent slice error; other scalars raise `TypeError`.
///
/// # Errors
///
/// Returns [`AdapterError`] for non-list response shapes, as described above.
///
/// # Examples
///
/// ```
/// use gossamer::adapters::common::subscript_hits;
/// use serde_json::json;
///
/// let binding = json!([1, 2, 3]);
/// let hits = subscript_hits(Some(&binding), 2, None).unwrap();
/// assert_eq!(hits.len(), 2);
/// ```
pub fn subscript_hits(
    v: Option<&Value>,
    max_results: i64,
    runtime_minor: Option<u32>,
) -> Result<Vec<&Value>> {
    match v {
        None => Ok(Vec::new()),
        Some(Value::Array(a)) => Ok(slice_refs(a, max_results)),
        Some(Value::String(s)) => {
            let chars: Vec<char> = s.chars().collect();
            let end = slice_len(chars.len(), max_results);
            if end == 0 {
                Ok(Vec::new())
            } else {
                Err(AdapterError::new(attr_error("str")))
            }
        }
        Some(Value::Object(_)) => Err(AdapterError::new(subscript_keyerror(
            max_results,
            runtime_minor,
        ))),
        Some(other) => Err(AdapterError::new(type_error_not_subscriptable(json_type(
            other,
        )))),
    }
}

/// Returns the compiled tag-stripping regex used by adapter kernels.
pub fn strip_tags_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"<[^>]+>").unwrap_or_else(|e| panic!("invalid strip-tags regex '<[^>]+>': {e}"))
    })
}

/// Builds the version-dependent `re.sub` type error message.
pub fn re_sub_type_error(t: &str, minor: Option<u32>) -> String {
    if minor.is_none_or(|m| m >= 11) {
        format!("TypeError: expected string or bytes-like object, got '{t}'")
    } else {
        "TypeError: expected string or bytes-like object".to_string()
    }
}

/// Builds the version-dependent string-subscript type error message.
pub fn str_subscript_error(minor: Option<u32>) -> String {
    if minor.is_none_or(|m| m >= 11) {
        "TypeError: string indices must be integers, not 'str'".to_string()
    } else {
        "TypeError: string indices must be integers".to_string()
    }
}

/// Strips HTML-like tags using the adapter's regex contract.
///
/// Falsy input returns `""`; truthy non-strings raise the version-dependent `re.sub` type error.
///
/// # Errors
///
/// Returns [`AdapterError`] when a truthy non-string value is supplied.
///
/// # Examples
///
/// ```
/// use gossamer::adapters::common::strip_tags_impl;
/// use serde_json::json;
///
/// assert_eq!(strip_tags_impl(&json!("<b>hi</b>"), None).unwrap(), " hi ");
/// ```
pub fn strip_tags_impl(text: &Value, runtime_minor: Option<u32>) -> Result<String> {
    if !is_truthy(text) {
        return Ok(String::new());
    }
    match text {
        Value::String(s) => Ok(strip_tags_re().replace_all(s, " ").to_string()),
        _ => Err(AdapterError::new(re_sub_type_error(
            json_type(text),
            runtime_minor,
        ))),
    }
}

/// Renders a JSON value with Python `str()` semantics used by adapter kernels.
pub fn py_str_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => py_str_number(n),
        _ => py_value_repr(v),
    }
}

/// Renders a JSON number with Python float formatting.
pub fn py_str_number(n: &serde_json::Number) -> String {
    if let Some(i) = n.as_i64() {
        return i.to_string();
    }
    if let Some(u) = n.as_u64() {
        return u.to_string();
    }
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
    if s.contains('.') { s } else { format!("{s}.0") }
}
