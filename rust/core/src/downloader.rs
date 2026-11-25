//! Deterministic download-candidate planning.

use serde_json::Value;

/// Validate inputs and build the ordered, deduplicated candidate list.
pub fn candidate_list(
    source: Option<&Value>,
    fallbacks: Option<&Value>,
) -> Result<Vec<String>, String> {
    let text = match source {
        Some(Value::String(text)) if !text.trim().is_empty() => text.clone(),
        _ => return Err("invalid_argument\0source must be a URL string".to_string()),
    };
    let Some(items) = fallbacks.and_then(Value::as_array) else {
        return Err(
            "invalid_argument\0fallback_urls must be a list of non-empty URL strings".to_string(),
        );
    };
    if items
        .iter()
        .any(|item| !matches!(item, Value::String(text) if !text.trim().is_empty()))
    {
        return Err(
            "invalid_argument\0fallback_urls must be a list of non-empty URL strings".to_string(),
        );
    }
    let mut candidates = vec![text];
    for item in items {
        let url = item.as_str().unwrap_or_default().to_string();
        if !candidates.contains(&url) {
            candidates.push(url);
        }
    }
    Ok(candidates)
}

/// Finalize a candidate attempt sequence into the public result payload.
pub fn finalize_candidates(
    source: &str,
    candidates: &[String],
    attempts: &[Value],
    results: &[Value],
) -> Value {
    for (index, attempt) in attempts.iter().enumerate() {
        if attempt.get("status").and_then(Value::as_str) == Some("downloaded") {
            let mut result = results
                .get(index)
                .cloned()
                .unwrap_or_else(|| attempt.clone());
            if let Some(object) = result.as_object_mut() {
                object.insert(
                    "requested_source".to_string(),
                    Value::String(source.to_string()),
                );
                object.insert("attempts".to_string(), Value::Array(attempts.to_vec()));
                object.insert(
                    "mirror_fallback_used".to_string(),
                    Value::Bool(candidates.get(index) != Some(&source.to_string())),
                );
            }
            return result;
        }
        let terminal = attempt
            .get("error")
            .and_then(Value::as_object)
            .and_then(|error| error.get("code"))
            .and_then(Value::as_str)
            .is_some_and(|code| matches!(code, "invalid_argument" | "file_exists"));
        if terminal {
            let mut result = results
                .get(index)
                .cloned()
                .unwrap_or_else(|| attempt.clone());
            if let Some(object) = result.as_object_mut() {
                object.insert("attempts".to_string(), Value::Array(attempts.to_vec()));
            }
            return result;
        }
    }
    let human_needed = attempts.iter().any(|attempt| {
        attempt
            .get("error")
            .and_then(Value::as_object)
            .and_then(|error| error.get("code"))
            .and_then(Value::as_str)
            .is_some_and(|code| matches!(code, "bot_wall" | "access_denied"))
    });
    let code = if human_needed {
        "human_action_needed"
    } else {
        "all_sources_failed"
    };
    let message = if human_needed {
        "All candidates were blocked by access controls or anti-bot challenges; human action is needed. No bypass was attempted."
    } else {
        "All caller-supplied download candidates failed."
    };
    let mut error = serde_json::json!({"code": code, "message": message});
    if let Some(object) = error.as_object_mut() {
        object.insert(
            "attempted_urls".to_string(),
            Value::Array(candidates.iter().cloned().map(Value::String).collect()),
        );
        object.insert("attempts".to_string(), Value::Array(attempts.to_vec()));
    }
    serde_json::json!({"source": source, "status": "error", "error": error})
}

/// Classify an HTTP response into the public download error payload.
pub fn http_error(
    status: i64,
    content_type: &str,
    sample: Option<&[u8]>,
    url: &str,
    retry_after: Option<&str>,
) -> Value {
    let (code, message) = match status {
        404 => ("not_found", "The requested file was not found."),
        401 | 403 => ("access_denied", "The server denied access to this file."),
        408 | 429 => ("rate_limited", "The server asked the client to slow down."),
        value if value >= 500 => ("server_error", "The server returned HTTP {status}."),
        _ => ("http_error", "The server returned HTTP {status}."),
    };
    let mut code = code.to_string();
    let mut message = message.replace("{status}", &status.to_string());
    let markers: [&[u8]; 6] = [
        b"cloudflare",
        b"captcha",
        b"attention required",
        b"access denied",
        b"akamai",
        b"robot check",
    ];
    if matches!(status, 401 | 403 | 406)
        && content_type.to_ascii_lowercase().contains("html")
        && sample.is_some_and(|bytes| {
            markers.iter().any(|marker| {
                bytes
                    .windows(marker.len())
                    .any(|window| window.eq_ignore_ascii_case(marker))
            })
        })
    {
        code = "bot_wall".to_string();
        message = "The server returned an anti-bot or browser challenge; no bypass was attempted."
            .to_string();
    }
    serde_json::json!({
        "code": code,
        "message": message,
        "http_status": status,
        "url": url,
        "retry_after": retry_after,
    })
}

