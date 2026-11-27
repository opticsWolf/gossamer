//! `gossamer inspect`: HTML inspection over Rust fetch/extraction kernels.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use clap::ArgMatches;
use gossamer::document::structured_html_payload;
use gossamer::fetch::fetch_html_full_single;
use gossamer::jsonfmt::{pretty_record, py_dumps};
use gossamer::metaextract::meta_extract_all;
use gossamer::miscutils::{classify_link, sha256_hex};
use gossamer::runtime::{RuntimeConfig, RuntimeState};
use gossamer::sections::select_relevant_sections;
use gossamer::service::fetch::{
    compact_metadata, raw_entry_matches_strategy, resolve_strategy, slice_markdown,
};
use gossamer::tables::extract_tables;
use gossamer::tokens::count_tokens;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn warning(payload: &str, url: &str) -> ExitCode {
    let body = json!({"warning": payload, "url": url});
    println!(
        "{}",
        serde_json::to_string_pretty(&body).unwrap_or_else(|_| body.to_string())
    );
    ExitCode::SUCCESS
}

fn known_flag(name: &str) -> bool {
    matches!(
        name,
        "--use-smart"
            | "--query"
            | "--offset"
            | "--max-chunks"
            | "--structured"
            | "--cache-dir"
            | "--config"
            | "--keystore"
            | "--log-level"
            | "--fetch-mode"
    )
}

