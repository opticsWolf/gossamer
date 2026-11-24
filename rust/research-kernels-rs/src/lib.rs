//! Python bindings for `gossamer/research_providers.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use pyo3::prelude::*;

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

fn service_json_fallback_decision_inner(
    rid: String,
    probe: Option<String>,
    is_tuple: bool,
) -> Result<Vec<Option<String>>, String> {
    let out = port::service_json_fallback_decision(rid, probe, is_tuple)?;
    Ok(out)
}

/// `_service_json_fallback_decision`.
#[pyfunction]
#[pyo3(name = "_service_json_fallback_decision", signature = (rid, probe, is_tuple))]
fn service_json_fallback_decision(
    py: Python<'_>,
    rid: String,
    probe: Option<String>,
    is_tuple: bool,
) -> PyResult<Py<PyAny>> {
    service_json_fallback_decision_inner(rid, probe, is_tuple)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_provider_indices_inner(
    provider_name: Option<String>,
    names: Vec<String>,
) -> Result<serde_json::Value, String> {
    let out = port::service_provider_indices(provider_name, names)?;
    serde_json::to_value(&out).map_err(|e| e.to_string())
}

/// `_service_provider_indices`.
#[pyfunction]
#[pyo3(name = "_service_provider_indices", signature = (provider_name, names))]
fn service_provider_indices(
    py: Python<'_>,
    provider_name: Option<String>,
    names: Vec<String>,
) -> PyResult<Py<PyAny>> {
    service_provider_indices_inner(provider_name, names)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_json_fallback_decision, m)?)?;
    m.add_function(wrap_pyfunction!(service_provider_indices, m)?)?;
    Ok(())
}
