//! Deterministic download-candidate planning.

use serde_json::Value;

/// Validate inputs and build the ordered, deduplicated candidate list.
pub fn candidate_list(source: Option<&Value>, fallbacks: Option<&Value>) -> Result<Vec<String>, String> {
    let text = match source {
        Some(Value::String(text)) if !text.trim().is_empty() => text.clone(),
        _ => return Err("invalid_argument\0source must be a URL string".to_string()),
    };
    let Some(items) = fallbacks.and_then(Value::as_array) else {
        return Err("invalid_argument\0fallback_urls must be a list of non-empty URL strings".to_string());
    };
    if items.iter().any(|item| !matches!(item, Value::String(text) if !text.trim().is_empty())) {
        return Err("invalid_argument\0fallback_urls must be a list of non-empty URL strings".to_string());
    }
    let mut candidates = vec![text];
    for item in items {
        let url = item.as_str().unwrap_or_default().to_string();
        if !candidates.contains(&url) { candidates.push(url); }
    }
    Ok(candidates)
}

/// Finalize a candidate attempt sequence into the public result payload.
pub fn finalize_candidates(source: &str, candidates: &[String], attempts: &[Value], results: &[Value]) -> Value {
    for (index, attempt) in attempts.iter().enumerate() {
        if attempt.get("status").and_then(Value::as_str) == Some("downloaded") {
            let mut result = results.get(index).cloned().unwrap_or_else(|| attempt.clone());
            if let Some(object) = result.as_object_mut() {
                object.insert("requested_source".to_string(), Value::String(source.to_string()));
                object.insert("attempts".to_string(), Value::Array(attempts.to_vec()));
                object.insert("mirror_fallback_used".to_string(), Value::Bool(candidates.get(index) != Some(&source.to_string())));
            }
            return result;
        }
        let terminal = attempt.get("error")
            .and_then(Value::as_object)
            .and_then(|error| error.get("code"))
            .and_then(Value::as_str)
            .is_some_and(|code| matches!(code, "invalid_argument" | "file_exists"));
        if terminal {
            let mut result = results.get(index).cloned().unwrap_or_else(|| attempt.clone());
            if let Some(object) = result.as_object_mut() {
                object.insert("attempts".to_string(), Value::Array(attempts.to_vec()));
            }
            return result;
        }
    }
    let human_needed = attempts.iter().any(|attempt| {
        attempt.get("error").and_then(Value::as_object).and_then(|error| error.get("code"))
            .and_then(Value::as_str)
            .is_some_and(|code| matches!(code, "bot_wall" | "access_denied"))
    });
    let code = if human_needed { "human_action_needed" } else { "all_sources_failed" };
    let message = if human_needed {
        "All candidates were blocked by access controls or anti-bot challenges; human action is needed. No bypass was attempted."
    } else {
        "All caller-supplied download candidates failed."
    };
    let mut error = serde_json::json!({"code": code, "message": message});
    if let Some(object) = error.as_object_mut() {
        object.insert("attempted_urls".to_string(), Value::Array(candidates.iter().cloned().map(Value::String).collect()));
        object.insert("attempts".to_string(), Value::Array(attempts.to_vec()));
    }
    serde_json::json!({"source": source, "status": "error", "error": error})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn candidates() {
        let list = candidate_list(Some(&Value::String("a".into())), Some(&serde_json::json!(["b", "a"]))).unwrap();
        assert_eq!(list, vec!["a", "b"]);
    }
}
