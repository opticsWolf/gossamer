//! `gossamer discover`: feed/sitemap discovery over Rust HTTP and parsers.

use std::collections::{HashSet, VecDeque};
use std::process::ExitCode;

use gossamer::cli_args::Parsed;
use gossamer::discovery::{fetch_text, find_feed_links};
use gossamer::document::prepare_url;
use gossamer::fetch::fetch_html_full_single;
use gossamer::jsonfmt::pretty_record;
use gossamer::jsonfmt::py_dumps_pretty;
use gossamer::runtime::RuntimeState;
use gossamer::sitemap::parse_sitemap;
use gossamer::ssrf::check_url;
use serde_json::{Value, json};

fn site_root(url: &str) -> Option<String> {
    let start = url.find("://")? + 3;
    let rest = &url[start..];
    let end = rest.find('/').unwrap_or(rest.len());
    let authority = &rest[..end];
    (!authority.is_empty()).then(|| format!("{}://{}", &url[..start - 3], authority))
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let Some(raw) = parsed.positional("url") else {
        return super::unsupported();
    };

    let url = match prepare_url(raw, None) {
        Ok(url) => url,
        Err(error) => {
            if error.error_type() == "SsrfBlockedError" {
                pyeprintln!(
                    "Blocked non-public URL {}: {}",
                    error.raw(),
                    error.message()
                );
            }
            pyprintln!("{}", pretty_record(&error.to_record()));
            return ExitCode::SUCCESS;
        }
    };

    let cache_dir = crate::units::settings::cache_dir(parsed);
    let runtime = match RuntimeState::new(crate::units::settings::runtime(parsed, &cache_dir)) {
        Ok(runtime) => runtime,
        Err(error) => {
            pyeprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
    let now = runtime.now();
    if !runtime.robots_allows(&url, now) {
        pyeprintln!("URL disallowed by robots.txt: {url}");
        pyprintln!(
            "{}",
            py_dumps_pretty(&json!({"warning": "URL disallowed by robots.txt", "url": url}))
        );
        return ExitCode::SUCCESS;
    }
    if !runtime.claim(&url) {
        pyeprintln!("URL already visited or in flight: {url}");
        pyprintln!(
            "{}",
            py_dumps_pretty(&json!({"warning": "URL already visited", "url": url}))
        );
        return ExitCode::SUCCESS;
    }

    let root = site_root(&url);
    let max_response_bytes = crate::units::settings::int(
        parsed,
        "max_response_bytes",
        "GOSSAMER_MAX_RESPONSE_BYTES",
        5 * 1024 * 1024,
    )
    .max(0) as usize;
    let feeds: Vec<Value> = match fetch_html_full_single(&url, 500, max_response_bytes) {
        Ok((html, _markdown, _links, _removed, meta)) => {
            let final_url = if meta.1.is_empty() {
                url.clone()
            } else {
                meta.1
            };
            find_feed_links(&html, &final_url)
        }
        Err(_) => {
            pyeprintln!("Discovery failed for {url}: browser_oxide is not installed");
            pyprintln!(
                "{}",
                py_dumps_pretty(&json!({
                    "error": "Discovery failed: browser_oxide is not installed", "url": url
                }))
            );
            return ExitCode::SUCCESS;
        }
    };

    let mut sitemaps = Vec::new();
    let mut found: Vec<String> = Vec::new();
    let mut truncated = false;
    if let Some(root) = root.clone() {
        let first = format!("{}/sitemap.xml", root.trim_end_matches('/'));
        if check_url(&first, gossamer::ssrf::env_allow_private(), None).is_ok() {
            let mut queue: VecDeque<(String, usize)> = VecDeque::from([(first, 0usize)]);
            let mut seen = HashSet::new();
            let mut fetched = 0usize;
            while let Some((sitemap_url, hop)) = queue.pop_front() {
                if fetched >= 10 || !seen.insert(sitemap_url.clone()) {
                    break;
                }
                if check_url(&sitemap_url, gossamer::ssrf::env_allow_private(), None).is_err() {
                    continue;
                }
                fetched += 1;
                let Ok((xml, _)) = fetch_text(&sitemap_url, 30.0) else {
                    continue;
                };
                let Some(parsed) = parse_sitemap(&xml, &sitemap_url, 500) else {
                    continue;
                };
                let kind = parsed
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or("urlset");
                let locs: Vec<String> = parsed
                    .get("locs")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                truncated |= parsed
                    .get("truncated")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                sitemaps.push(json!({"url": sitemap_url, "kind": kind, "count": locs.len()}));
                if kind == "index" && hop < 3 {
                    for child in locs {
                        if !seen.contains(&child) {
                            queue.push_back((child, hop + 1));
                        }
                    }
                } else {
                    for page_url in locs {
                        if !found.contains(&page_url) {
                            if found.len() >= 1000 {
                                truncated = true;
                                break;
                            }
                            found.push(page_url);
                        }
                    }
                }
            }
        }
    }

    let payload = json!({
        "url": url,
        "site_root": root,
        "feeds": feeds,
        "sitemaps": sitemaps,
        "urls": found,
        "count": found.len(),
        "truncated": truncated,
    });
    pyprintln!("{}", py_dumps_pretty(&payload));
    ExitCode::SUCCESS
}
