//! Python bindings for `gossamer/crawl.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn service_crawl_score_inner(
    label: Vec<String>,
    query_terms: Vec<String>,
    page_terms: Vec<String>,
    idf: Option<Bound<'_, PyAny>>,
    base_terms: Option<Bound<'_, PyAny>>,
    corpus_n: i64,
    path_prior: f64,
) -> Result<f64, String> {
    let idf: Option<Value> = match &idf {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let base_terms: Option<Value> = match &base_terms {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::service_crawl_score(
        label,
        query_terms,
        page_terms,
        idf,
        base_terms,
        corpus_n,
        path_prior,
    )?;
    Ok(out)
}

/// `_service_crawl_score`.
#[pyfunction]
#[pyo3(name = "_service_crawl_score", signature = (label, query_terms, page_terms, idf, base_terms, corpus_n, path_prior))]
fn service_crawl_score(
    label: Vec<String>,
    query_terms: Vec<String>,
    page_terms: Vec<String>,
    idf: Option<Bound<'_, PyAny>>,
    base_terms: Option<Bound<'_, PyAny>>,
    corpus_n: i64,
    path_prior: f64,
) -> PyResult<f64> {
    service_crawl_score_inner(
        label,
        query_terms,
        page_terms,
        idf,
        base_terms,
        corpus_n,
        path_prior,
    )
    .map_err(error)
}

fn service_shrink_crawl_inner(
    result: Bound<'_, PyAny>,
    budget: Option<i64>,
) -> Result<String, String> {
    let result: Value = crate::pyconv::FromPy::from_py(&result)?;
    let out = port::service_shrink_crawl(result, budget)?;
    Ok(out)
}

/// `_service_shrink_crawl`.
#[pyfunction]
#[pyo3(name = "_service_shrink_crawl", signature = (result, budget))]
fn service_shrink_crawl(result: Bound<'_, PyAny>, budget: Option<i64>) -> PyResult<String> {
    service_shrink_crawl_inner(result, budget).map_err(error)
}

fn service_crawl_tokens_inner(text: Option<String>) -> Result<Vec<String>, String> {
    let out = port::service_crawl_tokens(text)?;
    Ok(out)
}

/// `_service_crawl_tokens`.
#[pyfunction]
#[pyo3(name = "_service_crawl_tokens", signature = (text))]
fn service_crawl_tokens(text: Option<String>) -> PyResult<Vec<String>> {
    service_crawl_tokens_inner(text).map_err(error)
}

fn service_crawl_topic_words_inner(
    text: Option<String>,
    limit: i64,
) -> Result<Vec<String>, String> {
    let out = port::service_crawl_topic_words(text, limit)?;
    Ok(out)
}

/// `_service_crawl_topic_words`.
#[pyfunction]
#[pyo3(name = "_service_crawl_topic_words", signature = (text, limit))]
fn service_crawl_topic_words(text: Option<String>, limit: i64) -> PyResult<Vec<String>> {
    service_crawl_topic_words_inner(text, limit).map_err(error)
}

fn service_crawl_is_document_inner(url: String) -> Result<bool, String> {
    let out = port::service_crawl_is_document(url)?;
    Ok(out)
}

/// `_service_crawl_is_document`.
#[pyfunction]
#[pyo3(name = "_service_crawl_is_document", signature = (url))]
fn service_crawl_is_document(url: String) -> PyResult<bool> {
    service_crawl_is_document_inner(url).map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_crawl_score, m)?)?;
    m.add_function(wrap_pyfunction!(service_shrink_crawl, m)?)?;
    m.add_function(wrap_pyfunction!(service_crawl_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(service_crawl_topic_words, m)?)?;
    m.add_function(wrap_pyfunction!(service_crawl_is_document, m)?)?;
    Ok(())
}
