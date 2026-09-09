//! Pure kernels for the stateful cache / resource store (M20).
//!
//! The `Cache` and `ResourceStore` *objects* stay Python (locks, file
//! I/O, wall-clock TTL, byte payloads — the scope split keeps stateful
//! orchestration where it is). What ports here are the pure,
//! byte-exact helpers both sides must agree on so the on-disk layout
//! stays readable whichever side wrote it: the filename derivation,
//! the human-size formatting, the content-type sniffing, and the asset
//! slugger. Magic-byte sniffing stays Python (bytes do not cross the
//! JSON-string boundary).

use blake2::digest::Digest;
use blake2::Blake2b128;
use regex::Regex;
use std::sync::OnceLock;

/// `Cache._disk_key`: blake2b with a 16-byte digest, hex-encoded.
/// (Fixed-output `Blake2b128` — truncating a full blake2b-512 would
/// give a *different* digest, since the length is part of the
/// parameter block.)
pub fn cache_disk_key_impl(key: &str) -> String {
    let mut h = Blake2b128::new();
    Digest::update(&mut h, key.as_bytes());
    hex::encode(&Digest::finalize(h))
}

/// `Cache._human_size`: `f"{n:.1f} {unit}"` climbing B/KB/MB/GB/TB.
/// Python `.1f` and Rust `{:.1}` both correctly round the true binary
/// value, so outputs agree (fuzz-verified) — except `NaN`, which
/// Python renders `nan` and Rust renders `NaN`.
fn fmt_1f(v: f64) -> String {
    if v.is_nan() {
        "nan".to_string()
    } else {
        format!("{v:.1}")
    }
}

pub fn cache_human_size_impl(nbytes: f64) -> String {
    let mut n = nbytes;
    for unit in ["B", "KB", "MB", "GB"] {
        if n < 1024.0 {
            return format!("{} {unit}", fmt_1f(n));
        }
        n /= 1024.0;
    }
    format!("{} TB", fmt_1f(n))
}

/// `_detect_ext_from_content_type`: `image/svg+xml` -> `svg`, other
/// `image/*` -> the bare subtype (even odd ones — no validation), else
/// None. `None` crosses as JSON null.
pub fn resource_ext_from_content_type_impl(ctype: &str) -> Option<String> {
    let base = ctype.split(';').next().unwrap_or("").trim().to_lowercase();
    if base == "image/svg+xml" {
        return Some("svg".to_string());
    }
    if let Some(sub) = base.strip_prefix("image/") {
        if !sub.is_empty() {
            return Some(sub.to_string());
        }
    }
    None
}

fn slug_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^A-Za-z0-9._-]+").unwrap())
}

/// `_safe_name`: slug the base (runs of unsafe chars -> `-`, strip
/// `-._` ends, first 80 chars, `asset` fallback) and append `.ext`
/// when set.
pub fn resource_safe_name_impl(base: &str, ext: &str) -> String {
    let slug = slug_re().replace_all(base, "-");
    let slug = slug.trim_matches(['-', '.', '_']);
    let head = crate::pycompat::char_head(slug, 80);
    let slug = if head.is_empty() { "asset" } else { head };
    if ext.is_empty() {
        slug.to_string()
    } else {
        format!("{slug}.{ext}")
    }
}

/// Minimal hex encoding (avoids a one-use `hex` dependency).
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
        // 32 hex chars (16 bytes); stable for one input.
        let k = cache_disk_key_impl("https://example.com/x");
        assert_eq!(k.len(), 32);
        assert!(k.chars().all(|c| c.is_ascii_hexdigit()));
        assert_eq!(k, cache_disk_key_impl("https://example.com/x"));
        assert_ne!(k, cache_disk_key_impl("https://example.com/y"));
        assert_eq!(cache_disk_key_impl("").len(), 32);
    }

    #[test]
    fn human_size_shapes() {
        assert_eq!(cache_human_size_impl(0.0), "0.0 B");
        assert_eq!(cache_human_size_impl(1023.0), "1023.0 B");
        assert_eq!(cache_human_size_impl(1024.0), "1.0 KB");
        assert_eq!(
            cache_human_size_impl((5 * 1024 * 1024) as f64),
            "5.0 MB"
        );
        assert_eq!(cache_human_size_impl(3.0 * 1024.0 * 1024.0 * 1024.0), "3.0 GB");
        assert_eq!(
            cache_human_size_impl(2.0 * 1024.0 * 1024.0 * 1024.0 * 1024.0),
            "2.0 TB"
        );
        assert_eq!(cache_human_size_impl(f64::NAN), "nan TB");
        assert_eq!(cache_human_size_impl(f64::INFINITY), "inf TB");
        assert_eq!(cache_human_size_impl(f64::NEG_INFINITY), "-inf B");
    }

    #[test]
    fn ext_and_name_shapes() {
        assert_eq!(
            resource_ext_from_content_type_impl("image/svg+xml"),
            Some("svg".to_string())
        );
        assert_eq!(
            resource_ext_from_content_type_impl("Image/PNG; charset=x"),
            Some("png".to_string())
        );
        assert_eq!(resource_ext_from_content_type_impl("image/"), None);
        assert_eq!(resource_ext_from_content_type_impl("text/html"), None);
        assert_eq!(resource_ext_from_content_type_impl(""), None);
        assert_eq!(resource_safe_name_impl("a/b?c", "png"), "a-b-c.png");
        assert_eq!(resource_safe_name_impl("---", "png"), "asset.png");
        assert_eq!(resource_safe_name_impl("x", ""), "x");
        assert_eq!(resource_safe_name_impl("", ""), "asset");
    }
}

use pyo3::prelude::*;

#[pyfunction]
pub fn cache_disk_key(key: &str) -> String {
    cache_disk_key_impl(key)
}

#[pyfunction]
pub fn cache_human_size(nbytes: f64) -> String {
    cache_human_size_impl(nbytes)
}

#[pyfunction]
pub fn resource_ext_from_content_type(ctype: &str) -> Option<String> {
    resource_ext_from_content_type_impl(ctype)
}

#[pyfunction]
pub fn resource_safe_name(base: &str, ext: &str) -> String {
    resource_safe_name_impl(base, ext)
}
