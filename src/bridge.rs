//! PyO3 wrapper functions exposed through `_core` (plus logging/tables).

use pyo3::prelude::*;
use scraper::{ElementRef, Html, Selector};
use url::Url;
use html2md::parse_html;
use std::sync::Once;
use super::fetch::{ConditionalPage, ExtractedTableGrid, FullPage, PyBatchResult, extract_links_with_text, extract_main_content_anchored, fetch_and_extract_single, fetch_and_extract_single_anchored, fetch_conditional_single, fetch_html_full_single, fetch_many, max_response_bytes, process_html};

/// Python binding: process HTML already fetched/rendered by the caller
/// (e.g. via browser_oxide) -> (markdown, list_of_links, hidden_removed).
#[pyfunction]
pub(crate) fn process_rendered_html(
    py: Python<'_>,
    html: String,
    url: String,
) -> PyResult<(String, Vec<String>, usize)> {
    py.detach(|| {
        match process_html(&html, &url) {
            Ok((md, links, removed)) => Ok((md, links, removed)),
            Err(e) => Err(pyo3::exceptions::PyValueError::new_err(e)),
        }
    })
}

/// Python binding: fetch one URL -> (markdown, [(url, anchor_text)]) with
/// a larger candidate pool for LLM link triage.
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
        match fetch_and_extract_single_anchored(&url, max_links, cap) {
            Ok(res) => Ok(res),
            Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
        }
    })
}

/// Python binding: fetch one URL -> (html, markdown, [(url, anchor_text)]).
/// Like `fetch_and_extract_linked` but also returns the raw HTML so the
/// caller can extract metadata from it (C2).
#[pyfunction]
#[pyo3(signature = (url, max_links = 100, max_bytes = None))]
pub(crate) fn fetch_html_full(
    py: Python<'_>,
    url: String,
    max_links: usize,
    max_bytes: Option<usize>,
) -> PyResult<FullPage> {
    let cap = max_bytes.unwrap_or_else(max_response_bytes);
    py.detach(|| {
        match fetch_html_full_single(&url, max_links, cap) {
            Ok(res) => Ok(res),
            Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
        }
    })
}

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
        match fetch_conditional_single(
            &url,
            max_links,
            cap,
            etag.as_deref(),
            last_modified.as_deref(),
        ) {
            Ok(res) => Ok(res),
            Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
        }
    })
}

/// Python binding: extract (url, anchor_text) pairs from HTML already
/// fetched/rendered by the caller (e.g. via browser_oxide).
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
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("URL parse error: {}", e)))?;
        Ok(extract_links_with_text(&document, &base, max_links))
    })
}

/// Python binding: fetch one URL -> (markdown, list_of_links)
#[pyfunction]
#[pyo3(signature = (url, max_bytes = None))]
pub(crate) fn fetch_and_extract(
    py: Python<'_>,
    url: String,
    max_bytes: Option<usize>,
) -> PyResult<(String, Vec<String>)> {
    let cap = max_bytes.unwrap_or_else(max_response_bytes);
    py.detach(|| {
        match fetch_and_extract_single(&url, cap) {
            Ok((md, links)) => Ok((md, links)),
            Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
        }
    })
}

/// Python binding: batch fetch multiple URLs.
/// Returns list of tuples: (url, markdown_or_error, [(anchor_url, text)] or None)
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
                // Failure keeps the error in the markdown slot (M10 tags it
                // Python-side); html stays None.
                Err(e) => out.push((url, None, Some(e), None, None)),
            }
        }
        Ok(out)
    })
}

/// Python binding: run main-content heuristics on caller-supplied HTML.
/// Returns (matched_selector_label, markdown_of_that_region) so callers
/// gain visibility into which container the heuristic chose.
#[pyfunction]
#[pyo3(signature = (html))]
pub(crate) fn extract_main_content_markdown(
    py: Python<'_>,
    html: String,
) -> PyResult<(String, String)> {
    py.detach(|| {
        let document = Html::parse_document(&html);
        let (label, fragment, _removed) = extract_main_content_anchored(&document);
        Ok((label, parse_html(&fragment)))
    })
}

// ────────────────────────────────────────────────────────────────
// 9. Module definition
// ────────────────────────────────────────────────────────────────

// ────────────────────────────────────────────────────────────────
// Rust `tracing` -> Python `logging` bridge (Tier 2.6)
// ────────────────────────────────────────────────────────────────

/// A `log` logger that forwards records to Python's `logging` module.
/// Installed once via `init_rust_logging`; `tracing-log` forwards
/// `tracing` events into this logger.
pub(crate) struct PyLogLogger;

/// Map a textual level name to a `log::LevelFilter`.
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

/// Initialize the Rust `tracing` -> Python `logging` bridge (Tier 2.6).
///
/// Idempotent: the global logger/tracer is installed once; later calls only
/// adjust the max level and re-emit the init marker. Returns True on success.
#[pyfunction]
pub(crate) fn init_rust_logging(level: &str) -> bool {
    static INIT: Once = Once::new();
    static LOGGER: PyLogLogger = PyLogLogger;
    INIT.call_once(|| {
        let _ = log::set_logger(&LOGGER);
    });
    log::set_max_level(parse_level_filter(level));
    log::info!(
        "gossamer: rust logging bridge initialized level={}",
        level
    );
    true
}

