//! PyO3 wrapper functions exposed through `_core`.
//!
//! Business logic lives in the pure `gossamer` core (ADR-012). This module only marshals arguments,
//! maps errors to Python exceptions, and keeps the Python logging sink initialization.

use std::sync::Once;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use scraper::Html;
use url::Url;

use super::fetch::{
    ConditionalPage, ExtractedTableGrid, FullPage, PyBatchResult, extract_links_with_text,
    fetch_and_extract_single, fetch_and_extract_single_anchored,
    fetch_conditional_single, fetch_html_full_single, fetch_many, max_response_bytes, process_html,
};

/// Process HTML already fetched/rendered by the caller into markdown, links, and hidden count.
#[pyfunction]
pub(crate) fn process_rendered_html(
    py: Python<'_>,
    html: String,
    url: String,
) -> PyResult<(String, Vec<String>, usize)> {
    py.detach(|| process_html(&html, &url).map_err(PyValueError::new_err))
}

/// Fetch one URL with a larger anchor-text candidate pool for LLM link triage.
#[pyfunction]
#[pyo3(signature = (url, max_links = 100, max_bytes = None))]
pub(crate) fn fetch_and_extract_linked(
    py: Python<'_>,
    url: String,
    max_links: usize,
    max_bytes: Option<usize>,
) -> PyResult<(String, Vec<(String, String)>)> {
    let cap = max_bytes.unwrap_or_else(max_response_bytes);
    py.detach(|| {
        fetch_and_extract_single_anchored(&url, max_links, cap).map_err(PyRuntimeError::new_err)
    })
}

/// Fetch one URL and retain raw HTML for metadata extraction.
#[pyfunction]
#[pyo3(signature = (url, max_links = 100, max_bytes = None))]
pub(crate) fn fetch_html_full(
    py: Python<'_>,
    url: String,
    max_links: usize,
    max_bytes: Option<usize>,
) -> PyResult<FullPage> {
    let cap = max_bytes.unwrap_or_else(max_response_bytes);
    py.detach(|| fetch_html_full_single(&url, max_links, cap).map_err(PyRuntimeError::new_err))
}

/// Fetch one URL with conditional-request validators.
#[pyfunction]
#[pyo3(signature = (url, max_links = 100, max_bytes = None, etag = None, last_modified = None))]
pub(crate) fn fetch_html_conditional(
    py: Python<'_>,
    url: String,
    max_links: usize,
    max_bytes: Option<usize>,
    etag: Option<String>,
    last_modified: Option<String>,
) -> PyResult<ConditionalPage> {
    let cap = max_bytes.unwrap_or_else(max_response_bytes);
    py.detach(|| {
        fetch_conditional_single(
            &url,
            max_links,
            cap,
            etag.as_deref(),
            last_modified.as_deref(),
        )
        .map_err(PyRuntimeError::new_err)
    })
}

/// Extract `(url, anchor_text)` pairs from caller-supplied HTML.
#[pyfunction]
#[pyo3(signature = (html, url, max_links = 100))]
pub(crate) fn extract_links_from_html(
    py: Python<'_>,
    html: String,
    url: String,
    max_links: usize,
) -> PyResult<Vec<(String, String)>> {
    py.detach(|| {
        let document = Html::parse_document(&html);
        let base = Url::parse(&url)
            .map_err(|e| PyValueError::new_err(format!("URL parse error: {e}")))?;
        Ok(extract_links_with_text(&document, &base, max_links))
    })
}

/// Fetch one URL and return markdown plus a simple link list.
#[pyfunction]
#[pyo3(signature = (url, max_bytes = None))]
pub(crate) fn fetch_and_extract(
    py: Python<'_>,
    url: String,
    max_bytes: Option<usize>,
) -> PyResult<(String, Vec<String>)> {
    let cap = max_bytes.unwrap_or_else(max_response_bytes);
    py.detach(|| fetch_and_extract_single(&url, cap).map_err(PyRuntimeError::new_err))
}

/// Fetch several URLs concurrently.
#[pyfunction]
#[pyo3(signature = (urls, max_links = 500, max_concurrency = 8, domain_gap_ms = 0, max_bytes = None))]
pub(crate) fn batch_research(
    py: Python<'_>,
    urls: Vec<String>,
    max_links: usize,
    max_concurrency: usize,
    domain_gap_ms: u64,
    max_bytes: Option<usize>,
) -> PyResult<PyBatchResult> {
    let cap = max_bytes.unwrap_or_else(max_response_bytes);
    py.detach(|| {
        let results = fetch_many(urls, max_links, max_concurrency, domain_gap_ms, cap);
        let mut out = Vec::new();
        for (url, res) in results {
            match res {
                Ok((html, md, links, meta)) => {
                    out.push((url, Some(html), Some(md), Some(links), Some(meta)))
                }
                Err(e) => out.push((url, None, Some(e), None, None)),
            }
        }
        Ok(out)
    })
}

/// Run main-content heuristics on caller-supplied HTML.
#[pyfunction]
#[pyo3(signature = (html))]
pub(crate) fn extract_main_content_markdown(
    py: Python<'_>,
    html: String,
) -> PyResult<(String, String)> {
    py.detach(|| Ok(gossamer::fetch::extract_main_markdown(&html)))
}

/// A `log` logger that forwards records to Python's `logging` module.
pub(crate) struct PyLogLogger;

/// Maps a textual level name to a `log::LevelFilter`.
fn parse_level_filter(level: &str) -> log::LevelFilter {
    match level.to_ascii_lowercase().as_str() {
        "trace" => log::LevelFilter::Trace,
        "debug" => log::LevelFilter::Debug,
        "info" => log::LevelFilter::Info,
        "warn" | "warning" => log::LevelFilter::Warn,
        "error" => log::LevelFilter::Error,
        "off" => log::LevelFilter::Off,
        _ => log::LevelFilter::Info,
    }
}

/// Initializes the Rust logging bridge once and adjusts the active level.
#[pyfunction]
pub(crate) fn init_rust_logging(level: &str) -> bool {
    static INIT: Once = Once::new();
    static LOGGER: PyLogLogger = PyLogLogger;
    INIT.call_once(|| {
        let _ = log::set_logger(&LOGGER);
    });
    log::set_max_level(parse_level_filter(level));
    log::info!("gossamer: rust logging bridge initialized level={}", level);
    true
}

/// Extracts top-level HTML tables as `(name, headers, rows)` grids.
#[pyfunction]
#[pyo3(signature = (html, max_tables = 20, max_rows = 500))]
pub(crate) fn extract_tables_from_html(
    html: &str,
    max_tables: usize,
    max_rows: usize,
) -> PyResult<Vec<ExtractedTableGrid>> {
    Ok(gossamer::tables::extract_tables(html, max_tables, max_rows))
}
