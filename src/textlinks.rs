//! PyO3 binding for text-level link detection.
//!
//! The scanner lives in the pure `gossamer_core` crate (ADR-012). This module only marshals the
//! Python call into that crate.

use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (text, max_links = 50))]
pub fn text_links_scan(text: &str, max_links: usize) -> Vec<String> {
    gossamer::textlinks::scan_text_links(text, max_links)
}
