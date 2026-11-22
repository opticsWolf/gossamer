//! Output-budget kernels (port of the pure parts of `gossamer.budget`).
//!
//! Ported: two-pass truncation, JSON-fit testing, research-result shrinking, and parsed-payload
//! shrinking. JSON pretty printing matches `json.dumps(..., indent=2)` with `ensure_ascii` escaping.
//! Python callbacks and toolbox configuration stay in the binding layer.

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;

use serde_json::Value;

use crate::compat::{char_count, char_head};
use crate::tokens::{count_tokens, default_ellipsis, truncate_to_tokens, TokenError};

const TRUNCATE_MARKER: &str = "\n\n... [truncated]";
const SNIPPET_SUFFIX: &str = "...";

/// Broad category of a budget failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BudgetErrorKind {
    /// Malformed JSON or invalid value.
    Value,
    /// A snapshot item lacks a required mapping attribute.
    Attribute,
    /// A snapshot item is not iterable as the Python original expects.
    Type,
    /// An underlying token-budget failure.
    Token,
}

/// Canonical output-budget error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct BudgetError {
    kind: BudgetErrorKind,
    message: String,
    backtrace: Backtrace,
}

impl BudgetError {
    fn new(kind: BudgetErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the failure category.
    pub fn kind(&self) -> BudgetErrorKind {
        self.kind
    }

    /// Returns the Python-compatible message without an exception prefix.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace for diagnostics.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for BudgetError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for BudgetError {}

fn token_error(err: TokenError) -> BudgetError {
    BudgetError::new(BudgetErrorKind::Token, err.message().to_owned())
}

fn to_json_pretty_raw(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| "null".to_string())
}

fn to_json_ascii_pretty(value: &Value) -> String {
    let pretty = serde_json::to_string_pretty(value).unwrap_or_else(|_| "null".to_string());
    let mut out = String::with_capacity(pretty.len());
    for c in pretty.chars() {
        if (c as u32) < 0x80 {
            out.push(c);
        } else {
            let n = c as u32;
            if n < 0x10000 {
                out.push_str(&format!("\\u{n:04x}"));
            } else {
                let v = n - 0x10000;
                out.push_str(&format!(
                    "\\u{:04x}\\u{:04x}",
                    0xD800 + (v >> 10),
                    0xDC00 + (v & 0x3FF)
                ));
            }
        }
    }
    out
}

/// Truncates by token budget first, then by character limit.
///
/// # Errors
///
/// Returns [`BudgetErrorKind::Token`] when token counting or truncation fails.
pub fn truncate_text(
    text: &str,
    char_limit: usize,
    token_limit: i64,
    model: &str,
) -> Result<String, BudgetError> {
    let mut out = text.to_string();
    if token_limit > 0 {
        out = truncate_to_tokens(&out, token_limit, model, default_ellipsis()).map_err(token_error)?;
    }
    if char_count(&out) > char_limit {
        out = format!("{}{TRUNCATE_MARKER}", char_head(&out, char_limit));
    }
    Ok(out)
}

/// Reports whether text fits both budgets; zero disables a budget.
///
/// # Errors
///
/// Returns [`BudgetErrorKind::Token`] when token counting fails.
pub fn json_fits(
    text: &str,
    char_limit: i64,
    token_limit: i64,
    model: &str,
) -> Result<bool, BudgetError> {
    if char_limit != 0 && char_count(text) as i64 > char_limit {
        return Ok(false);
    }
    if token_limit != 0 && count_tokens(text, model).map_err(token_error)? as i64 > token_limit {
        return Ok(false);
    }
    Ok(true)
}

/// Splits a budget into character and token allowances after the links reserve.
pub fn content_split(chars: i64, tokens: i64, link_ratio: f64) -> (usize, i64) {
    let keep = 1.0 - link_ratio;
    let c = (chars as f64 * keep) as usize;
    let t = if tokens > 0 {
        (tokens as f64 * keep) as i64
    } else {
        0
    };
    (c, t)
}

fn attr_error(t: &str) -> BudgetError {
    BudgetError::new(
        BudgetErrorKind::Attribute,
        format!("'{t}' object has no attribute 'get'"),
    )
}

fn type_error_not_iterable(t: &str) -> BudgetError {
    BudgetError::new(
        BudgetErrorKind::Type,
        format!("'{t}' object is not iterable"),
    )
}

fn json_value_type(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_i64() || n.is_u64() {
                "int"
            } else {
                "float"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Shrinks a serialized research result to the requested budget.
///
/// A `None` budget re-serializes without modification, matching the Python original.
///
/// # Examples
///
/// ```
/// use gossamer::budget::shrink_research;
///
/// let out = shrink_research(r#"{"a": 1}"#, None).unwrap();
/// assert_eq!(out, "{\n  \"a\": 1\n}");
/// ```
///
/// # Errors
///
/// Returns [`BudgetErrorKind::Value`] for malformed JSON and the Python-compatible
/// [`BudgetErrorKind::Attribute`] / [`BudgetErrorKind::Type`] errors for malformed snapshots.
pub fn shrink_research(result_json: &str, budget: Option<usize>) -> Result<String, BudgetError> {
    let mut out: Value = serde_json::from_str(result_json)
        .map_err(|e| BudgetError::new(BudgetErrorKind::Value, e.to_string()))?;
    let Some(budget) = budget else {
        return Ok(to_json_ascii_pretty(&out));
    };
    if !matches!(&out, Value::Object(_)) {
        return Err(attr_error(json_value_type(&out)));
    }
    let has_sources = matches!(&out, Value::Object(map) if map.contains_key("sources"));
    if has_sources {
        shrink_sources(&mut out, budget)?;
        let len = match &out {
            Value::Object(map) => match map.get("sources") {
                Some(Value::Array(a)) => a.len(),
                _ => 0,
            },
            _ => 0,
        };
        let keep = (budget / 120).max(1);
        if len > keep
            && let Value::Object(map) = &mut out
            && let Some(Value::Array(items)) = map.get_mut("sources")
        {
            items.truncate(keep);
            map.insert(
                "sources_omitted".to_string(),
                Value::from((len - keep) as i64),
            );
        }
    }
    Ok(to_json_ascii_pretty(&out))
}

fn shrink_sources(out: &mut Value, budget: usize) -> Result<(), BudgetError> {
    let items: &mut Vec<Value> = match out {
        Value::Object(map) => match map.get_mut("sources") {
            Some(Value::Array(a)) => a,
            Some(Value::Null) => return Err(type_error_not_iterable("NoneType")),
            Some(Value::String(_)) => return Err(attr_error("str")),
            Some(Value::Bool(_)) => return Err(type_error_not_iterable("bool")),
            Some(Value::Number(n)) => {
                if n.is_i64() || n.is_u64() {
                    return Err(type_error_not_iterable("int"));
                }
                return Err(type_error_not_iterable("float"));
            }
            Some(Value::Object(_)) => return Err(attr_error("str")),
            None => return Ok(()),
        },
        _ => return Err(attr_error("dict")),
    };
    for source in items.iter_mut() {
        let map = match source {
            Value::Object(m) => m,
            _ => {
                return Err(attr_error(match source {
                    Value::String(_) => "str",
                    Value::Null => "NoneType",
                    Value::Bool(_) => "bool",
                    Value::Number(n) => {
                        if n.is_i64() || n.is_u64() {
                            "int"
                        } else {
                            "float"
                        }
                    }
                    Value::Array(_) => "list",
                    Value::Object(_) => "dict",
                }));
            }
        };
        if let Some(Value::Object(page)) = map.get_mut("result") {
            let md_cut = match page.get("markdown") {
                Some(Value::String(md)) if char_count(md) > budget => {
                    Some(char_head(md, budget).to_string())
                }
                _ => None,
            };
            if let Some(cut) = md_cut {
                page.insert(
                    "markdown".to_string(),
                    Value::String(format!("{cut}{TRUNCATE_MARKER}")),
                );
            }
            if let Some(Value::Array(links)) = page.get_mut("follow_up_links") {
                links.truncate(5);
            }
        }
        let snip_cut = match map.get("snippet") {
            Some(Value::String(snippet)) if char_count(snippet) > budget => {
                Some(char_head(snippet, budget).to_string())
            }
            _ => None,
        };
        if let Some(cut) = snip_cut {
            map.insert(
                "snippet".to_string(),
                Value::String(format!("{cut}{SNIPPET_SUFFIX}")),
            );
        }
    }
    Ok(())
}

/// Shrinks a serialized parsed-document payload; `None` budget returns input verbatim.
///
/// # Errors
///
/// Returns [`BudgetErrorKind::Value`] for malformed JSON.
pub fn shrink_payload(payload_json: &str, budget: Option<usize>) -> Result<String, BudgetError> {
    let Some(budget) = budget else {
        return Ok(payload_json.to_string());
    };
    let mut payload: Value = serde_json::from_str(payload_json).map_err(|_| {
        BudgetError::new(
            BudgetErrorKind::Value,
            "invalid ParsedDocumentPayload JSON",
        )
    })?;
    if let Value::Object(root) = &mut payload
        && let Some(Value::Array(pages)) = root.get_mut("pages")
    {
        for page in pages.iter_mut() {
            if let Value::Object(pm) = page {
                for key in ["raw_text", "markdown"] {
                    if let Some(Value::String(text)) = pm.get(key)
                        && char_count(text) > budget
                    {
                        pm.insert(
                            key.to_string(),
                            Value::String(format!(
                                "{}{TRUNCATE_MARKER}",
                                char_head(text, budget)
                            )),
                        );
                    }
                }
            }
        }
    }
    Ok(to_json_pretty_raw(&payload))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_two_passes() {
        let out = truncate_text("hello world foo", 5, 0, "gpt-4o").unwrap();
        assert_eq!(out, format!("hello{TRUNCATE_MARKER}"));
        let out = truncate_text("short", 100, 0, "gpt-4o").unwrap();
        assert_eq!(out, "short");
    }

    #[test]
    fn ascii_json_escapes_non_ascii() {
        let v = serde_json::json!({"a": "ü✓", "b": [1, "x"]});
        let s = to_json_ascii_pretty(&v);
        assert!(s.contains("\\u00fc"));
        assert!(s.contains("\\u2713"));
    }

    #[test]
    fn research_shrink_caps_and_drops() {
        let doc = serde_json::json!({
            "sources": [
                {"result": {"markdown": "0123456789abcdef", "follow_up_links": [1,2,3,4,5,6]},
                 "snippet": "abcdefghij0123456789"}
            ]
        });
        let out = shrink_research(&serde_json::to_string(&doc).unwrap(), Some(8)).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["sources"][0]["result"]["markdown"], "01234567\n\n... [truncated]");
        assert_eq!(v["sources"][0]["result"]["follow_up_links"].as_array().unwrap().len(), 5);
        assert_eq!(v["sources"][0]["snippet"], "abcdefgh...");
    }

    #[test]
    fn research_error_paths() {
        assert!(shrink_research("{\"sources\": null}", Some(8)).is_err());
        assert!(shrink_research("[1,2]", Some(8)).is_err());
        assert!(shrink_research("{\"sources\": [\"x\"]}", Some(8)).is_err());
        let out = shrink_research("{\"a\": 1}", Some(8)).unwrap();
        assert_eq!(out, "{\n  \"a\": 1\n}");
        let out = shrink_research("{\"a\": 1}", None).unwrap();
        assert_eq!(out, "{\n  \"a\": 1\n}");
    }
}
