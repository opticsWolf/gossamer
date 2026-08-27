use pyo3::prelude::*;
use scraper::{Html, Selector};
use url::Url;
use html2md::parse_html;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

// ────────────────────────────────────────────────────────────────
// 1. Shared Tokio runtime (singleton, avoids cold-start per call)
// ────────────────────────────────────────────────────────────────

fn shared_runtime() -> &'static tokio::runtime::Runtime {
    static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("Failed to create shared Tokio runtime")
    })
}

// ────────────────────────────────────────────────────────────────
// 2. HTTP client builder
// ────────────────────────────────────────────────────────────────

fn build_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        .timeout(Duration::from_secs(30))
        .connect_timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| format!("Client build error: {}", e))
}

// ────────────────────────────────────────────────────────────────
// 3. HTML content extraction
// ────────────────────────────────────────────────────────────────

/// Main-content heuristics, in priority order.
const MAIN_CONTENT_SELECTORS: &[&str] = &[
    "article",
    "main",
    "[role='main']",
    ".content",
    "#content",
];

/// Extract the main textual content from HTML using heuristics.
fn extract_main_content(document: &Html) -> String {
    extract_main_content_anchored(document).1
}

/// Like [`extract_main_content`] but also reports which selector won:
/// returns (selector_label, html_fragment). The label is one of the
/// entries of MAIN_CONTENT_SELECTORS, or "body" / "document" fallbacks.
fn extract_main_content_anchored(document: &Html) -> (String, String) {
    for sel_str in MAIN_CONTENT_SELECTORS {
        if let Ok(sel) = Selector::parse(sel_str) {
            if let Some(el) = document.select(&sel).next() {
                return ((*sel_str).to_string(), el.html());
            }
        }
    }

    let body_sel = Selector::parse("body").unwrap();
    if let Some(body) = document.select(&body_sel).next() {
        return ("body".to_string(), body.html());
    }

    ("document".to_string(), document.html())
}

// ────────────────────────────────────────────────────────────────
// 4. Link extraction
// ────────────────────────────────────────────────────────────────

/// Extract (absolute_url, anchor_text) pairs from the document.
fn extract_links_with_text(
    document: &Html,
    base_url: &Url,
    cap: usize,
) -> Vec<(String, String)> {
    let link_selector = Selector::parse("a[href]").unwrap();
    let mut links = Vec::new();
    let mut seen = HashSet::new();

    for element in document.select(&link_selector) {
        if let Some(href) = element.value().attr("href") {
            if href.starts_with("#")
                || href.starts_with("javascript:")
                || href.starts_with("mailto:")
                || href.starts_with("tel:")
            {
                continue;
            }

            if let Ok(absolute) = base_url.join(href) {
                let abs_str = absolute.to_string();
                let scheme = absolute.scheme();
                if (scheme == "http" || scheme == "https") && !seen.contains(&abs_str) {
                    seen.insert(abs_str.clone());
                    let text = element
                        .text()
                        .collect::<String>()
                        .split_whitespace()
                        .collect::<Vec<&str>>()
                        .join(" ");
                    links.push((abs_str, text));
                    if links.len() >= cap {
                        break;
                    }
                }
            }
        }
    }

    links
}

// ────────────────────────────────────────────────────────────────
// 5. Process rendered HTML (shared between static and smart fetch)
// ────────────────────────────────────────────────────────────────

fn process_html(html: &str, url: &str) -> Result<(String, Vec<String>), String> {
    process_html_anchored(html, url, 20).map(|(md, pairs)| {
        (md, pairs.into_iter().map(|(u, _)| u).collect())
    })
}

/// Like process_html but keeps anchor text alongside each URL.
fn process_html_anchored(
    html: &str,
    url: &str,
    cap: usize,
) -> Result<(String, Vec<(String, String)>), String> {
    let document = Html::parse_document(html);
    let base_url = Url::parse(url)
        .map_err(|e| format!("URL parse error: {}", e))?;

    let links = extract_links_with_text(&document, &base_url, cap);
    let main_html = extract_main_content(&document);
    let markdown = parse_html(&main_html);
    Ok((markdown, links))
}

// ────────────────────────────────────────────────────────────────
// 6. Fetch + extract (static, reqwest-based)
// ────────────────────────────────────────────────────────────────

