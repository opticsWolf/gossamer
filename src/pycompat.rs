//! Python-compatibility string primitives shared by ported modules.
//!
//! Python `str` semantics that Rust does not provide out of the box:
//! `strip()`-set trimming, char-based (never byte-based) slicing, and
//! char-index measurement. Centralized so every port behaves identically.

/// Python `str.strip()` set: ASCII whitespace + 0x1C–0x1F + NEL +
/// everything Unicode-White_Space. (Differs from Rust `.trim()` on
/// 0x1C–0x1F and U+0085.)
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(|c: char| {
        c.is_whitespace() || c == '\u{85}' || ('\u{1C}'..='\u{1F}').contains(&c)
    })
}

/// `s[:n]` by chars, clamped (Python slicing never raises).
pub fn char_head(s: &str, n: usize) -> &str {
    match s.char_indices().nth(n) {
        Some((i, _)) => &s[..i],
        None => s,
    }
}

/// Python `" ".join(text.split())`: split on runs of Python whitespace
/// (`str.split()` with no separator — Unicode White_Space plus
/// 0x1C–0x1F and NEL, which Rust `split_whitespace` misses) and
/// rejoin with single spaces.
pub fn py_collapse_ws(s: &str) -> String {
    s.split(|c: char| c.is_whitespace() || c == '\u{85}' || ('\u{1C}'..='\u{1F}').contains(&c))
        .filter(|w| !w.is_empty())
        .collect::<Vec<&str>>()
        .join(" ")
}

/// Minor version of the running interpreter, recorded once by
/// `#[pymodule]` at import. A handful of CPython error spellings
/// changed across versions (`d[slice]`, `re.sub` and `s[str]`
/// messages); kernels gate on this so one binary matches whatever
/// interpreter imports it. `None` outside a live interpreter
/// (Rust unit tests) follows the newest spelling.
static RUNTIME_MINOR: std::sync::OnceLock<u32> = std::sync::OnceLock::new();

pub fn note_runtime_minor(minor: u32) {
    let _ = RUNTIME_MINOR.set(minor);
}

pub fn runtime_minor() -> Option<u32> {
    RUNTIME_MINOR.get().copied()
}

