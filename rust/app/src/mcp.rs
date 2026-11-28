//! Standalone stdio MCP server for the pure Rust app.
//!
//! The CLI front door is unchanged: this is a separate binary target so the
//! frozen 12-tool schema and JSON-RPC transport can be exercised without
//! altering `gossamer --help`.

use std::io::{self, BufRead, Write};
use std::process::Command;

#[cfg(windows)]
const MCP_NEWLINE: &str = "\r\n";
#[cfg(not(windows))]
const MCP_NEWLINE: &str = "\n";

use gossamer::agent::url_error;
use gossamer::document::prepare_url;
use gossamer::mcp_dispatch::{check_sources_plan, extract_validation, tool_arguments};
use gossamer::mcp_transport::{
    error_response, initialize_result, list_resources_result, list_tools_result, read_resource,
    response, tool_call_result,
};
use gossamer::openaccess::normalize_doi;
use gossamer::ssrf::check_url;
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
    match check_url(url, gossamer::ssrf::env_allow_private(), None) {
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
                text.replace("\r\n", "\n")
                    .trim_end_matches(['\n', '\r'])
                    .to_string(),
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
            let mut args = vec!["research".to_string(), query];
            option(&merged, "category", "--category", &mut args);
            option(&merged, "provider", "--provider", &mut args);
            option(&merged, "max_results", "--max-results", &mut args);
            option(&merged, "filter", "--filter", &mut args);
            option(&merged, "select", "--select", &mut args);
            option(&merged, "title", "--title", &mut args);
            option(&merged, "author", "--author", &mut args);
            let providers = string_values(merged.get("providers"));
            if !providers.is_empty() {
                args.push("--providers".to_string());
                args.extend(providers);
            }
            cli_output(&args)
        }
        "web_search" => {
            let query = string_arg(&merged, "query");
            let mut args = vec!["search".to_string(), query];
            flag(&merged, "search_only", "--search-only", &mut args);
            option(&merged, "max_results", "--max-results", &mut args);
            option(&merged, "depth", "--depth", &mut args);
            option(&merged, "max_tokens", "--max-tokens", &mut args);
            option(&merged, "provider", "--provider", &mut args);
            option(&merged, "use_smart", "--use-smart", &mut args);
            cli_output(&args)
        }
        "inspect_html_page" => {
            let raw = string_arg(&merged, "url");
            let mut args = vec!["inspect".to_string(), raw];
            option(&merged, "use_smart", "--use-smart", &mut args);
            option(&merged, "query", "--query", &mut args);
            option(&merged, "offset", "--offset", &mut args);
            option(&merged, "max_chunks", "--max-chunks", &mut args);
            flag(&merged, "structured", "--structured", &mut args);
            cli_output(&args)
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
            option(&merged, "use_smart", "--use-smart", &mut args);
            cli_output(&args)
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
            option(&merged, "expected_format", "--expect-format", &mut args);
            flag(&merged, "overwrite", "--overwrite", &mut args);
            flag(&merged, "resume", "--resume", &mut args);
            let mirrors = string_values(merged.get("fallback_urls"));
            if !mirrors.is_empty() {
                args.push("--try-mirrors".to_string());
                args.extend(mirrors);
            }
            cli_output(&args)
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
            if merged.get("prefer_jats").and_then(Value::as_bool) == Some(false) {
                args.push("--no-prefer-jats".to_string());
            }
            cli_output(&args)
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
            cli_output(&["discover".to_string(), raw])
        }
        "crawl" => {
            let raw = string_arg(&merged, "root_url");
            match normalize_url(Some(&raw), None, None) {
                Ok(_url) => {
                    let mut args = vec!["crawl".to_string(), raw];
                    option(&merged, "query", "--query", &mut args);
                    option(&merged, "max_depth", "--max-depth", &mut args);
                    option(&merged, "max_pages", "--max-pages", &mut args);
                    flag(&merged, "same_host", "--same-host", &mut args);
                    option(&merged, "min_score", "--min-score", &mut args);
                    flag(&merged, "excerpts", "--excerpts", &mut args);
                    flag(&merged, "search_prior", "--search-prior", &mut args);
                    let seeds = string_values(merged.get("seed_urls"));
                    if !seeds.is_empty() {
                        args.push("--seed-urls".to_string());
                        args.extend(seeds);
                    }
                    option(&merged, "use_smart", "--use-smart", &mut args);
                    cli_output(&args)
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
            flag(&merged, "enrich", "--enrich", &mut args);
            if merged.get("dedupe").and_then(Value::as_bool) == Some(false) {
                args.push("--no-dedupe".to_string());
            }
            flag(&merged, "from_pdf", "--from-pdf", &mut args);
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
            let _ = write!(
                out,
                "{}{MCP_NEWLINE}",
                serde_json::to_string(&reply).unwrap_or_default()
            );
            let _ = out.flush();
        }
    }
}
