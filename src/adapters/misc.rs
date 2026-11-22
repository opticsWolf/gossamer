//! PyO3 compatibility surface. All parsing logic lives in `gossamer::adapters::misc`.

use pyo3::prelude::*;
use serde_json::Value;

use crate::adapters::common::to_py_err;
use crate::pycompat::runtime_minor;

use gossamer::adapters::common::AdapterError;
use gossamer::adapters::misc as core;

fn core_call<T>(result: std::result::Result<T, AdapterError>) -> std::result::Result<T, String> {
    result.map_err(|e| e.message().to_owned())
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, base_url = "https://api.open-meteo.com/v1/forecast"))]
pub fn openmeteo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
    base_url: &str,
) -> PyResult<String> {
    // Records cross as a JSON array (minus `raw`, re-attached by Python).
    core_call(core::openmeteo_parse_search_impl(response_json, max_results, base_url, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn openmeteo_parse_forecast(
    py: Python,
    data_json: &str,
    lat_s: &str,
    lon_s: &str,
    base_url: &str,
) -> PyResult<String> {
    core_call(core::openmeteo_parse_forecast_impl(data_json, lat_s, lon_s, base_url))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_id = "", max_results = 5))]
pub fn nvd_parse_vulns(
    py: Python,
    response_json: &str,
    fallback_id: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::nvd_parse_vulns_impl(response_json, fallback_id, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_id = ""))]
pub fn nvd_parse_fetch(py: Python, response_json: &str, fallback_id: &str) -> PyResult<String> {
    core_call(core::nvd_parse_fetch_impl(response_json, fallback_id, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn nvd_route_query(py: Python, query: &str) -> PyResult<(String, String)> {
    let _ = py;
    Ok(core::nvd_route_query_impl(query))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn github_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::github_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn github_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    core_call(core::github_parse_fetch_impl(response_json, &fallback, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, start = "", max_results = 5))]
pub fn nasa_parse_search(
    py: Python,
    response_json: &str,
    start: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::nasa_parse_search_impl(response_json, start, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn nasa_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    core_call(core::nasa_parse_fetch_impl(response_json, &fallback))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback = "", max_results = 5))]
pub fn swh_parse_search(
    py: Python,
    response_json: &str,
    fallback: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::swh_parse_search_impl(response_json, fallback, max_results))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback = ""))]
pub fn swh_parse_fetch_origin(
    py: Python,
    response_json: &str,
    fallback: &str,
) -> PyResult<String> {
    core_call(core::swh_parse_fetch_origin_impl(response_json, fallback))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, sid = ""))]
pub fn swh_parse_fetch_sid(
    py: Python,
    response_json: &str,
    sid: &str,
) -> PyResult<String> {
    core_call(core::swh_parse_fetch_sid_impl(response_json, sid, runtime_minor()))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn overpass_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::overpass_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}
