//! Pure kernels for the cache and resource store.
//!
//! Stateful cache/resource objects stay in Python (locks, file I/O, wall-clock TTLs, byte payloads).
//! This module owns the deterministic helpers both sides must agree on: disk keys, human sizes,
//! content-type sniffing, and asset slugs.

use std::sync::OnceLock;

use blake2::Blake2b128;
use blake2::digest::Digest;
use regex::Regex;

use crate::compat::char_head;

const SLUG_PATTERN: &str = r"[^A-Za-z0-9._-]+";

fn slug_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // Performance-only static: the compiled regex is immutable (M-AVOID-STATICS).
    RE.get_or_init(|| {
        Regex::new(SLUG_PATTERN)
            .unwrap_or_else(|e| panic!("asset slug regex {SLUG_PATTERN:?} must compile: {e}"))
    })
}

/// Returns the Blake2b-128 hex disk key for a cache key.
///
/// # Examples
///
/// ```
/// use gossamer::cacheutils::cache_disk_key;
///
/// let key = cache_disk_key("https://example.com/x");
/// assert_eq!(key.len(), 32);
/// ```
pub fn cache_disk_key(key: &str) -> String {
    let mut h = Blake2b128::new();
    Digest::update(&mut h, key.as_bytes());
    hex::encode(&Digest::finalize(h))
}

fn fmt_1f(v: f64) -> String {
    if v.is_nan() {
        "nan".to_string()
    } else {
        format!("{v:.1}")
    }
}

/// Formats a byte count as `f"{n:.1f} {unit}"` climbing B/KB/MB/GB/TB.
///
/// # Examples
///
/// ```
/// use gossamer::cacheutils::cache_human_size;
///
/// assert_eq!(cache_human_size(1024.0), "1.0 KB");
/// ```
pub fn cache_human_size(nbytes: f64) -> String {
    let mut n = nbytes;
    for unit in ["B", "KB", "MB", "GB"] {
        if n < 1024.0 {
            return format!("{} {unit}", fmt_1f(n));
        }
        n /= 1024.0;
    }
    format!("{} TB", fmt_1f(n))
}

/// Sniffs an image extension from an HTTP content type.
///
/// `image/svg+xml` → `svg`; other `image/*` → the bare subtype; anything else → `None`.
///
/// # Examples
///
/// ```
/// use gossamer::cacheutils::resource_ext_from_content_type;
///
/// assert_eq!(resource_ext_from_content_type("Image/PNG; charset=x").as_deref(), Some("png"));
/// assert_eq!(resource_ext_from_content_type("text/html"), None);
/// ```
pub fn resource_ext_from_content_type(ctype: &str) -> Option<String> {
    let base = ctype.split(';').next().unwrap_or("").trim().to_lowercase();
    if base == "image/svg+xml" {
        return Some("svg".to_string());
    }
    if let Some(sub) = base.strip_prefix("image/")
        && !sub.is_empty()
    {
        return Some(sub.to_string());
    }
    None
}

/// Slugs a base name and appends an optional extension.
///
/// Unsafe runs become `-`, leading/trailing `-._` are stripped, the result is truncated to 80
/// characters, and an empty slug falls back to `asset`.
///
/// # Examples
///
/// ```
/// use gossamer::cacheutils::resource_safe_name;
///
/// assert_eq!(resource_safe_name("a/b?c", "png"), "a-b-c.png");
/// assert_eq!(resource_safe_name("---", "png"), "asset.png");
/// ```
pub fn resource_safe_name(base: &str, ext: &str) -> String {
    let slug = slug_re().replace_all(base, "-");
    let slug = slug.trim_matches(['-', '.', '_']);
    let head = char_head(slug, 80);
    let slug = if head.is_empty() { "asset" } else { head };
    if ext.is_empty() {
        slug.to_string()
    } else {
        format!("{slug}.{ext}")
    }
}

mod hex {
    pub fn encode(bytes: &[u8]) -> String {
        const LUT: &[u8; 16] = b"0123456789abcdef";
        let mut out = String::with_capacity(bytes.len() * 2);
        for b in bytes {
            out.push(LUT[(b >> 4) as usize] as char);
            out.push(LUT[(b & 15) as usize] as char);
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disk_key_shape() {
        let k = cache_disk_key("https://example.com/x");
        assert_eq!(k.len(), 32);
        assert!(k.chars().all(|c| c.is_ascii_hexdigit()));
        assert_eq!(k, cache_disk_key("https://example.com/x"));
        assert_ne!(k, cache_disk_key("https://example.com/y"));
        assert_eq!(cache_disk_key("").len(), 32);
    }

    #[test]
    fn human_size_shapes() {
        assert_eq!(cache_human_size(0.0), "0.0 B");
        assert_eq!(cache_human_size(1023.0), "1023.0 B");
        assert_eq!(cache_human_size(1024.0), "1.0 KB");
        assert_eq!(cache_human_size((5 * 1024 * 1024) as f64), "5.0 MB");
        assert_eq!(cache_human_size(3.0 * 1024.0 * 1024.0 * 1024.0), "3.0 GB");
        assert_eq!(
            cache_human_size(2.0 * 1024.0 * 1024.0 * 1024.0 * 1024.0),
            "2.0 TB"
        );
        assert_eq!(cache_human_size(f64::NAN), "nan TB");
        assert_eq!(cache_human_size(f64::INFINITY), "inf TB");
        assert_eq!(cache_human_size(f64::NEG_INFINITY), "-inf B");
    }

    #[test]
    fn ext_and_name_shapes() {
        assert_eq!(
            resource_ext_from_content_type("image/svg+xml"),
            Some("svg".to_string())
        );
        assert_eq!(
            resource_ext_from_content_type("Image/PNG; charset=x"),
            Some("png".to_string())
        );
        assert_eq!(resource_ext_from_content_type("image/"), None);
        assert_eq!(resource_ext_from_content_type("text/html"), None);
        assert_eq!(resource_ext_from_content_type(""), None);
        assert_eq!(resource_safe_name("a/b?c", "png"), "a-b-c.png");
        assert_eq!(resource_safe_name("---", "png"), "asset.png");
        assert_eq!(resource_safe_name("x", ""), "x");
        assert_eq!(resource_safe_name("", ""), "asset");
    }
}
