//! PyO3 binding for token budgets.
//!
//! BPE logic and error messages live in the pure `gossamer` core crate (ADR-012). This module only
//! marshals arguments and maps [`gossamer::tokens::TokenError`] to `ValueError`.
//!
//! Pinned by `tests/test_rust_parity_tokens.py`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
pub fn resolve_encoding(model_name: &str) -> String {
    gossamer::tokens::resolve_encoding(model_name)
}

#[pyfunction]
pub fn embedded_encodings() -> Vec<String> {
    gossamer::tokens::embedded_encodings()
}

#[pyfunction]
#[pyo3(signature = (text, model_name = "gpt-4o"))]
pub fn count_tokens(text: &str, model_name: &str) -> PyResult<usize> {
    gossamer::tokens::count_tokens(text, model_name)
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}

#[pyfunction]
#[pyo3(signature = (text, max_tokens, model_name = "gpt-4o", ellipsis = None))]
pub fn truncate_to_tokens(
    text: &str,
    max_tokens: i64,
    model_name: &str,
    ellipsis: Option<&str>,
) -> PyResult<String> {
    let ellipsis = ellipsis.unwrap_or(gossamer::tokens::default_ellipsis());
    gossamer::tokens::truncate_to_tokens(text, max_tokens, model_name, ellipsis)
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}

#[pyfunction]
#[pyo3(signature = (pieces, max_tokens, model_name = "gpt-4o"))]
pub fn fit_context_window(
    pieces: Vec<String>,
    max_tokens: i64,
    model_name: &str,
) -> PyResult<Vec<String>> {
    gossamer::tokens::fit_context_window(pieces, max_tokens, model_name)
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}
