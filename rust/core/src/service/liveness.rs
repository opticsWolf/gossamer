//! Deterministic liveness verdicts for `gossamer::liveness`.
//!
//! Network probing, SSRF validation and throttling stay in the caller; this
//! module only maps a finished probe outcome into the public status dict.

use serde_json::Value;

/// Build the status dict for a completed liveness probe.
pub fn liveness_result(url: &str, http_status: Option<i64>, error: Option<&str>) -> Value {
    if let Some(error) = error {
        return serde_json::json!({
            "url": url,
            "status": "error",
            "alive": false,
            "http_status": http_status,
            "error": error,
        });
    }
    let alive = http_status.is_some_and(|status| (200..400).contains(&status));
    serde_json::json!({
        "url": url,
        "status": if alive { "ok" } else { "unreachable" },
        "alive": alive,
        "http_status": http_status,
    })
}



/// Perform a lightweight HEAD/GET reachability probe.
pub fn probe_liveness(url: &str, timeout: f64, method: &str) -> Value {
    fn error_name(error: &reqwest::Error) -> &'static str {
        if error.is_timeout() { "TimeoutException" }
        else if error.is_connect() { "ConnectError" }
        else if error.is_request() { "RequestError" }
        else { "HTTPError" }
    }
    let outcome = (|| -> Result<u16, String> {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| format!("RuntimeError: {error}"))?;
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::limited(10))
            .timeout(std::time::Duration::from_secs_f64(timeout.max(0.0)))
            .build()
            .map_err(|error| format!("RuntimeError: {error}"))?;
        runtime.block_on(async {
            let request = if method == "get" { client.get(url) } else { client.head(url) };
            let response = request.send().await.map_err(|error| {
                format!("{}: {}", error_name(&error), error)
            })?;
            Ok(response.status().as_u16())
        })
    })();
    match outcome {
        Ok(status) => serde_json::json!({"http_status": status, "error": Value::Null}),
        Err(error) => serde_json::json!({"http_status": Value::Null, "error": error}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn verdicts() {
        assert_eq!(liveness_result("https://x", Some(204), None)["status"], "ok");
        assert_eq!(liveness_result("https://x", Some(500), None)["status"], "unreachable");
        assert_eq!(liveness_result("https://x", None, Some("boom"))["status"], "error");
    }
}
