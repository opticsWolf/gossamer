//! PyO3 compatibility surface. All parsing logic lives in `gossamer::adapters::finance`.

use pyo3::prelude::*;
use serde_json::Value;

use crate::adapters::common::to_py_err;
use crate::pycompat::runtime_minor;

use gossamer::adapters::common::AdapterError;
use gossamer::adapters::finance as core;

fn core_call<T>(result: std::result::Result<T, AdapterError>) -> std::result::Result<T, String> {
    result.map_err(|e| e.message().to_owned())
}

#[pyfunction]
#[pyo3(signature = (spec = None))]
pub fn frankfurter_split_pair(
    py: Python,
    spec: Option<&str>,
) -> PyResult<(String, Option<String>)> {
    core_call(core::frankfurter_split_pair_impl(spec)).map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (body_json, base, date_fallback = None, max_results = 5))]
pub fn frankfurter_parse_rates(
    py: Python,
    body_json: &str,
    base: &str,
    date_fallback: Option<&str>,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::frankfurter_parse_rates_impl(body_json, base, date_fallback, max_results))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, record_id = None, fallback_json = None))]
pub fn yahoo_parse_fetch(
    py: Python,
    response_json: &str,
    record_id: Option<&str>,
    fallback_json: Option<&str>,
) -> PyResult<String> {
    // Exact fallback for every `record_id` spelling: None -> null,
    // JSON-native values (int/list/dict/...) via the Python-rendered
    // `fallback_json`, anything else (tuples, sets, objects) via the
    // pre-rendered `str(record_id)` string. Returns
    // `{"record": ..., "meta": ...}`; Python attaches raw.
    let fallback = match (record_id, fallback_json) {
        (None, _) => Value::Null,
        (Some(st), Some(j)) => {
            serde_json::from_str(j).unwrap_or(Value::String(st.to_string()))
        }
        (Some(st), None) => Value::String(st.to_string()),
    };
    core_call(core::yahoo_parse_fetch_impl(response_json, &fallback))
        .and_then(|(rec, meta)| {
            let mut both = serde_json::Map::new();
            both.insert("record".to_string(), rec);
            both.insert("meta".to_string(), meta);
            serde_json::to_string(&Value::Object(both)).map_err(|e| e.to_string())
        })
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn yahoo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::yahoo_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, code, max_results = 5))]
pub fn eurostat_parse_cells(
    py: Python,
    response_json: &str,
    code: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::eurostat_parse_cells_impl(response_json, code, max_results))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn coingecko_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::coingecko_parse_search_impl(response_json, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn coingecko_parse_markets(py: Python, response_json: &str, cid: &str) -> PyResult<String> {
    core_call(core::coingecko_parse_markets_impl(response_json, cid))
        .and_then(|v| match v {
            Some(rec) => serde_json::to_string(&rec).map_err(|e| e.to_string()),
            None => Ok("null".to_string()),
        })
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, query_json = "null", max_results = 5))]
pub fn alphavantage_parse_search(
    py: Python,
    response_json: &str,
    query_json: &str,
    max_results: i64,
) -> PyResult<String> {
    let query: Value =
        serde_json::from_str(query_json).unwrap_or(Value::Null);
    core_call(core::alphavantage_parse_search_impl(response_json, &query, max_results, runtime_minor()))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn alphavantage_parse_fetch(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    core_call(core::alphavantage_parse_fetch_impl(response_json, rid))
        .and_then(|(rec, meta)| {
            let mut both = serde_json::Map::new();
            both.insert("record".to_string(), rec);
            both.insert("meta".to_string(), meta);
            serde_json::to_string(&Value::Object(both)).map_err(|e| e.to_string())
        })
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn worldbank_note(py: Python) -> PyResult<String> {
    core_call(core::worldbank_note_impl())
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null", record_url = ""))]
pub fn worldbank_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
    record_url: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    core_call(core::worldbank_parse_fetch_impl(response_json, &fallback, record_url))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn fred_parse_official(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    core_call(core::fred_parse_official_impl(response_json, &fallback))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_text, fallback_json = "null"))]
pub fn fred_parse_csv(
    py: Python,
    response_text: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    core_call(core::fred_parse_csv_impl(response_text, &fallback))
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, flow = "", key = "", max_results = 5))]
pub fn bundesbank_parse(
    py: Python,
    response_xml: &str,
    flow: &str,
    key: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::bundesbank_parse_impl(response_xml, flow, key, max_results))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, flow = "", key = "", max_results = 5))]
pub fn bis_parse(
    py: Python,
    response_xml: &str,
    flow: &str,
    key: &str,
    max_results: i64,
) -> PyResult<String> {
    core_call(core::bis_parse_impl(response_xml, flow, key, max_results))
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}
