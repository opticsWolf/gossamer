//! Binding-layer adapter helpers.
//!
//! Parsing helpers live in the pure `gossamer::adapters::common` module (ADR-012). This file keeps
//! the legacy `String`-error signatures, supplies the current interpreter version, and owns
//! Python exception construction.

use pyo3::prelude::*;
use serde_json::Value;

pub use gossamer::adapters::common::{
    attr_error, is_truthy, json_type, py_str_value,
    sequence_item_error, slice_refs, str_subscript_error,
    type_error_not_iterable, type_error_not_subscriptable,
};

pub(crate) fn subscript_keyerror(max_results: i64) -> String {
    gossamer::adapters::common::subscript_keyerror(max_results, crate::pycompat::runtime_minor())
}

pub(crate) fn subscript_hits(
    v: Option<&Value>,
    max_results: i64,
) -> Result<Vec<&Value>, String> {
    gossamer::adapters::common::subscript_hits(v, max_results, crate::pycompat::runtime_minor())
        .map_err(|e| e.message().to_owned())
}

pub(crate) fn strip_tags_impl(text: &Value) -> Result<String, String> {
    gossamer::adapters::common::strip_tags_impl(text, crate::pycompat::runtime_minor())
        .map_err(|e| e.message().to_owned())
}

pub(crate) fn to_py_err(py: Python, e: String) -> pyo3::PyErr {
    use pyo3::exceptions::{
        PyAttributeError, PyIndexError, PyKeyError, PyRuntimeError, PyTypeError, PyValueError,
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
