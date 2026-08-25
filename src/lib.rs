use pyo3::prelude::*;
use scraper::{Html, Selector};
use url::Url;
use html2md::parse_html;
use std::collections::HashSet;
use std::sync::OnceLock;
use std::time::Duration;

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

/// Extract the main textual content from HTML using heuristics.
fn extract_main_content(document: &Html) -> String {
    let selectors = [
        Selector::parse("article").unwrap(),
        Selector::parse("main").unwrap(),
        Selector::parse("[role='main']").unwrap(),
        Selector::parse(".content").unwrap(),
        Selector::parse("#content").unwrap(),
    ];

    for sel in &selectors {
        let mut elements = document.select(sel);
        if let Some(el) = elements.next() {
            return el.html();
        }
    }

    let body_sel = Selector::parse("body").unwrap();
    if let Some(body) = document.select(&body_sel).next() {
        return body.html();
    }

    document.html()
}

// ────────────────────────────────────────────────────────────────
// 4. Link extraction
// ────────────────────────────────────────────────────────────────

fn extract_links(document: &Html, base_url: &Url) -> Vec<String> {
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
                    links.push(abs_str);
                    if links.len() >= 20 {
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
    let document = Html::parse_document(html);
    let base_url = Url::parse(url)
        .map_err(|e| format!("URL parse error: {}", e))?;

    let links = extract_links(&document, &base_url);
    let main_html = extract_main_content(&document);
    let markdown = parse_html(&main_html);
    Ok((markdown, links))
}

// ────────────────────────────────────────────────────────────────
// 6. Fetch + extract (static, reqwest-based)
// ────────────────────────────────────────────────────────────────

async fn fetch_and_extract_inner(url: &str) -> Result<(String, Vec<String>), String> {
    let client = build_client()?;
    let max_attempts = 3;
    let mut attempts = 0;
    let mut last_error = String::new();

    while attempts < max_attempts {
        match client
            .get(url)
            .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
            .header("Accept-Language", "en-US,en;q=0.5")
            // NOTE: do NOT set Accept-Encoding manually — reqwest then skips
            // its automatic gzip/brotli/deflate decoding and response.text()
            // yields raw compressed bytes.
            .header("DNT", "1")
            .header("Connection", "keep-alive")
            .send()
            .await
        {
            Ok(response) => {
                let status = response.status();
                if status.is_success() {
                    match response.text().await {
                        Ok(html) => {
                            return process_html(&html, url);
                        }
                        Err(e) => last_error = format!("Body read failed: {}", e),
                    }
                } else if status.as_u16() >= 500 && attempts < max_attempts - 1 {
                    last_error = format!("HTTP {} — will retry", status);
                } else {
                    return Err(format!("HTTP error: {}", status));
                }
            }
            Err(e) => last_error = format!("Request failed: {}", e),
        }

        attempts += 1;
        if attempts < max_attempts {
            let delay = Duration::from_millis(500 * 2u64.pow(attempts - 1));
            tokio::time::sleep(delay).await;
        }
    }

    Err(format!(
        "All {} attempts failed. Last error: {}",
        max_attempts, last_error
    ))
}

/// Fetch a single URL with exponential backoff retry.
fn fetch_and_extract_single(url: &str) -> Result<(String, Vec<String>), String> {
    let rt = shared_runtime();
    rt.block_on(fetch_and_extract_inner(url))
}

// ────────────────────────────────────────────────────────────────
// 7. Batch fetch (concurrent)
// ────────────────────────────────────────────────────────────────

async fn fetch_many_inner(
    urls: Vec<String>,
) -> Vec<(String, Result<(String, Vec<String>), String>)> {
    let mut handles = Vec::new();
    for url in urls {
        let handle = tokio::spawn(async move {
            let res = fetch_and_extract_inner(&url).await;
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

fn fetch_many(urls: Vec<String>) -> Vec<(String, Result<(String, Vec<String>), String>)> {
    let rt = shared_runtime();
    rt.block_on(fetch_many_inner(urls))
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
/// Returns list of tuples: (url, markdown_or_error, links_or_none)
#[pyfunction]
fn batch_research(
    py: Python<'_>,
    urls: Vec<String>,
) -> PyResult<Vec<(String, Option<String>, Option<Vec<String>>)>> {
    py.detach(|| {
        let results = fetch_many(urls);
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

// ────────────────────────────────────────────────────────────────
// 9. Module definition
// ────────────────────────────────────────────────────────────────

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fetch_and_extract, m)?)?;
    m.add_function(wrap_pyfunction!(batch_research, m)?)?;
    m.add_function(wrap_pyfunction!(process_rendered_html, m)?)?;
    Ok(())
}
