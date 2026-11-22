//! Blocking HTTP transport and HTML extraction kernels.
//!
//! Owns the runtime/client compatibility singletons, SSRF-net checks, hidden-node stripping, link
//! extraction, validation, retry, and fetch primitives. Network I/O uses `reqwest`; callers that
//! need deterministic replay can drive the pure HTML kernels with fixtures.
//!
//! The process-wide runtime/client/override state is intentional compatibility state (ADR-005):
//! it is configured once before first use so the shared connection pool and operator overrides
//! behave exactly like the original module.

use std::backtrace::Backtrace;
use std::collections::{HashMap, HashSet};
use std::error::Error;
use std::fmt;
use std::net::{IpAddr, ToSocketAddrs};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use html2md::parse_html;
use scraper::{ElementRef, Html, Selector, node::Element};
use url::{Host, Url};

/// Canonical fetch error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct FetchError {
    message: String,
    backtrace: Backtrace,
}

impl FetchError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the legacy-compatible error message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace for diagnostics.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for FetchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for FetchError {}

type Result<T> = std::result::Result<T, FetchError>;

fn shared_runtime() -> &'static tokio::runtime::Runtime {
    static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .unwrap_or_else(|e| panic!("failed to create shared Tokio runtime: {e}"))
    })
}

// ────────────────────────────────────────────────────────────────
// 2. HTTP client builder
// ────────────────────────────────────────────────────────────────

/// M9: the HTTP client is a process-wide singleton. A fresh client per
/// request made every fetch pay TLS handshake + connection-pool setup;
/// `reqwest::Client` pools connections per host, so sharing one client
/// across the shared runtime reuses warm connections. Same OnceLock
/// pattern as the runtime singleton above.
fn shared_client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        let ov = http_overrides()
            .lock()
            .unwrap_or_else(|e| panic!("http overrides lock poisoned: {e}"))
            .clone();
        let mut builder = reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(10))
            // Redirects are followed manually in fetch_attempt so that
            // every hop passes the SSRF guard (S1).
            .redirect(reqwest::redirect::Policy::none());
        // Tier 2.7: operator User-Agent override, else the default desktop UA.
        let ua = ov
            .user_agent
            .clone()
            .unwrap_or_else(|| DEFAULT_USER_AGENT.to_string());
        builder = builder.user_agent(ua);
        // Tier 2.7: proxy is applied at client build time (reqwest has no
        // per-request proxy). An invalid proxy URL is logged and ignored.
        if let Some(ref p) = ov.proxy {
            match reqwest::Proxy::all(p) {
                Ok(pr) => {
                    builder = builder.proxy(pr);
                }
                Err(e) => {
                    log::warn!("invalid proxy {p:?}: {e}; continuing without proxy");
                }
            }
        }
        // Tier 2.7: default headers (e.g. Authorization) + cookies for
        // authenticated sources, sent with every request.
        let mut hdrs = reqwest::header::HeaderMap::new();
        for (k, v) in &ov.headers {
            match (
                k.parse::<reqwest::header::HeaderName>(),
                v.parse::<reqwest::header::HeaderValue>(),
            ) {
                (Ok(name), Ok(val)) => {
                    hdrs.append(name, val);
                }
                _ => {
                    log::warn!("skipping invalid header name {k:?}");
                }
            }
        }
        if !ov.cookies.is_empty() {
            let cookie_val = ov
                .cookies
                .iter()
                .map(|(k, v)| format!("{k}={v}"))
                .collect::<Vec<_>>()
                .join("; ");
            match cookie_val.parse::<reqwest::header::HeaderValue>() {
                Ok(val) => {
                    hdrs.append(reqwest::header::COOKIE, val);
                }
                Err(e) => {
                    log::warn!("invalid cookie value: {e}");
                }
            }
        }
        if !hdrs.is_empty() {
            builder = builder.default_headers(hdrs);
        }
        builder
            .build()
            .unwrap_or_else(|e| panic!("failed to create shared HTTP client: {e}"))
    })
}

// ────────────────────────────────────────────────────────────────
// 2e. HTTP transport overrides (Tier 2.7, CODE_REVIEW_2026-08-27)
// ────────────────────────────────────────────────────────────────

/// Default desktop-Chrome User-Agent sent when no override is configured.
const DEFAULT_USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

