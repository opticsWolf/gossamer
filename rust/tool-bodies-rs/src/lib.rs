//! Python bindings for `gossamer/agent_tools.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_inspect_route_inner(
    structured: bool,
    store_dir: Option<String>,
) -> Result<String, String> {
    let out = port::service_inspect_route(structured, store_dir)?;
    Ok(out)
}

/// `_service_inspect_route`.
#[pyfunction]
#[pyo3(name = "_service_inspect_route", signature = (structured, store_dir))]
fn service_inspect_route(structured: bool, store_dir: Option<String>) -> PyResult<String> {
    service_inspect_route_inner(structured, store_dir).map_err(error)
}

fn service_tables_as_error_inner() -> Result<Value, String> {
    let out = port::service_tables_as_error()?;
    Ok(out)
}

/// `_service_tables_as_error`.
#[pyfunction]
#[pyo3(name = "_service_tables_as_error", signature = ())]
fn service_tables_as_error(py: Python<'_>) -> PyResult<Py<PyAny>> {
    service_tables_as_error_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_tool_arguments_inner(
    name: String,
    arguments: Bound<'_, PyAny>,
) -> Result<Option<Value>, String> {
    let arguments: Value = crate::pyconv::FromPy::from_py(&arguments)?;
    let out = port::service_tool_arguments(name, arguments)?;
    Ok(out)
}

/// `_service_tool_arguments`.
#[pyfunction]
#[pyo3(name = "_service_tool_arguments", signature = (name, arguments))]
fn service_tool_arguments(
    py: Python<'_>,
    name: String,
    arguments: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_tool_arguments_inner(name, arguments)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_url_error_inner(
    raw: String,
    reason: String,
    error_type: String,
) -> Result<Value, String> {
    let out = port::service_url_error(raw, reason, error_type)?;
    Ok(out)
}

/// `_service_url_error`.
#[pyfunction]
#[pyo3(name = "_service_url_error", signature = (raw, reason, error_type))]
fn service_url_error(
    py: Python<'_>,
    raw: String,
    reason: String,
    error_type: String,
) -> PyResult<Py<PyAny>> {
    service_url_error_inner(raw, reason, error_type)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_crawl_host_key_inner(url: String) -> Result<String, String> {
    let out = port::service_crawl_host_key(url)?;
    Ok(out)
}

/// `_service_crawl_host_key`.
#[pyfunction]
#[pyo3(name = "_service_crawl_host_key", signature = (url))]
fn service_crawl_host_key(url: String) -> PyResult<String> {
    service_crawl_host_key_inner(url).map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_inspect_route, m)?)?;
    m.add_function(wrap_pyfunction!(service_tables_as_error, m)?)?;
    m.add_function(wrap_pyfunction!(service_tool_arguments, m)?)?;
    m.add_function(wrap_pyfunction!(service_url_error, m)?)?;
    m.add_function(wrap_pyfunction!(service_crawl_host_key, m)?)?;
    Ok(())
}