/// Outcome of one fetch attempt.
/// `Err((message, retryable))`: only retryable errors consume another attempt.
async fn fetch_attempt(
    client: &reqwest::Client,
    url: &str,
) -> Result<String, (String, bool)> {
    // NOTE: do NOT set Accept-Encoding manually — reqwest then skips
    // its automatic gzip/brotli/deflate decoding and response.text()
    // yields raw compressed bytes.
    let response = client
        .get(url)
        .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        .header("Accept-Language", "en-US,en;q=0.5")
        .header("DNT", "1")
        .header("Connection", "keep-alive")
        .send()
        .await
        .map_err(|e| (format!("Request failed: {}", e), true))?;

    let status = response.status();
    if !status.is_success() {
        // Only server-side errors are worth retrying.
        let retryable = status.as_u16() >= 500;
        return Err((format!("HTTP error: {}", status), retryable));
    }

    response
        .text()
        .await
        .map_err(|e| (format!("Body read failed: {}", e), true))
}

async fn http_fetch_html(client: &reqwest::Client, url: &str) -> Result<String, String> {
    const MAX_ATTEMPTS: u32 = 3;
    let mut last_error = String::new();

    for attempt in 0..MAX_ATTEMPTS {
        match fetch_attempt(client, url).await {
            Ok(html) => return Ok(html),
            Err((msg, retryable)) => {
                if !retryable {
                    return Err(msg);
                }
                last_error = msg;
                if attempt == MAX_ATTEMPTS - 1 {
                    break;
                }
                // Exponential backoff: 500ms, 1s, …
                let delay = Duration::from_millis(500 * 2u64.pow(attempt));
                tokio::time::sleep(delay).await;
            }
        }
    }

    Err(format!(
        "All {} attempts failed. Last error: {}",
        MAX_ATTEMPTS, last_error
    ))
}

async fn fetch_pairs_inner(
    url: &str,
    cap: usize,
) -> Result<(String, Vec<(String, String)>), String> {
    let client = build_client()?;
    let html = http_fetch_html(&client, url).await?;
    process_html_anchored(&html, url, cap)
}

fn fetch_and_extract_single(url: &str) -> Result<(String, Vec<String>), String> {
    let rt = shared_runtime();
    rt.block_on(fetch_pairs_inner(url, 20)).map(|(md, pairs)| {
        (md, pairs.into_iter().map(|(u, _)| u).collect())
    })
}

/// Fetch with retry; keep (url, anchor_text) pairs for LLM link triage.
fn fetch_and_extract_single_anchored(
    url: &str,
    cap: usize,
) -> Result<(String, Vec<(String, String)>), String> {
    let rt = shared_runtime();
    rt.block_on(fetch_pairs_inner(url, cap))
}

// ────────────────────────────────────────────────────────────────
// 7. Batch fetch (concurrent)
// ────────────────────────────────────────────────────────────────

// Type aliases keep the batch return types within clippy's complexity budget.
type AnchoredPage = (String, Vec<(String, String)>);
type BatchOutcome = Vec<(String, Result<AnchoredPage, String>)>;
type PyBatchResult = Vec<(String, Option<String>, Option<Vec<(String, String)>>)>;


/// Cheap pseudo-random milliseconds in `0..bound`, used for politeness
/// jitter. Not cryptographic — just enough to desynchronize access.
fn jitter_ms(bound_ms: u64) -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos() as u64 % bound_ms.max(1))
        .unwrap_or(0)
}

async fn fetch_many_inner(
    urls: Vec<String>,
    cap: usize,
    max_concurrency: usize,
    domain_gap_ms: u64,
) -> BatchOutcome {
    // Bounds simultaneous connections so a large same-domain batch cannot
    // open unbounded sockets (self-DoS / rude to the target).
    let semaphore = Arc::new(tokio::sync::Semaphore::new(max_concurrency.max(1)));
    // Same-domain staggering: earliest allowed start per host, advanced by
    // `gap + jitter` on every scheduling decision. Different domains never
    // wait on each other; repeats of one domain are spaced apart so a batch
    // cannot hammer a single server.
    let schedule: Arc<Mutex<HashMap<String, Instant>>> =
        Arc::new(Mutex::new(HashMap::new()));
    let mut handles = Vec::new();
    for url in urls {
        let semaphore = Arc::clone(&semaphore);
        let schedule = Arc::clone(&schedule);
        let handle = tokio::spawn(async move {
            // NOTE: must stay fully async — calling the blocking
            // fetch_and_extract_single_anchored here would block_on() the
            // shared runtime from inside its own worker (panic).
            let _permit = semaphore
                .acquire_owned()
                .await
                .expect("semaphore closed unexpectedly");

            if domain_gap_ms > 0 {
                let domain = Url::parse(&url)
                    .ok()
                    .and_then(|u| u.host_str().map(str::to_string));
                if let Some(domain) = domain {
                    // Hold the lock only to reserve a slot — never across await.
                    let wait = {
                        let mut sched = schedule
                            .lock()
                            .unwrap_or_else(|e| e.into_inner());
                        let now = Instant::now();
                        let slot = sched.entry(domain).or_insert(now);
                        let earliest = *slot;
                        let step = Duration::from_millis(
                            domain_gap_ms + jitter_ms(1000),
                        );
                        *slot = earliest + step;
                        earliest.saturating_duration_since(now)
                    };
                    if !wait.is_zero() {
                        tokio::time::sleep(wait).await;
                    }
                }
            }

            let res = async {
                let client = build_client()?;
                let html = http_fetch_html(&client, &url).await?;
                process_html_anchored(&html, &url, cap)
            }
            .await;
            (url, res)
        });
        handles.push(handle);
    }

    let mut results = Vec::new();
    for handle in handles {
        match handle.await {
            Ok((url, res)) => results.push((url, res)),
            Err(e) => {
                results.push((String::new(), Err(format!("Task panicked: {}", e))));
            }
        }
    }
    results
}