/// Process-wide HTTP transport overrides: proxy, User-Agent, default
/// headers, and cookies. Set once via [`configure_http`] before the first
/// fetch; the lazily-built shared client bakes them in at first use.
///
/// The shared client is a process-wide singleton (connection pooling), so
/// these are process-level settings, not per-request ones. If
/// [`configure_http`] is called more than once, the last non-empty value wins.
#[derive(Clone, Default)]
struct HttpOverrides {
    proxy: Option<String>,
    user_agent: Option<String>,
    headers: Vec<(String, String)>,
    cookies: Vec<(String, String)>,
}

static HTTP_OVERRIDES: OnceLock<Mutex<HttpOverrides>> = OnceLock::new();

fn http_overrides() -> &'static Mutex<HttpOverrides> {
    HTTP_OVERRIDES.get_or_init(|| Mutex::new(HttpOverrides::default()))
}

/// Set HTTP transport overrides (proxy / User-Agent / headers / cookies).
///
/// A no-op when every argument is empty/None, so the default fetch path stays untouched.
///
/// Overrides are process-wide and are baked into the shared client on first use.
///
/// # Examples
///
/// ```
/// use gossamer::fetch::configure_http;
///
/// configure_http(None, Some("agent/1.0".into()), vec![], vec![]);
/// ```
pub fn configure_http(
    proxy: Option<String>,
    user_agent: Option<String>,
    headers: Vec<(String, String)>,
    cookies: Vec<(String, String)>,
) {
    let mut ov = http_overrides()
        .lock()
        .unwrap_or_else(|e| panic!("http overrides lock poisoned: {e}"));
    if let Some(p) = proxy
        && !p.trim().is_empty()
    {
        ov.proxy = Some(p);
    }
    if let Some(u) = user_agent
        && !u.trim().is_empty()
    {
        ov.user_agent = Some(u);
    }
    if !headers.is_empty() {
        ov.headers = headers;
    }
    if !cookies.is_empty() {
        ov.cookies = cookies;
    }
    log::info!(
        "gossamer: http overrides set proxy={} user_agent={} default_headers={} cookies={}",
        ov.proxy.as_deref().unwrap_or("-"),
        ov.user_agent.as_deref().unwrap_or("-"),
        ov.headers.len(),
        ov.cookies.len(),
    );
}

// ────────────────────────────────────────────────────────────────
// 2b. SSRF guard (S1, CODE_REVIEW_2026-08-27)
// ────────────────────────────────────────────────────────────────

/// Max redirect hops followed (matches reqwest's built-in default).
const MAX_REDIRECTS: u32 = 10;

// ────────────────────────────────────────────────────────────────
// 2c. Response size cap + content-type gate (S3, CODE_REVIEW_2026-08-27)
// ────────────────────────────────────────────────────────────────

/// S3: default response-body cap (5 MiB). A hostile or merely large
/// URL must not be read fully into memory.
const DEFAULT_MAX_RESPONSE_BYTES: usize = 5 * 1024 * 1024;

/// S3: operator override (same pattern as the S1 bypass). The
/// environment is under operator control, not the LLM's. Renamed with the
/// package; the legacy STITCH_* spelling still works.
fn env_first(new: &str, legacy: &str) -> Option<String> {
    std::env::var(new)
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| std::env::var(legacy).ok().filter(|v| !v.trim().is_empty()))
}

/// Returns the configured response-body cap in bytes.
///
/// Reads `GOSSAMER_MAX_RESPONSE_BYTES` (or the legacy `STITCH_*` spelling) and falls back to 5 MiB.
///
/// # Examples
///
/// ```
/// use gossamer::fetch::max_response_bytes;
///
/// assert!(max_response_bytes() > 0);
/// ```
pub fn max_response_bytes() -> usize {
    env_first(
        "GOSSAMER_MAX_RESPONSE_BYTES",
        "STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES",
    )
    .and_then(|v| v.trim().parse::<usize>().ok())
    .filter(|n| *n > 0)
    .unwrap_or(DEFAULT_MAX_RESPONSE_BYTES)
}

/// S3: the HTML fetch path accepts text-family media types. A missing
/// Content-Type header is allowed through (the Python-side
/// `_looks_like_text` check remains the safety net).
fn is_html_content_type(ctype: &str) -> bool {
    let media = ctype
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    media.starts_with("text/") || media == "application/xhtml+xml" || media == "application/xml"
}

