//! Standalone stdio MCP server for the pure Rust app.
//!
//! The CLI front door is unchanged: this is a separate binary target so the
//! frozen 12-tool schema and JSON-RPC transport can be exercised without
//! altering `gossamer --help`.

use std::io::{self, BufRead, Write};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use gossamer::agent::url_error;
use gossamer::document::{prepare_url, structured_html_payload};
use gossamer::fetch::fetch_html_full_single;
use gossamer::mcp_dispatch::{check_sources_plan, extract_validation, tool_arguments};
use gossamer::mcp_transport::{
    error_response, initialize_result, list_resources_result, list_tools_result, read_resource,
    response, tool_call_result,
};
use gossamer::metaextract::meta_extract_all;
use gossamer::miscutils::{classify_link, sha256_hex};
use gossamer::openaccess::normalize_doi;
use gossamer::search::{duckduckgo_results, duckduckgo_search_url};
use gossamer::service::fetch::{compact_metadata, slice_markdown};
use gossamer::ssrf::check_url;
use gossamer::tables::extract_tables;
use gossamer::taxonomy::CATEGORIES_JSON;
use gossamer::tokens::count_tokens;
use gossamer::urls::normalize_url;
use serde_json::{Value, json};

const KNOWN_TOOLS: [&str; 12] = [
    "web_search",
    "inspect_html_page",
    "batch_inspect_pages",
    "download_file",
    "locate_pdf",
    "extract_document",
    "discover_resources",
    "crawl",
    "manage_cache",
    "research_by_category",
    "export_citations",
    "check_sources",
];

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
}

fn string_arg(arguments: &Value, key: &str) -> String {
    arguments
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn ssrf_error(raw: &str, url: &str) -> (String, bool) {
    match check_url(url, false, None) {
        Ok(()) => (String::new(), false),
        Err(error) => {
            let message = error
                .message()
                .strip_prefix("SsrfBlockedError: ")
                .unwrap_or(error.message());
            (pretty(&url_error(raw, message, "SsrfBlockedError")), true)
        }
    }
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

fn inspect_live(
    raw: &str,
    structured: bool,
    query: Option<String>,
    offset: i64,
    max_chunks: i64,
) -> (String, bool) {
    let url = match prepare_url(raw, None) {
        Ok(url) => url,
        Err(error) => return (pretty(&error.to_record()), true),
    };
    let (text, failed) = ssrf_error(raw, &url);
    if failed {
        return (text, failed);
    }
    let Ok((html, full_markdown, links, _removed, meta)) =
        fetch_html_full_single(&url, 8000, 50_000_000)
    else {
        return (
            pretty(&json!({"error": format!("HTML inspection failed for {url}")})),
            true,
        );
    };
    let (http_status, final_url, content_type) = meta;
    let raw_metadata: Value =
        serde_json::from_str(&meta_extract_all(&html, Some(&final_url))).unwrap_or(Value::Null);
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
        return (
            serde_json::to_string(&payload).unwrap_or_else(|_| pretty(&payload)),
            false,
        );
    }
    let metadata = compact_metadata(&raw_metadata);
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
    let slice = slice_markdown(&full_markdown, offset, max_chunks, 8000, 0, "gpt-4o")
        .unwrap_or_else(|_| json!([full_markdown.clone(), full_markdown.chars().count(), false]));
    let markdown = slice
        .get(0)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let next_offset = slice.get(1).and_then(Value::as_i64);
    let has_more = slice.get(2).and_then(Value::as_bool).unwrap_or(false);
    let tokens = count_tokens(&markdown, "gpt-4o").unwrap_or(0);
    let mut payload = json!({
        "url": url,
        "markdown": markdown,
        "markdown_tokens": tokens,
        "follow_up_links": follow_up_links,
        "delivered_links": total_links,
        "total_links": total_links,
        "truncated": false,
        "fetch_method": "static",
        "cache_hit": false,
        "revalidated": false,
        "metadata": metadata,
        "query": query,
        "sections_available": 0,
        "sections_selected": 0,
        "section_anchors": [],
        "offset": offset,
        "next_offset": next_offset,
        "has_more": has_more,
        "chars_total": full_markdown.chars().count(),
        "fetched_at": timestamp(),
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
    (
        serde_json::to_string(&payload).unwrap_or_else(|_| pretty(&payload)),
        false,
    )
}

fn value_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Number(number) => number.to_string(),
        Value::Bool(value) => value.to_string(),
        other => other.to_string(),
    }
}

fn cli_output(arguments: &[String]) -> (String, bool) {
    let executable = std::env::current_exe().unwrap_or_default();
    let directory = executable.parent().unwrap_or(std::path::Path::new("."));
    let name = if cfg!(windows) {
        "gossamer.exe"
    } else {
        "gossamer"
    };
    let output = Command::new(directory.join(name)).args(arguments).output();
    match output {
        Ok(output) => {
            let mut text = String::from_utf8_lossy(&output.stdout).to_string();
            if text.trim().is_empty() {
                text = String::from_utf8_lossy(&output.stderr).to_string();
            }
            (
                text.trim_end_matches(['\n', '\r']).to_string(),
                !output.status.success(),
            )
        }
        Err(error) => (format!("CLI body unavailable: {error}"), true),
    }
}

fn cli_json_output(arguments: &[String], compact: bool) -> (String, bool) {
    let (text, failed) = cli_output(arguments);
    match serde_json::from_str::<Value>(&text) {
        Ok(value) => {
            let formatted = if compact {
                serde_json::to_string(&value)
            } else {
                serde_json::to_string_pretty(&value)
            };
            (formatted.unwrap_or(text), failed)
        }
        Err(_) => (text, failed),
    }
}

fn string_values(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items.iter().map(value_text).collect(),
        Some(Value::String(text)) => vec![text.clone()],
        _ => Vec::new(),
    }
}

