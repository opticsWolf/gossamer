//! Rust port of `benchmarks.py`.
//!
//! Each benchmark returns structured measurements; the front door formats them exactly like the
//! original. Timing is inherently variable, so app replay normalizes numeric timing fields while
//! all deterministic values (counts, verdicts, rates, skip reasons) are compared exactly.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use gossamer::fetch::{fetch_and_extract_single, fetch_many, max_response_bytes};
use gossamer::guard::chunk_text;
use gossamer::metaextract::meta_extract_all;
use gossamer::tokens::{count_tokens, default_ellipsis, truncate_to_tokens};
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
const BENCH_URL: &str = "https://example.com";

const SAMPLE_HTML: &str = r#"
<html>
<head>
    <title>Benchmark Page</title>
    <meta name="description" content="A page for benchmarking metadata extraction.">
    <meta property="og:title" content="OG Benchmark Title">
    <meta property="og:type" content="article">
    <meta property="og:image" content="https://example.com/og.png">
    <meta property="og:description" content="OG description text.">
    <meta property="og:site_name" content="Example Site">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Twitter Title">
    <meta name="twitter:description" content="Twitter description.">
    <meta name="twitter:image" content="https://example.com/tw.png">
    <link rel="canonical" href="https://example.com/bench">
    <script type="application/ld+json">
    {"@type":"Article","headline":"JSON-LD Headline","author":{"@type":"Person","name":"Jane Doe"}}
    </script>
</head>
<body>
    <div class="h-card"><span class="p-name">Card Person</span></div>
</body>
</html>
"#;

type Result<T> = std::result::Result<T, PortError>;

#[derive(Debug, Clone)]
struct Timing {
    avg_ms: f64,
    median_ms: f64,
    p95_ms: f64,
}

fn mean(values: &[f64]) -> f64 {
    values.iter().sum::<f64>() / values.len() as f64
}

fn median(values: &[f64]) -> f64 {
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    let mid = ordered.len() / 2;
    if ordered.len() % 2 == 1 {
        ordered[mid]
    } else {
        (ordered[mid - 1] + ordered[mid]) / 2.0
    }
}

fn timed<T, F: FnMut() -> T>(mut f: F, runs: usize, warmup: usize) -> (Timing, Option<T>) {
    let mut last = None;
    for _ in 0..warmup {
        last = Some(f());
    }
    let mut times = Vec::with_capacity(runs);
    for _ in 0..runs {
        let start = Instant::now();
        last = Some(f());
        times.push(start.elapsed().as_secs_f64() * 1000.0);
    }
    let mut p95 = times.clone();
    p95.sort_by(f64::total_cmp);
    let idx = (p95.len() as f64 * 0.95) as usize;
    (
        Timing {
            avg_ms: mean(&times),
            median_ms: median(&times),
            p95_ms: p95[idx.min(p95.len() - 1)],
        },
        last,
    )
}

fn py_round(value: f64) -> f64 {
    let floor = value.floor();
    if value - floor == 0.5 {
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    } else {
        value.round()
    }
}

fn percentile(values: &[f64], q: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    let idx = py_round(q / 100.0 * (ordered.len() - 1) as f64) as usize;
    ordered[idx.min(ordered.len() - 1)]
}

