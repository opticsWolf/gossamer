//! `gossamer discover`: feed/sitemap discovery over Rust HTTP and parsers.

use std::collections::{HashSet, VecDeque};
use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::discovery::{fetch_text, find_feed_links};
use gossamer::document::prepare_url;
use gossamer::jsonfmt::pretty_record;
use gossamer::sitemap::parse_sitemap;
use gossamer::ssrf::check_url;
use serde_json::{Value, json};

const KNOWN_FLAGS: [&str; 5] = [
    "--cache-dir",
    "--config",
    "--keystore",
    "--log-level",
    "--fetch-mode",
];

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn site_root(url: &str) -> Option<String> {
    let start = url.find("://")? + 3;
    let rest = &url[start..];
    let end = rest.find('/').unwrap_or(rest.len());
    let authority = &rest[..end];
    (!authority.is_empty()).then(|| format!("{}://{}", &url[..start - 3], authority))
}

pub fn run(command: &str, matches: &ArgMatches) -> ExitCode {
    if let Some(code) = super::cli::maybe_run(command, matches) {
        return code;
    }

    let args: Vec<OsString> = matches
        .get_many::<OsString>("args")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    if matches.get_count("help") > 0 {
        return unsupported();
    }
    let mut positional = Vec::new();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !KNOWN_FLAGS.contains(&value.as_str()) || index + 1 >= args.len() {
                return unsupported();
            }
            if value == "--fetch-mode"
                && !matches!(
                    args[index + 1].to_string_lossy().as_ref(),
                    "auto" | "browser" | "static"
                )
            {
                return unsupported();
            }
            index += 2;
            continue;
        }
        positional.push(value);
        index += 1;
    }
    if positional.len() != 1 {
        return unsupported();
    }

    let raw = &positional[0];
    let url = match prepare_url(raw, None) {
        Ok(url) => url,
        Err(error) => {
            if error.error_type() == "SsrfBlockedError" {
                eprintln!(
                    "Blocked non-public URL {}: {}",
                    error.raw(),
                    error.message()
                );
            }
            println!("{}", pretty_record(&error.to_record()));
            return ExitCode::SUCCESS;
        }
    };

    let root = site_root(&url);
    let feeds: Vec<Value> = match fetch_text(&url, 30.0) {
        Ok((body, final_url)) => find_feed_links(&body, &final_url),
        Err(error) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({
                    "error": format!("Discovery failed: {error}"), "url": url
                }))
                .unwrap_or_default()
            );
            return ExitCode::SUCCESS;
        }
    };

    let mut sitemaps = Vec::new();
    let mut found: Vec<String> = Vec::new();
    let mut truncated = false;
    if let Some(root) = root.clone() {
        let first = format!("{}/sitemap.xml", root.trim_end_matches('/'));
        if check_url(&first, false, None).is_ok() {
            let mut queue: VecDeque<(String, usize)> = VecDeque::from([(first, 0usize)]);
            let mut seen = HashSet::new();
            let mut fetched = 0usize;
            while let Some((sitemap_url, hop)) = queue.pop_front() {
                if fetched >= 10 || !seen.insert(sitemap_url.clone()) {
                    break;
                }
                if check_url(&sitemap_url, false, None).is_err() {
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
    println!(
        "{}",
        serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
    );
    ExitCode::SUCCESS
}
