#![forbid(unsafe_code)]
use gossamer::{jsonfmt, service::search};
use serde_json::json;
pub fn service_json_fallback_decision(
    rid: String,
    probe: Option<String>,
    is_tuple: bool,
) -> Result<Vec<Option<String>>, String> {
    Ok(jsonfmt::json_fallback(&rid, probe.as_deref(), is_tuple))
}
pub fn service_provider_indices(
    provider_name: Option<String>,
    names: Vec<String>,
) -> Result<Vec<usize>, String> {
    Ok(search::provider_indices(
        provider_name.as_deref(),
        &json!(names),
    ))
}
