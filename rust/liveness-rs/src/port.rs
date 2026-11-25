#![forbid(unsafe_code)]
use gossamer::service::liveness::{liveness_result, probe_liveness};
use serde_json::Value;
pub fn service_liveness_result(
    url: String,
    http_status: Option<i64>,
    error: Option<String>,
) -> Result<Value, String> {
    Ok(liveness_result(&url, http_status, error.as_deref()))
}

pub fn service_probe_liveness(url: String, timeout: f64, method: String) -> Result<Value, String> {
    Ok(probe_liveness(&url, timeout, &method))
}
