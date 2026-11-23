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

fn service_payload_skeleton_inner(
    markdown: String,
    url: String,
    metadata: Bound<'_, PyAny>,
    max_links: i64,
) -> Result<Value, String> {
    let metadata: Value = crate::pyconv::FromPy::from_py(&metadata)?;
    let out = port::service_payload_skeleton(markdown, url, metadata, max_links)?;
    Ok(out)
}

/// `_service_payload_skeleton`.
#[pyfunction]
#[pyo3(name = "_service_payload_skeleton", signature = (markdown, url, metadata, max_links))]
fn service_payload_skeleton(
    py: Python<'_>,
    markdown: String,
    url: String,
    metadata: Bound<'_, PyAny>,
    max_links: i64,
) -> PyResult<Py<PyAny>> {
    service_payload_skeleton_inner(markdown, url, metadata, max_links)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_tool_envelope_inner(
    name: String,
    arguments: Bound<'_, PyAny>,
    result: Bound<'_, PyAny>,
) -> Result<Value, String> {
    let arguments: Value = crate::pyconv::FromPy::from_py(&arguments)?;
    let result: Value = crate::pyconv::FromPy::from_py(&result)?;
    let out = port::service_tool_envelope(name, arguments, result)?;
    Ok(out)
}

/// `_service_tool_envelope`.
#[pyfunction]
#[pyo3(name = "_service_tool_envelope", signature = (name, arguments, result))]
fn service_tool_envelope(
    py: Python<'_>,
    name: String,
    arguments: Bound<'_, PyAny>,
    result: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_tool_envelope_inner(name, arguments, result)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_route_inner(token: String) -> Result<Option<String>, String> {
    let out = port::service_route(token)?;
    Ok(out)
}

/// `_service_route`.
#[pyfunction]
#[pyo3(name = "_service_route", signature = (token))]
fn service_route(token: String) -> PyResult<Option<String>> {
    service_route_inner(token).map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_payload_skeleton, m)?)?;
    m.add_function(wrap_pyfunction!(service_tool_envelope, m)?)?;
    m.add_function(wrap_pyfunction!(service_route, m)?)?;
    Ok(())
}
