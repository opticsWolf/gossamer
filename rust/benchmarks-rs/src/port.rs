//! Rust port of `benchmarks.py`.
//!
//! Timing and live-network benchmarks stay in Python: their output is wall-clock data, not a
//! deterministic compatibility contract. The labelled guard-corpus report is ported because it is
//! fixture-driven and can be replayed byte-for-byte.

use std::fs;
use std::path::{Path, PathBuf};

use gossamer::guard::chunk_text;
use port_error::{ErrorKind, PortError};
use serde_json::{Map, Value, json};

const STUB_MARKERS: [&str; 10] = [
    "ignore all previous instructions",
    "ignore your earlier instructions",
    "disregard your earlier instructions",
    "overriding everything above",
    "supersede the system prompt",
    "system notice",
    "jailbreak",
    "base64-encode",
    "do not mention this",
    "does not need to be mentioned",
];

const THRESHOLD: f64 = 0.7;
const CHUNK_CHARS: usize = 900;
const CHUNK_OVERLAP: usize = 120;
const MAX_CHUNKS: usize = 40;

type Result<T> = std::result::Result<T, PortError>;

fn unsupported(name: &str) -> String {
    format!(
        "NotImplementedError: {name} is wall-clock or network-bound; the front door falls back to Python"
    )
}

fn corpus_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .unwrap_or_else(|| Path::new("."))
        .join("tests")
        .join("fixtures")
        .join("guard_corpus")
}

fn universal_newlines(mut text: String) -> String {
    text = text.replace("\r\n", "\n");
    text.replace('\r', "\n")
}

fn markdown_files(folder: &Path) -> Result<Vec<PathBuf>> {
    let entries = fs::read_dir(folder).map_err(|e| {
        PortError::new(
            ErrorKind::Io,
            format!("cannot read guard corpus {}: {e}", folder.display()),
        )
    })?;
    let mut paths: Vec<PathBuf> = entries
        .filter_map(std::result::Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension().and_then(|ext| ext.to_str()) == Some("md") && path.is_file()
        })
        .collect();
    paths.sort();
    Ok(paths)
}

/// Loads the labelled guard corpus as `(name, injected, text)` rows.
pub fn load_guard_corpus_impl() -> Result<Vec<(String, bool, String)>> {
    let root = corpus_root();
    let mut out = Vec::new();
    for (label, injected) in [("benign", false), ("injected", true)] {
        for path in markdown_files(&root.join(label))? {
            let text = fs::read_to_string(&path)
                .map(universal_newlines)
                .map_err(|e| {
                    PortError::new(
                        ErrorKind::Io,
                        format!("cannot read {}: {e}", path.display()),
                    )
                })?;
            let name = format!(
                "{label}/{}",
                path.file_name().unwrap_or_default().to_string_lossy()
            );
            out.push((name, injected, text));
        }
    }
    Ok(out)
}

fn stub_score(text: &str) -> f64 {
    let low = text.to_lowercase();
    let hits = STUB_MARKERS
        .iter()
        .filter(|marker| low.contains(**marker))
        .count();
    if hits == 0 {
        0.02
    } else {
        (0.6 + 0.1 * hits as f64).min(0.99)
    }
}

fn scan(text: &str) -> (bool, f64) {
    let chunks = chunk_text(text, CHUNK_CHARS, CHUNK_OVERLAP, MAX_CHUNKS);
    let mut max_score = 0.0f64;
    let mut flagged = false;
    for (_offset, window) in chunks {
        let score = stub_score(&window);
        if score > max_score {
            max_score = score;
        }
        if score >= THRESHOLD {
            flagged = true;
        }
    }
    (flagged, max_score)
}

