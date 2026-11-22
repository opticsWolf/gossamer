//! PyO3 binding for guard kernels.
//!
//! All kernels live in the pure `gossamer` core crate (ADR-012). This module only marshals
//! arguments and maps [`gossamer::guard::GuardError`] to `ValueError`.
//!
//! Pinned by `tests/test_rust_parity_guard.py`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (scopes = None))]
pub fn normalize_scopes(scopes: Option<Vec<String>>) -> PyResult<Vec<String>> {
    gossamer::guard::normalize_scopes(scopes)
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}

#[pyfunction]
#[pyo3(signature = (mode, threshold, chunk_chars, chunk_overlap, max_chunks, scopes = None))]
pub fn validate_guard_config(
    mode: &str,
    threshold: f64,
    chunk_chars: i64,
    chunk_overlap: i64,
    max_chunks: i64,
    scopes: Option<Vec<String>>,
) -> PyResult<Vec<String>> {
    gossamer::guard::validate_guard_config(
        mode,
        threshold,
        chunk_chars,
        chunk_overlap,
        max_chunks,
        scopes,
    )
    .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}

#[pyfunction]
pub fn chunk_text(
    text: &str,
    chunk_chars: usize,
    overlap: usize,
    max_chunks: usize,
) -> Vec<(usize, String)> {
    gossamer::guard::chunk_text(text, chunk_chars, overlap, max_chunks)
}

#[pyfunction]
pub fn normalize_untrusted_text(text: &str) -> String {
    gossamer::guard::normalize_untrusted_text(text)
}

#[pyfunction]
pub fn redact_spans(text: &str, spans: Vec<(usize, usize, f64)>) -> String {
    gossamer::guard::redact_spans(text, spans)
}

#[pyfunction]
pub fn wrap_untrusted(markdown: &str, source_url: &str) -> String {
    gossamer::guard::wrap_untrusted(markdown, source_url)
}
