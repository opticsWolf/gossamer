#![forbid(unsafe_code)]
use gossamer::{cli_router, documents, mcp_dispatch};
use serde_json::Value;
pub fn service_payload_skeleton(
    markdown: String,
    url: String,
    metadata: Value,
    max_links: i64,
) -> Result<Value, String> {
    Ok(documents::payload_from_markdown(
        &markdown, &url, &metadata, max_links,
    ))
}
pub fn service_tool_envelope(
    name: String,
    arguments: Value,
    result: Value,
) -> Result<Value, String> {
    Ok(mcp_dispatch::tool_envelope(&name, &arguments, result))
}
pub fn service_route(token: String) -> Result<Option<String>, String> {
    Ok(cli_router::route(&token).map(str::to_string))
}
