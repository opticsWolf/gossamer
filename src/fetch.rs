//! PyO3-facing fetch adapter.
//!
//! All transport, SSRF, HTML processing, retry, and batch logic lives in the pure `gossamer` core
//! (ADR-012). This module only preserves the legacy `Result<_, String>` signatures, maps core
//! errors to strings for the existing binding layer, and keeps the Python logging sink.

use log::{Level, Log, Metadata, Record};
use pyo3::prelude::*;

use super::bridge::PyLogLogger;

pub use gossamer::fetch::{
    extract_links_with_text, extract_main_content_anchored, max_response_bytes,
};

pub type FullPage = gossamer::fetch::FullPage;
pub type ConditionalPage = gossamer::fetch::ConditionalPage;
pub type ExtractedTableGrid = gossamer::fetch::ExtractedTableGrid;
pub type PyBatchResult = gossamer::fetch::BatchResult;
type BatchOutcome =
    Vec<(String, Result<gossamer::fetch::BatchPage, String>)>;

#[pyfunction]
pub(crate) fn configure_http(
    proxy: Option<String>,
    user_agent: Option<String>,
    headers: Vec<(String, String)>,
    cookies: Vec<(String, String)>,
) {
    gossamer::fetch::configure_http(proxy, user_agent, headers, cookies);
}

pub(crate) fn process_html(
    html: &str,
    url: &str,
) -> Result<(String, Vec<String>, usize), String> {
    gossamer::fetch::process_html(html, url).map_err(|e| e.message().to_owned())
}

pub(crate) fn fetch_and_extract_single(
    url: &str,
    max_bytes: usize,
) -> Result<(String, Vec<String>), String> {
    gossamer::fetch::fetch_and_extract_single(url, max_bytes)
        .map_err(|e| e.message().to_owned())
}

pub(crate) fn fetch_and_extract_single_anchored(
    url: &str,
    cap: usize,
    max_bytes: usize,
) -> Result<(String, Vec<(String, String)>), String> {
    gossamer::fetch::fetch_and_extract_single_anchored(url, cap, max_bytes)
        .map_err(|e| e.message().to_owned())
}

pub(crate) fn fetch_html_full_single(
    url: &str,
    cap: usize,
    max_bytes: usize,
) -> Result<FullPage, String> {
    gossamer::fetch::fetch_html_full_single(url, cap, max_bytes)
        .map_err(|e| e.message().to_owned())
}

pub(crate) fn fetch_many(
    urls: Vec<String>,
    cap: usize,
    max_concurrency: usize,
    domain_gap_ms: u64,
    max_bytes: usize,
) -> BatchOutcome {
    let results = gossamer::fetch::fetch_many(urls, cap, max_concurrency, domain_gap_ms, max_bytes);
    results
        .into_iter()
        .map(|(url, res)| (url, res.map_err(|e| e.message().to_owned())))
        .collect()
}

pub(crate) fn fetch_conditional_single(
    url: &str,
    cap: usize,
    max_bytes: usize,
    etag: Option<&str>,
    last_modified: Option<&str>,
) -> Result<ConditionalPage, String> {
    gossamer::fetch::fetch_conditional_single(url, cap, max_bytes, etag, last_modified)
        .map_err(|e| e.message().to_owned())
}

impl Log for PyLogLogger {
    fn enabled(&self, _metadata: &Metadata) -> bool {
        true
    }

    // This callback may run on a tokio worker thread without the GIL, so the acquiring form is
    // required; `Python::attach` would panic here.
    #[allow(deprecated)]
    fn log(&self, record: &Record) {
        let level = match record.level() {
            Level::Error => 40,
            Level::Warn => 30,
            Level::Info => 20,
            Level::Debug => 10,
            Level::Trace => 10,
        };
        let message = format!("{}", record.args());
        let target = record.target().to_string();
        let _ = pyo3::Python::with_gil(|py| {
            let logging = py.import("logging").ok()?;
            let logger = logging.call_method1("getLogger", (target.as_str(),)).ok()?;
            logger.call_method1("log", (level, message.as_str())).ok();
            Some(())
        });
    }

    fn flush(&self) {}
}
