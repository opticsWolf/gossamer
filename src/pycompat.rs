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

/// Char count (`len(s)` in Python).
pub fn char_count(s: &str) -> usize {
    s.chars().count()
}

/// `s[:n]` by chars, clamped (Python slicing never raises).
pub fn char_head(s: &str, n: usize) -> &str {
    match s.char_indices().nth(n) {
        Some((i, _)) => &s[..i],
        None => s,
    }
}

/// `s[a:b]` by chars, clamped.
pub fn char_slice(s: &str, start: usize, end: usize) -> &str {
    let bytes: Vec<(usize, char)> = s.char_indices().collect();
    let n = bytes.len();
    let b = match bytes.get(start.min(n)) {
        Some((i, _)) => *i,
        None => s.len(),
    };
    let e = match bytes.get(end.min(n)) {
        Some((i, _)) => *i,
        None => s.len(),
    };
    if e < b {
        &s[b..b]
    } else {
        &s[b..e]
    }
}

/// Byte offset → char index (regex crate yields byte spans; Python
/// `re` on `str` yields char spans).
pub fn byte_to_char_idx(text: &str, byte: usize) -> usize {
    text[..byte.min(text.len())].chars().count()
}

/// Minimal Python-`repr()` for error messages: single quotes unless the
/// string holds a single quote but no double quote (CPython rule).
pub fn py_repr(s: &str) -> String {
    let (open, close, escape_single) = if s.contains('\'') && !s.contains('"') {
        ('"', '"', false)
    } else {
        ('\'', '\'', true)
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(open);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\'' if escape_single => out.push_str("\\'"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if !is_py_printable(c) => {
                let n = c as u32;
                if n < 0x100 {
                    out.push_str(&format!("\\x{n:02x}"));
                } else if n < 0x10000 {
                    out.push_str(&format!("\\u{n:04x}"));
                } else {
                    out.push_str(&format!("\\U{n:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(close);
    out
}

fn is_py_printable(c: char) -> bool {
    if (c as u32) < 0x20 || (c as u32) == 0x7f {
        return false;
    }
    !matches!(c, '\u{80}'..='\u{9f}')
}

/// Python `repr()` of a string list: `['a', 'b']` (single quotes).
pub fn py_list_repr(items: &[String]) -> String {
    let inner: Vec<String> = items.iter().map(|s| py_repr(s)).collect();
    format!("[{0}]", inner.join(", "))
}
