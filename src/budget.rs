//! Output-budget kernels (port of the pure parts of `gossamer.budget`).
//!
//! Ported: two-pass truncation, JSON-fit testing, research-result
//! shrinking, and parsed-payload shrinking. All JSON boundaries use
//! `json.dumps(..., indent=2)`-compatible pretty printing with
//! `ensure_ascii` escaping (non-ASCII → `\uXXXX`, lowercase hex).
//! Deliberately NOT ported: `_fit_json` (takes a Python `build`
//! callback), `_content_budget` field reads (one-line math done inline
//! here as `content_split`), and anything touching the toolbox.
//!
//! Fidelity notes: research shrinking replicates even the crash paths
//! (non-iterable `sources`, attribute access on items); payload
//! shrinking assumes parser-built payloads (malformed input raises a
//! plain `ValueError`, not pydantic's report — unreachable from the
//! parser output it consumes).
//!
//! Pinned by `tests/test_rust_parity_budget.py`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::Value;

use crate::pycompat::{char_count, char_head};
use crate::tokens::{count_tokens_impl, truncate_to_tokens_impl};

const TRUNCATE_MARKER: &str = "\n\n... [truncated]";
const SNIPPET_SUFFIX: &str = "...";

/// Plain pretty-print (raw UTF-8): matches pydantic `model_dump_json`,
/// which — unlike `json.dumps` — does not `ensure_ascii`-escape.
fn to_json_pretty_raw(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| "null".to_string())
}
/// pretty-print plus `\uXXXX` (lowercase, surrogate pairs) for every
/// non-ASCII char. ASCII output passes through untouched (serde already
/// escapes controls/quotes/backslashes the same way).
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

/// Mirror of `ContentBudget._truncate`: token pass first, then the char
/// safety cap with its own marker.
pub fn truncate_text_impl(
    text: &str,
    char_limit: usize,
    token_limit: i64,
    model: &str,
) -> Result<String, String> {
    let mut out = text.to_string();
    if token_limit > 0 {
        out = truncate_to_tokens_impl(&out, token_limit, model, crate::tokens::default_ellipsis())?;
    }
    if char_count(&out) > char_limit {
        out = format!("{}{TRUNCATE_MARKER}", char_head(&out, char_limit));
    }
    Ok(out)
}

/// Mirror of `ContentBudget._json_fits` (both budgets; zero disables).
pub fn json_fits_impl(
    text: &str,
    char_limit: i64,
    token_limit: i64,
    model: &str,
) -> Result<bool, String> {
    if char_limit != 0 && char_count(text) as i64 > char_limit {
        return Ok(false);
    }
    if token_limit != 0 && count_tokens_impl(text, model)? as i64 > token_limit {
        return Ok(false);
    }
    Ok(true)
}

/// Mirror of `ContentBudget._content_budget`: `(chars, tokens)` after
/// the links reserve.
pub fn content_split_impl(chars: i64, tokens: i64, link_ratio: f64) -> (usize, i64) {
    let keep = 1.0 - link_ratio;
    let c = (chars as f64 * keep) as usize;
    let t = if tokens > 0 {
        (tokens as f64 * keep) as i64
    } else {
        0
    };
    (c, t)
}

fn attr_error(t: &str) -> String {
    format!("AttributeError: '{t}' object has no attribute 'get'")
}

fn type_error_not_iterable(t: &str) -> String {
    format!("TypeError: '{t}' object is not iterable")
}

