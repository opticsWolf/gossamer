#![forbid(unsafe_code)]
use gossamer::providers::base as policy;
use gossamer::service::research as k;
use gossamer::xmlatom::xml_local_name;
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

pub fn service_local_name(tag: String) -> Result<String, String> {
    Ok(xml_local_name(&tag).to_string())
}

pub fn service_provider_factory_path(provider: String) -> Result<Option<String>, String> {
    Ok(k::provider_factory_path(&provider).map(str::to_string))
}

pub fn service_unknown_provider_message(provider: String) -> Result<String, String> {
    Ok(k::unknown_provider_message(&provider))
}