/// Structured result for `--corpus`; the binding layer formats it exactly like Python.
pub fn corpus_report() -> Result<Value> {
    let corpus = load_guard_corpus_impl()?;
    let mut rows = Vec::new();
    let (mut tp, mut fp, mut tn, mut fn_) = (0usize, 0usize, 0usize, 0usize);

    for (name, injected, text) in &corpus {
        let (flagged, max_score) = scan(text);
        if *injected && flagged {
            tp += 1;
        } else if *injected {
            fn_ += 1;
        } else if flagged {
            fp += 1;
        } else {
            tn += 1;
        }
        let mut row = Map::new();
        row.insert("name".to_owned(), json!(name));
        row.insert("injected".to_owned(), json!(injected));
        row.insert("flagged".to_owned(), json!(flagged));
        row.insert("max_score".to_owned(), json!(max_score));
        rows.push(Value::Object(row));
    }

    let benign = tn + fp;
    let inject = tp + fn_;
    let false_positive_rate = if benign == 0 {
        None
    } else {
        Some(fp as f64 / benign as f64)
    };
    let detection_rate = if inject == 0 {
        None
    } else {
        Some(tp as f64 / inject as f64)
    };

    Ok(json!({
        "backend": "stub detector (jailguard absent)",
        "rows": rows,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn_,
        "benign": benign,
        "inject": inject,
        "false_positive_rate": false_positive_rate,
        "detection_rate": detection_rate,
        "verdict": "suppressed -- stub backend: plumbing check, not detector accuracy",
    }))
}

/// Port of `arrow` (`benchmarks.py:50`). The original prints and returns `None`.
pub fn arrow(label: String, value: String) -> std::result::Result<Value, String> {
    let _ = (label, value);
    Ok(Value::Null)
}

/// Port of `bench_rust_fetch` (`benchmarks.py:59`). Intentionally unsupported in the deterministic port.
pub fn bench_rust_fetch() -> std::result::Result<Value, String> {
    Err(unsupported("bench_rust_fetch"))
}

/// Port of `bench_meta_oxide` (`benchmarks.py:99`). Intentionally unsupported in the deterministic port.
pub fn bench_meta_oxide() -> std::result::Result<Value, String> {
    Err(unsupported("bench_meta_oxide"))
}

/// Port of `bench_token_budget` (`benchmarks.py:133`). Intentionally unsupported in the deterministic port.
pub fn bench_token_budget() -> std::result::Result<Value, String> {
    Err(unsupported("bench_token_budget"))
}

/// Port of `bench_batch_fetch` (`benchmarks.py:154`). Intentionally unsupported in the deterministic port.
pub fn bench_batch_fetch() -> std::result::Result<Value, String> {
    Err(unsupported("bench_batch_fetch"))
}

/// Port of `bench_structured_parser` (`benchmarks.py:181`). Intentionally unsupported in the deterministic port.
pub fn bench_structured_parser() -> std::result::Result<Value, String> {
    Err(unsupported("bench_structured_parser"))
}

/// Port of `load_guard_corpus` (`benchmarks.py:255`).
pub fn load_guard_corpus() -> std::result::Result<Value, String> {
    load_guard_corpus_impl()
        .map(|rows| {
            Value::Array(
                rows.into_iter()
                    .map(|(name, injected, text)| json!({"$tuple": [name, injected, text]}))
                    .collect(),
            )
        })
        .map_err(|e| format!("ValueError: {}", e.message()))
}

/// Port of `bench_guard` (`benchmarks.py:279`). Intentionally unsupported in the deterministic port.
pub fn bench_guard() -> std::result::Result<Value, String> {
    Err(unsupported("bench_guard"))
}

/// Port of `bench_guard_corpus` (`benchmarks.py:309`).
pub fn bench_guard_corpus() -> std::result::Result<Value, String> {
    let report = corpus_report().map_err(|e| format!("ValueError: {}", e.message()))?;
    Ok(json!({
        "tp": report["tp"].clone(),
        "fp": report["fp"].clone(),
        "tn": report["tn"].clone(),
        "fn": report["fn"].clone(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stub_marker_scoring_matches_python() {
        assert_eq!(stub_score("clean text"), 0.02);
        assert_eq!(stub_score("This is a JAILBREAK"), 0.7);
        assert_eq!(stub_score("jailbreak and system notice"), 0.8);
    }

    #[test]
    fn corpus_report_is_structured() {
        let report = corpus_report().expect("corpus fixture");
        assert_eq!(report["backend"], json!("stub detector (jailguard absent)"));
        assert_eq!(report["fp"], json!(1));
        assert_eq!(report["fn"], json!(0));
        assert_eq!(report["rows"].as_array().map_or(0, Vec::len), 10);
    }
}
