//! Python bindings for `gossamer/fetch.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_strategy_for_inner(fetch_mode: String, use_smart: String) -> Result<String, String> {
    let out = port::service_strategy_for(fetch_mode, use_smart)?;
    Ok(out)
}

/// `_service_strategy_for`.
#[pyfunction]
#[pyo3(name = "_service_strategy_for", signature = (fetch_mode, use_smart))]
fn service_strategy_for(fetch_mode: String, use_smart: String) -> PyResult<String> {
    service_strategy_for_inner(fetch_mode, use_smart).map_err(error)
}

fn service_strategy_accepts_inner(
    strategy: String,
    method: Option<Bound<'_, PyAny>>,
    cached_strategy: Option<String>,
) -> Result<bool, String> {
    let method: Option<Value> = match &method {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::service_strategy_accepts(strategy, method, cached_strategy)?;
    Ok(out)
}

/// `_service_strategy_accepts`.
#[pyfunction]
#[pyo3(name = "_service_strategy_accepts", signature = (strategy, method=None, cached_strategy=None))]
fn service_strategy_accepts(
    strategy: String,
    method: Option<Bound<'_, PyAny>>,
    cached_strategy: Option<String>,
) -> PyResult<bool> {
    service_strategy_accepts_inner(strategy, method, cached_strategy).map_err(error)
}

fn service_cached_matches_strategy_inner(
    cached: Bound<'_, PyAny>,
    strategy: String,
) -> Result<bool, String> {
    let cached: Value = crate::pyconv::FromPy::from_py(&cached)?;
    let out = port::service_cached_matches_strategy(cached, strategy)?;
    Ok(out)
}

/// `_service_cached_matches_strategy`.
#[pyfunction]
#[pyo3(name = "_service_cached_matches_strategy", signature = (cached, strategy))]
fn service_cached_matches_strategy(cached: Bound<'_, PyAny>, strategy: String) -> PyResult<bool> {
    service_cached_matches_strategy_inner(cached, strategy).map_err(error)
}

fn service_raw_entry_matches_strategy_inner(
    entry: Bound<'_, PyAny>,
    strategy: String,
) -> Result<bool, String> {
    let entry: Value = crate::pyconv::FromPy::from_py(&entry)?;
    let out = port::service_raw_entry_matches_strategy(entry, strategy)?;
    Ok(out)
}

/// `_service_raw_entry_matches_strategy`.
#[pyfunction]
#[pyo3(name = "_service_raw_entry_matches_strategy", signature = (entry, strategy))]
fn service_raw_entry_matches_strategy(entry: Bound<'_, PyAny>, strategy: String) -> PyResult<bool> {
    service_raw_entry_matches_strategy_inner(entry, strategy).map_err(error)
}

fn service_looks_like_text_inner(md: String) -> Result<bool, String> {
    let out = port::service_looks_like_text(md)?;
    Ok(out)
}

/// `_service_looks_like_text`.
#[pyfunction]
#[pyo3(name = "_service_looks_like_text", signature = (md))]
fn service_looks_like_text(md: String) -> PyResult<bool> {
    service_looks_like_text_inner(md).map_err(error)
}

fn service_slice_markdown_inner(
    markdown: String,
    offset: i64,
    max_chunks: i64,
    md_chars: i64,
    md_tokens: i64,
    model: String,
) -> Result<Value, String> {
    let out =
        port::service_slice_markdown(markdown, offset, max_chunks, md_chars, md_tokens, model)?;
    Ok(out)
}

/// `_service_slice_markdown`.
#[pyfunction]
#[pyo3(name = "_service_slice_markdown", signature = (markdown, offset, max_chunks, md_chars, md_tokens, model))]
fn service_slice_markdown(
    py: Python<'_>,
    markdown: String,
    offset: i64,
    max_chunks: i64,
    md_chars: i64,
    md_tokens: i64,
    model: String,
) -> PyResult<Py<PyAny>> {
    service_slice_markdown_inner(markdown, offset, max_chunks, md_chars, md_tokens, model)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_compact_metadata_inner(raw: Bound<'_, PyAny>) -> Result<Value, String> {
    let raw: Value = crate::pyconv::FromPy::from_py(&raw)?;
    let out = port::service_compact_metadata(raw)?;
    Ok(out)
}

/// `_service_compact_metadata`.
#[pyfunction]
#[pyo3(name = "_service_compact_metadata", signature = (raw))]
fn service_compact_metadata(py: Python<'_>, raw: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    service_compact_metadata_inner(raw)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_format_follow_ups_inner(anchored_links: Bound<'_, PyAny>) -> Result<Vec<Value>, String> {
    let anchored_links: Vec<Value> = crate::pyconv::FromPy::from_py(&anchored_links)?;
    let out = port::service_format_follow_ups(anchored_links)?;
    Ok(out)
}

/// `_service_format_follow_ups`.
#[pyfunction]
#[pyo3(name = "_service_format_follow_ups", signature = (anchored_links))]
fn service_format_follow_ups(
    py: Python<'_>,
    anchored_links: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    service_format_follow_ups_inner(anchored_links)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_strategy_for, m)?)?;
    m.add_function(wrap_pyfunction!(service_strategy_accepts, m)?)?;
    m.add_function(wrap_pyfunction!(service_cached_matches_strategy, m)?)?;
    m.add_function(wrap_pyfunction!(service_raw_entry_matches_strategy, m)?)?;
    m.add_function(wrap_pyfunction!(service_looks_like_text, m)?)?;
    m.add_function(wrap_pyfunction!(service_slice_markdown, m)?)?;
    m.add_function(wrap_pyfunction!(service_compact_metadata, m)?)?;
    m.add_function(wrap_pyfunction!(service_format_follow_ups, m)?)?;
    Ok(())
}
