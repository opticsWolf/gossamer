//! PyO3 binding for URL identity primitives.
//!
//! Parsing, canonicalization, and hashing live in the pure `gossamer` core crate (ADR-012). This
//! module only marshals arguments and maps [`gossamer::urls::UrlError`] to `ValueError`.
//!
//! Pinned by `tests/test_rust_parity_urls.py`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Scheme detection used by the legacy robots wrapper.
pub(crate) fn has_scheme_pub(s: &str) -> bool {
    gossamer::urls::has_scheme(s, crate::pycompat::runtime_minor())
}

#[pyfunction]
#[pyo3(signature = (raw, base = None))]
pub fn normalize_url(raw: Option<&str>, base: Option<&str>) -> PyResult<String> {
    gossamer::urls::normalize_url(raw, base, crate::pycompat::runtime_minor())
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}

#[pyfunction]
#[pyo3(signature = (url, query = "keep"))]
pub fn canonical_url(url: &str, query: &str) -> PyResult<String> {
    gossamer::urls::canonical_url(url, query, crate::pycompat::runtime_minor())
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}

#[pyfunction]
#[pyo3(signature = (text = None))]
pub fn content_hash(text: Option<&str>) -> String {
    gossamer::urls::content_hash(text)
}
