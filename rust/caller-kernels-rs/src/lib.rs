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

fn service_inspect_request_inner(
    url: String,
    use_smart: String,
    query: Option<String>,
    offset: i64,
    max_chunks: i64,
    structured: bool,
    store_dir: Option<String>,
) -> Result<Value, String> {
    let out = port::service_inspect_request(
        url, use_smart, query, offset, max_chunks, structured, store_dir,
    )?;
    Ok(out)
}

/// `_service_inspect_request`.
#[pyfunction]
#[pyo3(name = "_service_inspect_request", signature = (url, use_smart, query, offset, max_chunks, structured, store_dir))]
fn service_inspect_request(
    py: Python<'_>,
    url: String,
    use_smart: String,
    query: Option<String>,
    offset: i64,
    max_chunks: i64,
    structured: bool,
    store_dir: Option<String>,
) -> PyResult<Py<PyAny>> {
    service_inspect_request_inner(
        url, use_smart, query, offset, max_chunks, structured, store_dir,
    )
    .map_err(error)
    .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_extract_validation_inner(
    tables_as: String,
    include_images: bool,
    structured: bool,
    store: bool,
) -> Result<Option<Value>, String> {
    let out = port::service_extract_validation(tables_as, include_images, structured, store)?;
    Ok(out)
}

/// `_service_extract_validation`.
#[pyfunction]
#[pyo3(name = "_service_extract_validation", signature = (tables_as, include_images, structured, store))]
fn service_extract_validation(
    py: Python<'_>,
    tables_as: String,
    include_images: bool,
    structured: bool,
    store: bool,
) -> PyResult<Py<PyAny>> {
    service_extract_validation_inner(tables_as, include_images, structured, store)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_batch_plan_inner(
    urls: Option<Bound<'_, PyAny>>,
    cached_keys: Bound<'_, PyAny>,
    visited_keys: Bound<'_, PyAny>,
    strategy: String,
) -> Result<Value, String> {
    let urls: Option<Value> = match &urls {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let cached_keys: Value = crate::pyconv::FromPy::from_py(&cached_keys)?;
    let visited_keys: Value = crate::pyconv::FromPy::from_py(&visited_keys)?;
    let out = port::service_batch_plan(urls, cached_keys, visited_keys, strategy)?;
    Ok(out)
}

/// `_service_batch_plan`.
#[pyfunction]
#[pyo3(name = "_service_batch_plan", signature = (urls, cached_keys, visited_keys, strategy))]
fn service_batch_plan(
    py: Python<'_>,
    urls: Option<Bound<'_, PyAny>>,
    cached_keys: Bound<'_, PyAny>,
    visited_keys: Bound<'_, PyAny>,
    strategy: String,
) -> PyResult<Py<PyAny>> {
    service_batch_plan_inner(urls, cached_keys, visited_keys, strategy)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_normalize_rate_limit_inner(
    delay: Option<Bound<'_, PyAny>>,
    fetch_delay: Option<f64>,
    jitter: Option<f64>,
) -> Result<Value, String> {
    let delay: Option<Value> = match &delay {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::service_normalize_rate_limit(delay, fetch_delay, jitter)?;
    Ok(out)
}

/// `_service_normalize_rate_limit`.
#[pyfunction]
#[pyo3(name = "_service_normalize_rate_limit", signature = (delay, fetch_delay, jitter))]
fn service_normalize_rate_limit(
    py: Python<'_>,
    delay: Option<Bound<'_, PyAny>>,
    fetch_delay: Option<f64>,
    jitter: Option<f64>,
) -> PyResult<Py<PyAny>> {
    service_normalize_rate_limit_inner(delay, fetch_delay, jitter)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_web_search_route_inner(search_only: bool) -> Result<String, String> {
    let out = port::service_web_search_route(search_only)?;
    Ok(out)
}

/// `_service_web_search_route`.
#[pyfunction]
#[pyo3(name = "_service_web_search_route", signature = (search_only))]
fn service_web_search_route(search_only: bool) -> PyResult<String> {
    service_web_search_route_inner(search_only).map_err(error)
}

fn service_search_cache_key_inner(
    query: Option<String>,
    max_results: i64,
    provider: Option<String>,
    merge: bool,
) -> Result<String, String> {
    let out = port::service_search_cache_key(query, max_results, provider, merge)?;
    Ok(out)
}

/// `_service_search_cache_key`.
#[pyfunction]
#[pyo3(name = "_service_search_cache_key", signature = (query, max_results, provider, merge))]
fn service_search_cache_key(
    query: Option<String>,
    max_results: i64,
    provider: Option<String>,
    merge: bool,
) -> PyResult<String> {
    service_search_cache_key_inner(query, max_results, provider, merge).map_err(error)
}

fn service_doc_extract_validation_inner(
    tables_as: String,
    include_images: bool,
    structured: bool,
    store: bool,
) -> Result<Option<Value>, String> {
    let out = port::service_doc_extract_validation(tables_as, include_images, structured, store)?;
    Ok(out)
}

/// `_service_doc_extract_validation`.
#[pyfunction]
#[pyo3(name = "_service_doc_extract_validation", signature = (tables_as, include_images, structured, store))]
fn service_doc_extract_validation(
    py: Python<'_>,
    tables_as: String,
    include_images: bool,
    structured: bool,
    store: bool,
) -> PyResult<Py<PyAny>> {
    service_doc_extract_validation_inner(tables_as, include_images, structured, store)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_throttle_gap_inner(
    elapsed: f64,
    fetch_interval: f64,
    jitter_value: f64,
    crawl_delay: Option<f64>,
) -> Result<Value, String> {
    let out = port::service_throttle_gap(elapsed, fetch_interval, jitter_value, crawl_delay)?;
    Ok(out)
}

/// `_service_throttle_gap`.
#[pyfunction]
#[pyo3(name = "_service_throttle_gap", signature = (elapsed, fetch_interval, jitter_value, crawl_delay))]
fn service_throttle_gap(
    py: Python<'_>,
    elapsed: f64,
    fetch_interval: f64,
    jitter_value: f64,
    crawl_delay: Option<f64>,
) -> PyResult<Py<PyAny>> {
    service_throttle_gap_inner(elapsed, fetch_interval, jitter_value, crawl_delay)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_inspect_request, m)?)?;
    m.add_function(wrap_pyfunction!(service_extract_validation, m)?)?;
    m.add_function(wrap_pyfunction!(service_batch_plan, m)?)?;
    m.add_function(wrap_pyfunction!(service_normalize_rate_limit, m)?)?;
    m.add_function(wrap_pyfunction!(service_web_search_route, m)?)?;
    m.add_function(wrap_pyfunction!(service_search_cache_key, m)?)?;
    m.add_function(wrap_pyfunction!(service_doc_extract_validation, m)?)?;
    m.add_function(wrap_pyfunction!(service_throttle_gap, m)?)?;
    Ok(())
}
