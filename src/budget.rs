//! PyO3 binding for output-budget kernels.
//!
//! All kernels live in the pure `gossamer` core crate (ADR-012). This module only marshals
//! arguments and maps [`gossamer::budget::BudgetError`] to the original Python exception types.
//!
//! Pinned by `tests/test_rust_parity_budget.py`.

use gossamer::budget::{BudgetError, BudgetErrorKind};
use pyo3::exceptions::{PyAttributeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::PyErr;

fn map_error(err: BudgetError) -> PyErr {
    match err.kind() {
        BudgetErrorKind::Attribute => PyAttributeError::new_err(err.message().to_owned()),
        BudgetErrorKind::Type => PyTypeError::new_err(err.message().to_owned()),
        BudgetErrorKind::Value => {
            PyValueError::new_err(format!("ValueError: {}", err.message()))
        }
        BudgetErrorKind::Token => PyValueError::new_err(err.message().to_owned()),
    }
}

#[pyfunction]
#[pyo3(signature = (text, char_limit, token_limit = 0, model_name = "gpt-4o"))]
pub fn budget_truncate(
    text: &str,
    char_limit: usize,
    token_limit: i64,
    model_name: &str,
) -> PyResult<String> {
    gossamer::budget::truncate_text(text, char_limit, token_limit, model_name).map_err(map_error)
}

#[pyfunction]
pub fn budget_json_fits(
    text: &str,
    char_limit: i64,
    token_limit: i64,
    model_name: &str,
) -> PyResult<bool> {
    gossamer::budget::json_fits(text, char_limit, token_limit, model_name).map_err(map_error)
}

#[pyfunction]
pub fn budget_content_split(chars: i64, tokens: i64, link_ratio: f64) -> (usize, i64) {
    gossamer::budget::content_split(chars, tokens, link_ratio)
}

#[pyfunction]
#[pyo3(signature = (result_json, budget = None))]
pub fn shrink_research_json(result_json: &str, budget: Option<usize>) -> PyResult<String> {
    gossamer::budget::shrink_research(result_json, budget).map_err(map_error)
}

#[pyfunction]
#[pyo3(signature = (payload_json, budget = None))]
pub fn shrink_payload_json(payload_json: &str, budget: Option<usize>) -> PyResult<String> {
    gossamer::budget::shrink_payload(payload_json, budget).map_err(map_error)
}
