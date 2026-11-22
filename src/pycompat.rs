//! Runtime interpreter version used for CPython-version-specific error spellings.

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