fn timestamp() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let days = secs.div_euclid(86_400);
    let seconds = secs.rem_euclid(86_400);
    // Howard Hinnant's civil-from-days algorithm (UTC).
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

    let mut url: Option<String> = None;
    let mut query: Option<String> = None;
    let mut use_smart = "auto".to_string();
    let mut fetch_mode = std::env::var("GOSSAMER_FETCH_MODE").unwrap_or_else(|_| "auto".into());
    let mut cache_dir =
        std::env::var("GOSSAMER_CACHE_DIR").unwrap_or_else(|_| ".gossamer_cache".into());
    let mut structured = false;
    let mut offset = 0i64;
    let mut max_chunks = 1i64;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !known_flag(&value) {
                return unsupported();
            }
            if value == "--structured" {
                structured = true;
                index += 1;
                continue;
            }
            if index + 1 >= args.len() {
                return unsupported();
            }
            let flag_value = args[index + 1].to_string_lossy().into_owned();
            if value == "--use-smart" {
                if !matches!(flag_value.as_str(), "auto" | "browser" | "static") {
                    return unsupported();
                }
                use_smart = flag_value.clone();
            }
            if value == "--query" {
                query = Some(flag_value);
            } else if value == "--fetch-mode" {
                fetch_mode = flag_value;
            } else if value == "--cache-dir" {
                cache_dir = flag_value;
            } else if value == "--offset"
                && let Ok(parsed) = flag_value.parse::<i64>()
            {
                offset = parsed;
            } else if value == "--max-chunks"
                && let Ok(parsed) = flag_value.parse::<i64>()
            {
                max_chunks = parsed;
            }
            index += 2;
            continue;
        }
        if url.is_some() {
            return unsupported();
        }
        url = Some(value);
        index += 1;
    }
    let Some(raw) = url else {
        return unsupported();
    };

    let runtime = match RuntimeState::new(RuntimeConfig::from_cache_dir(PathBuf::from(&cache_dir)))
    {
        Ok(value) => value,
        Err(error) => {
            eprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
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
            println!("{}", pretty_record(&error.to_record()));
            return ExitCode::SUCCESS;
        }
    };
    let now = runtime.now();
    let strategy = resolve_strategy(&fetch_mode, &use_smart).unwrap_or("auto");
    let cached = (!structured)
        .then(|| runtime.page_cache_get(&url, now))
        .flatten()
        .filter(|entry| raw_entry_matches_strategy(entry, strategy));

    let (html, full_markdown, links, entry_meta, provenance, raw_metadata, from_cache) =
        if let Some(entry) = cached {
            let markdown = entry
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
            let entry_meta = entry.get("meta").cloned().unwrap_or_else(|| json!({}));
            let prov = entry_meta
                .get("provenance")
                .cloned()
                .unwrap_or_else(|| json!({}));
            (
                String::new(),
                markdown,
                links,
                entry_meta,
                prov,
                Value::Null,
                true,
            )
        } else {
            if !runtime.robots_allows(&url, now) {
                return warning("URL disallowed by robots.txt", &url);
            }
            if !runtime.claim(&url) {
                return warning("URL already visited", &url);
            }
            let sleep = runtime.throttle_gap(&url, None, now);
            if sleep > 0.0 {
                thread::sleep(Duration::from_secs_f64(sleep));
            }
            let (html, full_markdown, links, _removed, meta) =
                match fetch_html_full_single(&url, 500, 50_000_000) {
                    Ok(value) => value,
                    Err(error) => {
                        runtime.release(&url);
                        let detail = match use_smart.as_str() {
                            "static" => error.to_string(),
                            "browser" => "cannot unpack non-iterable NoneType object".to_string(),
                            _ => "browser_oxide is not installed".to_string(),
                        };
                        let label = if structured {
                            "Structured HTML inspection failed"
                        } else {
                            "HTML inspection failed"
                        };
                        eprintln!("{label} for {url}: {detail}");
                        let payload = json!({"error": format!("{label}: {detail}")});
                        println!(
                            "{}",
                            serde_json::to_string_pretty(&payload)
                                .unwrap_or_else(|_| payload.to_string())
                        );
                        return ExitCode::SUCCESS;
                    }
                };
            let (status, final_url, content_type) = meta;
            let final_url = if final_url.is_empty() {
                url.clone()
            } else {
                final_url
            };
            let raw_metadata: Value =
                serde_json::from_str(&meta_extract_all(&html, Some(&final_url)))
                    .unwrap_or(Value::Null);
            let fetched_at = timestamp();
            let prov = json!({
                "fetched_at": fetched_at,
                "http_status": status,
                "final_url": final_url,
                "content_type": content_type,
            });
            let mut entry_meta = compact_metadata(&raw_metadata);
            if let Some(map) = entry_meta.as_object_mut() {
                map.insert("provenance".to_string(), prov.clone());
                map.insert("fetch_strategy".to_string(), json!(strategy));
            }
            let link_pairs: Vec<Value> = links
                .iter()
                .map(|(link_url, anchor)| json!([link_url, anchor]))
                .collect();
            let entry = json!({
                "markdown": full_markdown,
                "links": link_pairs,
                "meta": entry_meta,
                "method": "static",
            });
            runtime.page_cache_put(&url, &entry, runtime.now());
            runtime.mark_visited(&url);
            runtime.release(&url);
            (
                html,
                full_markdown,
                links,
                entry_meta,
                prov,
                raw_metadata,
                false,
            )
        };
    let http_status = provenance
        .get("http_status")
        .cloned()
        .unwrap_or(Value::Null);
    let final_url = provenance
        .get("final_url")
        .cloned()
        .unwrap_or_else(|| json!(url));
    let content_type = provenance
        .get("content_type")
        .cloned()
        .unwrap_or(Value::Null);
    let fetched_at = provenance
        .get("fetched_at")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(timestamp);
    if structured {
        let link_pairs: Vec<Value> = links
            .iter()
            .map(|(link_url, anchor)| json!([link_url, anchor]))
            .collect();
        let tables = extract_tables(&html, 20, 500);
        let payload = structured_html_payload(
            &full_markdown,
            &Value::Array(link_pairs),
            &raw_metadata,
            &url,
            20,
            &tables,
        );
        println!(
            "{}",
            serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
        );
        return ExitCode::SUCCESS;
    }
    let metadata = compact_metadata(&entry_meta);

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

    let chars_total = full_markdown.chars().count();
    let explicit_paging = offset > 0 || max_chunks != 1;
    let query_text = query.as_deref().map(str::trim).filter(|v| !v.is_empty());
    let selection = query_text
        .filter(|_| !explicit_paging)
        .and_then(|value| select_relevant_sections(&full_markdown, value, 8000));
    let (
        markdown,
        next_offset,
        has_more,
        query_echo,
        sections_available,
        sections_selected,
        section_anchors,
        markdown_truncated,
        offset_out,
    ) = if let Some(selected) = selection {
        (
            selected.markdown,
            Value::Null,
            false,
            query_text
                .map(str::to_string)
                .map(Value::String)
                .unwrap_or(Value::Null),
            selected.total_sections as i64,
            selected.selected_count as i64,
            Value::Array(
                selected
                    .anchors
                    .into_iter()
                    .map(Value::String)
                    .collect::<Vec<_>>(),
            ),
            true,
            0,
        )
    } else {
        let slice = slice_markdown(&full_markdown, offset, max_chunks, 8000, 0, "gpt-4o")
            .unwrap_or_else(|_| json!([full_markdown.clone(), chars_total, false]));
        let markdown = slice
            .get(0)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let next_offset = slice.get(1).cloned().unwrap_or(Value::Null);
        let has_more = slice.get(2).and_then(Value::as_bool).unwrap_or(false);
        let truncated = offset > 0 || has_more || markdown != full_markdown;
        (
            markdown,
            next_offset,
            has_more,
            Value::Null,
            0,
            0,
            Value::Array(Vec::new()),
            truncated,
            offset,
        )
    };
    let tokens = count_tokens(&markdown, "gpt-4o").unwrap_or(0);

    let mut payload = json!({
        "url": url,
        "markdown": markdown,
        "markdown_tokens": tokens,
        "follow_up_links": follow_up_links,
        "delivered_links": total_links,
        "total_links": total_links,
        "truncated": markdown_truncated || total_links >= 500,
        "fetch_method": "static",
        "cache_hit": from_cache,
        "revalidated": false,
        "metadata": metadata,
        "query": query_echo,
        "sections_available": sections_available,
        "sections_selected": sections_selected,
        "section_anchors": section_anchors,
        "offset": offset_out,
        "next_offset": next_offset,
        "has_more": has_more,
        "chars_total": chars_total,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "final_url": final_url,
        "content_type": content_type,
        "content_hash": sha256_hex(&full_markdown),
        "guard": Value::Null,
    });
    if offset > 0 && offset >= full_markdown.chars().count() as i64 && !full_markdown.is_empty() {
        payload["warning"] = json!(format!(
            "offset {offset} is past the end of the content ({} chars); nothing new was returned",
            full_markdown.chars().count()
        ));
    }
    println!("{}", py_dumps(&payload));
    ExitCode::SUCCESS
}
