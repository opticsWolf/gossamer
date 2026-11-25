//! Python bindings for `gossamer/downloader.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_candidate_list_inner(
    source: Option<Bound<'_, PyAny>>,
    fallback_urls: Option<Bound<'_, PyAny>>,
) -> Result<Value, String> {
    let source: Option<Value> = match &source {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let fallback_urls: Option<Value> = match &fallback_urls {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::service_candidate_list(source, fallback_urls)?;
    Ok(out)
}

/// `_service_candidate_list`.
#[pyfunction]
#[pyo3(name = "_service_candidate_list", signature = (source, fallback_urls))]
fn service_candidate_list(
    py: Python<'_>,
    source: Option<Bound<'_, PyAny>>,
    fallback_urls: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    service_candidate_list_inner(source, fallback_urls)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_finalize_candidates_inner(
    source: String,
    candidates: Vec<String>,
    attempts: Bound<'_, PyAny>,
    full_results: Option<Bound<'_, PyAny>>,
) -> Result<Value, String> {
    let attempts: Vec<Value> = crate::pyconv::FromPy::from_py(&attempts)?;
    let full_results: Option<Vec<Value>> = match &full_results {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::service_finalize_candidates(source, candidates, attempts, full_results)?;
    Ok(out)
}

/// `_service_finalize_candidates`.
#[pyfunction]
#[pyo3(name = "_service_finalize_candidates", signature = (source, candidates, attempts, full_results=None))]
fn service_finalize_candidates(
    py: Python<'_>,
    source: String,
    candidates: Vec<String>,
    attempts: Bound<'_, PyAny>,
    full_results: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    service_finalize_candidates_inner(source, candidates, attempts, full_results)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_http_error_inner(
    status: i64,
    content_type: Option<String>,
    sample: Bound<'_, PyAny>,
    url: String,
    retry_after: Option<String>,
) -> Result<Value, String> {
    let sample: Value = crate::pyconv::FromPy::from_py(&sample)?;
    let out = port::service_http_error(status, content_type, sample, url, retry_after)?;
    Ok(out)
}

/// `_service_http_error`.
#[pyfunction]
#[pyo3(name = "_service_http_error", signature = (status, content_type, sample, url, retry_after))]
fn service_http_error(
    py: Python<'_>,
    status: i64,
    content_type: Option<String>,
    sample: Bound<'_, PyAny>,
    url: String,
    retry_after: Option<String>,
) -> PyResult<Py<PyAny>> {
    service_http_error_inner(status, content_type, sample, url, retry_after)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_candidate_list, m)?)?;
    m.add_function(wrap_pyfunction!(service_finalize_candidates, m)?)?;
    m.add_function(wrap_pyfunction!(service_http_error, m)?)?;
    Ok(())
}
