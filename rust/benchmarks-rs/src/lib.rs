//! Python bindings for `benchmarks.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use pyo3::prelude::*;

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

fn arrow_inner(label: String, value: String) -> Result<serde_json::Value, String> {
    let out = port::arrow(label, value)?;
    Ok(out)
}

/// `arrow`.
#[pyfunction]
#[pyo3(name = "arrow", signature = (label, value))]
fn arrow(py: Python<'_>, label: String, value: String) -> PyResult<Py<PyAny>> {
    arrow_inner(label, value)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn bench_rust_fetch_inner() -> Result<serde_json::Value, String> {
    let out = port::bench_rust_fetch()?;
    Ok(out)
}

/// `bench_rust_fetch`.
#[pyfunction]
#[pyo3(name = "bench_rust_fetch", signature = ())]
fn bench_rust_fetch(py: Python<'_>) -> PyResult<Py<PyAny>> {
    bench_rust_fetch_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn bench_meta_oxide_inner() -> Result<serde_json::Value, String> {
    let out = port::bench_meta_oxide()?;
    Ok(out)
}

/// `bench_meta_oxide`.
#[pyfunction]
#[pyo3(name = "bench_meta_oxide", signature = ())]
fn bench_meta_oxide(py: Python<'_>) -> PyResult<Py<PyAny>> {
    bench_meta_oxide_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn bench_token_budget_inner() -> Result<serde_json::Value, String> {
    let out = port::bench_token_budget()?;
    Ok(out)
}

/// `bench_token_budget`.
#[pyfunction]
#[pyo3(name = "bench_token_budget", signature = ())]
fn bench_token_budget(py: Python<'_>) -> PyResult<Py<PyAny>> {
    bench_token_budget_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn bench_batch_fetch_inner() -> Result<serde_json::Value, String> {
    let out = port::bench_batch_fetch()?;
    Ok(out)
}

/// `bench_batch_fetch`.
#[pyfunction]
#[pyo3(name = "bench_batch_fetch", signature = ())]
fn bench_batch_fetch(py: Python<'_>) -> PyResult<Py<PyAny>> {
    bench_batch_fetch_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn bench_structured_parser_inner() -> Result<serde_json::Value, String> {
    let out = port::bench_structured_parser()?;
    Ok(out)
}

/// `bench_structured_parser`.
#[pyfunction]
#[pyo3(name = "bench_structured_parser", signature = ())]
fn bench_structured_parser(py: Python<'_>) -> PyResult<Py<PyAny>> {
    bench_structured_parser_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn load_guard_corpus_inner() -> Result<serde_json::Value, String> {
    let out = port::load_guard_corpus()?;
    Ok(out)
}

/// `load_guard_corpus`.
#[pyfunction]
#[pyo3(name = "load_guard_corpus", signature = ())]
fn load_guard_corpus(py: Python<'_>) -> PyResult<Py<PyAny>> {
    load_guard_corpus_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn bench_guard_inner() -> Result<serde_json::Value, String> {
    let out = port::bench_guard()?;
    Ok(out)
}

/// `bench_guard`.
#[pyfunction]
#[pyo3(name = "bench_guard", signature = ())]
fn bench_guard(py: Python<'_>) -> PyResult<Py<PyAny>> {
    bench_guard_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn bench_guard_corpus_inner() -> Result<serde_json::Value, String> {
    let out = port::bench_guard_corpus()?;
    Ok(out)
}

/// `bench_guard_corpus`.
#[pyfunction]
#[pyo3(name = "bench_guard_corpus", signature = ())]
fn bench_guard_corpus(py: Python<'_>) -> PyResult<Py<PyAny>> {
    bench_guard_corpus_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(arrow, m)?)?;
    m.add_function(wrap_pyfunction!(bench_rust_fetch, m)?)?;
    m.add_function(wrap_pyfunction!(bench_meta_oxide, m)?)?;
    m.add_function(wrap_pyfunction!(bench_token_budget, m)?)?;
    m.add_function(wrap_pyfunction!(bench_batch_fetch, m)?)?;
    m.add_function(wrap_pyfunction!(bench_structured_parser, m)?)?;
    m.add_function(wrap_pyfunction!(load_guard_corpus, m)?)?;
    m.add_function(wrap_pyfunction!(bench_guard, m)?)?;
    m.add_function(wrap_pyfunction!(bench_guard_corpus, m)?)?;
    Ok(())
}
