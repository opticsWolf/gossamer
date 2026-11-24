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

fn service_research_plan_inner(
    topic: String,
    depth: Option<Bound<'_, PyAny>>,
    max_results: Option<Bound<'_, PyAny>>,
    max_pages: i64,
) -> Result<Value, String> {
    let depth: Option<Value> = match &depth {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let max_results: Option<Value> = match &max_results {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::service_research_plan(topic, depth, max_results, max_pages)?;
    Ok(out)
}

/// `_service_research_plan`.
#[pyfunction]
#[pyo3(name = "_service_research_plan", signature = (topic, depth, max_results, max_pages))]
fn service_research_plan(
    py: Python<'_>,
    topic: String,
    depth: Option<Bound<'_, PyAny>>,
    max_results: Option<Bound<'_, PyAny>>,
    max_pages: i64,
) -> PyResult<Py<PyAny>> {
    service_research_plan_inner(topic, depth, max_results, max_pages)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(service_research_plan, m)?)?;
    Ok(())
}
