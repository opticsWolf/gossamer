//! Python bindings for `gossamer/open_access.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_normalize_doi_inner(value: Bound<'_, PyAny>) -> Result<String, String> {
    let value: Value = crate::pyconv::FromPy::from_py(&value)?;
    let out = port::service_normalize_doi(value)?;
    Ok(out)
}

/// `_service_normalize_doi`.
#[pyfunction]
#[pyo3(name = "_service_normalize_doi", signature = (value))]
fn service_normalize_doi(value: Bound<'_, PyAny>) -> PyResult<String> {
    service_normalize_doi_inner(value).map_err(error)
}

fn service_oa_candidate_inner(
    location: Bound<'_, PyAny>,
    is_best: bool,
    work_is_oa: bool,
) -> Result<Option<Value>, String> {
    let location: Value = crate::pyconv::FromPy::from_py(&location)?;
    let out = port::service_oa_candidate(location, is_best, work_is_oa)?;
    Ok(out)
}

/// `_service_oa_candidate`.
#[pyfunction]
#[pyo3(name = "_service_oa_candidate", signature = (location, is_best, work_is_oa))]
fn service_oa_candidate(
    py: Python<'_>,
    location: Bound<'_, PyAny>,
    is_best: bool,
    work_is_oa: bool,
) -> PyResult<Py<PyAny>> {
    service_oa_candidate_inner(location, is_best, work_is_oa)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_unpaywall_candidate_inner(
    location: Bound<'_, PyAny>,
    is_best: bool,
) -> Result<Option<Value>, String> {
    let location: Value = crate::pyconv::FromPy::from_py(&location)?;
    let out = port::service_unpaywall_candidate(location, is_best)?;
    Ok(out)
}

/// `_service_unpaywall_candidate`.
#[pyfunction]
#[pyo3(name = "_service_unpaywall_candidate", signature = (location, is_best))]
fn service_unpaywall_candidate(
    py: Python<'_>,
    location: Bound<'_, PyAny>,
    is_best: bool,
) -> PyResult<Py<PyAny>> {
    service_unpaywall_candidate_inner(location, is_best)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_unpaywall_candidates_inner(payload: Bound<'_, PyAny>) -> Result<Value, String> {
    let payload: Value = crate::pyconv::FromPy::from_py(&payload)?;
    let out = port::service_unpaywall_candidates(payload)?;
    Ok(out)
}

/// `_service_unpaywall_candidates`.
#[pyfunction]
#[pyo3(name = "_service_unpaywall_candidates", signature = (payload))]
fn service_unpaywall_candidates(py: Python<'_>, payload: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    service_unpaywall_candidates_inner(payload)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_normalize_doi, m)?)?;
    m.add_function(wrap_pyfunction!(service_oa_candidate, m)?)?;
    m.add_function(wrap_pyfunction!(service_unpaywall_candidate, m)?)?;
    m.add_function(wrap_pyfunction!(service_unpaywall_candidates, m)?)?;
    Ok(())
}