// ── Tier 3.11: HTML table extraction ────────────────────────────────────

fn parse_span(value: &str) -> usize {
    value.trim().parse::<usize>().unwrap_or(1).max(1)
}

fn collapse_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn cap_text(mut text: String, limit: usize) -> String {
    if text.len() > limit {
        // Leave room for the "..." suffix so the capped result still
        // honors the limit.
        let mut end = limit.saturating_sub(3);
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
        if limit >= 3 {
            text.push_str("...");
        }
    }
    text
}

/// Extract tables from an HTML document as (name, headers, rows) grids.
///
/// Only top-level tables (tables nested inside other tables are skipped)
/// are extracted. Rows may live in `<thead>`/`<tbody>`/`<tfoot>` or
/// directly under the `<table>`. colspan/rowspan cells are expanded so
/// every row has the same width; cells covered by a span are filled with
/// the empty string. The first row becomes `headers` when it contains at
/// least one `<th>`. Table names come from `<caption>` when present,
/// otherwise `table-N` (1-based, in document order).
#[pyfunction]
#[pyo3(signature = (html, max_tables = 20, max_rows = 500))]
pub(crate) fn extract_tables_from_html(
    html: &str,
    max_tables: usize,
    max_rows: usize,
) -> PyResult<Vec<ExtractedTableGrid>> {
    const MAX_CELL_CHARS: usize = 1000;
    let document = Html::parse_document(html);
    let table_sel = Selector::parse("table").unwrap();
    let caption_sel = Selector::parse("caption").unwrap();

    let mut out: Vec<ExtractedTableGrid> = Vec::new();

    for table in document.select(&table_sel) {
        if out.len() >= max_tables {
            break;
        }
        // Skip tables nested inside another table: their rows would be
        // double-counted in the outer grid, and nested tables are
        // themselves extracted separately when top-level.
        if table
            .ancestors()
            .any(|a| a.value().as_element().is_some_and(|e| e.name() == "table"))
        {
            continue;
        }

        // Rows: direct <tr> children, or <tr> children of thead/tbody/tfoot
        // (rows belonging to a nested table are excluded by construction).
        let mut row_els: Vec<ElementRef> = Vec::new();
        for child in table.child_elements() {
            match child.value().name() {
                "tr" => row_els.push(child),
                "thead" | "tbody" | "tfoot" => {
                    for row in child.child_elements().filter(|c| c.value().name() == "tr") {
                        row_els.push(row);
                    }
                }
                _ => {}
            }
        }
        if row_els.is_empty() {
            continue;
        }

        // Build the grid, expanding colspan/rowspan.
        let mut grid: Vec<Vec<Option<String>>> = Vec::new();
        let mut occupied: Vec<Vec<bool>> = Vec::new();
        let mut first_row_has_th = false;

        for (r, row) in row_els.iter().enumerate() {
            while grid.len() <= r {
                grid.push(Vec::new());
                occupied.push(Vec::new());
            }
            let mut c = 0usize;
            for cell in row.child_elements() {
                let tag = cell.value().name();
                if tag != "td" && tag != "th" {
                    continue;
                }
                // Skip columns covered by an earlier rowspan.
                loop {
                    while occupied[r].len() <= c {
                        occupied[r].push(false);
                    }
                    if !occupied[r][c] {
                        break;
                    }
                    c += 1;
                }
                let colspan = parse_span(cell.attr("colspan").unwrap_or("1"));
                let rowspan = parse_span(cell.attr("rowspan").unwrap_or("1"));
                if r == 0 && tag == "th" {
                    first_row_has_th = true;
                }
                let text = cap_text(
                    collapse_whitespace(&cell.text().collect::<String>()),
                    MAX_CELL_CHARS,
                );
                for dr in 0..rowspan {
                    let rr = r + dr;
                    while grid.len() <= rr {
                        grid.push(Vec::new());
                        occupied.push(Vec::new());
                    }
                    for dc in 0..colspan {
                        let cc = c + dc;
                        while grid[rr].len() <= cc {
                            grid[rr].push(None);
                            occupied[rr].push(false);
                        }
                        grid[rr][cc] = Some(if dr == 0 && dc == 0 {
                            text.clone()
                        } else {
                            String::new()
                        });
                        occupied[rr][cc] = true;
                    }
                }
                c += colspan;
            }
        }

        let mut rows: Vec<Vec<String>> = grid
            .into_iter()
            .map(|row| {
                row.into_iter()
                    .map(|cell| cell.unwrap_or_default())
                    .collect()
            })
            .collect();
        let n_cols = rows.iter().map(|row| row.len()).max().unwrap_or(0);
        if n_cols == 0 {
            continue;
        }
        for row in rows.iter_mut() {
            row.resize(n_cols, String::new());
        }
        if rows.len() > max_rows {
            rows.truncate(max_rows);
        }

        let mut headers: Vec<String> = Vec::new();
        if first_row_has_th {
            headers = rows.remove(0);
        }
        if rows.is_empty() && headers.is_empty() {
            continue;
        }

        let name = table
            .select(&caption_sel)
            .next()
            .map(|cap| collapse_whitespace(&cap.text().collect::<String>()))
            .filter(|t| !t.is_empty())
            .unwrap_or_else(|| format!("table-{}", out.len() + 1));

        out.push((name, headers, rows));
    }
    Ok(out)
}
