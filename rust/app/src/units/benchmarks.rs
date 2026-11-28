//! Glue for `gossamer benchmarks`.
//!
//! All three benchmark modes are implemented in Rust. Timing values are normalized during app
//! replay; deterministic counts, verdicts, rates, and skip reasons are compared exactly.

use std::process::ExitCode;

use gossamer::cli_help::CLI_HELP_JSON;
use serde_json::Value;

fn fallback() -> ExitCode {
    unsupported()
}

fn unknown_flag_error(args: &[String]) -> Option<ExitCode> {
    let unknown = args.iter().find(|arg| {
        arg.starts_with('-') && !matches!(arg.as_str(), "--corpus" | "--guard" | "--help" | "-h")
    })?;
    let all: Value = serde_json::from_str(CLI_HELP_JSON).ok()?;
    let entry = all.get("benchmarks-unknown")?;
    let stderr = entry
        .get("stderr")
        .and_then(Value::as_str)
        .unwrap_or("")
        .replace("--bogus", unknown.as_str());
    pyeprint!("{stderr}");
    let status = entry.get("status").and_then(Value::as_u64).unwrap_or(2);
    Some(ExitCode::from(status.min(255) as u8))
}

fn u64_field(value: &Value, key: &str) -> u64 {
    value.get(key).and_then(Value::as_u64).unwrap_or(0)
}

fn f64_field(value: &Value, key: &str) -> f64 {
    value.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

fn str_field<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or("")
}

fn arrow(label: &str, value: &str) {
    pyprintln!("    >> {label}: {value}");
}

fn timing_line(label: &str, timing: &Value) {
    pyprintln!(
        "  {label}: avg={:.1}ms  median={:.1}ms  p95={:.1}ms",
        f64_field(timing, "avg_ms"),
        f64_field(timing, "median_ms"),
        f64_field(timing, "p95_ms")
    );
}

fn banner() {
    pyprintln!("============================================================");
    pyprintln!("  gossamer — Performance Benchmarks");
    pyprintln!("============================================================");
}

fn done() {
    pyprintln!();
    pyprintln!("============================================================");
    pyprintln!("  Done.");
    pyprintln!("============================================================");
}

fn print_fetch(report: &Value) {
    pyprintln!();
    pyprintln!("=== Rust Core Fetch (example.com) ===");
    timing_line("fetch_and_extract (warm)", report);
    arrow(
        "result",
        &format!(
            "{} chars, {} links",
            u64_field(report, "chars"),
            u64_field(report, "links")
        ),
    );
}

fn print_meta(report: &Value) {
    pyprintln!();
    pyprintln!("=== meta-oxide Extraction ===");
    timing_line("extract_all (meta-oxide)", report);
    arrow(
        "result",
        &format!(
            "{} metadata categories extracted",
            u64_field(report, "keys")
        ),
    );
    pyprintln!("    (BeautifulSoup not installed — skipping comparison)");
}

fn print_tokens(report: &Value) {
    pyprintln!();
    pyprintln!("=== Token Budget (tiktoken) ===");
    timing_line(
        "count_tokens (300KB text)",
        report.get("count").unwrap_or(report),
    );
    arrow("result", &format!("{} tokens", u64_field(report, "tokens")));
    timing_line(
        "truncate_to_tokens (300KB to 500 tokens)",
        report.get("truncate").unwrap_or(report),
    );
    arrow(
        "result",
        &format!(
            "{} chars, ~{} tokens",
            u64_field(report, "chars"),
            u64_field(report, "truncated_tokens")
        ),
    );
}

fn print_batch(report: &Value) {
    pyprintln!();
    pyprintln!("=== Batch Fetch (concurrent) ===");
    pyprintln!(
        "  Sequential (5 × example.com): {:.1}ms",
        f64_field(report, "seq_ms")
    );
    pyprintln!(
        "  Batch concurrent (5 × example.com): {:.1}ms",
        f64_field(report, "batch_ms")
    );
    pyprintln!("  Speedup: {:.1}x", f64_field(report, "speedup"));
    arrow(
        "result",
        &format!(
            "{} results, {} successful",
            u64_field(report, "results"),
            u64_field(report, "successes")
        ),
    );
}

fn print_structured(report: &Value) {
    pyprintln!();
    pyprintln!("=== Structured Parser (pdf_oxide) ===");
    pyprintln!("    (Skipped — {})", str_field(report, "skip"));
}

