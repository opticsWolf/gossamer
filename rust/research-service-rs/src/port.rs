#![forbid(unsafe_code)]
use gossamer::providers::base as policy;
use gossamer::service::research as k;
use serde_json::Value;
pub fn service_normalize_research(
    records: Value,
    max_results: Option<i64>,
) -> Result<Vec<Value>, String> {
    Ok(k::normalize_records(&records, max_results.unwrap_or(0)))
}
pub fn service_research_order(
    providers: Value,
    preferred: Option<Value>,
) -> Result<Vec<String>, String> {
    Ok(k::provider_order(
        &providers,
        preferred.as_ref().and_then(Value::as_str),
    ))
}

pub fn service_rate_state_from_headers(
    headers: Value,
    default_rps: Option<f64>,
) -> Result<Value, String> {
    Ok(policy::rate_state_from_headers(&headers, default_rps))
}
