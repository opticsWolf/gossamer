//! Python bindings for `gossamer/models.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_fetch_stats_dict_inner(
    fetches: i64,
    errors: i64,
    bytes_downloaded: i64,
    latencies: Vec<f64>,
    by_domain: Bound<'_, PyAny>,
    by_error: Bound<'_, PyAny>,
) -> Result<Value, String> {
    let by_domain: Value = crate::pyconv::FromPy::from_py(&by_domain)?;
    let by_error: Value = crate::pyconv::FromPy::from_py(&by_error)?;
    let out = port::service_fetch_stats_dict(
        fetches,
        errors,
        bytes_downloaded,
        latencies,
        by_domain,
        by_error,
    )?;
    Ok(out)
}

/// `_service_fetch_stats_dict`.
#[pyfunction]
#[pyo3(name = "_service_fetch_stats_dict", signature = (fetches, errors, bytes_downloaded, latencies, by_domain, by_error))]
fn service_fetch_stats_dict(
    py: Python<'_>,
    fetches: i64,
    errors: i64,
    bytes_downloaded: i64,
    latencies: Vec<f64>,
    by_domain: Bound<'_, PyAny>,
    by_error: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_fetch_stats_dict_inner(
        fetches,
        errors,
        bytes_downloaded,
        latencies,
        by_domain,
        by_error,
    )
    .map_err(error)
    .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_fetch_stats_dict, m)?)?;
    Ok(())
}
