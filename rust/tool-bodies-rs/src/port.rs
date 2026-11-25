#![forbid(unsafe_code)]
use gossamer::agent as a;
use gossamer::mcp_dispatch as k;
use serde_json::Value;
pub fn service_inspect_route(
    structured: bool,
    store_dir: Option<String>,
) -> Result<String, String> {
    Ok(k::inspect_route(structured, store_dir.as_deref()).to_string())
}
pub fn service_tables_as_error() -> Result<Value, String> {
    Ok(k::tables_as_error())
}
pub fn service_tool_arguments(name: String, arguments: Value) -> Result<Option<Value>, String> {
    Ok(k::tool_arguments(&name, &arguments))
}

pub fn service_url_error(raw: String, reason: String, error_type: String) -> Result<Value, String> {
    Ok(a::url_error(&raw, &reason, &error_type))
}

pub fn service_crawl_host_key(url: String) -> Result<String, String> {
    Ok(a::crawl_host_key(&url))
}
