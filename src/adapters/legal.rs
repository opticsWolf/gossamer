//! PyO3 compatibility surface. All parsing logic lives in `gossamer::adapters::legal`.

use pyo3::prelude::*;
use serde_json::Value;

use crate::adapters::common::to_py_err;
use crate::pycompat::runtime_minor;

use gossamer::adapters::common::AdapterError;
use gossamer::adapters::legal as core;

fn core_call<T>(result: std::result::Result<T, AdapterError>) -> std::result::Result<T, String> {
    result.map_err(|e| e.message().to_owned())
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn courtlistener_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::courtlistener_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn courtlistener_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::courtlistener_parse_fetch_impl(response_json, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn govinfo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::govinfo_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn govinfo_parse_fetch(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    core_call(core::govinfo_parse_fetch_impl(response_json, rid))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json))]
pub fn hudoc_parse_search(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::hudoc_parse_search_impl(response_json))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn hudoc_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::hudoc_parse_fetch_impl(response_json))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn oldp_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::oldp_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn oldp_parse_case(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::oldp_parse_case_impl(response_json, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn oldp_parse_law(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    core_call(core::oldp_parse_law_impl(response_json, rid))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn fed_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::fed_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn fed_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    core_call(core::fed_parse_fetch_impl(response_json, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn congress_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::congress_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn congress_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    core_call(core::congress_parse_fetch_impl(response_json, &fallback))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, dataset = "", max_results = 5))]
pub fn census_parse_search(
    py: Python,
    response_json: &str,
    dataset: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::census_parse_search_impl(response_json, dataset, max_results))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, dataset = "", rid = ""))]
pub fn census_parse_fetch(
    py: Python,
    response_json: &str,
    dataset: &str,
    rid: &str,
) -> PyResult<String> {
    core_call(core::census_parse_fetch_impl(response_json, dataset, rid))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, part = ""))]
pub fn ecfr_find_part(
    py: Python,
    response_json: &str,
    part: &str,
) -> PyResult<String> {
    core_call(core::ecfr_find_part_impl(response_json, part))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (node_json, title_s = "", part_s = ""))]
pub fn ecfr_part_record(
    py: Python,
    node_json: &str,
    title_s: &str,
    part_s: &str,
) -> PyResult<String> {
    core_call(core::ecfr_part_record_impl(node_json, title_s, part_s))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (tree_json, title_s = ""))]
pub fn ecfr_title_record(
    py: Python,
    tree_json: &str,
    title_s: &str,
) -> PyResult<String> {
    core_call(core::ecfr_title_record_impl(tree_json, title_s, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}