/// Mirror of `ContentBudget._shrink_research` over a JSON snapshot.
pub fn shrink_research_impl(
    result_json: &str,
    budget: Option<usize>,
) -> Result<String, String> {
    let mut out: Value =
        serde_json::from_str(result_json).map_err(|e| format!("ValueError: {e}"))?;
    let Some(budget) = budget else {
        // `budget=None` re-serializes without touching anything (mirrors
        // `json.dumps(out, indent=2)` on the deepcopy).
        return Ok(to_json_ascii_pretty(&out));
    };
    // NOTE: `out.get("sources", [])` — a missing key skips both the loop
    // and the drop stage; anything else must be a list or the original
    // raises exactly as below. A non-object result raises AttributeError
    // on `.get`, like the original.
    if !matches!(&out, Value::Object(_)) {
        return Err(attr_error(json_value_type(&out)));
    }
    let has_sources = matches!(&out, Value::Object(map) if map.contains_key("sources"));
    if has_sources {
        shrink_sources(&mut out, budget)?;
        // Drop whole sources from the tail when even trimmed ones overflow.
        let len = match &out {
            Value::Object(map) => match map.get("sources") {
                Some(Value::Array(a)) => a.len(),
                _ => 0,
            },
            _ => 0,
        };
        let keep = (budget / 120).max(1);
        if len > keep {
            if let Value::Object(map) = &mut out {
                if let Some(Value::Array(items)) = map.get_mut("sources") {
                    items.truncate(keep);
                }
                map.insert(
                    "sources_omitted".to_string(),
                    Value::from((len - keep) as i64),
                );
            }
        }
    }
    Ok(to_json_ascii_pretty(&out))
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

/// The per-source shrink loop (borrowed separately from the drop stage).
fn shrink_sources(out: &mut Value, budget: usize) -> Result<(), String> {
    let items: &mut Vec<Value> = match out {
        Value::Object(map) => match map.get_mut("sources") {
            Some(Value::Array(a)) => a,
            Some(Value::Null) => {
                return Err(type_error_not_iterable("NoneType"));
            }
            Some(Value::String(_)) => {
                // Iterating a string yields chars; `.get` on the first
                // char raises AttributeError (type is what matters).
                return Err(attr_error("str"));
            }
            Some(Value::Bool(_)) => return Err(type_error_not_iterable("bool")),
            Some(Value::Number(n)) => {
                if n.is_i64() || n.is_u64() {
                    return Err(type_error_not_iterable("int"));
                } else {
                    return Err(type_error_not_iterable("float"));
                }
            }
            Some(Value::Object(_)) => {
                // Iterating a dict yields its keys (strings).
                return Err(attr_error("str"));
            }
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

/// Mirror of `ContentBudget._shrink_parsed_payload` over a JSON snapshot.
/// `None` budget returns the input verbatim (no re-serialization).
pub fn shrink_payload_impl(payload_json: &str, budget: Option<usize>) -> Result<String, String> {
    let Some(budget) = budget else {
        return Ok(payload_json.to_string());
    };
    let mut payload: Value = serde_json::from_str(payload_json)
        .map_err(|_| "ValueError: invalid ParsedDocumentPayload JSON".to_string())?;
    if let Value::Object(root) = &mut payload {
        if let Some(Value::Array(pages)) = root.get_mut("pages") {
            for page in pages.iter_mut() {
                if let Value::Object(pm) = page {
                    for key in ["raw_text", "markdown"] {
                        if let Some(Value::String(text)) = pm.get(key) {
                            if char_count(text) > budget {
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
        }
    }
    Ok(to_json_pretty_raw(&payload))
}

// ── PyO3 wrappers ────────────────────────────────────────────────

#[pyfunction]
#[pyo3(signature = (text, char_limit, token_limit = 0, model_name = "gpt-4o"))]
pub fn budget_truncate(
    text: &str,
    char_limit: usize,
    token_limit: i64,
    model_name: &str,
) -> PyResult<String> {
    truncate_text_impl(text, char_limit, token_limit, model_name)
        .map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn budget_json_fits(
    text: &str,
    char_limit: i64,
    token_limit: i64,
    model_name: &str,
) -> PyResult<bool> {
    json_fits_impl(text, char_limit, token_limit, model_name).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn budget_content_split(chars: i64, tokens: i64, link_ratio: f64) -> (usize, i64) {
    content_split_impl(chars, tokens, link_ratio)
}

#[pyfunction]
#[pyo3(signature = (result_json, budget = None))]
pub fn shrink_research_json(result_json: &str, budget: Option<usize>) -> PyResult<String> {
    shrink_research_impl(result_json, budget).map_err(|e| {
        if let Some(msg) = e.strip_prefix("AttributeError: ") {
            pyo3::exceptions::PyAttributeError::new_err(msg.to_string())
        } else if let Some(msg) = e.strip_prefix("TypeError: ") {
            pyo3::exceptions::PyTypeError::new_err(msg.to_string())
        } else {
            PyValueError::new_err(e)
        }
    })
}

#[pyfunction]
#[pyo3(signature = (payload_json, budget = None))]
pub fn shrink_payload_json(payload_json: &str, budget: Option<usize>) -> PyResult<String> {
    shrink_payload_impl(payload_json, budget).map_err(PyValueError::new_err)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_two_passes() {
        let out = truncate_text_impl("hello world foo", 5, 0, "gpt-4o").unwrap();
        assert_eq!(out, format!("hello{TRUNCATE_MARKER}"));
        let out = truncate_text_impl("short", 100, 0, "gpt-4o").unwrap();
        assert_eq!(out, "short");
    }

    #[test]
    fn ascii_json_escapes_non_ascii() {
        let v = serde_json::json!({"a": "ü✓", "b": [1, "x"]});
        let s = to_json_ascii_pretty(&v);
        assert!(s.contains("\\u00fc"));
        assert!(s.contains("\\u2713"));
        assert_eq!(
            s,
            "{\n  \"a\": \"\\u00fc\\u2713\",\n  \"b\": [\n    1,\n    \"x\"\n  ]\n}"
        );
    }

    #[test]
    fn research_shrink_caps_and_drops() {
        let doc = serde_json::json!({
            "sources": [
                {"result": {"markdown": "0123456789abcdef", "follow_up_links": [1,2,3,4,5,6]},
                 "snippet": "abcdefghij0123456789"}
            ]
        });
        let out = shrink_research_impl(&serde_json::to_string(&doc).unwrap(), Some(8)).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["sources"][0]["result"]["markdown"], "01234567\n\n... [truncated]");
        assert_eq!(v["sources"][0]["result"]["follow_up_links"].as_array().unwrap().len(), 5);
        assert_eq!(v["sources"][0]["snippet"], "abcdefgh...");
    }

    #[test]
    fn research_error_paths() {
        assert!(shrink_research_impl("{\"sources\": null}", Some(8)).is_err());
        assert!(shrink_research_impl("[1,2]", Some(8)).is_err());
        assert!(shrink_research_impl("{\"sources\": [\"x\"]}", Some(8)).is_err());
        // Missing sources + budget: no-op re-serialization.
        let out = shrink_research_impl("{\"a\": 1}", Some(8)).unwrap();
        assert_eq!(out, "{\n  \"a\": 1\n}");
        // None budget re-serializes (mirrors json.dumps(indent=2)).
        let out = shrink_research_impl("{\"a\": 1}", None).unwrap();
        assert_eq!(out, "{\n  \"a\": 1\n}");
    }
}
