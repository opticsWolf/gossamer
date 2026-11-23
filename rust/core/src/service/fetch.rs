//! Deterministic kernels of `FetchService`.
//!
//! The service still owns network I/O, browser escalation and toolbox state in
//! Python. These are the pure decisions and payload-shaping steps that every
//! fetch path uses: strategy resolution/compatibility, text sanity checks,
//! budgeted slicing, metadata compaction and follow-up formatting.

use std::backtrace::Backtrace;

use serde_json::{Map, Value, json};
use unic_ucd_category::GeneralCategory;

use crate::adapters::common::is_truthy;
use crate::budget::truncate_text;
use crate::compat::py_strip;
use crate::documents::follow_up_candidates;

/// Invalid render strategy requested by a caller.
#[derive(Debug)]
pub struct FetchServiceError {
    message: String,
    backtrace: Backtrace,
}

impl FetchServiceError {
    fn new(message: String) -> Self {
        Self {
            message,
            backtrace: Backtrace::capture(),
        }
    }

    /// Message without the backtrace.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Backtrace captured where the error was created.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl std::fmt::Display for FetchServiceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for FetchServiceError {}

/// Coerce a per-call `use_smart` value to `auto`, `browser` or `static`.
///
/// Values are stripped and lowercased, matching the Python enum coercion.
///
/// # Errors
/// [`FetchServiceError`] when the value is not a valid strategy.
pub fn coerce_fetch_mode(value: &str) -> Result<&str, FetchServiceError> {
    let normalized = value.trim().to_lowercase();
    match normalized.as_str() {
        "auto" => Ok("auto"),
        "browser" => Ok("browser"),
        "static" => Ok("static"),
        _ => Err(FetchServiceError::new(format!(
            "Invalid use_smart {}; expected one of ('auto', 'browser', 'static')",
            crate::compat::py_repr(value)
        ))),
    }
}

/// Resolve `(fetch_mode, use_smart)` into one of four strategies.
///
/// # Errors
/// [`FetchServiceError`] when `use_smart` is not a valid strategy.
pub fn resolve_strategy(
    fetch_mode: &str,
    use_smart: &str,
) -> Result<&'static str, FetchServiceError> {
    let use_smart = coerce_fetch_mode(use_smart)?;
    if use_smart == "static" {
        return Ok("static-only");
    }
    if use_smart == "browser" {
        return Ok("browser-first");
    }
    match fetch_mode {
        "static" => Ok("static-only"),
        "browser" => Ok("browser-only"),
        _ => Ok("auto"),
    }
}

/// Whether a cached render is compatible with the requested strategy.
pub fn strategy_accepts(
    strategy: &str,
    method: Option<&str>,
    cached_strategy: Option<&str>,
) -> bool {
    match strategy {
        "auto" => true,
        "static-only" => method == Some("static"),
        "browser-only" => matches!(method, Some("browser" | "stealth-fallback")),
        "browser-first" => {
            matches!(method, Some("browser" | "stealth-fallback"))
                || (method == Some("static") && cached_strategy == Some("browser-first"))
        }
        _ => false,
    }
}

/// Strategy compatibility for a `(markdown, links, meta, method)` cache tuple.
pub fn cached_matches_strategy(cached: &Value, strategy: &str) -> bool {
    let Some(items) = cached.as_array() else {
        return false;
    };
    let Some(method) = items.get(3).and_then(Value::as_str) else {
        return false;
    };
    let cached_strategy = items
        .get(2)
        .and_then(Value::as_object)
        .and_then(|meta| meta.get("fetch_strategy"))
        .and_then(Value::as_str);
    strategy_accepts(strategy, Some(method), cached_strategy)
}

/// Strategy compatibility for a raw page-cache entry dict.
pub fn raw_entry_matches_strategy(entry: &Value, strategy: &str) -> bool {
    if !is_truthy(entry) {
        return false;
    }
    let method = entry.get("method").and_then(Value::as_str);
    let cached_strategy = entry
        .get("meta")
        .and_then(Value::as_object)
        .and_then(|meta| meta.get("fetch_strategy"))
        .and_then(Value::as_str);
    strategy_accepts(strategy, method, cached_strategy)
}

