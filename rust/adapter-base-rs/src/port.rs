#![forbid(unsafe_code)]
use gossamer::providers::base as k;
use serde_json::Value;
pub fn service_retryable_status(status: i64) -> Result<bool, String> {
    Ok(k::retryable_status(status))
}
pub fn service_retry_wait(
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> Result<Option<f64>, String> {
    Ok(k::retry_wait(
        attempt,
        max_attempts,
        delay,
        backoff,
        retry_after,
        jitter_value,
    ))
}
pub fn service_quota_message(
    name: String,
    used: i64,
    quota: i64,
    window: String,
) -> Result<String, String> {
    Ok(k::quota_message(&name, used, quota, &window))
}
pub fn service_politeness_wait(
    last_search: f64,
    now: f64,
    delay: f64,
    jitter: f64,
    jitter_value: f64,
) -> Result<f64, String> {
    Ok(k::politeness_wait(
        last_search,
        now,
        delay,
        jitter,
        jitter_value,
    ))
}

pub fn service_retry_plan(
    kind: String,
    status: Option<i64>,
    attempt: i64,
    max_attempts: i64,
    delay: f64,
    backoff: f64,
    retry_after: Option<f64>,
    jitter_value: f64,
) -> Result<Value, String> {
    Ok(k::retry_decision(
        &kind,
        status,
        attempt,
        max_attempts,
        delay,
        backoff,
        retry_after,
        jitter_value,
    ))
}

pub fn service_quota_period(window: String, now_iso: String) -> Result<String, String> {
    Ok(k::quota_period(&window, &now_iso))
}
