//! PyO3 binding for JATS conversion.
//!
//! The converter and all MathML handling live in the pure `gossamer` core (ADR-012). This module
//! only marshals the JSON result and maps `JatsError` to `ValueError`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::json;

/// Python binding: JATS XML string -> JSON
/// `{markdown, title, n_formulas, n_figures, n_tables}`.
#[pyfunction]
pub(crate) fn jats_convert_jats(py: Python<'_>, xml: String) -> PyResult<String> {
    py.detach(|| {
        match gossamer::jats::convert_jats_str(&xml) {
            Ok(out) => Ok(json!({
                "markdown": out.markdown,
                "title": out.title,
                "n_formulas": out.n_formulas,
                "n_figures": out.n_figures,
                "n_tables": out.n_tables,
            })
            .to_string()),
            Err(e) => Err(PyValueError::new_err(e.message().to_owned())),
        }
    })
}
