//! Python bindings for `gossamer/citations.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_citation_style_inner(style: Option<String>) -> Result<Value, String> {
    let out = port::service_citation_style(style)?;
    Ok(Value::String(out))
}

/// `_service_citation_style`.
#[pyfunction]
#[pyo3(name = "_service_citation_style", signature = (style))]
fn service_citation_style(py: Python<'_>, style: Option<String>) -> PyResult<Py<PyAny>> {
    service_citation_style_inner(style)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_citation_input_inner(value: String) -> Result<Value, String> {
    let out = port::service_citation_input(value)?;
    Ok(out)
}

/// `_service_citation_input`.
#[pyfunction]
#[pyo3(name = "_service_citation_input", signature = (value))]
fn service_citation_input(py: Python<'_>, value: String) -> PyResult<Py<PyAny>> {
    service_citation_input_inner(value)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_find_doi_in_text_inner(text: Bound<'_, PyAny>) -> Result<Option<String>, String> {
    let text: Value = crate::pyconv::FromPy::from_py(&text)?;
    let out = port::service_find_doi_in_text(text)?;
    Ok(out)
}

/// `_service_find_doi_in_text`.
#[pyfunction]
#[pyo3(name = "_service_find_doi_in_text", signature = (text))]
fn service_find_doi_in_text(text: Bound<'_, PyAny>) -> PyResult<Option<String>> {
    service_find_doi_in_text_inner(text).map_err(error)
}

fn service_enrichment_plan_inner(
    record: Bound<'_, PyAny>,
    hits: Bound<'_, PyAny>,
) -> Result<Value, String> {
    let record: Value = crate::pyconv::FromPy::from_py(&record)?;
    let hits: Value = crate::pyconv::FromPy::from_py(&hits)?;
    let out = port::service_enrichment_plan(record, hits)?;
    Ok(out)
}

/// `_service_enrichment_plan`.
#[pyfunction]
#[pyo3(name = "_service_enrichment_plan", signature = (record, hits))]
fn service_enrichment_plan(
    py: Python<'_>,
    record: Bound<'_, PyAny>,
    hits: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_enrichment_plan_inner(record, hits)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_citation_style, m)?)?;
    m.add_function(wrap_pyfunction!(service_citation_input, m)?)?;
    m.add_function(wrap_pyfunction!(service_find_doi_in_text, m)?)?;
    m.add_function(wrap_pyfunction!(service_enrichment_plan, m)?)?;
    Ok(())
}
