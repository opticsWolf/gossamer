//! Python bindings for `gossamer/research_providers.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_normalize_research_inner(
    records: Bound<'_, PyAny>,
    max_results: Option<i64>,
) -> Result<Vec<Value>, String> {
    let records: Value = crate::pyconv::FromPy::from_py(&records)?;
    let out = port::service_normalize_research(records, max_results)?;
    Ok(out)
}

/// `_service_normalize_research`.
#[pyfunction]
#[pyo3(name = "_service_normalize_research", signature = (records, max_results=0))]
fn service_normalize_research(
    py: Python<'_>,
    records: Bound<'_, PyAny>,
    max_results: Option<i64>,
) -> PyResult<Py<PyAny>> {
    service_normalize_research_inner(records, max_results)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_research_order_inner(
    providers: Bound<'_, PyAny>,
    preferred: Option<Bound<'_, PyAny>>,
) -> Result<Vec<String>, String> {
    let providers: Value = crate::pyconv::FromPy::from_py(&providers)?;
    let preferred: Option<Value> = match &preferred {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::service_research_order(providers, preferred)?;
    Ok(out)
}

/// `_service_research_order`.
#[pyfunction]
#[pyo3(name = "_service_research_order", signature = (providers, preferred))]
fn service_research_order(
    providers: Bound<'_, PyAny>,
    preferred: Option<Bound<'_, PyAny>>,
) -> PyResult<Vec<String>> {
    service_research_order_inner(providers, preferred).map_err(error)
}

fn service_rate_state_from_headers_inner(
    headers: Bound<'_, PyAny>,
    default_rps: Option<f64>,
) -> Result<Value, String> {
    let headers: Value = crate::pyconv::FromPy::from_py(&headers)?;
    let out = port::service_rate_state_from_headers(headers, default_rps)?;
    Ok(out)
}

/// `_service_rate_state_from_headers`.
#[pyfunction]
#[pyo3(name = "_service_rate_state_from_headers", signature = (headers, default_rps))]
fn service_rate_state_from_headers(
    py: Python<'_>,
    headers: Bound<'_, PyAny>,
    default_rps: Option<f64>,
) -> PyResult<Py<PyAny>> {
    service_rate_state_from_headers_inner(headers, default_rps)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_local_name_inner(tag: String) -> Result<String, String> {
    let out = port::service_local_name(tag)?;
    Ok(out)
}

/// `_service_local_name`.
#[pyfunction]
#[pyo3(name = "_service_local_name", signature = (tag))]
fn service_local_name(tag: String) -> PyResult<String> {
    service_local_name_inner(tag).map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_normalize_research, m)?)?;
    m.add_function(wrap_pyfunction!(service_research_order, m)?)?;
    m.add_function(wrap_pyfunction!(service_rate_state_from_headers, m)?)?;
    m.add_function(wrap_pyfunction!(service_local_name, m)?)?;
    Ok(())
}
