//! Binding-layer adapter helpers.
//!
//! Parsing helpers live in the pure `gossamer::adapters::common` module (ADR-012). This file keeps
//! It owns Python exception construction for legacy `String` errors.

use pyo3::prelude::*;

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