/// Operator-controlled bypass for the SSRF guard (developers and tests
/// that need local servers). The environment is under operator control,
/// not the LLM's.
fn ssrf_bypass() -> bool {
    env_first(
        "GOSSAMER_ALLOW_PRIVATE",
        "STITCH_WEB_RESEARCHER_ALLOW_PRIVATE",
    )
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
                || v4.is_private() // 10/8, 172.16/12, 192.168/16
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

fn ip_check(ip: IpAddr) -> Result<()> {
    if is_disallowed_ip(ip) {
        Err(FetchError::new(format!("address {} is not public", ip)))
    } else {
        Ok(())
    }
}

/// SSRF guard (S1): reject URLs whose host is not a public, resolvable
/// address. Catches direct targets, CNAME chains, and — via a per-hop
/// call in `fetch_attempt` — redirects. DNS is resolved here and again by
/// the client; a hostile DNS could rebind between lookups, but the common
/// SSRF vectors (metadata URLs, private ranges, internal names) are caught.
async fn validate_public_host(url: &Url) -> Result<()> {
    if ssrf_bypass() {
        return Ok(());
    }

    let host = match url.host() {
        Some(Host::Ipv4(ip)) => return ip_check(ip.into()),
        Some(Host::Ipv6(ip)) => return ip_check(ip.into()),
        Some(Host::Domain(d)) => d.to_string(),
        None => return Err(FetchError::new("URL has no host")),
    };

    let lower = host.to_ascii_lowercase();
    if lower == "localhost"
        || lower.ends_with(".local")
        || lower.ends_with(".internal")
        || lower.ends_with(".localhost")
    {
        return Err(FetchError::new(format!(
            "host '{}' is an internal name",
            host
        )));
    }

    let port = url.port_or_known_default().unwrap_or(80);
    let dns_host = host.clone();
    let addrs = tokio::task::spawn_blocking(move || {
        (dns_host.as_str(), port)
            .to_socket_addrs()
            .map(|a| a.collect::<Vec<_>>())
    })
    .await
    .map_err(|e| FetchError::new(format!("DNS task failed: {}", e)))?
    .map_err(|e| FetchError::new(format!("DNS resolution failed: {}", e)))?;

    if addrs.is_empty() {
        return Err(FetchError::new(format!(
            "DNS returned no addresses for '{}'",
            host
        )));
    }
    for sa in &addrs {
        if is_disallowed_ip(sa.ip()) {
            return Err(FetchError::new(format!(
                "host '{}' resolves to non-public address {}",
                host,
                sa.ip()
            )));
        }
    }
    Ok(())
}

// ────────────────────────────────────────────────────────────────
// 3. HTML content extraction
// ────────────────────────────────────────────────────────────────

/// Main-content heuristics, in priority order.
const MAIN_CONTENT_SELECTORS: &[&str] =
    &["article", "main", "[role='main']", ".content", "#content"];

/// S2: HTML void elements — re-serialized without a closing tag.
const VOID_ELEMENTS: &[&str] = &[
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
];

/// S2 (CODE_REVIEW §3.2): true for nodes a human visitor cannot see —
/// the classic carrier for indirect prompt injection against browsing
/// agents. Covers the `hidden` attribute, `aria-hidden`, `noscript`,
/// `<template>`, and display/visibility/off-screen style tricks.
fn is_hidden_node(el: &Element) -> bool {
    let name = el.name();
    if name == "noscript" || name == "template" {
        return true;
    }
    // Boolean `hidden` attribute — presence hides regardless of value.
    if el.attr("hidden").is_some() {
        return true;
    }
    if el
        .attr("aria-hidden")
        .is_some_and(|v| v.eq_ignore_ascii_case("true"))
    {
        return true;
    }
    if let Some(style) = el.attr("style") {
        // Normalize: lowercase + strip all whitespace so both
        // "display: none" and "display:none" (and CRLF variants) match.
        let norm: String = style
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect::<String>()
            .to_ascii_lowercase();
        if norm.contains("display:none")
            || norm.contains("visibility:hidden")
            || norm.contains("left:-9999px")
            || norm.contains("top:-9999px")
            || norm.contains("left:-9999em")
            || norm.contains("top:-9999em")
        {
            return true;
        }
    }
    false
}

/// Re-escape attribute values for re-serialization (scraper hands back
/// already-unescaped values).
fn escape_attr_value(out: &mut String, value: &str) {
    for c in value.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '"' => out.push_str("&quot;"),
            '<' => out.push_str("&lt;"),
            _ => out.push(c),
        }
    }
}

