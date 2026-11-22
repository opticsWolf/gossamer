//! PyO3 binding for research-query classification.
//!
//! The scoring kernel and keyword tables live in the pure `gossamer` core crate (ADR-012). This
//! module only forwards the optional query.
//!
//! Pinned by `tests/test_rust_parity_categories.py`.

use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (query = None))]
pub fn classify_query(query: Option<&str>) -> String {
    gossamer::categories::classify_query(query)
}
