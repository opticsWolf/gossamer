//! `gossamer batch`: parallel-equivalent inspection envelope over Rust fetches.

use std::collections::HashSet;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use clap::ArgMatches;
use gossamer::fetch::fetch_html_full_single;
use gossamer::jsonfmt::py_dumps;
use gossamer::metaextract::meta_extract_all;
use gossamer::miscutils::{classify_link, sha256_hex};
use gossamer::runtime::{RuntimeConfig, RuntimeState};
use gossamer::service::fetch::{
    compact_metadata, raw_entry_matches_strategy, resolve_strategy, slice_markdown,
};
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

fn entry_from_cache(url: &str, entry: &Value) -> Value {
    let full = entry
        .get("markdown")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let links = entry
        .get("links")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let pair = item.as_array()?;
                    Some((
                        pair.first()?.as_str()?.to_string(),
                        pair.get(1)?.as_str()?.to_string(),
                    ))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let meta = entry.get("meta").cloned().unwrap_or_else(|| json!({}));
    let prov = meta.get("provenance").cloned().unwrap_or_else(|| json!({}));
    finalize_entry(url, &full, links, &meta, &prov, true)
}

fn finalize_entry(
    url: &str,
    full: &str,
    links: Vec<(String, String)>,
    meta: &Value,
    prov: &Value,
    from_cache: bool,
) -> Value {
    let metadata = compact_metadata(meta);
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
    let slice = slice_markdown(full, 0, 1, 8000, 0, "gpt-4o")
        .unwrap_or_else(|_| json!([full.to_string(), full.chars().count(), false]));
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
        "truncated": has_more || markdown != full || total_links >= 500,
        "fetch_method": "static",
        "cache_hit": from_cache,
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
        "fetched_at": prov
            .get("fetched_at")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(timestamp),
        "http_status": prov.get("http_status").cloned().unwrap_or(Value::Null),
        "final_url": prov
            .get("final_url")
            .cloned()
            .unwrap_or_else(|| json!(url)),
        "content_type": prov.get("content_type").cloned().unwrap_or(Value::Null),
        "content_hash": sha256_hex(full),
        "guard": Value::Null,
    })
}

fn build_entry(runtime: &RuntimeState, url: &str, strategy: &str, now: f64) -> Value {
    if let Some(entry) = runtime
        .page_cache_get(url, now)
        .filter(|entry| raw_entry_matches_strategy(entry, strategy))
    {
        return entry_from_cache(url, &entry);
    }
    let sleep = runtime.throttle_gap(url, None, now);
    if sleep > 0.0 {
        thread::sleep(Duration::from_secs_f64(sleep));
    }
    let Ok((html, full, links, _removed, meta)) = fetch_html_full_single(url, 500, 50_000_000)
    else {
        runtime.release(url);
        return json!({"url": url, "error": "fetch failed"});
    };
    let (status, final_url, content_type) = meta;
    let final_url = if final_url.is_empty() {
        url.to_string()
    } else {
        final_url
    };
    let raw_meta: Value =
        serde_json::from_str(&meta_extract_all(&html, Some(&final_url))).unwrap_or(Value::Null);
    let prov = json!({
        "fetched_at": timestamp(),
        "http_status": status,
        "final_url": final_url,
        "content_type": content_type,
    });
    let mut meta = compact_metadata(&raw_meta);
    if let Some(map) = meta.as_object_mut() {
        map.insert("provenance".to_string(), prov.clone());
        map.insert("fetch_strategy".to_string(), json!(strategy));
    }
    let link_pairs: Vec<Value> = links
        .iter()
        .map(|(link_url, anchor)| json!([link_url, anchor]))
        .collect();
    let entry = json!({
        "markdown": full,
        "links": link_pairs,
        "meta": meta,
        "method": "static",
    });
    runtime.page_cache_put(url, &entry, runtime.now());
    runtime.mark_visited(url);
    runtime.release(url);
    finalize_entry(url, &full, links, &meta, &prov, false)
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
    let mut use_smart = "auto".to_string();
    let mut fetch_mode = std::env::var("GOSSAMER_FETCH_MODE").unwrap_or_else(|_| "auto".into());
    let mut cache_dir =
        std::env::var("GOSSAMER_CACHE_DIR").unwrap_or_else(|_| ".gossamer_cache".into());
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value == "--use-smart" {
            if index + 1 >= args.len() {
                return unsupported();
            }
            let requested = args[index + 1].to_string_lossy().into_owned();
            if !matches!(requested.as_str(), "auto" | "browser" | "static") {
                return unsupported();
            }
            use_smart = requested;
            index += 2;
            continue;
        }
        if matches!(
            value.as_str(),
            "--cache-dir" | "--config" | "--keystore" | "--log-level" | "--fetch-mode"
        ) {
            if index + 1 >= args.len() {
                return unsupported();
            }
            let flag_value = args[index + 1].to_string_lossy().into_owned();
            if value == "--cache-dir" {
                cache_dir = flag_value;
            } else if value == "--fetch-mode" {
                fetch_mode = flag_value;
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

    let runtime = match RuntimeState::new(RuntimeConfig::from_cache_dir(PathBuf::from(&cache_dir)))
    {
        Ok(value) => value,
        Err(error) => {
            eprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
    let strategy = resolve_strategy(&fetch_mode, &use_smart).unwrap_or("auto");
    let mut output: Vec<Value> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    let mut rejected: HashSet<String> = HashSet::new();
    for raw in urls {
        let url = match runtime.prepare_url(&raw) {
            Ok(url) => url,
            Err(error) => {
                if error.error_type() == "SsrfBlockedError" {
                    eprintln!(
                        "Blocked non-public URL {}: {}",
                        error.raw(),
                        error.message()
                    );
                }
                if rejected.insert(raw.clone()) {
                    output.push(error.to_record());
                }
                continue;
            }
        };
        let key = runtime.identity_key(&url);
        if !seen.insert(key) {
            continue;
        }
        let now = runtime.now();
        let cached = runtime
            .page_cache_get(&url, now)
            .is_some_and(|entry| raw_entry_matches_strategy(&entry, strategy));
        if !cached {
            if strategy != "auto" {
                runtime.forget_visited(&url);
            }
            if runtime.is_visited(&url) || !runtime.robots_allows(&url, now) || !runtime.claim(&url)
            {
                continue;
            }
        }
        output.push(build_entry(&runtime, &url, strategy, now));
    }
    println!("{}", py_dumps(&Value::Array(output)));
    ExitCode::SUCCESS
}