/// S2: re-serialize an element subtree, skipping hidden nodes. Scraper's
/// DOM is immutable, so the fragment is rebuilt instead of mutated.
fn serialize_visible(el: ElementRef<'_>, out: &mut String, removed: &mut usize) {
    if is_hidden_node(el.value()) {
        *removed += 1;
        return;
    }
    let name = el.value().name();
    out.push('<');
    out.push_str(name);
    for (attr_name, attr_value) in el.value().attrs() {
        out.push(' ');
        out.push_str(attr_name);
        out.push_str("=\"");
        escape_attr_value(out, attr_value);
        out.push('"');
    }
    out.push('>');
    for child in el.children() {
        let child_node = child.value();
        if child_node.is_element() {
            if let Some(child_el) = ElementRef::wrap(child) {
                serialize_visible(child_el, out, removed);
            }
        } else if let Some(text) = child_node.as_text() {
            out.push_str(text);
        } else if let Some(comment) = child_node.as_comment() {
            out.push_str("<!--");
            out.push_str(comment);
            out.push_str("-->");
        }
    }
    if !VOID_ELEMENTS.contains(&name) {
        out.push_str("</");
        out.push_str(name);
        out.push('>');
    }
}

/// S2: strip hidden subtrees from an HTML fragment.
/// Returns (cleaned_html, number_of_removed_nodes).
fn strip_hidden(fragment: &str) -> (String, usize) {
    let doc = Html::parse_fragment(fragment);
    let mut out = String::new();
    let mut removed = 0;
    for child in doc.root_element().children() {
        let child_node = child.value();
        if child_node.is_element() {
            if let Some(el) = ElementRef::wrap(child) {
                serialize_visible(el, &mut out, &mut removed);
            }
        } else if let Some(text) = child_node.as_text() {
            out.push_str(text);
        }
    }
    (out, removed)
}

/// Extracts main content with heading/body heuristics and strips hidden subtrees.
///
/// Returns `(selector_label, cleaned_html_fragment, hidden_nodes_removed)`. The label is one of
/// `MAIN_CONTENT_SELECTORS`, or the `body` / `document` fallback.
///
/// # Examples
///
/// ```
/// use gossamer::fetch::extract_main_content_anchored;
/// use scraper::Html;
///
/// let doc = Html::parse_document("<article><p>hello</p></article>");
/// let (label, html, removed) = extract_main_content_anchored(&doc);
/// assert_eq!(label, "article");
/// assert_eq!(removed, 0);
/// assert!(html.contains("hello"));
/// ```
pub fn extract_main_content_anchored(document: &Html) -> (String, String, usize) {
    for sel_str in MAIN_CONTENT_SELECTORS {
        if let Ok(sel) = Selector::parse(sel_str)
            && let Some(el) = document.select(&sel).next()
        {
            let (clean, removed) = strip_hidden(&el.html());
            return ((*sel_str).to_string(), clean, removed);
        }
    }

    let body_sel = Selector::parse("body")
        .unwrap_or_else(|e| panic!("invalid fixed CSS selector \"body\": {e}"));
    if let Some(body) = document.select(&body_sel).next() {
        let (clean, removed) = strip_hidden(&body.html());
        return ("body".to_string(), clean, removed);
    }

    let (clean, removed) = strip_hidden(&document.html());
    ("document".to_string(), clean, removed)
}

// ────────────────────────────────────────────────────────────────
// 4. Link extraction
// ────────────────────────────────────────────────────────────────