fn flag(arguments: &Value, key: &str, flag: &str, out: &mut Vec<String>) {
    if arguments.get(key).and_then(Value::as_bool).unwrap_or(false) {
        out.push(flag.to_string());
    }
}

fn option(arguments: &Value, key: &str, flag: &str, out: &mut Vec<String>) {
    if let Some(value) = arguments.get(key)
        && !value.is_null()
    {
        out.push(flag.to_string());
        out.push(value_text(value));
    }
}

fn dispatch_tool(name: &str, arguments: &Value) -> (String, bool) {
    let merged = tool_arguments(name, arguments).unwrap_or_else(|| arguments.clone());
    match name {
        "research_by_category" => {
            let query = string_arg(&merged, "query");
            if query.trim().is_empty() {
                let taxonomy: Value =
                    serde_json::from_str(CATEGORIES_JSON).unwrap_or_else(|_| json!([]));
                return (pretty(&taxonomy), false);
            }
            let mut args = vec!["research".to_string(), query];
            option(&merged, "category", "--category", &mut args);
            option(&merged, "max_results", "--max-results", &mut args);
            cli_json_output(&args, false)
        }
        "web_search" => {
            let query = string_arg(&merged, "query");
            if query.trim().is_empty() {
                return (
                    pretty(&json!({"error": "research: topic is required"})),
                    true,
                );
            }
            let url = duckduckgo_search_url(&query);
            let results = fetch_results(
                &url,
                string_arg(&merged, "max_results")
                    .parse::<usize>()
                    .unwrap_or(5),
            );
            (pretty(&Value::Array(results)), false)
        }
        "inspect_html_page" => {
            let raw = string_arg(&merged, "url");
            let structured = merged
                .get("structured")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let query = merged
                .get("query")
                .and_then(Value::as_str)
                .map(str::to_string);
            let offset = merged.get("offset").and_then(Value::as_i64).unwrap_or(0);
            let max_chunks = merged
                .get("max_chunks")
                .and_then(Value::as_i64)
                .unwrap_or(1);
            inspect_live(&raw, structured, query, offset, max_chunks)
        }
        "batch_inspect_pages" => {
            let urls = merged.get("urls").cloned().unwrap_or(Value::Null);
            let items = match urls {
                Value::Array(items) => items,
                _ => Vec::new(),
            };
            if items.is_empty() {
                return ("[]".to_string(), false);
            }
            let mut args = vec!["batch".to_string()];
            args.extend(items.iter().map(value_text));
            cli_json_output(&args, false)
        }
        "download_file" => {
            let source = string_arg(&merged, "source");
            let output_path = string_arg(&merged, "output_path");
            let url = match normalize_url(Some(&source), None, None) {
                Ok(url) => url,
                Err(error) => {
                    let message = error
                        .message()
                        .strip_prefix("ValueError: ")
                        .unwrap_or(error.message());
                    return (
                        pretty(&json!({
                            "source": source,
                            "status": "error",
                            "error": {"code": "invalid_url", "message": message},
                        })),
                        true,
                    );
                }
            };
            let (text, failed) = ssrf_error(&source, &url);
            if failed {
                return (text, failed);
            }
            let mut args = vec![
                "download".to_string(),
                source,
                "-o".to_string(),
                output_path,
            ];
            option(&merged, "min_bytes", "--min-bytes", &mut args);
            option(&merged, "max_bytes", "--max-bytes", &mut args);
            flag(&merged, "overwrite", "--overwrite", &mut args);
            flag(&merged, "resume", "--resume", &mut args);
            if let Some(mirrors) = merged.get("mirrors") {
                let values = string_values(Some(mirrors));
                if !values.is_empty() {
                    args.push("--try-mirrors".to_string());
                    args.extend(values);
                }
            }
            cli_json_output(&args, false)
        }
        "locate_pdf" => {
            let doi = string_arg(&merged, "doi");
            match normalize_doi(&doi) {
                Ok(_normalized) => cli_json_output(&["locate-pdf".to_string(), doi], false),
                Err(error) => (
                    pretty(&json!({
                        "doi": doi,
                        "status": "error",
                        "error": {
                            "code": "invalid_doi",
                            "message": error.strip_prefix("ValueError: ").unwrap_or(&error),
                        },
                        "candidates": [],
                    })),
                    true,
                ),
            }
        }
        "extract_document" => {
            let source = string_arg(&merged, "source");
            let tables_as = string_arg(&merged, "tables_as");
            let include_images = merged
                .get("include_images")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let structured = merged
                .get("structured")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let store = merged
                .get("store")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            if let Some(error) = extract_validation(&tables_as, include_images, structured, store) {
                return (pretty(&error), true);
            }
            let mut args = vec!["extract".to_string(), source];
            option(&merged, "pages", "--pages", &mut args);
            option(&merged, "store_dir", "--store-dir", &mut args);
            option(&merged, "tables_as", "--tables-as", &mut args);
            flag(&merged, "structured", "--structured", &mut args);
            flag(&merged, "store", "--store", &mut args);
            flag(&merged, "include_images", "--include-images", &mut args);
            cli_json_output(&args, true)
        }
        "discover_resources" => {
            let raw = string_arg(&merged, "url");
            let url = match prepare_url(&raw, None) {
                Ok(url) => url,
                Err(error) => return (pretty(&error.to_record()), true),
            };
            let (text, failed) = ssrf_error(&raw, &url);
            if failed {
                return (text, failed);
            }
            cli_json_output(&["discover".to_string(), raw], false)
        }
        "crawl" => {
            let raw = string_arg(&merged, "root_url");
            match normalize_url(Some(&raw), None, None) {
                Ok(_url) => {
                    let mut args = vec!["crawl".to_string(), raw];
                    option(&merged, "query", "--query", &mut args);
                    option(&merged, "max_depth", "--max-depth", &mut args);
                    option(&merged, "max_pages", "--max-pages", &mut args);
                    cli_json_output(&args, false)
                }
                Err(error) => {
                    let message = error
                        .message()
                        .strip_prefix("ValueError: ")
                        .unwrap_or(error.message());
                    (pretty(&json!({"error": format!("crawl: {message}")})), true)
                }
            }
        }
        "manage_cache" => {
            let action = string_arg(&merged, "action");
            let action = if matches!(action.as_str(), "prune" | "clear" | "reset") {
                action
            } else {
                "prune".to_string()
            };
            let args = vec!["cache".to_string(), "--action".to_string(), action];
            cli_json_output(&args, false)
        }
        "export_citations" => {
            let style = string_arg(&merged, "style");
            if !matches!(style.as_str(), "bibtex" | "csl-json" | "apa" | "mla") {
                return (
                    pretty(&json!({
                        "error": format!(
                            "unknown citation style {}; expected one of ('bibtex', 'csl-json', 'apa', 'mla')",
                            gossamer::compat::py_repr(&style)
                        ),
                    })),
                    true,
                );
            }
            let mut args = vec!["cite".to_string(), "--style".to_string(), style];
            let results = merged.get("results").cloned().unwrap_or(Value::Null);
            if let Value::Array(items) = results {
                for item in items {
                    match item {
                        Value::String(text) => args.push(text),
                        other => args.push(serde_json::to_string(&other).unwrap_or_default()),
                    }
                }
            }
            cli_output(&args)
        }
        "check_sources" => {
            let mode = merged.get("mode").and_then(Value::as_str);
            let urls = merged.get("urls");
            let plan = check_sources_plan(mode, urls, "list");
            if plan.get("error").is_some() {
                return (pretty(&plan), false);
            }
            let mut args = vec!["check".to_string()];
            if let Some(mode) = mode {
                args.push("--mode".to_string());
                args.push(mode.to_string());
            }
            args.extend(string_values(urls));
            cli_json_output(&args, false)
        }
        other => (format!("Unknown tool: {other}"), true),
    }
}

