//! `gossamer batch`: parallel-equivalent inspection envelope over Rust fetches.

use std::ffi::OsString;
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use clap::ArgMatches;
use gossamer::document::prepare_url;
use gossamer::fetch::fetch_html_full_single;
use gossamer::jsonfmt::py_dumps;
use gossamer::metaextract::meta_extract_all;
use gossamer::miscutils::{classify_link, sha256_hex};
use gossamer::service::fetch::{compact_metadata, slice_markdown};
use gossamer::tokens::count_tokens;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn timestamp() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let days = secs.div_euclid(86_400);
    let seconds = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    let year = year + i64::from(month <= 2);
    format!(
        "{year:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}+00:00",
        month,
        day,
        seconds / 3600,
        (seconds % 3600) / 60,
        seconds % 60,
        now.subsec_micros()
    )
}

fn build_entry(url: &str, strategy: &str) -> Value {
    let Ok((html, full, links, _removed, meta)) = fetch_html_full_single(url, 8000, 50_000_000)
    else {
        return json!({"url": url, "error": "fetch failed"});
    };
    let (status, final_url, content_type) = meta;
    let raw_meta: Value =
        serde_json::from_str(&meta_extract_all(&html, Some(&final_url))).unwrap_or(Value::Null);
    let mut metadata = compact_metadata(&raw_meta);
    if let Some(map) = metadata.as_object_mut() {
        map.insert("fetch_strategy".to_string(), json!(strategy));
    }
    let mut follow_up_links = Vec::new();
    let mut seen = Vec::new();
    for (link_url, anchor) in &links {
        if link_url.is_empty() || seen.contains(link_url) {
            continue;
        }
        seen.push(link_url.clone());
        let title = anchor.trim();
        follow_up_links.push(json!({
            "url": link_url,
            "title": if title.is_empty() { "(untitled)" } else { title },
            "type": classify_link(link_url),
        }));
    }
    let total_links = follow_up_links.len();
    let slice = slice_markdown(&full, 0, 1, 8000, 0, "gpt-4o")
        .unwrap_or_else(|_| json!([full.clone(), full.chars().count(), false]));
    let markdown = slice
        .get(0)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let next_offset = slice.get(1).and_then(Value::as_i64);
    let has_more = slice.get(2).and_then(Value::as_bool).unwrap_or(false);
    json!({
        "url": url,
        "markdown": markdown,
        "markdown_tokens": count_tokens(&markdown, "gpt-4o").unwrap_or(0),
        "follow_up_links": follow_up_links,
        "delivered_links": total_links,
        "total_links": total_links,
        "truncated": false,
        "fetch_method": "static",
        "cache_hit": false,
        "revalidated": false,
        "metadata": metadata,
        "query": Value::Null,
        "sections_available": 0,
        "sections_selected": 0,
        "section_anchors": [],
        "offset": 0,
        "next_offset": next_offset,
        "has_more": has_more,
        "chars_total": full.chars().count(),
        "fetched_at": timestamp(),
        "http_status": status,
        "final_url": final_url,
        "content_type": content_type,
        "content_hash": sha256_hex(&full),
        "guard": Value::Null,
    })
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

    let mut urls: Vec<String> = Vec::new();
    let mut strategy = "auto".to_string();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value == "--use-smart" {
            if index + 1 >= args.len() {
                return unsupported();
            }
            strategy = args[index + 1].to_string_lossy().into_owned();
            if !matches!(strategy.as_str(), "auto" | "browser" | "static") {
                return unsupported();
            }
            index += 2;
            continue;
        }
        if value.starts_with('-') {
            return unsupported();
        }
        urls.push(value);
        index += 1;
    }
    if urls.is_empty() {
        return unsupported();
    }

    let mut output: Vec<Value> = Vec::new();
    let mut emitted: Vec<String> = Vec::new();
    for raw in urls {
        let url = match prepare_url(&raw, None) {
            Ok(url) => url,
            Err(error) => {
                if error.error_type() == "SsrfBlockedError" {
                    eprintln!(
                        "Blocked non-public URL {}: {}",
                        error.raw(),
                        error.message()
                    );
                }
                if !emitted.contains(&raw) {
                    emitted.push(raw.clone());
                    output.push(error.to_record());
                }
                continue;
            }
        };
        if emitted.contains(&url) {
            continue;
        }
        emitted.push(url.clone());
        output.push(build_entry(&url, &strategy));
    }
    println!("{}", py_dumps(&Value::Array(output)));
    ExitCode::SUCCESS
}
