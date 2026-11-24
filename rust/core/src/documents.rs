//! Payload-schema helpers shared by `models.py` and `structured_parser.py`.
//!
//! These are the deterministic parts of the "documents" unit: turning raw
//! office IR/follow-up links into the JSON shapes every CLI command and MCP
//! tool emits, and making markdown links self-contained. Binary PDF/Office
//! extraction itself stays behind the Python optional extras for now.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Value, json};

use crate::config::py_str_value;
use crate::miscutils::classify_link;

/// Inline markdown link. Rust's regex engine has no lookbehind, so image
/// links are filtered by checking the byte immediately before the match.
/// Kept in a lazy static because it is an immutable compiled pattern used on
/// every page (performance only; no state depends on it).
const MD_INLINE_LINK_PATTERN: &str = r#"\[([^\]]*)\]\(([^()\s]+)(?:\s+"([^"]*)")?\)"#;

static MD_INLINE_LINK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(MD_INLINE_LINK_PATTERN)
        .unwrap_or_else(|e| panic!("invalid markdown link regex {MD_INLINE_LINK_PATTERN:?}: {e}"))
});

/// Targets that are already self-contained or must not be rewritten.
const MD_NON_LINK_PREFIXES: [&str; 7] =
    ["#", "//", "mailto:", "tel:", "javascript:", "data:", "ftp:"];

/// Flatten one office IR table cell to its text.
///
/// Only paragraph blocks with text runs contribute; other block types and
/// empty runs are ignored, matching the Python helper.
pub fn office_cell_text(cell: &Value) -> String {
    let mut parts: Vec<String> = Vec::new();
    let Some(blocks) = cell.get("content").and_then(Value::as_array) else {
        return String::new();
    };
    for block in blocks {
        if block.get("type").and_then(Value::as_str) != Some("paragraph") {
            continue;
        }
        let Some(runs) = block.get("content").and_then(Value::as_array) else {
            continue;
        };
        for run in runs {
            if let Some(text) = run.get("text").and_then(Value::as_str) {
                let text = text.trim();
                if !text.is_empty() {
                    parts.push(text.to_string());
                }
            }
        }
    }
    parts.join(" ")
}

/// All tables in an office IR object as `[title, rows]` pairs.
///
/// Sections without a title use `Section {index}` with a 1-based index. A
/// table contributes only when it has at least one non-empty row.
pub fn office_ir_tables(ir: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    let Some(sections) = ir.get("sections").and_then(Value::as_array) else {
        return out;
    };
    for (index, section) in sections.iter().enumerate() {
        if !section.is_object() {
            continue;
        }
        let title = section
            .get("title")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| format!("Section {}", index + 1));
        let Some(elements) = section.get("elements").and_then(Value::as_array) else {
            continue;
        };
        for element in elements {
            if element.get("type").and_then(Value::as_str) != Some("table") {
                continue;
            }
            let mut rows: Vec<Vec<String>> = Vec::new();
            if let Some(raw_rows) = element.get("rows").and_then(Value::as_array) {
                for row in raw_rows {
                    let Some(cells) = row.get("cells").and_then(Value::as_array) else {
                        continue;
                    };
                    let rendered: Vec<String> = cells
                        .iter()
                        .filter(|cell| cell.is_object())
                        .map(office_cell_text)
                        .collect();
                    if !rendered.is_empty() {
                        rows.push(rendered);
                    }
                }
            }
            if !rows.is_empty() {
                out.push(json!([title, rows]));
            }
        }
    }
    out
}

/// Deduplicated, typed follow-up candidates as JSON objects.
///
/// Inputs are `(url, anchor_text)` pairs; a bare item is stringified and gets
/// an empty title. Duplicate URLs are dropped after the first occurrence and
/// `max_links <= 0` means unlimited.
pub fn follow_up_candidates(links: &Value, max_links: i64) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let Some(items) = links.as_array() else {
        return out;
    };
    for item in items {
        if max_links > 0 && out.len() as i64 >= max_links {
            break;
        }
        let (url, text) = match item.as_array() {
            Some(pair) if pair.len() == 2 => (py_str_value(&pair[0]), py_str_value(&pair[1])),
            _ => (py_str_value(item), String::new()),
        };
        if url.is_empty() || seen.iter().any(|value| value == &url) {
            continue;
        }
        seen.push(url.clone());
        let title = text.trim();
        let title = if title.is_empty() {
            "(untitled)".to_string()
        } else {
            title.to_string()
        };
        let kind = classify_link(&url).to_string();
        out.push(json!({"title": title, "url": url, "type": kind}));
    }
    out
}

