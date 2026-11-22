//! PyO3 binding for result deduplication.
//!
//! Key computation and the collision loop live in the pure `gossamer` core crate (ADR-012). This
//! module only converts Python-provided field dictionaries into core `DedupeItem`s and applies the
//! default `by` fields.
//!
//! Pinned by `tests/test_rust_parity_dedupe.py`.

use pyo3::prelude::*;

/// Raw identity fields for one item; all optional (absent/empty → None).
#[derive(FromPyObject)]
pub struct DedupeItem {
    #[pyo3(item)]
    doi: Option<String>,
    #[pyo3(item)]
    url: Option<String>,
    #[pyo3(item)]
    title: Option<String>,
    #[pyo3(item)]
    snippet: Option<String>,
    #[pyo3(item)]
    summary: Option<String>,
    #[pyo3(item)]
    description: Option<String>,
}

impl DedupeItem {
    fn into_core(self) -> gossamer::dedupe::DedupeItem {
        gossamer::dedupe::DedupeItem::new(
            self.doi,
            self.url,
            self.title,
            self.snippet,
            self.summary,
            self.description,
        )
    }
}

/// Runs the first-seen collision loop over pre-extracted items.
#[pyfunction]
#[pyo3(signature = (items, by = None))]
pub fn dedupe_plan(
    items: Vec<DedupeItem>,
    by: Option<Vec<String>>,
) -> (Vec<usize>, Vec<(usize, String, String)>) {
    let fields = by.unwrap_or_else(|| {
        vec!["doi".to_string(), "url".to_string(), "hash".to_string()]
    });
    let items: Vec<_> = items.into_iter().map(DedupeItem::into_core).collect();
    gossamer::dedupe::dedupe_plan(&items, &fields, crate::pycompat::runtime_minor())
}
