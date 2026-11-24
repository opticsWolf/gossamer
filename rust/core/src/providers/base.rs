//! Deterministic retry, quota and politeness decisions.

use serde_json::Value;

const MAX_RETRY_AFTER_SECONDS: f64 = 60.0;

/// True for transient HTTP statuses handled by the retry decorator.
pub fn retryable_status(status: i64) -> bool {
    (408..=408).contains(&status) || (425..=425).contains(&status) || status == 429 || (500..600).contains(&status)
}

/// Seconds to wait before another attempt, or `None` when retrying must stop.
pub fn retry_wait(
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    _backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> Option<f64> {
    if attempt >= max_attempts - 1 { return None; }
    if let Some(value) = retry_after
        && value > MAX_RETRY_AFTER_SECONDS { return None; }
    let base = delay.max(retry_after.unwrap_or(0.0));
    let jitter = jitter_value.clamp(0.0, (0.25_f64).min(base * 0.1));
    Some((base + jitter).min(MAX_RETRY_AFTER_SECONDS))
}

/// Exact message for a local quota gate.
pub fn quota_message(name: &str, used: i64, quota: i64, window: &str) -> String {
    format!("{} quota exhausted ({}/{} per {}); retry after the window resets", crate::compat::py_repr(name), used, quota, window)
}

/// Minimum remaining politeness delay for one call.
pub fn politeness_wait(last_search: f64, now: f64, delay: f64, jitter: f64, jitter_value: f64) -> f64 {
    if delay <= 0.0 { return 0.0; }
    let _ = jitter;
    let gap = delay + jitter_value.max(0.0);
    (gap - (now - last_search)).max(0.0)
}

pub fn normalize_rate_limit(delay: Option<&Value>, fetch_delay: Option<f64>, jitter: Option<f64>) -> Value {
    let mut search_interval = 1.0_f64;
    let mut limit_jitter = 0.0_f64;
    let mut quota = Value::Null;
    let mut quota_window = "day".to_string();
    if let Some(value) = delay {
        if let Some(number) = value.as_f64() {
            search_interval = number;
        } else if let Some(object) = value.as_object() {
            if let Some(number) = object.get("search_interval").and_then(Value::as_f64) {
                search_interval = number;
            }
            if let Some(number) = object.get("jitter").and_then(Value::as_f64) {
                limit_jitter = number;
            }
            if let Some(value) = object.get("quota") {
                quota = value.clone();
            }
            if let Some(window) = object.get("quota_window").and_then(Value::as_str) {
                quota_window = window.to_string();
            }
        }
    }
    if let Some(number) = jitter {
        limit_jitter = number;
    }
    let mut fetch_interval = 0.5_f64;
    if delay.is_some_and(|value| value.is_object())
        && let Some(object) = delay.and_then(Value::as_object)
        && let Some(number) = object.get("fetch_interval").and_then(Value::as_f64)
    {
        fetch_interval = number;
    }
    if let Some(number) = fetch_delay {
        fetch_interval = number;
    }
    serde_json::json!({
        "search_interval": search_interval,
        "fetch_interval": fetch_interval,
        "jitter": limit_jitter,
        "quota": quota,
        "quota_window": quota_window,
    })
}

/// Decide retry/raise behavior for one failed provider call.
#[expect(clippy::too_many_arguments, reason = "mirrors the Python retry decorator inputs exactly")]
pub fn retry_decision(
    kind: &str,
    status: Option<i64>,
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> Value {
    if kind == "quota" {
        return serde_json::json!({"action": "raise", "final": false, "cap": false});
    }
    let retryable = kind == "transport" || status.is_some_and(retryable_status);
    if !retryable {
        return serde_json::json!({"action": "raise", "final": false, "cap": false});
    }
    if retry_after.is_some_and(|value| value > MAX_RETRY_AFTER_SECONDS) {
        return serde_json::json!({"action": "raise", "final": false, "cap": true});
    }
    if attempt >= max_attempts - 1 {
        return serde_json::json!({"action": "raise", "final": true, "cap": false});
    }
    let base = delay.max(retry_after.unwrap_or(0.0));
    let jitter = jitter_value.clamp(0.0, MAX_RETRY_AFTER_SECONDS.min(base * 0.1));
    let wait = (base + jitter).min(MAX_RETRY_AFTER_SECONDS);
    let _ = backoff;
    serde_json::json!({"action": "retry", "wait": wait})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn retry_rules() {
        assert!(retryable_status(429));
        assert!(!retryable_status(404));
        assert_eq!(retry_wait(0, 3, 1.0, 2.0, None, 0.05), Some(1.05));
        assert_eq!(retry_wait(2, 3, 1.0, 2.0, None, 0.05), None);
        assert_eq!(retry_wait(0, 3, 1.0, 2.0, Some(90.0), 0.0), None);
        assert_eq!(retry_decision("status", Some(429), 0, 3, 1.0, 2.0, None, 0.05)["action"], "retry");
    }
    #[test]
    fn politeness_and_quota() {
        assert!((politeness_wait(10.0, 10.5, 1.0, 0.2, 0.1) - 0.6).abs() < 1e-9);
        assert_eq!(quota_message("google", 100, 100, "day"), "'google' quota exhausted (100/100 per day); retry after the window resets");
    }
}
