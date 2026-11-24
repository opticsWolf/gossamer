//! Python bindings for `gossamer/meta_extractor.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_merge_into_document_metadata_inner(
    html_meta: Bound<'_, PyAny>,
    base_metadata: Bound<'_, PyAny>,
) -> Result<Value, String> {
    let html_meta: Value = crate::pyconv::FromPy::from_py(&html_meta)?;
    let base_metadata: Value = crate::pyconv::FromPy::from_py(&base_metadata)?;
    let out = port::service_merge_into_document_metadata(html_meta, base_metadata)?;
    Ok(out)
}

/// `_service_merge_into_document_metadata`.
#[pyfunction]
#[pyo3(name = "_service_merge_into_document_metadata", signature = (html_meta, base_metadata))]
fn service_merge_into_document_metadata(
    py: Python<'_>,
    html_meta: Bound<'_, PyAny>,
    base_metadata: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_merge_into_document_metadata_inner(html_meta, base_metadata)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_merge_into_document_metadata, m)?)?;
    Ok(())
}
