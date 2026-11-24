#![forbid(unsafe_code)]
use gossamer::service::liveness::liveness_result;
use serde_json::Value;
pub fn service_liveness_result(
    url: String,
    http_status: Option<i64>,
    error: Option<String>,
) -> Result<Value, String> {
    Ok(liveness_result(&url, http_status, error.as_deref()))
}
