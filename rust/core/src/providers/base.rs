//! Deterministic retry, quota and politeness decisions.

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
    let gap = delay + jitter_value.clamp(0.0, jitter.max(0.0));
    (gap - (now - last_search)).max(0.0)
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
    }
    #[test]
    fn politeness_and_quota() {
        assert!((politeness_wait(10.0, 10.5, 1.0, 0.2, 0.1) - 0.6).abs() < 1e-9);
        assert_eq!(quota_message("google", 100, 100, "day"), "'google' quota exhausted (100/100 per day); retry after the window resets");
    }
}
