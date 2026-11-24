#![forbid(unsafe_code)]
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