fn fetch_many(
    urls: Vec<String>,
    cap: usize,
    max_concurrency: usize,
    domain_gap_ms: u64,
) -> BatchOutcome {
    let rt = shared_runtime();
    rt.block_on(fetch_many_inner(urls, cap, max_concurrency, domain_gap_ms))
}

// ────────────────────────────────────────────────────────────────
// 8. Python bindings
// ────────────────────────────────────────────────────────────────

/// Python binding: process HTML already fetched/rendered by the caller
/// (e.g. via browser_oxide) -> (markdown, list_of_links).
#[pyfunction]
fn process_rendered_html(
    py: Python<'_>,
    html: String,
    url: String,
) -> PyResult<(String, Vec<String>)> {
    py.detach(|| {
        match process_html(&html, &url) {
            Ok((md, links)) => Ok((md, links)),
            Err(e) => Err(pyo3::exceptions::PyValueError::new_err(e)),
        }
    })
}

/// Python binding: fetch one URL -> (markdown, [(url, anchor_text)]) with
/// a larger candidate pool for LLM link triage.
#[pyfunction]
#[pyo3(signature = (url, max_links = 100))]
fn fetch_and_extract_linked(
    py: Python<'_>,
    url: String,
    max_links: usize,
) -> PyResult<(String, Vec<(String, String)>)> {
    py.detach(|| {
        match fetch_and_extract_single_anchored(&url, max_links) {
            Ok(res) => Ok(res),
            Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
        }
    })
}

/// Python binding: extract (url, anchor_text) pairs from HTML already
/// fetched/rendered by the caller (e.g. via browser_oxide).
#[pyfunction]
#[pyo3(signature = (html, url, max_links = 100))]
fn extract_links_from_html(
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
fn fetch_and_extract(py: Python<'_>, url: String) -> PyResult<(String, Vec<String>)> {
    py.detach(|| {
        match fetch_and_extract_single(&url) {
            Ok((md, links)) => Ok((md, links)),
            Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
        }
    })
}

/// Python binding: batch fetch multiple URLs.
/// Returns list of tuples: (url, markdown_or_error, [(anchor_url, text)] or None)
#[pyfunction]
#[pyo3(signature = (urls, max_links = 500, max_concurrency = 8, domain_gap_ms = 0))]
fn batch_research(
    py: Python<'_>,
    urls: Vec<String>,
    max_links: usize,
    max_concurrency: usize,
    domain_gap_ms: u64,
) -> PyResult<PyBatchResult> {
    py.detach(|| {
        let results = fetch_many(urls, max_links, max_concurrency, domain_gap_ms);
        let mut out = Vec::new();
        for (url, res) in results {
            match res {
                Ok((md, links)) => out.push((url, Some(md), Some(links))),
                Err(e) => out.push((url, Some(e), None)),
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
fn extract_main_content_markdown(
    py: Python<'_>,
    html: String,
) -> PyResult<(String, String)> {
    py.detach(|| {
        let document = Html::parse_document(&html);
        let (label, fragment) = extract_main_content_anchored(&document);
        Ok((label, parse_html(&fragment)))
    })
}

// ────────────────────────────────────────────────────────────────
// 9. Module definition
// ────────────────────────────────────────────────────────────────

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fetch_and_extract, m)?)?;
    m.add_function(wrap_pyfunction!(batch_research, m)?)?;
    m.add_function(wrap_pyfunction!(process_rendered_html, m)?)?;
    m.add_function(wrap_pyfunction!(fetch_and_extract_linked, m)?)?;
    m.add_function(wrap_pyfunction!(extract_links_from_html, m)?)?;
    m.add_function(wrap_pyfunction!(extract_main_content_markdown, m)?)?;
    Ok(())
}