fn fetch_results(url: &str, max_results: usize) -> Vec<Value> {
    let body = match fetch_text(url) {
        Ok(body) => body,
        Err(_) => return Vec::new(),
    };
    duckduckgo_results(&body, max_results)
}

fn fetch_text(url: &str) -> Result<String, String> {
    gossamer::discovery::fetch_text(url, 20.0).map(|(body, _)| body)
}

fn handle_request(request: &Value) -> Option<Value> {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let method = request.get("method").and_then(Value::as_str).unwrap_or("");
    match method {
        "initialize" => Some(response(&id, initialize_result())),
        "ping" => Some(response(&id, json!({}))),
        "tools/list" => Some(response(&id, list_tools_result())),
        "resources/list" => Some(response(&id, list_resources_result())),
        "resources/read" => {
            let uri = request
                .get("params")
                .and_then(|value| value.get("uri"))
                .and_then(Value::as_str)
                .unwrap_or("");
            match read_resource(uri) {
                Some(result) => Some(response(&id, result)),
                None => Some(error_response(&id, -32602, "unknown resource")),
            }
        }
        "tools/call" => {
            let params = request.get("params").unwrap_or(&Value::Null);
            let name = params.get("name").and_then(Value::as_str).unwrap_or("");
            let arguments = params
                .get("arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));
            let (text, _dispatch_error) = dispatch_tool(name, &arguments);
            let is_error = !KNOWN_TOOLS.contains(&name);
            Some(response(&id, tool_call_result(&text, is_error)))
        }
        method if method.starts_with("notifications/") => None,
        _ => Some(error_response(&id, -32601, "Method not found")),
    }
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for line in stdin.lock().lines() {
        let Ok(line) = line else {
            continue;
        };
        if line.trim().is_empty() {
            continue;
        }
        let Ok(request) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if let Some(reply) = handle_request(&request) {
            let _ = writeln!(out, "{}", serde_json::to_string(&reply).unwrap_or_default());
            let _ = out.flush();
        }
    }
}
