//! Deterministic resource-store helpers.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::urlparts::join_url;

/// Canonical image suffix from magic bytes, or `None` when unknown.
pub fn detect_ext_from_magic(data: &[u8]) -> Option<&'static str> {
    const MAGIC: [(&[u8], &str); 5] = [
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff", "jpg"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b"BM", "bmp"),
    ];
    for (magic, ext) in MAGIC {
        if data.starts_with(magic) {
            return Some(ext);
        }
    }
    if data.len() >= 12 && &data[..4] == b"RIFF" && &data[8..12] == b"WEBP" {
        return Some("webp");
    }
    None
}


/// Markdown inline image matcher, mirroring the Python regex exactly.
static MD_IMG_ANY_RE: LazyLock<Regex> = LazyLock::new(|| {
    let pattern = r#"!\[([^\]]*)\]\(\s*([^)\s"']+)(?:\s+"([^"]*)")?\s*\)"#;
    Regex::new(pattern).unwrap_or_else(|e| panic!("invalid image regex {pattern:?}: {e}"))
});

/// Extract image references with their resolved URLs.
pub fn image_refs(markdown: &str, base_url: &str) -> Vec<Value> {
    MD_IMG_ANY_RE.captures_iter(markdown).map(|caps| {
        let target = caps.get(2).map(|m| m.as_str()).unwrap_or("");
        let valid = !(target.starts_with("./") || target.starts_with('#'))
            && !["mailto:", "tel:", "data:", "javascript:"].iter().any(|p| target.starts_with(p));
        let resolved = if !valid {
            Value::Null
        } else if ["http://", "https://", "ftp://"].iter().any(|p| target.starts_with(p)) {
            Value::String(target.to_string())
        } else {
            Value::String(join_url(base_url, target))
        };
        serde_json::json!({
            "whole": caps.get(0).map(|m| m.as_str()).unwrap_or(""),
            "alt": caps.get(1).map(|m| m.as_str()).unwrap_or(""),
            "target": target,
            "title": caps.get(3).map(|m| Value::String(m.as_str().to_string())).unwrap_or(Value::Null),
            "resolved": resolved,
            "resolvable": !resolved.is_null(),
        })
    }).collect()
}

/// Rewrite image references using caller-supplied local filenames.
pub fn rewrite_image_refs(refs: &Value, markdown: &str, stem: &str, resolutions: &Value) -> Value {
    let mut rewritten = markdown.to_string();
    let mut skipped: Vec<Value> = Vec::new();
    let mut count = 0usize;
    let mut files: Vec<String> = Vec::new();
    let Some(refs) = refs.as_array() else { return serde_json::json!({"markdown": "", "referenced": 0, "files": [], "skipped": []}); };
    for reference in refs {
        let whole = reference.get("whole").and_then(Value::as_str).unwrap_or("");
        let resolved = reference.get("resolved").cloned().unwrap_or(Value::Null);
        if resolved.is_null() { continue; }
        let Some(url) = resolved.as_str() else { continue; };
        let local = resolutions.get(url).cloned().unwrap_or(Value::Null);
        let Some(name) = local.as_str() else {
            skipped.push(serde_json::json!({"url": url, "reason": "skipped"}));
            continue;
        };
        let rel = format!("./{stem}.files/{name}");
        let alt = reference.get("alt").and_then(Value::as_str).unwrap_or("");
        let replacement = match reference.get("title").and_then(Value::as_str) {
            Some(title) if !title.is_empty() => format!("![{alt}]({rel} \"{title}\")"),
            _ => format!("![{alt}]({rel})"),
        };
        if let Some(position) = rewritten.find(whole) {
            rewritten.replace_range(position..position + whole.len(), &replacement);
        }
        count += 1;
        let file = format!("{stem}.files/{name}");
        if !files.contains(&file) { files.push(file); }
    }
    files.sort();
    serde_json::json!({
        "markdown": rewritten,
        "referenced": count,
        "files": files,
        "skipped": skipped,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn magic() {
        assert_eq!(detect_ext_from_magic(b"\x89PNG\r\n\x1a\nxxxx"), Some("png"));
        assert_eq!(detect_ext_from_magic(b"RIFFxxxxWEBP"), Some("webp"));
        assert_eq!(detect_ext_from_magic(b"xxxx"), None);
    }
}
