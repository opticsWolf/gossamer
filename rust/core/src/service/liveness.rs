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