/// Extracts absolute `(url, anchor_text)` pairs from a parsed document.
///
/// Fragments, non-HTTP schemes, and duplicate URLs are skipped; iteration stops at `cap` links.
///
/// # Examples
///
/// ```
/// use gossamer::fetch::extract_links_with_text;
/// use scraper::Html;
/// use url::Url;
///
/// let doc = Html::parse_document(r#"<a href="/x">Example</a>"#);
/// let base = Url::parse("https://example.com").unwrap();
/// let links = extract_links_with_text(&doc, &base, 10);
/// assert_eq!(links, vec![("https://example.com/x".into(), "Example".into())]);
/// ```
pub fn extract_links_with_text(
    document: &Html,
    base_url: &Url,
    cap: usize,
) -> Vec<(String, String)> {
    let link_selector = Selector::parse("a[href]")
        .unwrap_or_else(|e| panic!("invalid fixed CSS selector \"a[href]\": {e}"));
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

/// Returns the selected main-content label and its markdown rendering.
///
/// # Examples
///
/// ```
/// use gossamer::fetch::extract_main_markdown;
///
/// let (label, md) = extract_main_markdown("<article><p>hello</p></article>");
/// assert_eq!(label, "article");
/// assert!(md.contains("hello"));
/// ```
pub fn extract_main_markdown(html: &str) -> (String, String) {
    let document = Html::parse_document(html);
    let (label, fragment, _removed) = extract_main_content_anchored(&document);
    (label, parse_html(&fragment))
}

/// Converts already-fetched HTML into markdown, unique links, and hidden-node count.
///
/// # Errors
///
/// Returns [`FetchError`] when `url` is not a valid absolute URL.
///
/// # Examples
///
/// ```
/// use gossamer::fetch::process_html;
///
/// let (md, links, removed) = process_html(
///     "<p><a href='https://example.com'>x</a></p>",
///     "https://example.com",
/// )
/// .unwrap();
/// assert!(links.contains(&"https://example.com/".to_string()));
/// assert_eq!(removed, 0);
/// # let _ = md;
/// ```
pub fn process_html(html: &str, url: &str) -> Result<(String, Vec<String>, usize)> {
    process_html_anchored(html, url, 20)
        .map(|(md, pairs, removed)| (md, pairs.into_iter().map(|(u, _)| u).collect(), removed))
}

/// Like process_html but keeps anchor text alongside each URL.
/// Third tuple member: hidden nodes stripped from the fragment (S2).
fn process_html_anchored(html: &str, url: &str, cap: usize) -> Result<ProcessedPage> {
    let document = Html::parse_document(html);
    let base_url =
        Url::parse(url).map_err(|e| FetchError::new(format!("URL parse error: {}", e)))?;

    let links = extract_links_with_text(&document, &base_url, cap);
    // S2: the fragment is re-serialized without hidden subtrees before
    // markdown conversion.
    let (_label, main_html, removed) = extract_main_content_anchored(&document);
    let markdown = parse_html(&main_html);
    Ok((markdown, links, removed))
}

// ────────────────────────────────────────────────────────────────
// 6. Fetch + extract (static, reqwest-based)
// ────────────────────────────────────────────────────────────────

/// Parse a numeric Retry-After header (delta-seconds form) into seconds,
/// capped so a hostile header cannot stall the retry loop indefinitely
/// (M15). HTTP-date values are ignored — the plain backoff applies.
fn retry_after_seconds(headers: &reqwest::header::HeaderMap) -> Option<u64> {
    const CAP_SECS: u64 = 60;
    headers
        .get(reqwest::header::RETRY_AFTER)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.trim().parse::<u64>().ok())
        .map(|secs| secs.min(CAP_SECS))
}

/// Outcome of one fetch attempt.
/// `Err((message, retryable, retry_after_secs))`: only retryable errors
/// consume another attempt; `retry_after_secs` carries the server's
/// Retry-After suggestion (M15) so the caller can honor it.
/// HTTP provenance of a successful fetch: `(status, final_url,
/// content_type)`. `final_url` is the URL after all redirects (every hop
/// passed the SSRF guard); `content_type` is None when the server sent no
/// Content-Type header. Tier 1.3: provenance in every payload.
pub type FetchMeta = (u16, String, Option<String>);

/// Tier 1.4: extract the conditional-request validators (ETag,
/// Last-Modified) a server advertised in its response headers, so the
/// caller can store them for the next revalidation.
fn validators_from_headers(
    headers: &reqwest::header::HeaderMap,
) -> (Option<String>, Option<String>) {
    let etag = headers
        .get(reqwest::header::ETAG)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let last_modified = headers
        .get(reqwest::header::LAST_MODIFIED)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    (etag, last_modified)
}

async fn fetch_attempt(
    client: &reqwest::Client,
    url: &str,
    max_bytes: usize,
    etag: Option<&str>,
    last_modified: Option<&str>,
) -> std::result::Result<
    (String, FetchMeta, Option<String>, Option<String>),
    (String, bool, Option<u64>),
