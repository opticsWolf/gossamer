//! PyO3 binding for small deterministic string kernels.
//!
//! All kernels live in the pure `gossamer` core crate (ADR-012). This module only marshals
//! arguments and maps [`gossamer::miscutils::PageRangeError`] to `ValueError`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
pub fn domain_of(url: &str) -> String {
    gossamer::miscutils::domain_of(url)
}

#[pyfunction]
pub fn sha256_hex(text: &str) -> String {
    gossamer::miscutils::sha256_hex(text)
}

#[pyfunction]
pub fn classify_link(url: &str) -> String {
    gossamer::miscutils::classify_link(url).to_string()
}

#[pyfunction]
pub fn parse_page_range(spec: &str) -> PyResult<(i128, Option<i128>)> {
    gossamer::miscutils::parse_page_range(spec)
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}