/// Heuristic that rejects empty or binary-garbage payloads.
///
/// Head, middle and tail windows are sampled independently; any window with
/// at least 2% control/format/unassigned/private/surrogate code points (other
/// than newline, carriage return and tab) fails the gate.
pub fn looks_like_text(markdown: &str) -> bool {
    if py_strip(markdown).is_empty() {
        return false;
    }
    let chars: Vec<char> = markdown.chars().collect();
    let n = chars.len();
    let midpoint = n / 2;
    let samples = [
        slice_chars(&chars, 0, 2000.min(n)),
        slice_chars(
            &chars,
            midpoint.saturating_sub(1000),
            (midpoint + 1000).min(n),
        ),
        slice_chars(&chars, n.saturating_sub(2000), n),
    ];
    samples.iter().all(|sample| bad_ratio(sample) < 0.02)
}

fn bad_ratio(sample: &[char]) -> f64 {
    if sample.is_empty() {
        return 0.0;
    }
    let bad = sample
        .iter()
        .filter(|c| {
            !matches!(c, '\n' | '\r' | '\t')
                && matches!(
                    GeneralCategory::of(**c),
                    GeneralCategory::Control
                        | GeneralCategory::Unassigned
                        | GeneralCategory::PrivateUse
                        | GeneralCategory::Surrogate
                )
        })
        .count();
    bad as f64 / sample.len() as f64
}

fn slice_chars(chars: &[char], start: usize, end: usize) -> &[char] {
    &chars[start.min(chars.len())..end.min(chars.len()).max(start.min(chars.len()))]
}

/// Serve consecutive budget-sized chunks from full markdown.
///
/// Returns `(delivered, next_offset, has_more)` as a JSON triple. `next_offset`
/// is the end of the raw slice, so a token-budget cut cannot cause overlap on
/// resume.
///
/// # Errors
/// [`FetchServiceError`] when token truncation fails for `model`.
pub fn slice_markdown(
    markdown: &str,
    offset: i64,
    max_chunks: i64,
    md_chars: i64,
    md_tokens: i64,
    model: &str,
) -> Result<Value, FetchServiceError> {
    let limit = md_chars.max(1) as usize;
    let chars: Vec<char> = markdown.chars().collect();
    let total = chars.len();
    let mut pos = offset.clamp(0, total as i64) as usize;
    let mut parts = String::new();
    for _ in 0..max_chunks.max(1) {
        if pos >= total {
            break;
        }
        let end = if pos + limit >= total {
            total
        } else {
            let window = &chars[pos..pos + limit];
            let boundary = (0..window.len())
                .rev()
                .find(|idx| window[*idx] == '\n' && window.get(*idx + 1) == Some(&'\n'))
                .filter(|idx| *idx > limit / 2);
            match boundary {
                Some(idx) => pos + idx,
                None => pos + limit,
            }
        };
        let slice: String = chars[pos..end].iter().collect();
        parts.push_str(
            &truncate_text(&slice, limit, md_tokens, model)
                .map_err(|e| FetchServiceError::new(format!("markdown truncation failed: {e}")))?,
        );
        pos = end;
    }
    Ok(json!([parts, pos, pos < total]))
}

/// Compact raw meta-oxide output for LLM context.
pub fn compact_metadata(raw: &Value) -> Value {
    let Some(raw) = raw.as_object() else {
        return Value::Object(Map::new());
    };
    let mut compact = Map::new();
    let fields = [
        ("meta", "title", "title"),
        ("meta", "description", "description"),
        ("meta", "canonical", "canonical"),
        ("meta", "language", "language"),
        ("opengraph", "title", "og_title"),
        ("opengraph", "type", "og_type"),
        ("opengraph", "image", "og_image"),
        ("twitter", "card", "twitter_card"),
        ("twitter", "title", "twitter_title"),
    ];
    for (section, source, target) in fields {
        if let Some(value) = raw
            .get(section)
            .and_then(Value::as_object)
            .and_then(|section| section.get(source))
            .filter(|value| is_truthy(value))
        {
            compact.insert(target.to_string(), value.clone());
        }
    }
    if let Some(jsonld) = raw.get("jsonld").and_then(Value::as_array)
        && !jsonld.is_empty()
    {
        compact.insert(
            "jsonld".to_string(),
            Value::Array(jsonld.iter().take(2).cloned().collect()),
        );
    }
    if let Some(rel) = raw.get("rel_links").and_then(Value::as_object) {
        if let Some(canonical) = rel
            .get("canonical")
            .and_then(Value::as_array)
            .and_then(|items| items.first())
            .filter(|value| is_truthy(value))
        {
            compact.insert("canonical".to_string(), canonical.clone());
        }
        if let Some(alternate) = rel.get("alternate").filter(|value| is_truthy(value)) {
            compact.insert("alternates".to_string(), alternate.clone());
        }
    }
    if let Some(removed) = raw
        .get("hidden_blocks_removed")
        .filter(|value| is_truthy(value))
    {
        compact.insert("hidden_blocks_removed".to_string(), removed.clone());
    }
    Value::Object(compact)
}

