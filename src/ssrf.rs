//! PyO3 binding for the SSRF guard.
//!
//! Policy and error messages live in the pure `gossamer` core crate (ADR-012). This module only
//! detaches from the GIL and maps the core error string to `ValueError`, which the Python wrapper
//! converts to `SsrfBlockedError`.
//!
//! Pinned by `tests/test_rust_parity_ssrf.py`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (url, allow_private = false))]
pub fn ssrf_check_url(py: Python, url: &str, allow_private: bool) -> PyResult<()> {
    let runtime_minor = py.version_info().minor as u32;
    py.detach(|| gossamer::ssrf::check_url(url, allow_private, Some(runtime_minor)))
        .map_err(|e| PyValueError::new_err(e.message().to_owned()))
}
