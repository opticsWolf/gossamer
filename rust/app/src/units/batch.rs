//! `gossamer batch`: parallel-equivalent inspection envelope over Rust fetches.

use std::collections::HashSet;
use std::process::ExitCode;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use gossamer::budget::{content_split, truncate_text};
use gossamer::cli_args::Parsed;
use gossamer::documents::absolutize_markdown_links;
use gossamer::fetch::fetch_html_full_single;
use gossamer::jsonfmt::py_dumps_utf8;
use gossamer::metaextract::meta_extract_all;
use gossamer::miscutils::{classify_link, sha256_hex};
use gossamer::runtime::RuntimeState;
use gossamer::service::fetch::{compact_metadata, raw_entry_matches_strategy, resolve_strategy};
use gossamer::tokens::count_tokens;
use serde_json::{Value, json};

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

fn entry_from_cache(url: &str, entry: &Value, parsed: &Parsed) -> Value {
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
    finalize_entry(url, &full, links, &meta, &prov, true, parsed)
}

fn out_len_reached(items: &[Value], cap: i64) -> bool {
    cap > 0 && items.len() as i64 >= cap
}

fn finalize_entry(
    url: &str,
    full: &str,
    links: Vec<(String, String)>,
    meta: &Value,
    prov: &Value,
    from_cache: bool,
    parsed: &Parsed,
) -> Value {
    let metadata = compact_metadata(meta);
    let full = absolutize_markdown_links(full, url);
    let max_chars = crate::units::settings::int(
        parsed,
        "max_markdown_chars",
        "GOSSAMER_MAX_MARKDOWN_CHARS",
        8000,
    );
    let max_tokens = crate::units::settings::int(parsed, "max_tokens", "GOSSAMER_MAX_TOKENS", 0);
    let model = crate::units::settings::text(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
    let candidate_cap =
        crate::units::settings::int(parsed, "candidate_cap", "GOSSAMER_CANDIDATE_CAP", 500).max(1);
    let (md_chars, md_tokens) = content_split(max_chars, max_tokens, 0.25);
    let mut follow_up_links = Vec::new();
    let mut seen = Vec::new();
    for (link_url, anchor) in &links {
        if out_len_reached(&follow_up_links, candidate_cap) {
            break;
        }
        if link_url.is_empty() || seen.contains(link_url) {
            continue;
        }
        seen.push(link_url.clone());
        let title = anchor.trim();
        follow_up_links.push(json!({
            "title": if title.is_empty() { "(untitled)" } else { title },
            "url": link_url,
            "type": classify_link(link_url),
        }));
    }
    let total_links = follow_up_links.len();
    let markdown =
        truncate_text(&full, md_chars, md_tokens, &model).unwrap_or_else(|_| full.clone());
    let markdown_truncated = markdown != full;
    let next_offset = if markdown_truncated {
        markdown.chars().count()
    } else {
        full.chars().count()
    };
    let has_more = markdown_truncated;
    let mut payload = json!({
        "url": url,
        "markdown": markdown,
        "markdown_tokens": count_tokens(&markdown, &model).unwrap_or(0),
        "follow_up_links": follow_up_links,
        "delivered_links": total_links,
        "total_links": total_links,
        "truncated": markdown_truncated || total_links as i64 >= candidate_cap,
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
        "content_hash": sha256_hex(&full),
        "guard": Value::Null,
    });
    if crate::units::inspect::apply_guard(&mut payload, parsed, url, "page_markdown") {
        return json!({
            "url": url,
            "error": "content withheld by prompt-injection guard",
            "guard": payload.get("guard").cloned().unwrap_or(Value::Null),
        });
    }
    crate::units::inspect::fit_inspection(&mut payload, max_chars, max_tokens, &model);
    payload
}

fn candidate_cap_for(parsed: &Parsed) -> i64 {
    crate::units::settings::int(parsed, "candidate_cap", "GOSSAMER_CANDIDATE_CAP", 500).max(1)
}

fn max_response_for(parsed: &Parsed) -> usize {
    crate::units::settings::int(
        parsed,
        "max_response_bytes",
        "GOSSAMER_MAX_RESPONSE_BYTES",
        5 * 1024 * 1024,
    )
    .max(0) as usize
}

fn build_entry(
    runtime: &RuntimeState,
    url: &str,
    strategy: &str,
    now: f64,
    parsed: &Parsed,
) -> Value {
    if let Some(entry) = runtime
        .page_cache_get(url, now)
        .filter(|entry| raw_entry_matches_strategy(entry, strategy))
    {
        return entry_from_cache(url, &entry, parsed);
    }
    if strategy == "browser-only" {
        runtime.release(url);
        return json!({
            "url": url,
            "error": "browser_oxide is not installed; install the optional extra (pip install \"gossamer-web[browser]\") or use use_smart='static'",
        });
    }
    let sleep = runtime.throttle_gap(url, None, now);
    if sleep > 0.0 {
        thread::sleep(Duration::from_secs_f64(sleep));
    }
    let (html, full, links, _removed, meta) = match fetch_html_full_single(
        url,
        candidate_cap_for(parsed) as usize,
        max_response_for(parsed),
    ) {
        Ok(value) => value,
        Err(error) => {
            runtime.release(url);
            return json!({"url": url, "error": error.to_string()});
        }
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
    let mut meta = raw_meta;
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
    finalize_entry(url, &full, links, &meta, &prov, false, parsed)
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let urls: Vec<String> = parsed.positionals("urls").to_vec();
    let use_smart = parsed.value("use_smart").unwrap_or("auto").to_string();
    let fetch_mode = parsed.fetch_mode().map(str::to_string).unwrap_or_else(|| {
        crate::units::settings::text(parsed, "fetch_mode", "GOSSAMER_FETCH_MODE", "auto")
    });
    let cache_dir = crate::units::settings::cache_dir(parsed);

    let runtime = match RuntimeState::new(crate::units::settings::runtime(parsed, &cache_dir)) {
        Ok(value) => value,
        Err(error) => {
            pyeprintln!("gossamer: error: {error}");
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
                    pyeprintln!(
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
            if runtime.is_visited(&url) {
                pyeprintln!("Skipping already-visited URL in batch: {url}");
                continue;
            }
            if !runtime.robots_allows(&url, now) {
                pyeprintln!("Skipping robots-disallowed URL in batch: {url}");
                continue;
            }
            if !runtime.claim(&url) {
                pyeprintln!("URL already in flight: {url}");
                continue;
            }
        }
        output.push(build_entry(&runtime, &url, strategy, now, parsed));
    }
    pyprintln!("{}", py_dumps_utf8(&Value::Array(output)));
    ExitCode::SUCCESS
}
