//! PyO3 compatibility surface. All parsing logic lives in `gossamer::adapters::scholar`.

use pyo3::prelude::*;
use serde_json::Value;

use crate::adapters::common::to_py_err;
use crate::pycompat::runtime_minor;

use gossamer::adapters::common::AdapterError;
use gossamer::adapters::scholar as core;

fn core_call<T>(result: std::result::Result<T, AdapterError>) -> std::result::Result<T, String> {
    result.map_err(|e| e.message().to_owned())
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn zenodo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::zenodo_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn zenodo_parse_fetch(py: Python, response_json: &str, record_id_s: &str) -> PyResult<String> {
    core_call(core::zenodo_parse_fetch_impl(response_json, record_id_s, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, server = "biorxiv"))]
pub fn biorxiv_parse_collection(
    py: Python,
    response_json: &str,
    max_results: i64,
    server: &str,
) -> PyResult<String> {
    core_call(core::biorxiv_parse_collection_impl(response_json, max_results, server, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, server = "biorxiv"))]
pub fn biorxiv_parse_fetch(py: Python, response_json: &str, server: &str) -> PyResult<String> {
    core_call(core::biorxiv_parse_fetch_impl(response_json, server, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn chemrxiv_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::chemrxiv_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn chemrxiv_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::chemrxiv_parse_fetch_impl(response_json, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn openalex_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::openalex_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn openalex_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::openalex_parse_fetch_impl(response_json))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn semanticscholar_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::semanticscholar_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn semanticscholar_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::semanticscholar_parse_fetch_impl(response_json))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn huggingface_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::huggingface_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn huggingface_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::huggingface_parse_fetch_impl(response_json))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn huggingface_papers_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::huggingface_papers_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn crossref_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::crossref_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn crossref_parse_fetch(py: Python, response_json: &str, fallback_json: &str) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    core_call(core::crossref_parse_fetch_impl(response_json, &fallback, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, base = "https://openlibrary.org"))]
pub fn openlibrary_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
    base: &str,
) -> PyResult<String> {
    core_call(core::openlibrary_parse_search_impl(response_json, max_results, base, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, key = "", base = "https://openlibrary.org"))]
pub fn openlibrary_parse_fetch(
    py: Python,
    response_json: &str,
    key: &str,
    base: &str,
) -> PyResult<String> {
    core_call(core::openlibrary_parse_fetch_impl(response_json, key, base))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn doaj_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::doaj_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, max_results = 5))]
pub fn arxiv_parse_search(
    py: Python,
    response_xml: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::arxiv_parse_search_impl(response_xml, max_results))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, ident = "", url_fallback_json = "null"))]
pub fn arxiv_parse_fetch(
    py: Python,
    response_xml: &str,
    ident: &str,
    url_fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(url_fallback_json).unwrap_or(Value::Null);
    core_call(core::arxiv_parse_fetch_impl(response_xml, ident, &fallback))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn pubmed_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::pubmed_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, rid = ""))]
pub fn pubmed_parse_fetch(
    py: Python,
    response_xml: &str,
    rid: &str,
) -> PyResult<String> {
    core_call(core::pubmed_parse_fetch_impl(response_xml, rid))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}