/// Rewrite relative markdown hrefs against `base_url`.
///
/// # Examples
/// ```
/// use gossamer::documents::absolutize_markdown_links;
/// let out = absolutize_markdown_links("[A](/a)", "https://example.test/dir/p");
/// assert_eq!(out, "[A](https://example.test/a)");
/// ```
///
/// Absolute URLs, protocol-relative, fragment-only and non-resource schemes
/// pass through unchanged. Image links are never rewritten. The operation is
/// idempotent.
pub fn absolutize_markdown_links(markdown: &str, base_url: &str) -> String {
    if markdown.is_empty() || !markdown.contains('(') {
        return markdown.to_string();
    }
    let base = url::Url::parse(base_url).ok();
    MD_INLINE_LINK
        .replace_all(markdown, |caps: &regex::Captures| {
            let matched = caps.get(0).expect("regex capture group 0 must exist");
            let whole = matched.as_str();
            if matched.start() > 0 && markdown.as_bytes().get(matched.start() - 1) == Some(&b'!') {
                return whole.to_string();
            }
            let target = caps.get(2).map_or("", |m| m.as_str());
            let lowered = target.to_lowercase();
            if MD_NON_LINK_PREFIXES
                .iter()
                .any(|prefix| lowered.starts_with(prefix))
            {
                return whole.to_string();
            }
            let absolute = base
                .as_ref()
                .and_then(|base| base.join(target).ok())
                .map(|url| url.to_string())
                .unwrap_or_else(|| target.to_string());
            if absolute == target {
                return whole.to_string();
            }
            let text = caps.get(1).map_or("", |m| m.as_str());
            match caps.get(3) {
                Some(title) => format!("[{text}]({absolute} \"{}\")", title.as_str()),
                None => format!("[{text}]({absolute})"),
            }
        })
        .into_owned()
}

pub fn payload_from_markdown(markdown: &str, url: &str, _metadata: &Value, max_links: i64) -> Value {
    let links = follow_up_candidates(&Value::Array(Vec::new()), max_links);
    json!({
        "metadata": {"file_name": url, "format": "html", "canonical": url},
        "pages": [{"page_number": 1, "raw_text": markdown, "markdown": markdown, "tables": []}],
        "tables": [],
        "links": links,
        "guard": Value::Null,
    })
}


/// Normalize raw batch engine tuples into tagged entry payloads.
pub fn normalize_batch_results(results: &[Value]) -> Vec<Value> {
    fn as_array(value: &Value) -> Option<&Vec<Value>> {
        if let Some(array) = value.as_array() {
            return Some(array);
        }
        value.get("$tuple").and_then(Value::as_array)
    }
    results
        .iter()
        .filter_map(as_array)
        .map(|item| {
            let url = item.first().cloned().unwrap_or(Value::Null);
            let html = item.get(1).cloned().unwrap_or(Value::Null);
            let md = item.get(2).cloned().unwrap_or(Value::Null);
            let links = item.get(3).cloned().unwrap_or(Value::Null);
            let prov = item.get(4).cloned().unwrap_or(Value::Null);
            let success = !md.is_null() && !links.is_null();
            if success {
                serde_json::json!({
                    "url": url, "markdown": md, "links": links,
                    "error": Value::Null, "html": html, "prov": prov,
                })
            } else {
                let error = match md {
                    Value::String(text) if !text.is_empty() => Value::String(text),
                    _ => Value::String("Unknown error".to_string()),
                };
                serde_json::json!({
                    "url": url, "markdown": Value::Null, "links": Value::Null,
                    "error": error, "html": Value::Null, "prov": Value::Null,
                })
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn office_cells_collect_paragraph_runs() {
        let cell = json!({"content": [
            {"type": "paragraph", "content": [{"text": " a "}, {"text": "b"}]},
            {"type": "image", "content": [{"text": "ignored"}]}
        ]});
        assert_eq!(office_cell_text(&cell), "a b");
    }

    #[test]
    fn follow_up_candidates_dedupe_and_type() {
        let links = json!([
            ["https://x.test/a", " A "],
            ["https://x.test/a", "again"],
            ["https://x.test/report.pdf", ""],
            "not-a-pair"
        ]);
        let out = follow_up_candidates(&links, 0);
        assert_eq!(out.len(), 3);
        assert_eq!(out[0]["title"], "A");
        assert_eq!(out[1]["url"], "https://x.test/report.pdf");
        assert_eq!(out[1]["type"], "document");
        assert_eq!(out[2]["title"], "(untitled)");
    }

    #[test]
    fn markdown_absolutization_is_idempotent() {
        let once = absolutize_markdown_links("[A](/a) [B](child?x=1)", "https://x.test/dir/p");
        let twice = absolutize_markdown_links(&once, "https://x.test/dir/p");
        assert_eq!(
            once,
            "[A](https://x.test/a) [B](https://x.test/dir/child?x=1)"
        );
        assert_eq!(twice, once);
    }

    #[test]
    fn markdown_images_and_special_schemes_pass_through() {
        let input = "![img](/i.png) [M](mailto:a@b.c) [F](#frag)";
        assert_eq!(absolutize_markdown_links(input, "https://x.test/p"), input);
    }
}
