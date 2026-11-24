#![forbid(unsafe_code)]
use gossamer::stats::fetch_stats_dict;
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
