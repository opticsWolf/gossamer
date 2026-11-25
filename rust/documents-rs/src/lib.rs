//! Python bindings for `gossamer/structured_parser.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn office_ir_tables_inner(ir: Bound<'_, PyAny>) -> Result<Vec<Value>, String> {
    let ir: Value = crate::pyconv::FromPy::from_py(&ir)?;
    let out = port::office_ir_tables(ir)?;
    Ok(out)
}

/// `office_ir_tables`.
#[pyfunction]
#[pyo3(name = "office_ir_tables", signature = (ir))]
fn office_ir_tables(py: Python<'_>, ir: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    office_ir_tables_inner(ir)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn office_cell_text_inner(cell: Bound<'_, PyAny>) -> Result<String, String> {
    let cell: Value = crate::pyconv::FromPy::from_py(&cell)?;
    let out = port::office_cell_text(cell)?;
    Ok(out)
}

/// `_office_cell_text`.
#[pyfunction]
#[pyo3(name = "_office_cell_text", signature = (cell))]
fn office_cell_text(cell: Bound<'_, PyAny>) -> PyResult<String> {
    office_cell_text_inner(cell).map_err(error)
}

fn build_follow_up_candidates_inner(
    anchored_links: Bound<'_, PyAny>,
    max_links: i64,
) -> Result<Vec<Value>, String> {
    let anchored_links: Vec<Value> = crate::pyconv::FromPy::from_py(&anchored_links)?;
    let out = port::build_follow_up_candidates(anchored_links, max_links)?;
    Ok(out)
}

/// `build_follow_up_candidates`.
#[pyfunction]
#[pyo3(name = "build_follow_up_candidates", signature = (anchored_links, max_links=0))]
fn build_follow_up_candidates(
    py: Python<'_>,
    anchored_links: Bound<'_, PyAny>,
    max_links: i64,
) -> PyResult<Py<PyAny>> {
    build_follow_up_candidates_inner(anchored_links, max_links)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn absolutize_markdown_links_inner(markdown: String, base_url: String) -> Result<String, String> {
    let out = port::absolutize_markdown_links(markdown, base_url)?;
    Ok(out)
}

/// `_absolutize_markdown_links`.
#[pyfunction]
#[pyo3(name = "_absolutize_markdown_links", signature = (markdown, base_url))]
fn absolutize_markdown_links(markdown: String, base_url: String) -> PyResult<String> {
    absolutize_markdown_links_inner(markdown, base_url).map_err(error)
}

fn service_normalize_batch_results_inner(results: Bound<'_, PyAny>) -> Result<Vec<Value>, String> {
    let results: Vec<Value> = crate::pyconv::FromPy::from_py(&results)?;
    let out = port::service_normalize_batch_results(results)?;
    Ok(out)
}

/// `_service_normalize_batch_results`.
#[pyfunction]
#[pyo3(name = "_service_normalize_batch_results", signature = (results))]
fn service_normalize_batch_results(
    py: Python<'_>,
    results: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_normalize_batch_results_inner(results)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_sanitize_filename_inner(
    name: String,
    fallback: Option<String>,
) -> Result<String, String> {
    let out = port::service_sanitize_filename(name, fallback)?;
    Ok(out)
}

/// `_service_sanitize_filename`.
#[pyfunction]
#[pyo3(name = "_service_sanitize_filename", signature = (name, fallback))]
fn service_sanitize_filename(name: String, fallback: Option<String>) -> PyResult<String> {
    service_sanitize_filename_inner(name, fallback).map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(office_ir_tables, m)?)?;
    m.add_function(wrap_pyfunction!(office_cell_text, m)?)?;
    m.add_function(wrap_pyfunction!(build_follow_up_candidates, m)?)?;
    m.add_function(wrap_pyfunction!(absolutize_markdown_links, m)?)?;
    m.add_function(wrap_pyfunction!(service_normalize_batch_results, m)?)?;
    m.add_function(wrap_pyfunction!(service_sanitize_filename, m)?)?;
    Ok(())
}
