use pyo3::prelude::*;
use scraper::{Html, Selector};
use url::{Host, Url};
use html2md::parse_html;
use std::collections::{HashMap, HashSet};
use std::net::{IpAddr, ToSocketAddrs};
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
        // Redirects are followed manually in fetch_attempt so that every
        // hop passes the SSRF guard (S1).
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| format!("Client build error: {}", e))
}

// ────────────────────────────────────────────────────────────────
// 2b. SSRF guard (S1, CODE_REVIEW_2026-08-27)
// ────────────────────────────────────────────────────────────────

/// Max redirect hops followed (matches reqwest's built-in default).
const MAX_REDIRECTS: u32 = 10;

/// Operator-controlled bypass for the SSRF guard (developers and tests
/// that need local servers). The environment is under operator control,
/// not the LLM's.
fn ssrf_bypass() -> bool {
    std::env::var("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE")
        .ok()
        .map(|v| {
            matches!(
                v.trim().to_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

/// True for addresses that must never be fetched: loopback, private
/// (RFC1918 / ULA), link-local (cloud metadata), or unspecified.
fn is_disallowed_ip(ip: IpAddr) -> bool {
    // IPv4-mapped IPv6 (::ffff:a.b.c.d) inherits the v4 decision.
    let ip = match ip {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => IpAddr::V6(v6),
        },
        other => other,
    };
    match ip {
        IpAddr::V4(v4) => {
            v4.is_unspecified()
                || v4.is_loopback()
                || v4.is_link_local() // 169.254.0.0/16 — AWS/Azure/GCP metadata
                || v4.is_private()    // 10/8, 172.16/12, 192.168/16
        }
        IpAddr::V6(v6) => {
            // `Ipv6Addr::is_private`/`is_link_local` are not stable yet, so
            // the ULA prefix (fc00::/7, incl. GCP metadata fd00:ec2::254)
            // is checked manually.
            v6.is_unspecified()
                || v6.is_loopback()
                || v6.is_unicast_link_local() // fe80::/10
                || (v6.segments()[0] & 0xfe00) == 0xfc00 // fc00::/7 ULA
        }
    }
}

fn ip_check(ip: IpAddr) -> Result<(), String> {
    if is_disallowed_ip(ip) {
        Err(format!("address {} is not public", ip))
    } else {
        Ok(())
    }
}

/// SSRF guard (S1): reject URLs whose host is not a public, resolvable
/// address. Catches direct targets, CNAME chains, and — via a per-hop
/// call in `fetch_attempt` — redirects. DNS is resolved here and again by
/// the client; a hostile DNS could rebind between lookups, but the common
/// SSRF vectors (metadata URLs, private ranges, internal names) are caught.
async fn validate_public_host(url: &Url) -> Result<(), String> {
    if ssrf_bypass() {
        return Ok(());
    }

    let host = match url.host() {
        Some(Host::Ipv4(ip)) => return ip_check(ip.into()),
        Some(Host::Ipv6(ip)) => return ip_check(ip.into()),
        Some(Host::Domain(d)) => d.to_string(),
        None => return Err("URL has no host".to_string()),
    };

    let lower = host.to_ascii_lowercase();
    if lower == "localhost"
        || lower.ends_with(".local")
        || lower.ends_with(".internal")
        || lower.ends_with(".localhost")
    {
        return Err(format!("host '{}' is an internal name", host));
    }

    let port = url.port_or_known_default().unwrap_or(80);
    let dns_host = host.clone();
    let addrs = tokio::task::spawn_blocking(move || {
        (dns_host.as_str(), port)
            .to_socket_addrs()
            .map(|a| a.collect::<Vec<_>>())
    })
    .await
    .map_err(|e| format!("DNS task failed: {}", e))?
    .map_err(|e| format!("DNS resolution failed: {}", e))?;

    if addrs.is_empty() {
        return Err(format!("DNS returned no addresses for '{}'", host));
    }
    for sa in &addrs {
        if is_disallowed_ip(sa.ip()) {
            return Err(format!(
                "host '{}' resolves to non-public address {}",
                host,
                sa.ip()
            ));
        }
    }
    Ok(())
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
    let mut current = match Url::parse(url) {
        Ok(u) => u,
        Err(e) => return Err((format!("URL parse error: {}", e), false)),
    };

    // The client is built with `redirect::Policy::none()`, so redirects are
    // followed here — every hop must pass the SSRF guard (S1).
    for _hop in 0..=MAX_REDIRECTS {
        // S1: block loopback / RFC1918 / link-local (cloud metadata) /
        // internal names, whether targeted directly or via CNAME/redirect.
        if let Err(reason) = validate_public_host(&current).await {
            return Err((
                format!("Blocked by SSRF guard: {}", reason),
                false,
            ));
        }

        // NOTE: do NOT set Accept-Encoding manually — reqwest then skips
        // its automatic gzip/brotli/deflate decoding and response.text()
        // yields raw compressed bytes.
        let response = client
            .get(current.as_str())
            .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
            .header("Accept-Language", "en-US,en;q=0.5")
            .header("DNT", "1")
            .header("Connection", "keep-alive")
            .send()
            .await
            .map_err(|e| (format!("Request failed: {}", e), true))?;

        let status = response.status();
        if status.is_redirection() {
            let location = response
                .headers()
                .get(reqwest::header::LOCATION)
                .and_then(|v| v.to_str().ok())
                .map(str::to_string)
                .ok_or_else(|| {
                    ("Redirect without Location header".to_string(), false)
                })?;
            current = current
                .join(&location)
                .map_err(|e| (format!("Invalid redirect target: {}", e), false))?;
            continue;
        }

        if !status.is_success() {
            // Only server-side errors are worth retrying.
            let retryable = status.as_u16() >= 500;
            return Err((format!("HTTP error: {}", status), retryable));
        }

        return response
            .text()
            .await
            .map_err(|e| (format!("Body read failed: {}", e), true));
    }

    Err((format!("Too many redirects (max {})", MAX_REDIRECTS), false))
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

/// Fetch one URL and keep the raw HTML alongside the extraction results:
/// (html, markdown, [(url, anchor_text)]). The HTML is retained so the
/// caller can run its own metadata extraction (e.g. meta-oxide) on the
/// exact bytes that were rendered into the markdown — no second network
/// round-trip (C2 fix, CODE_REVIEW_2026-08-27).
fn fetch_html_full_single(url: &str, cap: usize) -> Result<FullPage, String> {
    let rt = shared_runtime();
    rt.block_on(async {
        let client = build_client()?;
        let html = http_fetch_html(&client, url).await?;
        let (md, links) = process_html_anchored(&html, url, cap)?;
        Ok((html, md, links))
    })
}

// ────────────────────────────────────────────────────────────────
// 7. Batch fetch (concurrent)
// ────────────────────────────────────────────────────────────────

// Type aliases keep the batch return types within clippy's complexity budget.
type AnchoredPage = (String, Vec<(String, String)>);
/// Full fetch payload: (raw_html, markdown, [(url, anchor_text)]).
type FullPage = (String, String, Vec<(String, String)>);
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

/// Python binding: fetch one URL -> (html, markdown, [(url, anchor_text)]).
/// Like `fetch_and_extract_linked` but also returns the raw HTML so the
/// caller can extract metadata from it (C2).
#[pyfunction]
#[pyo3(signature = (url, max_links = 100))]
fn fetch_html_full(py: Python<'_>, url: String, max_links: usize) -> PyResult<FullPage> {
    py.detach(|| {
        match fetch_html_full_single(&url, max_links) {
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
    m.add_function(wrap_pyfunction!(fetch_html_full, m)?)?;
    m.add_function(wrap_pyfunction!(extract_links_from_html, m)?)?;
    m.add_function(wrap_pyfunction!(extract_main_content_markdown, m)?)?;
    Ok(())
}
