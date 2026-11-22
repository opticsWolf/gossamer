//! PyO3 binding for metadata extraction kernels.
//!
//! All extraction/mapping logic lives in the pure `gossamer` core crate (ADR-012); this module only
//! converts the optional Python string argument to `Option<&str>` and returns JSON strings.

use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_all(html: &str, base_url: Option<String>) -> String {
    gossamer::metaextract::meta_extract_all(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_meta(html: &str, base_url: Option<String>) -> String {
    gossamer::metaextract::meta_extract_meta(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_opengraph(html: &str, base_url: Option<String>) -> String {
    gossamer::metaextract::meta_extract_opengraph(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_twitter(html: &str, base_url: Option<String>) -> String {
    gossamer::metaextract::meta_extract_twitter(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_jsonld(html: &str, base_url: Option<String>) -> String {
    gossamer::metaextract::meta_extract_jsonld(html, base_url.as_deref())
}