/// Format anchored links as title/type follow-up candidates without a cap.
pub fn format_follow_ups(links: &Value) -> Vec<Value> {
    follow_up_candidates(links, 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strategies_match_the_reference_table() {
        assert_eq!(resolve_strategy("auto", "static").ok(), Some("static-only"));
        assert_eq!(resolve_strategy("static", "auto").ok(), Some("static-only"));
        assert_eq!(
            resolve_strategy("auto", "browser").ok(),
            Some("browser-first")
        );
        assert_eq!(
            resolve_strategy("browser", "AUTO").ok(),
            Some("browser-only")
        );
        assert_eq!(resolve_strategy("auto", "auto").ok(), Some("auto"));
        assert!(resolve_strategy("auto", "bogus").is_err());
    }

    #[test]
    fn browser_first_reuses_its_static_fallback() {
        assert!(strategy_accepts(
            "browser-first",
            Some("static"),
            Some("browser-first")
        ));
        assert!(!strategy_accepts("browser-first", Some("static"), None));
        assert!(!strategy_accepts("static-only", Some("browser"), None));
    }

    #[test]
    fn binary_garbage_is_rejected() {
        assert!(looks_like_text("hello world"));
        assert!(!looks_like_text(""));
        assert!(!looks_like_text("\u{0}\u{1}\u{2}\u{3}"));
    }

    #[test]
    fn compact_metadata_caps_jsonld_and_overrides_canonical() {
        let raw = json!({
            "meta": {"title": "T", "canonical": "https://old.test"},
            "jsonld": [{"a": 1}, {"b": 2}, {"c": 3}],
            "rel_links": {"canonical": ["https://new.test"], "alternate": ["/rss"]}
        });
        let out = compact_metadata(&raw);
        assert_eq!(out["canonical"], "https://new.test");
        assert_eq!(out["jsonld"].as_array().map_or(0, Vec::len), 2);
        assert_eq!(out["alternates"][0], "/rss");
    }
}

/// Pure dispatch decision for a fetch strategy.
///
/// Static and browser I/O stay with the caller; this function only chooses
/// which already-computed outcome should be returned, or reports that the
/// browser must be invoked.
///
/// # Errors
/// [`FetchServiceError`] when `auto` falls back and no browser is installed.
fn as_sequence(value: &Value) -> Option<&Vec<Value>> {
    value
        .get("$tuple")
        .and_then(Value::as_array)
        .or_else(|| value.as_array())
}

pub fn dispatch_decision(
    url: &str,
    strategy: &str,
    static_result: Option<&Value>,
    static_error: Option<&str>,
    browser_result: Option<&Value>,
    browser_error: Option<&str>,
    browser_available: bool,
) -> Result<Value, FetchServiceError> {
    match strategy {
        "static-only" => Ok(json!({"kind": "result", "value": static_result})),
        "browser-only" => match browser_result {
            Some(result) => Ok(json!({"kind": "result", "value": result})),
            None => Ok(json!({"kind": "browser"})),
        },
        "browser-first" => {
            if browser_error.is_none() {
                Ok(json!({"kind": "result", "value": browser_result}))
            } else {
                Ok(json!({"kind": "result", "value": static_result}))
            }
        }
        "auto" => {
            let static_text = static_result
                .and_then(as_sequence)
                .and_then(|items| items.first())
                .and_then(Value::as_str);
            if static_error.is_none() && static_text.is_some_and(looks_like_text) {
                return Ok(json!({"kind": "result", "value": static_result}));
            }
            if let Some(result) = browser_result {
                return Ok(json!({"kind": "result", "value": result}));
            }
            if let Some(error) = browser_error {
                return Err(FetchServiceError::new(error.to_string()));
            }
            if !browser_available {
                return Err(FetchServiceError::new(format!(
                    "Fetch failed for {url} and browser_oxide is not installed"
                )));
            }
            Ok(json!({"kind": "browser"}))
        }
        other => Err(FetchServiceError::new(format!(
            "unknown fetch strategy: {other}"
        ))),
    }
}