fn timing_json(t: &Timing) -> Value {
    json!({
        "avg_ms": t.avg_ms,
        "median_ms": t.median_ms,
        "p95_ms": t.p95_ms,
    })
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

///
/// # Errors
///
/// Returns `PortError` when fixtures, network, or parser inputs cannot be processed.
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
    let mut max_score = 0.0f64;
    let mut flagged = false;
    for (_offset, window) in chunk_text(text, CHUNK_CHARS, CHUNK_OVERLAP, MAX_CHUNKS) {
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

///
/// # Errors
///
/// Returns `PortError` when fixtures, network, or parser inputs cannot be processed.
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

///
/// # Errors
///
/// Returns `PortError` when fixtures, network, or parser inputs cannot be processed.
/// Structured result for `bench_rust_fetch`.
pub fn rust_fetch_report() -> Result<Value> {
    let cap = max_response_bytes();
    let (timing, outcome) = timed(|| fetch_and_extract_single(BENCH_URL, cap), 5, 1);
    let (markdown, links) = outcome
        .ok_or_else(|| {
            PortError::new(
                ErrorKind::Io,
                "fetch benchmark produced no result".to_owned(),
            )
        })?
        .map_err(|e| PortError::new(ErrorKind::Io, e.message().to_owned()))?;
    let mut out = timing_json(&timing);
    let map = out.as_object_mut().expect("timing_json returns an object");
    map.insert("chars".to_owned(), json!(markdown.chars().count()));
    map.insert("links".to_owned(), json!(links.len()));
    Ok(out)
}

///
/// # Errors
///
/// Returns `PortError` when fixtures, network, or parser inputs cannot be processed.
/// Structured result for `bench_meta_oxide`.
pub fn meta_oxide_report() -> Result<Value> {
    let (timing, outcome) = timed(|| meta_extract_all(SAMPLE_HTML, Some(BENCH_URL)), 20, 1);
    let raw = outcome.ok_or_else(|| {
        PortError::new(
            ErrorKind::Value,
            "metadata benchmark produced no result".to_owned(),
        )
    })?;
    let parsed: Value = serde_json::from_str(&raw).map_err(|e| {
        PortError::new(
            ErrorKind::Value,
            format!("metadata benchmark returned invalid JSON: {e}"),
        )
    })?;
    let keys = parsed.as_object().map_or(0, Map::len);
    let mut out = timing_json(&timing);
    let map = out.as_object_mut().expect("timing_json returns an object");
    map.insert("keys".to_owned(), json!(keys));
    Ok(out)
}

///
/// # Errors
///
/// Returns `PortError` when fixtures, network, or parser inputs cannot be processed.
/// Structured result for `bench_token_budget`.
pub fn token_budget_report() -> Result<Value> {
    let long_text = "The quick brown fox jumps over the lazy dog. ".repeat(5000);
    let (count_timing, count_outcome) = timed(|| count_tokens(&long_text, "gpt-4o"), 5, 1);
    let tokens = count_outcome
        .ok_or_else(|| {
            PortError::new(
                ErrorKind::Value,
                "token count produced no result".to_owned(),
            )
        })?
        .map_err(|e| PortError::new(ErrorKind::Value, e.message().to_owned()))?;

    let (truncate_timing, truncate_outcome) = timed(
        || truncate_to_tokens(&long_text, 500, "gpt-4o", default_ellipsis()),
        5,
        1,
    );
    let truncated = truncate_outcome
        .ok_or_else(|| {
            PortError::new(
                ErrorKind::Value,
                "token truncation produced no result".to_owned(),
            )
        })?
        .map_err(|e| PortError::new(ErrorKind::Value, e.message().to_owned()))?;
    let truncated_tokens = count_tokens(&truncated, "gpt-4o").map_err(|e| {
        PortError::new(
            ErrorKind::Value,
            format!("token recount failed: {}", e.message()),
        )
    })?;

    Ok(json!({
        "count": timing_json(&count_timing),
        "tokens": tokens,
        "truncate": timing_json(&truncate_timing),
        "chars": truncated.chars().count(),
        "truncated_tokens": truncated_tokens,
    }))
}

///
/// # Errors
///
/// Returns `PortError` when fixtures, network, or parser inputs cannot be processed.
/// Structured result for `bench_batch_fetch`.
pub fn batch_fetch_report() -> Result<Value> {
    let cap = max_response_bytes();
    let urls = vec![BENCH_URL.to_owned(); 5];
    let seq_start = Instant::now();
    for url in &urls {
        fetch_and_extract_single(url, cap).map_err(|e| {
            PortError::new(
                ErrorKind::Io,
                format!("sequential fetch failed: {}", e.message()),
            )
        })?;
    }
    let seq_ms = seq_start.elapsed().as_secs_f64() * 1000.0;

    let batch_start = Instant::now();
    let results = fetch_many(urls, 500, 8, 0, cap);
    let batch_ms = batch_start.elapsed().as_secs_f64() * 1000.0;
    let successes = results.len();

    Ok(json!({
        "seq_ms": seq_ms,
        "batch_ms": batch_ms,
        "speedup": seq_ms / batch_ms,
        "results": successes,
        "successes": successes,
    }))
}

/// Structured result for `bench_structured_parser`.
pub fn structured_parser_report() -> Value {
    json!({"skip": "requires a sample PDF file"})
}

///
/// # Errors
///
/// Returns `PortError` when fixtures, network, or parser inputs cannot be processed.
/// Structured result for `bench_guard`.
pub fn guard_benchmark_report() -> Result<Value> {
    let corpus = load_guard_corpus_impl()?;
    if corpus.is_empty() {
        return Err(PortError::new(
            ErrorKind::Lookup,
            "guard corpus is empty".to_owned(),
        ));
    }
    let corpus_chars: usize = corpus.iter().map(|(_, _, text)| text.chars().count()).sum();

    let off_start = Instant::now();
    for (_name, _injected, text) in &corpus {
        let _ = scan(text);
    }
    let off_s = off_start.elapsed().as_secs_f64();

    let mut cache: HashMap<String, f64> = HashMap::new();
    let mut cache_hits = 0usize;
    let mut chunks_scanned = 0usize;
    let mut flagged = 0usize;
    let mut call_ms = Vec::with_capacity(corpus.len());
    let on_start = Instant::now();
    for (_name, _injected, text) in &corpus {
        let call_start = Instant::now();
        for (_offset, window) in chunk_text(text, CHUNK_CHARS, CHUNK_OVERLAP, MAX_CHUNKS) {
            chunks_scanned += 1;
            let score = match cache.get(&window) {
                Some(score) => {
                    cache_hits += 1;
                    *score
                }
                None => {
                    let score = stub_score(&window);
                    cache.insert(window, score);
                    score
                }
            };
            if score >= THRESHOLD {
                flagged += 1;
            }
        }
        call_ms.push(call_start.elapsed().as_secs_f64() * 1000.0);
    }
    let on_s = on_start.elapsed().as_secs_f64();

    Ok(json!({
        "backend": "stub detector (jailguard absent)",
        "corpus": corpus.len(),
        "corpus_chars": corpus_chars,
        "off_ms": off_s * 1000.0,
        "on_ms": on_s * 1000.0,
        "overhead_ms": (on_s - off_s) * 1000.0,
        "per_document_ms": (on_s - off_s) * 1000.0 / corpus.len() as f64,
        "calls": corpus.len(),
        "chunks_scanned": chunks_scanned,
        "p50_ms": percentile(&call_ms, 50.0),
        "p95_ms": percentile(&call_ms, 95.0),
        "flag_rate": flagged as f64 / corpus.len() as f64,
        "cache_hits": cache_hits,
    }))
}

/// Port of `arrow` (`benchmarks.py:50`). The original prints and returns `None`.
pub fn arrow(label: String, value: String) -> std::result::Result<Value, String> {
    let _ = (label, value);
    Ok(Value::Null)
}

/// Port of `bench_rust_fetch` (`benchmarks.py:59`).
pub fn bench_rust_fetch() -> std::result::Result<Value, String> {
    rust_fetch_report().map_err(|e| format!("RuntimeError: {}", e.message()))
}

/// Port of `bench_meta_oxide` (`benchmarks.py:99`).
pub fn bench_meta_oxide() -> std::result::Result<Value, String> {
    meta_oxide_report().map_err(|e| format!("RuntimeError: {}", e.message()))
}

/// Port of `bench_token_budget` (`benchmarks.py:133`).
pub fn bench_token_budget() -> std::result::Result<Value, String> {
    token_budget_report().map_err(|e| format!("RuntimeError: {}", e.message()))
}

/// Port of `bench_batch_fetch` (`benchmarks.py:154`).
pub fn bench_batch_fetch() -> std::result::Result<Value, String> {
    batch_fetch_report().map_err(|e| format!("RuntimeError: {}", e.message()))
}

/// Port of `bench_structured_parser` (`benchmarks.py:181`).
pub fn bench_structured_parser() -> std::result::Result<Value, String> {
    Ok(structured_parser_report())
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

/// Port of `bench_guard` (`benchmarks.py:279`).
pub fn bench_guard() -> std::result::Result<Value, String> {
    guard_benchmark_report().map_err(|e| format!("RuntimeError: {}", e.message()))
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

    #[test]
    fn python_round_is_bankers_rounding() {
        assert_eq!(py_round(4.5), 4.0);
        assert_eq!(py_round(5.5), 6.0);
        assert_eq!(py_round(4.4), 4.0);
    }

    #[test]
    fn percentile_uses_nearest_rank() {
        let values = [1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(percentile(&values, 50.0), 3.0);
        assert_eq!(percentile(&values, 95.0), 5.0);
    }
}
