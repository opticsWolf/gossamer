//! PyO3 binding for robots.txt kernels.
//!
//! Parsing and matching live in the pure `gossamer` core crate (ADR-012). This module only marshals
//! arguments and supplies the interpreter version for CPython-compatible scheme detection.
//!
//! Pinned by `tests/test_rust_parity_robots.py`.

use pyo3::prelude::*;

#[pyfunction]
pub fn robots_url_path(url: &str) -> String {
    gossamer::robots::url_path(url, crate::pycompat::runtime_minor())
}

#[pyfunction]
pub fn robots_parse(text: &str, user_agent: &str) -> (Vec<(bool, String)>, Option<f64>) {
    gossamer::robots::parse_robots(text, user_agent)
}

#[pyfunction]
pub fn robots_match_url(rules: Vec<(bool, String)>, url_path: &str) -> bool {
    gossamer::robots::match_rules(&rules, url_path)
}
