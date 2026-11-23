//! Thin bindings over `gossamer::service::fetch` for the deterministic
//! FetchService kernels. Network I/O and toolbox state stay in Python.

#![forbid(unsafe_code)]

use gossamer::service::fetch as kernels;
use serde_json::{Map, Value, json};

/// Errors carry the Python exception name so generated bindings map them.
/// Recorded Python tuples arrive as `{"$tuple": [...]}`.
fn sequence(value: &Value) -> Option<&Vec<Value>> {
    value
        .get("$tuple")
        .and_then(Value::as_array)
        .or_else(|| value.as_array())
}

fn failure(error: kernels::FetchServiceError) -> String {
    format!("ValueError: {}", error.message())
}

/// Port of `_service_strategy_for`.
pub fn service_strategy_for(fetch_mode: String, use_smart: String) -> Result<String, String> {
    kernels::resolve_strategy(&fetch_mode, &use_smart)
        .map(str::to_string)
        .map_err(failure)
}

/// Port of `_service_strategy_accepts`.
pub fn service_strategy_accepts(
    strategy: String,
    method: Option<Value>,
    cached_strategy: Option<String>,
) -> Result<bool, String> {
    let method = method
        .as_ref()
        .and_then(|value| value.as_str())
        .or_else(|| method.as_ref().and_then(|value| value.as_str()));
    Ok(kernels::strategy_accepts(
        &strategy,
        method,
        cached_strategy.as_deref(),
    ))
}

/// Port of `_service_cached_matches_strategy`.
pub fn service_cached_matches_strategy(cached: Value, strategy: String) -> Result<bool, String> {
    let decoded = sequence(&cached)
        .cloned()
        .map(Value::Array)
        .unwrap_or(cached);
    Ok(kernels::cached_matches_strategy(&decoded, &strategy))
}

/// Port of `_service_raw_entry_matches_strategy`.
pub fn service_raw_entry_matches_strategy(entry: Value, strategy: String) -> Result<bool, String> {
    Ok(kernels::raw_entry_matches_strategy(&entry, &strategy))
}

/// Port of `_service_looks_like_text`.
pub fn service_looks_like_text(md: String) -> Result<bool, String> {
    Ok(kernels::looks_like_text(&md))
}

/// Port of `_service_slice_markdown`.
pub fn service_slice_markdown(
    markdown: String,
    offset: i64,
    max_chunks: i64,
    md_chars: i64,
    md_tokens: i64,
    model: String,
) -> Result<Value, String> {
    kernels::slice_markdown(&markdown, offset, max_chunks, md_chars, md_tokens, &model)
        .map(|out| {
            let items = out.as_array().cloned().unwrap_or_default();
            json!({"$tuple": items})
        })
        .map_err(failure)
}

/// Port of `_service_compact_metadata`.
pub fn service_compact_metadata(raw: Value) -> Result<Value, String> {
    Ok(kernels::compact_metadata(&raw))
}

/// Port of `_service_format_follow_ups`.
pub fn service_format_follow_ups(anchored_links: Vec<Value>) -> Result<Vec<Value>, String> {
    let decoded: Vec<Value> = anchored_links
        .into_iter()
        .map(|item| sequence(&item).cloned().map(Value::Array).unwrap_or(item))
        .collect();
    Ok(kernels::format_follow_ups(&Value::Array(decoded))
        .into_iter()
        .map(|candidate| {
            let mut wrapper = Map::new();
            wrapper.insert(
                "$object".to_string(),
                Value::String("FollowUpCandidate".into()),
            );
            wrapper.insert("attrs".to_string(), candidate);
            Value::Object(wrapper)
        })
        .collect())
}
