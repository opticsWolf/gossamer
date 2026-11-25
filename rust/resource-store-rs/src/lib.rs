//! Python bindings for `gossamer/resource_store.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_detect_ext_from_magic_inner(data: Bound<'_, PyAny>) -> Result<Option<String>, String> {
    let data: Value = crate::pyconv::FromPy::from_py(&data)?;
    let out = port::service_detect_ext_from_magic(data)?;
    Ok(out)
}

/// `_service_detect_ext_from_magic`.
#[pyfunction]
#[pyo3(name = "_service_detect_ext_from_magic", signature = (data))]
fn service_detect_ext_from_magic(data: Bound<'_, PyAny>) -> PyResult<Option<String>> {
    service_detect_ext_from_magic_inner(data).map_err(error)
}

fn service_extract_image_refs_inner(
    markdown: String,
    base_url: String,
) -> Result<Vec<Value>, String> {
    let out = port::service_extract_image_refs(markdown, base_url)?;
    Ok(out)
}

/// `_service_extract_image_refs`.
#[pyfunction]
#[pyo3(name = "_service_extract_image_refs", signature = (markdown, base_url))]
fn service_extract_image_refs(
    py: Python<'_>,
    markdown: String,
    base_url: String,
) -> PyResult<Py<PyAny>> {
    service_extract_image_refs_inner(markdown, base_url)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_rewrite_image_refs_inner(
    refs: Bound<'_, PyAny>,
    markdown: String,
    stem: String,
    resolutions: Bound<'_, PyAny>,
) -> Result<Value, String> {
    let refs: Vec<Value> = crate::pyconv::FromPy::from_py(&refs)?;
    let resolutions: Value = crate::pyconv::FromPy::from_py(&resolutions)?;
    let out = port::service_rewrite_image_refs(refs, markdown, stem, resolutions)?;
    Ok(out)
}

/// `_service_rewrite_image_refs`.
#[pyfunction]
#[pyo3(name = "_service_rewrite_image_refs", signature = (refs, markdown, stem, resolutions))]
fn service_rewrite_image_refs(
    py: Python<'_>,
    refs: Bound<'_, PyAny>,
    markdown: String,
    stem: String,
    resolutions: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_rewrite_image_refs_inner(refs, markdown, stem, resolutions)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_embedded_figures_inner(
    markdown: String,
    stem: String,
    images: Bound<'_, PyAny>,
) -> Result<Value, String> {
    let images: Value = crate::pyconv::FromPy::from_py(&images)?;
    let out = port::service_embedded_figures(markdown, stem, images)?;
    Ok(out)
}

/// `_service_embedded_figures`.
#[pyfunction]
#[pyo3(name = "_service_embedded_figures", signature = (markdown, stem, images))]
fn service_embedded_figures(
    py: Python<'_>,
    markdown: String,
    stem: String,
    images: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_embedded_figures_inner(markdown, stem, images)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_detect_ext_from_magic, m)?)?;
    m.add_function(wrap_pyfunction!(service_extract_image_refs, m)?)?;
    m.add_function(wrap_pyfunction!(service_rewrite_image_refs, m)?)?;
    m.add_function(wrap_pyfunction!(service_embedded_figures, m)?)?;
    Ok(())
}