/// Validate a completed download payload, returning an error payload if invalid.
#[expect(
    clippy::too_many_arguments,
    reason = "mirrors the Python validation inputs exactly"
)]
pub fn validate_finished_file(
    data: Option<&[u8]>,
    sample: Option<&[u8]>,
    content_type: Option<&str>,
    size: i64,
    minimum: i64,
    expect_pdf: bool,
    http_status: i64,
    url: &str,
) -> Option<Value> {
    if size < minimum {
        return Some(serde_json::json!({
            "code": "too_small",
            "message": format!("The file contained {size} bytes; minimum is {minimum}."),
            "bytes": size, "http_status": http_status, "url": url,
        }));
    }
    let is_pdf = data.is_some_and(|bytes| bytes.starts_with(b"%PDF-"));
    if !expect_pdf || is_pdf {
        return None;
    }
    let sample = sample.unwrap_or(&[]);
    let lowered: Vec<u8> = sample.iter().map(u8::to_ascii_lowercase).collect();
    let trimmed = lowered
        .iter()
        .copied()
        .skip_while(u8::is_ascii_whitespace)
        .collect::<Vec<_>>();
    let is_html = content_type
        .unwrap_or("")
        .to_ascii_lowercase()
        .contains("html")
        || trimmed.starts_with(b"<html");
    let markers: [&[u8]; 6] = [
        b"cloudflare",
        b"captcha",
        b"attention required",
        b"access denied",
        b"akamai",
        b"robot check",
    ];
    let bot = markers
        .iter()
        .any(|marker| lowered.windows(marker.len()).any(|w| w == *marker));
    let (code, message) = if is_html && bot {
        (
            "bot_wall",
            "The server returned an anti-bot/browser challenge instead of a PDF; no bypass was attempted.",
        )
    } else if is_html {
        (
            "unexpected_content",
            "The server returned HTML instead of a PDF.",
        )
    } else {
        (
            "invalid_file",
            "The response does not have a PDF signature (%PDF-).",
        )
    };
    Some(serde_json::json!({
        "code": code, "message": message, "bytes": size,
        "content_type": content_type, "http_status": http_status, "url": url,
    }))
}

/// Download a bounded byte payload with final provenance.
pub fn download_bytes(
    url: &str,
    timeout_seconds: f64,
    max_bytes: usize,
) -> Result<(Vec<u8>, u16, String, Option<String>), String> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| format!("RuntimeError: {error}"))?;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::limited(10))
        .timeout(std::time::Duration::from_secs_f64(timeout_seconds.max(0.0)))
        .build()
        .map_err(|error| format!("RuntimeError: {error}"))?;
    runtime.block_on(async {
        let response = client
            .get(url)
            .send()
            .await
            .map_err(|error| format!("HTTPError: {error}"))?;
        let status = response.status().as_u16();
        let final_url = response.url().to_string();
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(str::to_string);
        let bytes = response
            .bytes()
            .await
            .map_err(|error| format!("HTTPError: {error}"))?
            .to_vec();
        if bytes.len() > max_bytes {
            return Err(format!("The download exceeded the {max_bytes}-byte limit."));
        }
        Ok((bytes, status, final_url, content_type))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn candidates() {
        let list = candidate_list(
            Some(&Value::String("a".into())),
            Some(&serde_json::json!(["b", "a"])),
        )
        .unwrap();
        assert_eq!(list, vec!["a", "b"]);
    }
}

/// Download a bounded byte payload with final provenance.
pub fn download_bytes(
    url: &str,
    timeout_seconds: f64,
    max_bytes: usize,
) -> Result<(Vec<u8>, u16, String, Option<String>), String> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| format!("RuntimeError: {error}"))?;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::limited(10))
        .timeout(std::time::Duration::from_secs_f64(timeout_seconds.max(0.0)))
        .build()
        .map_err(|error| format!("RuntimeError: {error}"))?;
    runtime.block_on(async {
        let response = client
            .get(url)
            .send()
            .await
            .map_err(|error| format!("HTTPError: {error}"))?;
        let status = response.status().as_u16();
        let final_url = response.url().to_string();
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(str::to_string);
        let bytes = response
            .bytes()
            .await
            .map_err(|error| format!("HTTPError: {error}"))?
            .to_vec();
        if bytes.len() > max_bytes {
            return Err(format!("The download exceeded the {max_bytes}-byte limit."));
        }
        Ok((bytes, status, final_url, content_type))
    })
}
