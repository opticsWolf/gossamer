//! Deterministic fetch-statistics payload assembly.

use serde_json::{Map, Value, json};

/// Python-compatible one-decimal rounding (banker's rounding).
fn round1(value: f64) -> f64 {
    match format!("{value:.1}").parse::<f64>() {
        Ok(rounded) => rounded,
        Err(_) => value,
    }
}

/// Linear-interpolated percentile matching Python's `numpy`-free original.
fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    if sorted.len() == 1 {
        return sorted[0];
    }
    let k = (sorted.len() - 1) as f64 * p;
    let f = k.floor();
    let c = k.ceil();
    if f == c {
        sorted[k as usize]
    } else {
        sorted[f as usize] * (c - k) + sorted[c as usize] * (k - f)
    }
}

/// Build the exact `FetchStats.to_dict()` payload.
pub fn fetch_stats_dict(
    fetches: i64,
    errors: i64,
    bytes_downloaded: i64,
    latencies: &[f64],
    by_domain: &Value,
    by_error: &Value,
) -> Value {
    let mut lats: Vec<f64> = latencies.to_vec();
    lats.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let latency = json!({
        "p50": round1(percentile(&lats, 0.50) * 1000.0),
        "p95": round1(percentile(&lats, 0.95) * 1000.0),
        "p99": round1(percentile(&lats, 0.99) * 1000.0),
        "max": if lats.is_empty() { 0.0 } else { round1(lats[lats.len() - 1] * 1000.0) },
    });
    let mut domains: Vec<(String, i64)> = by_domain
        .as_object()
        .into_iter()
        .flat_map(|object| object.iter())
        .filter_map(|(key, value)| value.as_i64().map(|count| (key.clone(), count)))
        .collect();
    domains.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    let mut domain_map = Map::new();
    for (key, count) in domains {
        domain_map.insert(key, json!(count));
    }
    let mut error_map = Map::new();
    if let Some(object) = by_error.as_object() {
        for (key, value) in object {
            error_map.insert(key.clone(), value.clone());
        }
    }
    json!({
        "fetches": fetches,
        "errors": errors,
        "bytes_downloaded": bytes_downloaded,
        "latency_ms": latency,
        "requests_by_domain": Value::Object(domain_map),
        "errors_by_class": Value::Object(error_map),
    })
}

fn bump_key(bucket: &mut Map<String, Value>, key: &str, max_keys: usize) {
    if !bucket.contains_key(key)
        && bucket.len() >= max_keys
        && let Some(first) = bucket.keys().next().cloned()
    {
        bucket.remove(&first);
    }
    let count = bucket
        .get(key)
        .and_then(Value::as_i64)
        .unwrap_or(0)
        .saturating_add(1);
    bucket.insert(key.to_string(), json!(count));
}

fn state_i64(state: &Value, key: &str) -> i64 {
    state.get(key).and_then(Value::as_i64).unwrap_or(0)
}

/// Apply one successful or failed fetch to a serializable stats snapshot.
///
/// Python keeps the lock and mutable instance; Rust owns counter, bounded
/// latency-window, and FIFO key-eviction decisions.
pub fn fetch_stats_record(
    state: &Value,
    domain: &str,
    latency: f64,
    nbytes: Option<i64>,
    error_class: Option<&str>,
    latency_window: usize,
    max_keys: usize,
) -> Value {
    let mut latencies: Vec<f64> = state
        .get("latencies")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_f64)
        .collect();
    latencies.push(latency);
    let window = latency_window.max(1);
    while latencies.len() > window {
        latencies.remove(0);
    }
    let mut by_domain = state
        .get("by_domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let mut by_error = state
        .get("by_error")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    bump_key(&mut by_domain, domain, max_keys.max(1));
    let errors = state_i64(state, "errors") + i64::from(error_class.is_some());
    let bytes = state_i64(state, "bytes") + nbytes.unwrap_or(0);
    if let Some(class) = error_class {
        bump_key(&mut by_error, class, max_keys.max(1));
    }
    json!({
        "latencies": latencies,
        "bytes": bytes,
        "fetches": state_i64(state, "fetches") + 1,
        "errors": errors,
        "by_domain": by_domain,
        "by_error": by_error,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn stats_record_bounds_and_evicts() {
        let state = json!({
            "latencies": [0.1, 0.2],
            "bytes": 5,
            "fetches": 2,
            "errors": 0,
            "by_domain": {"a.test": 2, "b.test": 1},
            "by_error": {}
        });
        let out = fetch_stats_record(&state, "c.test", 0.3, Some(10), Some("ValueError"), 2, 2);
        assert_eq!(out["latencies"].as_array().map_or(0, Vec::len), 2);
        assert_eq!(out["bytes"], json!(15));
        assert_eq!(out["fetches"], json!(3));
        assert_eq!(out["errors"], json!(1));
        assert!(!out["by_domain"].get("a.test").is_some());
        assert_eq!(out["by_domain"]["b.test"], json!(1));
        assert_eq!(out["by_domain"]["c.test"], json!(1));
        assert_eq!(out["by_error"]["ValueError"], json!(1));
    }

    #[test]
    fn stats_shape_and_rounding() {
        let out = fetch_stats_dict(
            2,
            1,
            100,
            &[0.00125, 0.00225],
            &json!({"b.test": 1, "a.test": 2}),
            &json!({"ValueError": 1}),
        );
        assert_eq!(out["latency_ms"]["max"], json!(2.2));
        assert_eq!(out["requests_by_domain"]["a.test"], json!(2));
    }
}
