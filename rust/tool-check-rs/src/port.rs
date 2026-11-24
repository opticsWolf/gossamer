#![forbid(unsafe_code)]
use gossamer::mcp_dispatch::check_sources_plan;
use serde_json::Value;
pub fn service_check_sources_plan(
    mode: Option<String>,
    urls: Option<Value>,
    type_name: Option<String>,
) -> Result<Value, String> {
    Ok(check_sources_plan(
        mode.as_deref(),
        urls.as_ref(),
        type_name.as_deref().unwrap_or(""),
    ))
}
