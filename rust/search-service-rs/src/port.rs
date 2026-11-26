#![forbid(unsafe_code)]
use gossamer::service::search as k;
use serde_json::Value;

pub fn service_dedup_results(results: Value) -> Result<Vec<Value>, String> {
    Ok(k::dedup_results(&results))
}
pub fn service_fallback_note(
    provider_name: Option<Value>,
    registered: Value,
) -> Result<Value, String> {
    let name = provider_name.as_ref().and_then(Value::as_str);
    Ok(k::fallback_note(name, &registered).unwrap_or(Value::Null))
}
pub fn service_resolve_order(
    provider_name: Option<Value>,
    registered: Value,
) -> Result<Vec<String>, String> {
    Ok(k::resolve_order(
        provider_name.as_ref().and_then(Value::as_str),
        &registered,
    ))
}

pub fn service_failover_results(
    provider_results: Value,
    max_results: usize,
) -> Result<Value, String> {
    let out = k::failover_results(&provider_results, max_results);
    if let Some(message) = out.get("error").and_then(serde_json::Value::as_str) {
        Err(message.to_string())
    } else {
        Ok(out)
    }
}

pub fn service_merged_results(
    provider_results: Value,
    max_results: usize,
) -> Result<Value, String> {
    let out = k::merged_results(&provider_results, max_results);
    if let Some(message) = out.get("error").and_then(serde_json::Value::as_str) {
        Err(message.to_string())
    } else {
        Ok(out)
    }
}
