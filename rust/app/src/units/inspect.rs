//! `gossamer inspect`: HTML inspection over Rust fetch/extraction kernels.

use std::process::ExitCode;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use gossamer::budget::truncate_text;
use gossamer::cli_args::Parsed;
use gossamer::document::structured_html_payload;
use gossamer::fetch::fetch_html_full_single;
use gossamer::jsonfmt::pretty_record;
use gossamer::jsonfmt::{py_dumps_pretty, py_dumps_utf8};
use gossamer::metaextract::meta_extract_all;
use gossamer::miscutils::{classify_link, sha256_hex};
use gossamer::runtime::RuntimeState;
use gossamer::sections::select_relevant_sections;
use gossamer::service::fetch::{
    compact_metadata, raw_entry_matches_strategy, resolve_strategy, slice_markdown,
};
use gossamer::tables::extract_tables;
use gossamer::tokens::count_tokens;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    pyeprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn warning(payload: &str, url: &str) -> ExitCode {
    let body = json!({"warning": payload, "url": url});
    pyprintln!("{}", py_dumps_pretty(&body));
    ExitCode::SUCCESS
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

fn config_int(parsed: &Parsed, key: &str, env: &str, default: i64) -> i64 {
    crate::units::settings::int(parsed, key, env, default)
}

fn config_str(parsed: &Parsed, key: &str, env: &str, default: &str) -> String {
    crate::units::settings::text(parsed, key, env, default)
}

pub(crate) fn fit_inspection(payload: &mut Value, max_chars: i64, max_tokens: i64, model: &str) {
    let original_total = payload
        .get("follow_up_links")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    let fits = |value: &Value| {
        let text = gossamer::jsonfmt::py_dumps_utf8(value);
        gossamer::budget::json_fits(&text, max_chars, max_tokens, model).unwrap_or(false)
    };
    while !fits(payload) {
        let Some(links) = payload
            .get_mut("follow_up_links")
            .and_then(Value::as_array_mut)
        else {
            break;
        };
        if links.is_empty() {
            break;
        }
        let keep = links.len() / 2;
        links.truncate(keep);
        if let Some(object) = payload.as_object_mut() {
            object.insert("truncated".to_string(), Value::Bool(true));
        }
    }
    let delivered = payload
        .get("follow_up_links")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    if let Some(object) = payload.as_object_mut() {
        object.insert("delivered_links".to_string(), json!(delivered));
        object.insert(
            "total_links".to_string(),
            json!(original_total.max(original_total)),
        );
    }
}

fn scope_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Bool(flag) => {
            if *flag {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Number(number) => number.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

pub(crate) fn apply_guard(
    payload: &mut Value,
    parsed: &Parsed,
    url: &str,
    main_scope: &str,
) -> bool {
    let Some(config) = crate::units::guard::config(parsed) else {
        return false;
    };
    let has_markdown = payload.get("markdown").is_some();
    let main_text = payload
        .get("markdown")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| {
            payload
                .pointer("/pages/0/markdown")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_default();
    let meta_text = payload
        .get("metadata")
        .and_then(Value::as_object)
        .map(|metadata| {
            metadata
                .values()
                .filter(|value| !value.is_null() && **value != Value::Bool(false))
                .map(scope_text)
                .filter(|text| !text.is_empty())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default();
    let title_text = payload
        .get("follow_up_links")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("title").and_then(Value::as_str))
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default();
    let pairs = [
        (main_scope, main_text.clone()),
        ("page_metadata", meta_text),
        ("follow_up_titles", title_text),
    ];
    let Some(outcome) = crate::units::guard::evaluate(&config, &pairs, main_scope) else {
        return false;
    };
    if let Some(object) = payload.as_object_mut() {
        object.insert("guard".to_string(), outcome.block.clone());
    }
    if outcome.withheld {
        if let Some(object) = payload.as_object_mut() {
            object.insert("markdown".to_string(), json!(""));
            object.insert("markdown_tokens".to_string(), json!(0));
            object.insert("follow_up_links".to_string(), json!([]));
            object.insert("delivered_links".to_string(), json!(0));
        }
        if let Some(pages) = payload.get_mut("pages").and_then(Value::as_array_mut) {
            for page in pages {
                if let Some(object) = page.as_object_mut() {
                    object.insert("markdown".to_string(), json!(""));
                }
            }
        }
        return true;
    }
    let guarded = crate::units::guard::annotate(&config, &outcome, &main_text, url);
    if main_scope == "page_markdown"
        && has_markdown
        && let Some(object) = payload.as_object_mut()
    {
        object.insert("markdown".to_string(), json!(guarded));
        let model =
            crate::units::settings::text(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
        object.insert(
            "markdown_tokens".to_string(),
            json!(gossamer::tokens::count_tokens(&guarded, &model).unwrap_or(0)),
        );
    }
    if main_scope == "page_markdown"
        && let Some(page) = payload.pointer_mut("/pages/0")
        && let Some(object) = page.as_object_mut()
    {
        object.insert("markdown".to_string(), json!(guarded));
    }
    false
}

fn fit_structured(
    payload_json: &str,
    url: &str,
    max_chars: i64,
    max_tokens: i64,
    model: &str,
    cache_hit: bool,
) -> String {
    let fits = |text: &str| {
        gossamer::budget::json_fits(text, max_chars, max_tokens, model).unwrap_or(false)
    };
    if fits(payload_json) {
        return payload_json.to_string();
    }
    let mut budget = if max_chars > 0 {
        max_chars
    } else {
        payload_json.chars().count() as i64
    };
    while budget > 240 {
        budget /= 2;
        if let Ok(candidate) = gossamer::budget::shrink_payload(payload_json, Some(budget as usize))
            && fits(&candidate)
        {
            return candidate;
        }
    }
    pyeprintln!(
        "Payload could not be shrunk into the output budget; returning an overflow envelope instead of invalid JSON"
    );
    let mut overflow = json!({
        "url": url,
        "error": "page too large for the output budget",
    });
    if cache_hit && let Some(object) = overflow.as_object_mut() {
        object.insert("cache_hit".to_string(), Value::Bool(true));
    }
    py_dumps_pretty(&overflow)
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let Some(raw) = parsed.positional("url").map(str::to_string) else {
        return unsupported();
    };
    let query = parsed.value("query").map(str::to_string);
    let use_smart = parsed.value("use_smart").unwrap_or("auto").to_string();
    let fetch_mode = parsed.fetch_mode().map(str::to_string).unwrap_or_else(|| {
        crate::units::settings::text(parsed, "fetch_mode", "GOSSAMER_FETCH_MODE", "auto")
    });
    let cache_dir = crate::units::settings::cache_dir(parsed);
    let structured = parsed.flag("structured");
    let offset = parsed.int("offset").unwrap_or(0).max(0);
    let max_chunks = parsed.int("max_chunks").unwrap_or(1).max(1);
    let max_chars = config_int(
        parsed,
        "max_markdown_chars",
        "GOSSAMER_MAX_MARKDOWN_CHARS",
        8000,
    );
    let max_tokens = config_int(parsed, "max_tokens", "GOSSAMER_MAX_TOKENS", 0);
    let model = config_str(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
    let max_links = config_int(parsed, "max_links", "GOSSAMER_MAX_LINKS", 20).max(0);
    let candidate_cap = config_int(parsed, "candidate_cap", "GOSSAMER_CANDIDATE_CAP", 500).max(1);
    let (md_chars, md_tokens) = gossamer::budget::content_split(max_chars, max_tokens, 0.25);

    let runtime = match RuntimeState::new(crate::units::settings::runtime(parsed, &cache_dir)) {
        Ok(value) => value,
        Err(error) => {
            pyeprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
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
            pyprintln!("{}", pretty_record(&error.to_record()));
            return ExitCode::SUCCESS;
        }
    };
    let now = runtime.now();
    let strategy = resolve_strategy(&fetch_mode, &use_smart).unwrap_or("auto");
    let structured_key = {
        let mut key = format!("structured:{}", runtime.identity_key(&url));
        if strategy != "auto" {
            key.push_str(&format!("#strategy={strategy}"));
        }
        key
    };
    if structured && let Some(raw) = runtime.cache().get(&structured_key, now) {
        pyprintln!(
            "{}",
            fit_structured(&raw, &url, max_chars, max_tokens, &model, true)
        );
        return ExitCode::SUCCESS;
    }
    let cached = (!structured)
        .then(|| runtime.page_cache_get(&url, now))
        .flatten()
        .filter(|entry| raw_entry_matches_strategy(entry, strategy));

    let (html, mut full_markdown, links, entry_meta, provenance, raw_metadata, from_cache) =
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
                pyeprintln!("URL disallowed by robots.txt: {url}");
                return warning("URL disallowed by robots.txt", &url);
            }
            if !runtime.claim(&url) {
                pyeprintln!("URL already visited or in flight: {url}");
                return warning("URL already visited", &url);
            }
            let sleep = runtime.throttle_gap(&url, None, now);
            if sleep > 0.0 {
                thread::sleep(Duration::from_secs_f64(sleep));
            }
            if strategy == "browser-only" {
                runtime.release(&url);
                let detail = "browser_oxide is not installed; install the optional extra (pip install \"gossamer-web[browser]\") or use use_smart='static'";
                let label = if structured {
                    "Structured HTML inspection failed"
                } else {
                    "HTML inspection failed"
                };
                pyeprintln!("{label} for {url}: {detail}");
                pyprintln!(
                    "{}",
                    py_dumps_pretty(&json!({"error": format!("{label}: {detail}")}))
                );
                return ExitCode::SUCCESS;
            }
            let (html, full_markdown, links, removed, meta) =
                match fetch_html_full_single(&url, candidate_cap as usize, 50_000_000) {
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
                        pyeprintln!("{label} for {url}: {detail}");
                        let payload = json!({"error": format!("{label}: {detail}")});
                        pyprintln!("{}", py_dumps_pretty(&payload));
                        return ExitCode::SUCCESS;
                    }
                };
            let (status, final_url, content_type) = meta;
            let final_url = if final_url.is_empty() {
                url.clone()
            } else {
                final_url
            };
            let mut raw_metadata: Value =
                serde_json::from_str(&meta_extract_all(&html, Some(&final_url)))
                    .unwrap_or(Value::Null);
            if removed > 0
                && let Some(map) = raw_metadata.as_object_mut()
            {
                map.insert("hidden_blocks_removed".to_string(), json!(removed));
            }
            let fetched_at = timestamp();
            let prov = json!({
                "fetched_at": fetched_at,
                "http_status": status,
                "final_url": final_url,
                "content_type": content_type,
            });
            let mut entry_meta = raw_metadata.clone();
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
    full_markdown = gossamer::documents::absolutize_markdown_links(&full_markdown, &url);
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
            max_links,
            &tables,
        );
        let mut payload = payload;
        if apply_guard(&mut payload, parsed, &url, "page_markdown") {
            let error = json!({
                "error": "content withheld by prompt-injection guard",
                "url": url,
                "guard": payload.get("guard").cloned().unwrap_or(Value::Null),
            });
            pyprintln!("{}", py_dumps_pretty(&error));
            return ExitCode::SUCCESS;
        }
        let payload_json = gossamer::jsonfmt::py_dumps_pretty_utf8(&payload);
        runtime
            .cache()
            .put(&structured_key, &payload_json, runtime.now());
        pyprintln!(
            "{}",
            fit_structured(&payload_json, &url, max_chars, max_tokens, &model, false,)
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
            "title": if title.is_empty() { "(untitled)" } else { title },
            "url": link_url,
            "type": classify_link(link_url),
        }));
    }
    let total_links = follow_up_links.len();

    let chars_total = full_markdown.chars().count();
    let explicit_paging = offset > 0 || max_chunks != 1;
    let query_text = query.as_deref().map(str::trim).filter(|v| !v.is_empty());
    let selection = query_text
        .filter(|_| !explicit_paging)
        .and_then(|value| select_relevant_sections(&full_markdown, value, md_chars as i64));
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
        let selected_markdown = truncate_text(&selected.markdown, md_chars, md_tokens, &model)
            .unwrap_or(selected.markdown);
        (
            selected_markdown,
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
        let slice = slice_markdown(
            &full_markdown,
            offset,
            max_chunks,
            md_chars as i64,
            md_tokens,
            &model,
        )
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
    let tokens = count_tokens(&markdown, &model).unwrap_or(0);

    let mut payload = json!({
        "url": url,
        "markdown": markdown,
        "markdown_tokens": tokens,
        "follow_up_links": follow_up_links,
        "delivered_links": total_links,
        "total_links": total_links,
        "truncated": markdown_truncated || total_links as i64 >= candidate_cap,
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
    });
    if offset > 0 && offset >= full_markdown.chars().count() as i64 && !full_markdown.is_empty() {
        payload["warning"] = json!(format!(
            "offset {offset} is past the end of the content ({} chars); nothing new was returned",
            full_markdown.chars().count()
        ));
    }
    if let Some(map) = payload.as_object_mut() {
        map.insert("fetched_at".to_string(), json!(fetched_at));
        map.insert("http_status".to_string(), http_status);
        map.insert("final_url".to_string(), final_url);
        map.insert("content_type".to_string(), content_type);
        map.insert(
            "content_hash".to_string(),
            json!(sha256_hex(&full_markdown)),
        );
        map.insert("guard".to_string(), Value::Null);
    }
    if apply_guard(&mut payload, parsed, &url, "page_markdown") {
        let error = json!({
            "error": "content withheld by prompt-injection guard",
            "url": url,
            "guard": payload.get("guard").cloned().unwrap_or(Value::Null),
        });
        pyprintln!("{}", py_dumps_pretty(&error));
        return ExitCode::SUCCESS;
    }
    fit_inspection(&mut payload, max_chars, max_tokens, &model);
    pyprintln!("{}", py_dumps_utf8(&payload));
    ExitCode::SUCCESS
}
