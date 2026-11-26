//! Python bindings for `gossamer/search.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_dedup_results_inner(results: Bound<'_, PyAny>) -> Result<Vec<Value>, String> {
    let results: Value = crate::pyconv::FromPy::from_py(&results)?;
    let out = port::service_dedup_results(results)?;
    Ok(out)
}

/// `serviceDedupResults`.
#[pyfunction]
#[pyo3(name = "_service_dedup_results", signature = (results))]
fn service_dedup_results(py: Python<'_>, results: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    service_dedup_results_inner(results)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_fallback_note_inner(
    provider_name: Option<Bound<'_, PyAny>>,
    registered: Bound<'_, PyAny>,
) -> Result<Value, String> {
    let provider_name: Option<Value> = match &provider_name {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let registered: Value = crate::pyconv::FromPy::from_py(&registered)?;
    let out = port::service_fallback_note(provider_name, registered)?;
    Ok(out)
}

/// `serviceFallbackNote`.
#[pyfunction]
#[pyo3(name = "_service_fallback_note", signature = (provider_name, registered))]
fn service_fallback_note(
    py: Python<'_>,
    provider_name: Option<Bound<'_, PyAny>>,
    registered: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_fallback_note_inner(provider_name, registered)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_resolve_order_inner(
    provider_name: Option<Bound<'_, PyAny>>,
    registered: Bound<'_, PyAny>,
) -> Result<Vec<String>, String> {
    let provider_name: Option<Value> = match &provider_name {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let registered: Value = crate::pyconv::FromPy::from_py(&registered)?;
    let out = port::service_resolve_order(provider_name, registered)?;
    Ok(out)
}

/// `serviceResolveOrder`.
#[pyfunction]
#[pyo3(name = "_service_resolve_order", signature = (provider_name, registered))]
fn service_resolve_order(
    provider_name: Option<Bound<'_, PyAny>>,
    registered: Bound<'_, PyAny>,
) -> PyResult<Vec<String>> {
    service_resolve_order_inner(provider_name, registered).map_err(error)
}

fn service_failover_results_inner(
    provider_results: Bound<'_, PyAny>,
    max_results: usize,
) -> Result<Value, String> {
    let provider_results: Value = crate::pyconv::FromPy::from_py(&provider_results)?;
    port::service_failover_results(provider_results, max_results)
}

/// `serviceFailoverResults`.
#[pyfunction]
#[pyo3(name = "_service_failover_results", signature = (provider_results, max_results))]
fn service_failover_results(
    py: Python<'_>,
    provider_results: Bound<'_, PyAny>,
    max_results: usize,
) -> PyResult<Py<PyAny>> {
    service_failover_results_inner(provider_results, max_results)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_merged_results_inner(
    provider_results: Bound<'_, PyAny>,
    max_results: usize,
) -> Result<Value, String> {
    let provider_results: Value = crate::pyconv::FromPy::from_py(&provider_results)?;
    port::service_merged_results(provider_results, max_results)
}

/// `serviceMergedResults`.
#[pyfunction]
#[pyo3(name = "_service_merged_results", signature = (provider_results, max_results))]
fn service_merged_results(
    py: Python<'_>,
    provider_results: Bound<'_, PyAny>,
    max_results: usize,
) -> PyResult<Py<PyAny>> {
    service_merged_results_inner(provider_results, max_results)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}
#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_dedup_results, m)?)?;
    m.add_function(wrap_pyfunction!(service_fallback_note, m)?)?;
    m.add_function(wrap_pyfunction!(service_resolve_order, m)?)?;
    m.add_function(wrap_pyfunction!(service_failover_results, m)?)?;
    m.add_function(wrap_pyfunction!(service_merged_results, m)?)?;
    Ok(())
}