> {
    let mut current = match Url::parse(url) {
        Ok(u) => u,
        Err(e) => return Err((format!("URL parse error: {}", e), false, None)),
    };

    // The client is built with `redirect::Policy::none()`, so redirects are
    // followed here — every hop must pass the SSRF guard (S1).
    for _hop in 0..=MAX_REDIRECTS {
        // S1: block loopback / RFC1918 / link-local (cloud metadata) /
        // internal names, whether targeted directly or via CNAME/redirect.
        if let Err(reason) = validate_public_host(&current).await {
            return Err((format!("Blocked by SSRF guard: {}", reason), false, None));
        }

        // NOTE: do NOT set Accept-Encoding manually — reqwest then skips
        // its automatic gzip/brotli/deflate decoding and response.text()
        // yields raw compressed bytes.
        let mut request = client
            .get(current.as_str())
            .header(
                "Accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            .header("Accept-Language", "en-US,en;q=0.5")
            .header("DNT", "1")
            .header("Connection", "keep-alive");
        // Tier 1.4: conditional-request validators — a server that still
        // has the cached copy answers 304 and no body travels.
        if let Some(tag) = etag {
            request = request.header(reqwest::header::IF_NONE_MATCH, tag);
        }
        if let Some(lm) = last_modified {
            request = request.header(reqwest::header::IF_MODIFIED_SINCE, lm);
        }
        let mut response = request
            .send()
            .await
            .map_err(|e| (format!("Request failed: {}", e), true, None))?;

        let status = response.status();
        // Tier 2.6: observability -- per-hop status for the Python logging
        // bridge (only emitted when GOSSAMER_RUST_LOG is enabled).
        log::debug!(
            "HTTP response status={} url={}",
            status.as_u16(),
            current.as_str()
        );
        // Tier 1.4: 304 Not Modified — the caller's cached copy is still
        // current. No body follows, but the response may carry rotated
        // validators, which we hand back for storage.
        if status.as_u16() == 304 {
            let (etag2, lm2) = validators_from_headers(response.headers());
            let meta: FetchMeta = (status.as_u16(), current.as_str().to_string(), None);
            log::debug!("304 Not Modified (revalidated) url={}", current.as_str());
            return Ok((String::new(), meta, etag2, lm2));
        }
        if status.is_redirection() {
            let location = response
                .headers()
                .get(reqwest::header::LOCATION)
                .and_then(|v| v.to_str().ok())
                .map(str::to_string)
                .ok_or_else(|| ("Redirect without Location header".to_string(), false, None))?;
            current = current
                .join(&location)
                .map_err(|e| (format!("Invalid redirect target: {}", e), false, None))?;
            continue;
        }

        if !status.is_success() {
            // M15: rate limits (429) and service unavailability (503) are
            // exactly the cases worth backing off on, alongside generic
            // server errors (5xx). The server may suggest how long to wait.
            let code = status.as_u16();
            let retryable = code == 429 || code == 503 || code >= 500;
            let retry_after = retry_after_seconds(response.headers());
            log::warn!(
                "HTTP error status={} url={} retryable={} retry_after_secs={:?}",
                status.as_u16(),
                current.as_str(),
                retryable,
                retry_after
            );
            return Err((format!("HTTP error: {}", status), retryable, retry_after));
        }

        // S3: content-type gate — binary bodies must not be lossily
        // UTF-8-decoded into the markdown pipeline. The error names the
        // real type so the agent can switch to extract_document.
        let ctype: Option<String> = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .map(str::to_string);
        if let Some(ctype) = &ctype
            && !is_html_content_type(ctype)
        {
            return Err((
                format!(
                    "Unsupported content type: {} (HTML fetch accepts text/*, application/xhtml+xml, application/xml; use extract_document for binary formats)",
                    ctype
                ),
                false,
                None,
            ));
        }

        // S3: size cap — Content-Length allows an early reject; otherwise
        // stream chunk by chunk and abort once the cap is exceeded.
        if let Some(declared) = response.content_length()
            && declared as usize > max_bytes
        {
            return Err((
                format!(
                    "Response too large: declared {} bytes (cap {})",
                    declared, max_bytes
                ),
                false,
                None,
            ));
        }
        let mut body: Vec<u8> = Vec::new();
        loop {
            match response.chunk().await {
                Ok(Some(chunk)) => {
                    body.extend_from_slice(&chunk);
                    if body.len() > max_bytes {
                        return Err((
                            format!("Response exceeds size cap ({} bytes)", max_bytes),
                            false,
                            None,
                        ));
                    }
                }
                Ok(None) => break,
                Err(e) => return Err((format!("Body read failed: {}", e), true, None)),
            }
        }
        let html = String::from_utf8_lossy(&body).into_owned();
        // Tier 1.3: provenance — status of the final hop, the URL after
        // all redirects, and the content type the server advertised.
        let meta: FetchMeta = (status.as_u16(), current.as_str().to_string(), ctype);
        // Tier 1.4: validators for the next revalidation.
        let (etag2, lm2) = validators_from_headers(response.headers());
        log::debug!("fetched HTML url={} bytes={}", current.as_str(), html.len());
        return Ok((html, meta, etag2, lm2));
    }

    Err((
        format!("Too many redirects (max {})", MAX_REDIRECTS),
        false,
        None,
    ))
}

async fn http_fetch_html_ext(
    client: &reqwest::Client,
    url: &str,
    max_bytes: usize,
    etag: Option<&str>,
    last_modified: Option<&str>,
) -> Result<(String, FetchMeta, Option<String>, Option<String>)> {
    const MAX_ATTEMPTS: u32 = 3;
    let mut last_error = String::new();

    for attempt in 0..MAX_ATTEMPTS {
        match fetch_attempt(client, url, max_bytes, etag, last_modified).await {
            Ok(res) => return Ok(res),
            Err((msg, retryable, retry_after)) => {
                if !retryable {
                    return Err(FetchError::new(msg));
                }
                last_error = msg;
                if attempt == MAX_ATTEMPTS - 1 {
                    break;
                }
                // Exponential backoff: 500ms, 1s, … — extended to the
                // server-requested Retry-After when the server sent one (M15).
                let mut delay = Duration::from_millis(500 * 2u64.pow(attempt));
                if let Some(secs) = retry_after {
                    delay = delay.max(Duration::from_secs(secs));
                }
                tokio::time::sleep(delay).await;
            }
        }
    }

    Err(FetchError::new(format!(
        "All {} attempts failed. Last error: {}",
        MAX_ATTEMPTS, last_error
    )))
}

/// Fetch one URL with retry, without conditional-request validators.
async fn http_fetch_html(
    client: &reqwest::Client,
    url: &str,
    max_bytes: usize,
) -> Result<(String, FetchMeta)> {
    let (html, meta, _etag, _last_modified) =
        http_fetch_html_ext(client, url, max_bytes, None, None).await?;
    Ok((html, meta))
}

async fn fetch_pairs_inner(
    url: &str,
    cap: usize,
    max_bytes: usize,
) -> Result<(String, Vec<(String, String)>)> {
    let (html, _meta) = http_fetch_html(shared_client(), url, max_bytes).await?;
    let (md, pairs, _removed) = process_html_anchored(&html, url, cap)?;
    Ok((md, pairs))
}

/// Fetches one URL with retry and returns markdown plus unique links.
///
/// # Errors
///
/// Returns [`FetchError`] for transport, SSRF, validation, size, or processing failures.
pub fn fetch_and_extract_single(url: &str, max_bytes: usize) -> Result<(String, Vec<String>)> {
    let rt = shared_runtime();
    rt.block_on(fetch_pairs_inner(url, 20, max_bytes))
        .map(|(md, pairs)| (md, pairs.into_iter().map(|(u, _)| u).collect()))
}

/// Fetches one URL with retry and keeps `(url, anchor_text)` pairs for link triage.
///
/// # Errors
///
/// Returns [`FetchError`] for SSRF blocks, invalid URLs, HTTP retries exhaustion, size/content-type
/// violations, or page-processing failures.
pub fn fetch_and_extract_single_anchored(
    url: &str,
    cap: usize,
    max_bytes: usize,
) -> Result<(String, Vec<(String, String)>)> {
    let rt = shared_runtime();
    rt.block_on(fetch_pairs_inner(url, cap, max_bytes))
}

/// Fetch one URL and keep the raw HTML alongside the extraction results:
/// (html, markdown, [(url, anchor_text)], hidden_removed, FetchMeta). The
/// HTML is retained so the caller can run its own metadata extraction
/// (e.g. meta-oxide) on the exact bytes that were rendered into the
/// markdown — no second network round-trip (C2 fix,
/// CODE_REVIEW_2026-08-27). FetchMeta carries HTTP provenance: status,
/// final URL after redirects, content type (Tier 1.3).
/// Fetches one URL and returns raw HTML, markdown, links, hidden count, and provenance.
///
/// # Errors
///
/// Returns [`FetchError`] for transport, SSRF, validation, size, or processing failures.
pub fn fetch_html_full_single(url: &str, cap: usize, max_bytes: usize) -> Result<FullPage> {
    let rt = shared_runtime();
    rt.block_on(async {
        let (html, meta) = http_fetch_html(shared_client(), url, max_bytes).await?;
        let (md, links, removed) = process_html_anchored(&html, url, cap)?;
        Ok((html, md, links, removed, meta))
    })
}

// ────────────────────────────────────────────────────────────────
// 7. Batch fetch (concurrent)
// ────────────────────────────────────────────────────────────────

// Type aliases keep the batch return types within clippy's complexity budget.
/// Processed page: (markdown, [(url, anchor_text)], hidden_nodes_removed).
pub type ProcessedPage = (String, Vec<(String, String)>, usize);
/// Full fetch payload: (raw_html, markdown, [(url, anchor_text)],
/// hidden_nodes_removed, provenance). The provenance tuple is
/// FetchMeta: (http_status, final_url, content_type).
pub type FullPage = (String, String, Vec<(String, String)>, usize, FetchMeta);
/// One batch page: (raw_html, markdown, [(url, anchor_text)]). The HTML is
/// carried out so the Python layer can run meta-oxide extraction on batch
/// entries too -- without it the batch path returned empty metadata while
/// single-page reads returned the real thing (bugfix 5).
pub type BatchPage = (String, String, Vec<(String, String)>, FetchMeta);
pub type BatchOutcome = Vec<(String, Result<BatchPage>)>;
pub type BatchResult = Vec<(
    String,
    Option<String>,
    Option<String>,
    Option<Vec<(String, String)>>,
    Option<FetchMeta>,
)>;

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
    max_bytes: usize,
) -> BatchOutcome {
    // Bounds simultaneous connections so a large same-domain batch cannot
    // open unbounded sockets (self-DoS / rude to the target).
    let semaphore = Arc::new(tokio::sync::Semaphore::new(max_concurrency.max(1)));
    // Same-domain staggering: earliest allowed start per host, advanced by
    // `gap + jitter` on every scheduling decision. Different domains never
    // wait on each other; repeats of one domain are spaced apart so a batch
    // cannot hammer a single server.
    let schedule: Arc<Mutex<HashMap<String, Instant>>> = Arc::new(Mutex::new(HashMap::new()));
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
                .unwrap_or_else(|e| panic!("batch semaphore closed: {e}"));

            if domain_gap_ms > 0 {
                let domain = Url::parse(&url)
                    .ok()
                    .and_then(|u| u.host_str().map(str::to_string));
                if let Some(domain) = domain {
                    // Hold the lock only to reserve a slot — never across await.
                    let wait = {
                        let mut sched = schedule.lock().unwrap_or_else(|e| e.into_inner());
                        let now = Instant::now();
                        let slot = sched.entry(domain).or_insert(now);
                        let earliest = *slot;
                        let step = Duration::from_millis(domain_gap_ms + jitter_ms(1000));
                        *slot = earliest + step;
                        earliest.saturating_duration_since(now)
                    };
                    if !wait.is_zero() {
                        tokio::time::sleep(wait).await;
                    }
                }
            }

            let res = async {
                let (html, meta) = http_fetch_html(shared_client(), &url, max_bytes).await?;
                let (md, pairs, _removed) = process_html_anchored(&html, &url, cap)?;
                Ok((html, md, pairs, meta))
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
                results.push((
                    String::new(),
                    Err(FetchError::new(format!("Task panicked: {}", e))),
                ));
            }
        }
    }
    results
}

