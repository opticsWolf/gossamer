//! Deterministic resource-store helpers.

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
