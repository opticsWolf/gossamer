//! Glue for `gossamer benchmarks`.
//!
//! All three benchmark modes are implemented in Rust. Timing values are normalized during app
//! replay; deterministic counts, verdicts, rates, and skip reasons are compared exactly.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::cli_help::CLI_HELP_JSON;
use serde_json::Value;

fn fallback(args: &[OsString], help_count: u8) -> ExitCode {
    let mut full: Vec<OsString> = Vec::with_capacity(args.len() + help_count as usize + 1);
    full.push(OsString::from("benchmarks"));
    full.extend(args.iter().cloned());
    for _ in 0..help_count {
        full.push(OsString::from("--help"));
    }
    crate::original::run(&full)
}

fn unknown_flag_error(args: &[OsString]) -> Option<ExitCode> {
    let unknown = args
        .iter()
        .map(|arg| arg.to_string_lossy().into_owned())
        .find(|arg| {
            arg.starts_with('-')
                && !matches!(arg.as_str(), "--corpus" | "--guard" | "--help" | "-h")
        })?;
    let all: Value = serde_json::from_str(CLI_HELP_JSON).ok()?;
    let entry = all.get("benchmarks-unknown")?;
    let stderr = entry
        .get("stderr")
        .and_then(Value::as_str)
        .unwrap_or("")
        .replace("--bogus", &unknown);
    eprint!("{stderr}");
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
    println!("    >> {label}: {value}");
}

fn timing_line(label: &str, timing: &Value) {
    println!(
        "  {label}: avg={:.1}ms  median={:.1}ms  p95={:.1}ms",
        f64_field(timing, "avg_ms"),
        f64_field(timing, "median_ms"),
        f64_field(timing, "p95_ms")
    );
}

fn banner() {
    println!("============================================================");
    println!("  gossamer — Performance Benchmarks");
    println!("============================================================");
}

fn done() {
    println!();
    println!("============================================================");
    println!("  Done.");
    println!("============================================================");
}

fn print_fetch(report: &Value) {
    println!();
    println!("=== Rust Core Fetch (example.com) ===");
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
    println!();
    println!("=== meta-oxide Extraction ===");
    timing_line("extract_all (meta-oxide)", report);
    arrow(
        "result",
        &format!(
            "{} metadata categories extracted",
            u64_field(report, "keys")
        ),
    );
    println!("    (BeautifulSoup not installed — skipping comparison)");
}

fn print_tokens(report: &Value) {
    println!();
    println!("=== Token Budget (tiktoken) ===");
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
    println!();
    println!("=== Batch Fetch (concurrent) ===");
    println!(
        "  Sequential (5 × example.com): {:.1}ms",
        f64_field(report, "seq_ms")
    );
    println!(
        "  Batch concurrent (5 × example.com): {:.1}ms",
        f64_field(report, "batch_ms")
    );
    println!("  Speedup: {:.1}x", f64_field(report, "speedup"));
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
    println!();
    println!("=== Structured Parser (pdf_oxide) ===");
    println!("    (Skipped — {})", str_field(report, "skip"));
}

fn print_guard(report: &Value) {
    println!();
    println!("=== Prompt-injection guard (enabled=False vs True) ===");
    arrow("backend", str_field(report, "backend"));
    arrow(
        "corpus",
        &format!(
            "{} documents, {} chars",
            u64_field(report, "corpus"),
            u64_field(report, "corpus_chars")
        ),
    );
    println!("  Guard off: {:.1}ms", f64_field(report, "off_ms"));
    println!("  Guard on:  {:.1}ms", f64_field(report, "on_ms"));
    println!(
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
    println!();
    println!("=== Prompt-injection guard -- labelled corpus ===");
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
                println!("  MISS  {name}");
            } else if !injected && flagged {
                println!("  FALSE {name}  (score={:.2})", f64_field(row, "max_score"));
            }
        }
    }

    let benign = u64_field(report, "benign");
    let inject = u64_field(report, "inject");
    println!(
        "  benign   : {}/{} clean, {} false positive(s)",
        u64_field(report, "tn"),
        benign,
        u64_field(report, "fp")
    );
    println!(
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

pub fn run(_command: &str, matches: &ArgMatches) -> ExitCode {
    let args: Vec<OsString> = matches
        .get_many::<OsString>("args")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    let help_count = matches.get_count("help");
    if help_count > 0 {
        return fallback(&args, help_count);
    }
    if let Some(code) = unknown_flag_error(&args) {
        return code;
    }
    let mode = match args.len() {
        0 => "all",
        1 if args[0] == "--corpus" => "corpus",
        1 if args[0] == "--guard" => "guard",
        _ => return fallback(&args, help_count),
    };

    // Precompute before writing anything so a failure can fall back cleanly.
    let corpus = match benchmarks_rs::port::corpus_report() {
        Ok(report) => report,
        Err(_) => return fallback(&args, help_count),
    };
    let guard = if mode == "all" || mode == "guard" {
        match benchmarks_rs::port::guard_benchmark_report() {
            Ok(report) => Some(report),
            Err(_) => return fallback(&args, help_count),
        }
    } else {
        None
    };
    let rest = if mode == "all" {
        let fetched = match benchmarks_rs::port::rust_fetch_report() {
            Ok(report) => report,
            Err(_) => return fallback(&args, help_count),
        };
        let meta = match benchmarks_rs::port::meta_oxide_report() {
            Ok(report) => report,
            Err(_) => return fallback(&args, help_count),
        };
        let tokens = match benchmarks_rs::port::token_budget_report() {
            Ok(report) => report,
            Err(_) => return fallback(&args, help_count),
        };
        let batch = match benchmarks_rs::port::batch_fetch_report() {
            Ok(report) => report,
            Err(_) => return fallback(&args, help_count),
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
