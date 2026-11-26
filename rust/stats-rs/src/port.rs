#![forbid(unsafe_code)]
use gossamer::stats::{fetch_stats_dict, fetch_stats_record};
use serde_json::Value;
pub fn service_fetch_stats_dict(
    fetches: i64,
    errors: i64,
    bytes_downloaded: i64,
    latencies: Vec<f64>,
    by_domain: Value,
    by_error: Value,
) -> Result<Value, String> {
    Ok(fetch_stats_dict(
        fetches,
        errors,
        bytes_downloaded,
        &latencies,
        &by_domain,
        &by_error,
    ))
}

pub fn service_fetch_stats_record(
    state: Value,
    domain: String,
    latency: f64,
    nbytes: Option<i64>,
    error_class: Option<String>,
    latency_window: usize,
    max_keys: usize,
) -> Result<Value, String> {
    Ok(fetch_stats_record(
        &state,
        &domain,
        latency,
        nbytes,
        error_class.as_deref(),
        latency_window,
        max_keys,
    ))
}