/// Fetches multiple URLs concurrently with domain staggering and bounded concurrency.
///
/// Per-URL failures are returned in each tuple's `Err`, not as a global error.
pub fn fetch_many(
    urls: Vec<String>,
    cap: usize,
    max_concurrency: usize,
    domain_gap_ms: u64,
    max_bytes: usize,
) -> BatchOutcome {
    let rt = shared_runtime();
    rt.block_on(fetch_many_inner(
        urls,
        cap,
        max_concurrency,
        domain_gap_ms,
        max_bytes,
    ))
}

// ────────────────────────────────────────────────────────────────
// 8. Conditional fetch
// ────────────────────────────────────────────────────────────────

/// Conditional fetch result (Tier 1.4):
/// (not_modified, raw_html, markdown, [(url, anchor_text)],
/// hidden_nodes_removed, FetchMeta, etag, last_modified).
pub type ConditionalPage = (
    bool,
    String,
    String,
    Vec<(String, String)>,
    usize,
    FetchMeta,
    Option<String>,
    Option<String>,
);

/// Fetches one URL with conditional request validators.
///
/// Sends `If-None-Match` / `If-Modified-Since` when supplied. A 304 yields
/// `not_modified = true` with empty html/markdown/links; refreshed validators are always returned.
///
/// # Errors
///
/// Returns [`FetchError`] for transport, SSRF, validation, size, or processing failures.
pub fn fetch_conditional_single(
    url: &str,
    cap: usize,
    max_bytes: usize,
    etag: Option<&str>,
    last_modified: Option<&str>,
) -> Result<ConditionalPage> {
    let rt = shared_runtime();
    rt.block_on(async {
        let (html, meta, etag2, lm2) =
            http_fetch_html_ext(shared_client(), url, max_bytes, etag, last_modified).await?;
        if meta.0 == 304 {
            // Not modified: no body to process — the caller serves the
            // cached copy. Validators still come back (they may rotate).
            return Ok((
                true,
                String::new(),
                String::new(),
                Vec::new(),
                0usize,
                meta,
                etag2,
                lm2,
            ));
        }
        let (md, links, removed) = process_html_anchored(&html, url, cap)?;
        Ok((false, html, md, links, removed, meta, etag2, lm2))
    })
}
