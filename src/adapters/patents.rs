//! PyO3 adapter for patent parsing kernels.
//!
//! Pure kernels live in `gossamer::adapters::patents` (ADR-012). This module preserves legacy
//! `String`-error signatures, supplies the current interpreter version, and marshals JSON results.

use pyo3::prelude::*;
use serde_json::Value;

use super::common::to_py_err;
use crate::pycompat::runtime_minor;

fn map_err(e: gossamer::adapters::common::AdapterError) -> String {
    e.message().to_owned()
}

pub fn patentsview_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    gossamer::adapters::patents::patentsview_parse_search_impl(
        response_json,
        max_results,
        runtime_minor(),
    )
    .map_err(map_err)
}

pub fn patentsview_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    gossamer::adapters::patents::patentsview_parse_fetch_impl(response_json).map_err(map_err)
}

pub fn epo_parse_search_impl(
    response_xml: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    gossamer::adapters::patents::epo_parse_search_impl(response_xml, max_results).map_err(map_err)
}

pub fn epo_parse_fetch_impl(response_xml: &str) -> Result<Value, String> {
    gossamer::adapters::patents::epo_parse_fetch_impl(response_xml).map_err(map_err)
}

pub fn kipris_parse_impl(
    response_xml: &str,
) -> Result<Vec<serde_json::Map<String, Value>>, String> {
    gossamer::adapters::patents::kipris_parse_impl(response_xml).map_err(map_err)
}

pub fn kipris_parse_search_impl(
    response_xml: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    gossamer::adapters::patents::kipris_parse_search_impl(response_xml, max_results).map_err(map_err)
}

pub fn kipris_parse_fetch_impl(response_xml: &str) -> Result<Value, String> {
    gossamer::adapters::patents::kipris_parse_fetch_impl(response_xml).map_err(map_err)
}

pub fn lens_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    gossamer::adapters::patents::lens_parse_search_impl(
        response_json,
        max_results,
        runtime_minor(),
    )
    .map_err(map_err)
}

pub fn lens_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    gossamer::adapters::patents::lens_parse_fetch_impl(response_json).map_err(map_err)
}

pub fn google_patents_parse_fetch_impl(
    html: &str,
    rid: &str,
) -> Result<(Value, Value), String> {
    gossamer::adapters::patents::google_patents_parse_fetch_impl(html, rid).map_err(map_err)
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn patentsview_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    patentsview_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn patentsview_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    patentsview_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, max_results = 5))]
pub fn epo_parse_search(
    py: Python,
    response_xml: &str,
    max_results: i64,
) -> PyResult<String> {
    epo_parse_search_impl(response_xml, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn epo_parse_fetch(py: Python, response_xml: &str) -> PyResult<String> {
    epo_parse_fetch_impl(response_xml)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, max_results = 5))]
pub fn kipris_parse_search(
    py: Python,
    response_xml: &str,
    max_results: i64,
) -> PyResult<String> {
    kipris_parse_search_impl(response_xml, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn kipris_parse_fetch(py: Python, response_xml: &str) -> PyResult<String> {
    kipris_parse_fetch_impl(response_xml)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn lens_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    lens_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn lens_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    lens_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn google_patents_parse_fetch(py: Python, html: &str, rid: &str) -> PyResult<String> {
    google_patents_parse_fetch_impl(html, rid)
        .and_then(|(rec, meta)| {
            let mut both = serde_json::Map::new();
            both.insert("record".to_string(), rec);
            both.insert("meta".to_string(), meta);
            serde_json::to_string(&Value::Object(both)).map_err(|e| e.to_string())
        })
        .map_err(|e| to_py_err(py, e))
}
