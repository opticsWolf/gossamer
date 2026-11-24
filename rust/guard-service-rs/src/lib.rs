//! Python bindings for `gossamer/guard.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use pyo3::prelude::*;
use serde_json::Value;

pub mod js;
pub mod port;
pub mod pyconv;
pub mod pynum;

#[allow(unused_imports)]
use pynum::Num;

fn error(message: String) -> PyErr {
    use pyo3::exceptions as e;
    let (kind, text) = message.split_once(": ").unwrap_or(("", message.as_str()));
    let text = text.to_string();
    match kind {
        "TypeError" => e::PyTypeError::new_err(text),
        "KeyError" => e::PyKeyError::new_err(text),
        "IndexError" => e::PyIndexError::new_err(text),
        "OverflowError" => e::PyOverflowError::new_err(text),
        "ZeroDivisionError" => e::PyZeroDivisionError::new_err(text),
        "AttributeError" => e::PyAttributeError::new_err(text),
        "RuntimeError" => e::PyRuntimeError::new_err(text),
        "NotImplementedError" => e::PyNotImplementedError::new_err(text),
        "LookupError" => e::PyLookupError::new_err(text),
        "ArithmeticError" => e::PyArithmeticError::new_err(text),
        "UnicodeError" => e::PyUnicodeError::new_err(text),
        "ValueError" => e::PyValueError::new_err(text),
        _ => e::PyValueError::new_err(message),
    }
}

fn service_guard_pairs_inner(
    mode: Option<String>,
    scopes: Bound<'_, PyAny>,
    scope_texts: Bound<'_, PyAny>,
    main_scope: String,
) -> Result<Value, String> {
    let scopes: Value = crate::pyconv::FromPy::from_py(&scopes)?;
    let scope_texts: Value = crate::pyconv::FromPy::from_py(&scope_texts)?;
    let out = port::service_guard_pairs(mode, scopes, scope_texts, main_scope)?;
    Ok(out)
}

/// `_service_guard_pairs`.
#[pyfunction]
#[pyo3(name = "_service_guard_pairs", signature = (mode, scopes, scope_texts, main_scope))]
fn service_guard_pairs(
    py: Python<'_>,
    mode: Option<String>,
    scopes: Bound<'_, PyAny>,
    scope_texts: Bound<'_, PyAny>,
    main_scope: String,
) -> PyResult<Py<PyAny>> {
    service_guard_pairs_inner(mode, scopes, scope_texts, main_scope)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_merge_reports_inner(reports: Bound<'_, PyAny>) -> Result<Value, String> {
    let reports: Value = crate::pyconv::FromPy::from_py(&reports)?;
    let out = port::service_merge_reports(reports)?;
    Ok(out)
}

/// `_service_merge_reports`.
#[pyfunction]
#[pyo3(name = "_service_merge_reports", signature = (reports))]
fn service_merge_reports(py: Python<'_>, reports: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    service_merge_reports_inner(reports)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_guard_pairs, m)?)?;
    m.add_function(wrap_pyfunction!(service_merge_reports, m)?)?;
    Ok(())
}