fn print_guard(report: &Value) {
    pyprintln!();
    pyprintln!("=== Prompt-injection guard (enabled=False vs True) ===");
    arrow("backend", str_field(report, "backend"));
    arrow(
        "corpus",
        &format!(
            "{} documents, {} chars",
            u64_field(report, "corpus"),
            u64_field(report, "corpus_chars")
        ),
    );
    pyprintln!("  Guard off: {:.1}ms", f64_field(report, "off_ms"));
    pyprintln!("  Guard on:  {:.1}ms", f64_field(report, "on_ms"));
    pyprintln!(
        "  Overhead:  +{:.1}ms ({:.1}ms per document)",
        f64_field(report, "overhead_ms"),
        f64_field(report, "per_document_ms")
    );
    arrow("calls", &u64_field(report, "calls").to_string());
    arrow(
        "chunks scanned",
        &u64_field(report, "chunks_scanned").to_string(),
    );
    arrow(
        "p50 / p95 ms",
        &format!(
            "{:.1} / {:.1}",
            f64_field(report, "p50_ms"),
            f64_field(report, "p95_ms")
        ),
    );
    arrow(
        "flag rate",
        &format!("{:.2}", f64_field(report, "flag_rate")),
    );
    arrow("cache hits", &u64_field(report, "cache_hits").to_string());
}

fn print_corpus(report: &Value) {
    pyprintln!();
    pyprintln!("=== Prompt-injection guard -- labelled corpus ===");
    arrow("backend", str_field(report, "backend"));

    if let Some(rows) = report.get("rows").and_then(Value::as_array) {
        for row in rows {
            let name = str_field(row, "name");
            let injected = row
                .get("injected")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let flagged = row.get("flagged").and_then(Value::as_bool).unwrap_or(false);
            if injected && !flagged {
                pyprintln!("  MISS  {name}");
            } else if !injected && flagged {
                pyprintln!("  FALSE {name}  (score={:.2})", f64_field(row, "max_score"));
            }
        }
    }

    let benign = u64_field(report, "benign");
    let inject = u64_field(report, "inject");
    pyprintln!(
        "  benign   : {}/{} clean, {} false positive(s)",
        u64_field(report, "tn"),
        benign,
        u64_field(report, "fp")
    );
    pyprintln!(
        "  injected : {}/{} detected, {} missed",
        u64_field(report, "tp"),
        inject,
        u64_field(report, "fn")
    );
    if benign > 0 {
        arrow(
            "false-positive rate",
            &format!("{:.2}", f64_field(report, "false_positive_rate")),
        );
    }
    if inject > 0 {
        arrow(
            "detection rate",
            &format!("{:.2}", f64_field(report, "detection_rate")),
        );
    }
    arrow("verdict", str_field(report, "verdict"));
}

fn unsupported() -> ExitCode {
    pyeprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

pub fn run(argv: &[String]) -> ExitCode {
    if argv
        .iter()
        .any(|arg| matches!(arg.as_str(), "-h" | "--help"))
    {
        return super::cli::emit("benchmarks-help");
    }
    let args: Vec<String> = argv.to_vec();
    if let Some(code) = unknown_flag_error(&args) {
        return code;
    }
    let mode = match args.len() {
        0 => "all",
        1 if args[0] == "--corpus" => "corpus",
        1 if args[0] == "--guard" => "guard",
        _ => return fallback(),
    };

    // Precompute before writing anything so a failure can fall back cleanly.
    let corpus = match benchmarks_rs::port::corpus_report() {
        Ok(report) => report,
        Err(_) => return fallback(),
    };
    let guard = if mode == "all" || mode == "guard" {
        match benchmarks_rs::port::guard_benchmark_report() {
            Ok(report) => Some(report),
            Err(_) => return fallback(),
        }
    } else {
        None
    };
    let rest = if mode == "all" {
        let fetched = match benchmarks_rs::port::rust_fetch_report() {
            Ok(report) => report,
            Err(_) => return fallback(),
        };
        let meta = match benchmarks_rs::port::meta_oxide_report() {
            Ok(report) => report,
            Err(_) => return fallback(),
        };
        let tokens = match benchmarks_rs::port::token_budget_report() {
            Ok(report) => report,
            Err(_) => return fallback(),
        };
        let batch = match benchmarks_rs::port::batch_fetch_report() {
            Ok(report) => report,
            Err(_) => return fallback(),
        };
        Some((fetched, meta, tokens, batch))
    } else {
        None
    };

    banner();
    if let Some((fetched, meta, tokens, batch)) = rest {
        print_fetch(&fetched);
        print_meta(&meta);
        print_tokens(&tokens);
        print_batch(&batch);
        print_structured(&benchmarks_rs::port::structured_parser_report());
    }
    if let Some(report) = guard {
        print_guard(&report);
    }
    print_corpus(&corpus);
    done();
    ExitCode::SUCCESS
}
