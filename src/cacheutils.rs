//! PyO3 binding for cache/resource-store kernels.
//!
//! All kernels live in the pure `gossamer` core crate (ADR-012). This module only marshals
//! arguments to `_core` and back.
//!
//! Pinned by `tests/test_rust_parity_adapters.py::test_cacheutils_parity` and
//! `tests/test_resource_store.py`.

use pyo3::prelude::*;

#[pyfunction]
pub fn cache_disk_key(key: &str) -> String {
    gossamer::cacheutils::cache_disk_key(key)
}

#[pyfunction]
pub fn cache_human_size(nbytes: f64) -> String {
    gossamer::cacheutils::cache_human_size(nbytes)
}

#[pyfunction]
pub fn resource_ext_from_content_type(ctype: &str) -> Option<String> {
    gossamer::cacheutils::resource_ext_from_content_type(ctype)
}

#[pyfunction]
pub fn resource_safe_name(base: &str, ext: &str) -> String {
    gossamer::cacheutils::resource_safe_name(base, ext)
}
