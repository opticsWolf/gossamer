//! Python-compatibility primitives used by core error messages.
//!
//! These are portable Rust functions; they reproduce CPython string spellings so error text stays
//! byte-for-byte compatible with the legacy implementation. Interpreter-dependent behavior is
//! passed in explicitly by callers rather than stored in a static (ADR-012, `M-AVOID-STATICS`).

/// Python `str.strip()` set: ASCII whitespace + 0x1C–0x1F + NEL + Unicode White_Space.
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

/// Minimal Python `repr()` for error messages: single quotes unless the string holds a single quote
/// but no double quote (CPython rule).
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repr_uses_cpython_quote_rule() {
        assert_eq!(py_repr("abc"), "'abc'");
        assert_eq!(py_repr("it's"), "\"it's\"");
        assert_eq!(py_repr("a\\b"), "'a\\\\b'");
    }
}
