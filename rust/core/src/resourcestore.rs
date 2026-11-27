//! Deterministic resource-store helpers.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::{cacheutils::resource_safe_name, urlparts::join_url};

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

/// Filename decision for one downloaded resource.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredResource {
    /// Canonical on-disk filename.
    pub filename: String,
    /// First 12 hex digits of the SHA-256 digest.
    pub digest: String,
}

fn canonical_image_ext(ext: &str) -> Option<&'static str> {
    Some(match ext.to_ascii_lowercase().as_str() {
        "png" => "png",
        "jpg" | "jpeg" => "jpg",
        "gif" => "gif",
        "webp" => "webp",
        "bmp" => "bmp",
        "svg" => "svg",
        "tiff" | "tif" => "tiff",
        _ => return None,
    })
}

/// Plans the filename for one fetched image without touching the filesystem.
///
/// Returns `None` for non-image URLs, private URLs, empty/oversized bodies, and
/// unknown image formats, matching `ResourceStore._store_url`.
pub fn store_url_decision(
    url: &str,
    content_type: &str,
    data: &[u8],
    max_bytes: usize,
    seen_filenames: &[String],
) -> Option<StoredResource> {
    if !(url.starts_with("http://") || url.starts_with("https://") || url.starts_with("ftp://")) {
        return None;
    }
    let allow_private = [
        "GOSSAMER_ALLOW_PRIVATE",
        "STITCH_WEB_RESEARCHER_ALLOW_PRIVATE",
    ]
    .iter()
    .any(|name| {
        std::env::var(name).is_ok_and(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
    });
    if !allow_private && crate::ssrf::check_url(url, false, None).is_err() {
        return None;
    }
    if data.is_empty() || data.len() > max_bytes {
        return None;
    }
    let detected = crate::cacheutils::resource_ext_from_content_type(content_type)
        .or_else(|| detect_ext_from_magic(data).map(str::to_string))?;
    let ext = canonical_image_ext(&detected)?;
    let digest = crate::miscutils::sha256_bytes(data)[..12].to_string();
    let base = url.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    let stem = base.rfind('.').map(|index| &base[..index]).unwrap_or(base);
    let stem = if stem.is_empty() { "image" } else { stem };
    let mut candidate = resource_safe_name(stem, ext);
    let mut n = 1usize;
    while seen_filenames.iter().any(|name| name == &candidate) {
        candidate = format!("{stem}-{n}-{digest}.{ext}");
        n += 1;
    }
    Some(StoredResource {
        filename: candidate,
        digest,
    })
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
    let Some(refs) = refs.as_array() else {
        return serde_json::json!({"markdown": "", "referenced": 0, "files": [], "skipped": []});
    };
    for reference in refs {
        let whole = reference.get("whole").and_then(Value::as_str).unwrap_or("");
        let resolved = reference.get("resolved").cloned().unwrap_or(Value::Null);
        if resolved.is_null() {
            continue;
        }
        let Some(url) = resolved.as_str() else {
            continue;
        };
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
        if !files.contains(&file) {
            files.push(file);
        }
    }
    files.sort();
    serde_json::json!({
        "markdown": rewritten,
        "referenced": count,
        "files": files,
        "skipped": skipped,
    })
}

/// Plan the embedded-figure manifest without touching the filesystem.
pub fn embedded_figures(markdown: &str, stem: &str, images: &[Value]) -> Value {
    let mut injected: Vec<(usize, String)> = Vec::new();
    let mut written: Vec<String> = Vec::new();
    for (index, image) in images.iter().enumerate() {
        let Some(object) = image.as_object() else {
            continue;
        };
        let Some(data) = object.get("data") else {
            continue;
        };
        let bytes = data
            .as_array()
            .or_else(|| data.get("$bytes").and_then(Value::as_array));
        let Some(bytes) = bytes else {
            continue;
        };
        if bytes.is_empty() {
            continue;
        }
        let raw: Vec<u8> = bytes
            .iter()
            .filter_map(Value::as_u64)
            .map(|b| b.min(255) as u8)
            .collect();
        let detected = detect_ext_from_magic(&raw).unwrap_or("img").to_string();
        let declared = object
            .get("ext")
            .and_then(Value::as_str)
            .unwrap_or(&detected);
        let ext = match declared.strip_prefix('.').unwrap_or(declared) {
            "jpeg" => "jpg",
            "jpg" => "jpg",
            "png" => "png",
            "gif" => "gif",
            "webp" => "webp",
            "bmp" => "bmp",
            "svg" => "svg",
            "tiff" | "tif" => "tiff",
            other => other,
        };
        let name = object
            .get("filename")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(|| resource_safe_name(&format!("figure-{}", index + 1), ext));
        if !written.contains(&name) {
            written.push(name.clone());
        }
        injected.push((index + 1, name));
    }
    let mut md = markdown.to_string();
    if !injected.is_empty() {
        md = format!(
            "{}

## Figures

",
            md.trim_end()
        );
        for (index, name) in &injected {
            md.push_str(&format!(
                "![figure {}](./{}.files/{})

",
                index, stem, name
            ));
        }
    }
    let files: Vec<String> = written
        .iter()
        .map(|name| format!("{stem}.files/{name}"))
        .collect();
    serde_json::json!({
        "markdown": md,
        "embedded": injected.len(),
        "files": files,
        "written": written,
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
