//! PyO3 binding for section-selection kernels.
//!
//! All algorithms live in the pure `gossamer` core crate (ADR-012). This module only defines the
//! Python-visible data classes and delegates each call.
//!
//! Pinned by `tests/test_rust_parity_sections.py`.

use pyo3::prelude::*;
use pyo3::types::PyTuple;

#[pyclass]
pub struct Section {
    #[pyo3(get)]
    pub anchor: String,
    #[pyo3(get)]
    pub text: String,
    #[pyo3(get)]
    pub offset: usize,
}

#[pyclass]
pub struct SectionSelection {
    #[pyo3(get)]
    pub markdown: String,
    #[pyo3(get)]
    pub total_sections: usize,
    #[pyo3(get)]
    pub selected_count: usize,
    anchors: Vec<String>,
}

#[pymethods]
impl SectionSelection {
    /// Tuple (not list): the dataclass contract compares
    /// `sel.anchors == ("One", "Two")`.
    #[getter]
    fn anchors<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyTuple>> {
        PyTuple::new(py, &self.anchors)
    }
}

#[pyfunction]
pub fn split_sections(markdown: &str) -> Vec<Section> {
    gossamer::sections::split_sections(markdown)
        .into_iter()
        .map(|s| Section {
            anchor: s.anchor,
            text: s.text,
            offset: s.offset,
        })
        .collect()
}

#[pyfunction]
pub fn tokenize_text(text: &str) -> Vec<String> {
    gossamer::sections::tokenize_text(text)
}

#[pyfunction]
#[pyo3(signature = (query_tokens, docs, k1 = 1.5, b = 0.75))]
pub fn bm25_scores(query_tokens: Vec<String>, docs: Vec<String>, k1: f64, b: f64) -> Vec<f64> {
    gossamer::sections::bm25_scores(&query_tokens, &docs, k1, b)
}

#[pyfunction]
pub fn select_relevant_sections(
    markdown: &str,
    query: &str,
    max_chars: i64,
) -> Option<SectionSelection> {
    gossamer::sections::select_relevant_sections(markdown, query, max_chars).map(|o| {
        SectionSelection {
            markdown: o.markdown,
            total_sections: o.total_sections,
            selected_count: o.selected_count,
            anchors: o.anchors,
        }
    })
}
