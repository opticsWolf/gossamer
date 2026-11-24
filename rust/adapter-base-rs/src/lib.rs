//! Python bindings for `gossamer/search_providers.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_retryable_status_inner(status: i64) -> Result<bool, String> {
    let out = port::service_retryable_status(status)?;
    Ok(out)
}

/// `_service_retryable_status`.
#[pyfunction]
#[pyo3(name = "_service_retryable_status", signature = (status))]
fn service_retryable_status(status: i64) -> PyResult<bool> {
    service_retryable_status_inner(status).map_err(error)
}

fn service_retry_wait_inner(
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> Result<Option<f64>, String> {
    let out = port::service_retry_wait(
        attempt,
        max_attempts,
        delay,
        backoff,
        retry_after,
        jitter_value,
    )?;
    Ok(out)
}

/// `_service_retry_wait`.
#[pyfunction]
#[pyo3(name = "_service_retry_wait", signature = (attempt, max_attempts, delay, backoff, retry_after, jitter_value))]
fn service_retry_wait(
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> PyResult<Option<f64>> {
    service_retry_wait_inner(
        attempt,
        max_attempts,
        delay,
        backoff,
        retry_after,
        jitter_value,
    )
    .map_err(error)
}

fn service_quota_message_inner(
    name: String,
    used: i64,
    quota: i64,
    window: String,
) -> Result<String, String> {
    let out = port::service_quota_message(name, used, quota, window)?;
    Ok(out)
}

/// `_service_quota_message`.
#[pyfunction]
#[pyo3(name = "_service_quota_message", signature = (name, used, quota, window))]
fn service_quota_message(name: String, used: i64, quota: i64, window: String) -> PyResult<String> {
    service_quota_message_inner(name, used, quota, window).map_err(error)
}

fn service_politeness_wait_inner(
    last_search: f64,
    now: f64,
    delay: f64,
    jitter: f64,
    jitter_value: f64,
) -> Result<f64, String> {
    let out = port::service_politeness_wait(last_search, now, delay, jitter, jitter_value)?;
    Ok(out)
}

/// `_service_politeness_wait`.
#[pyfunction]
#[pyo3(name = "_service_politeness_wait", signature = (last_search, now, delay, jitter, jitter_value))]
fn service_politeness_wait(
    last_search: f64,
    now: f64,
    delay: f64,
    jitter: f64,
    jitter_value: f64,
) -> PyResult<f64> {
    service_politeness_wait_inner(last_search, now, delay, jitter, jitter_value).map_err(error)
}

fn service_retry_plan_inner(
    kind: String,
    status: Option<i64>,
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> Result<Value, String> {
    let out = port::service_retry_plan(
        kind,
        status,
        attempt,
        max_attempts,
        delay,
        backoff,
        retry_after,
        jitter_value,
    )?;
    Ok(out)
}

/// `_service_retry_plan`.
#[pyfunction]
#[pyo3(name = "_service_retry_plan", signature = (kind, status, attempt, max_attempts, delay, backoff, retry_after, jitter_value))]
fn service_retry_plan(
    py: Python<'_>,
    kind: String,
    status: Option<i64>,
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> PyResult<Py<PyAny>> {
    service_retry_plan_inner(
        kind,
        status,
        attempt,
        max_attempts,
        delay,
        backoff,
        retry_after,
        jitter_value,
    )
    .map_err(error)
    .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn service_quota_period_inner(window: String, now_iso: String) -> Result<String, String> {
    let out = port::service_quota_period(window, now_iso)?;
    Ok(out)
}

/// `_service_quota_period`.
#[pyfunction]
#[pyo3(name = "_service_quota_period", signature = (window, now_iso))]
fn service_quota_period(window: String, now_iso: String) -> PyResult<String> {
    service_quota_period_inner(window, now_iso).map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_retryable_status, m)?)?;
    m.add_function(wrap_pyfunction!(service_retry_wait, m)?)?;
    m.add_function(wrap_pyfunction!(service_quota_message, m)?)?;
    m.add_function(wrap_pyfunction!(service_politeness_wait, m)?)?;
    m.add_function(wrap_pyfunction!(service_retry_plan, m)?)?;
    m.add_function(wrap_pyfunction!(service_quota_period, m)?)?;
    Ok(())
}
