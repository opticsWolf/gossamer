//! Standalone stdio MCP server for the pure Rust app.
//!
//! The CLI front door is unchanged: this is a separate binary target so the
//! frozen 12-tool schema and JSON-RPC transport can be exercised without
//! altering `gossamer --help`.

use std::io::{self, BufRead, Write};

use gossamer::agent::url_error;
use gossamer::document::prepare_url;
use gossamer::mcp_dispatch::{check_sources_plan, extract_validation, tool_arguments};
use gossamer::mcp_transport::{
    error_response, initialize_result, list_resources_result, list_tools_result, read_resource,
    response, tool_call_result,
};
use gossamer::openaccess::normalize_doi;
use gossamer::search::{duckduckgo_results, duckduckgo_search_url};
use gossamer::ssrf::check_url;
use gossamer::taxonomy::CATEGORIES_JSON;
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
            let url = duckduckgo_search_url(&query);
            let results = fetch_results(&url, 5);
            (pretty(&Value::Array(results)), false)
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
            let url = match prepare_url(&raw, None) {
                Ok(url) => url,
                Err(error) => return (pretty(&error.to_record()), true),
            };
            let (text, failed) = ssrf_error(&raw, &url);
            if failed {
                return (text, failed);
            }
            (
                pretty(
                    &json!({"url": url, "markdown": "", "error": "live fetch requires the Rust app inspect unit"}),
                ),
                true,
            )
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
            (
                pretty(
                    &json!({"error": "batch inspection is not available in the MCP transport slice"}),
                ),
                true,
            )
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
            (
                pretty(&json!({
                    "source": url,
                    "status": "error",
                    "error": {"code": "download_error", "message": format!("output_path: {output_path}")},
                })),
                true,
            )
        }
        "locate_pdf" => {
            let doi = string_arg(&merged, "doi");
            match normalize_doi(&doi) {
                Ok(normalized) => (
                    pretty(&json!({
                        "doi": normalized,
                        "provider": "openalex",
                        "sources": ["openalex"],
                        "status": "not_found",
                        "candidates": [],
                    })),
                    false,
                ),
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
            (
                pretty(&json!({
                    "source": source,
                    "content": "",
                    "content_tokens": 0,
                    "cache_hit": false,
                })),
                false,
            )
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
            (
                pretty(&json!({"url": url, "feeds": [], "sitemaps": []})),
                false,
            )
        }
        "crawl" => {
            let raw = string_arg(&merged, "root_url");
            match normalize_url(Some(&raw), None, None) {
                Ok(url) => (
                    pretty(&json!({"root": url, "pages": [], "count": 0})),
                    false,
                ),
                Err(error) => {
                    let message = error
                        .message()
                        .strip_prefix("ValueError: ")
                        .unwrap_or(error.message());
                    (pretty(&json!({"error": format!("crawl: {message}")})), true)
                }
            }
        }
        "manage_cache" => (
            pretty(&json!({"prune": {
                "removed_expired": 0,
                "removed_evicted": 0,
                "total_entries": 0,
                "total_bytes": 0,
                "max_disk_bytes": 0,
            }})),
            false,
        ),
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
            ("[]".to_string(), false)
        }
        "check_sources" => {
            let mode = merged.get("mode").and_then(Value::as_str);
            let urls = merged.get("urls");
            let plan = check_sources_plan(mode, urls, "list");
            (pretty(&plan), plan.get("error").is_some())
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
