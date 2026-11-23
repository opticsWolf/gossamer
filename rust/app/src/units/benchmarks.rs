//! Glue for `gossamer benchmarks`.
//!
//! Only the deterministic `--corpus` report is handled in Rust. Wall-clock and live-network modes
//! fall back to the original benchmark harness so their output stays byte-for-byte compatible.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
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

fn u64_field(report: &Value, key: &str) -> u64 {
    report.get(key).and_then(Value::as_u64).unwrap_or(0)
}

fn f64_field(report: &Value, key: &str) -> f64 {
    report.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

fn print_corpus_report(report: &Value) {
    println!("============================================================");
    println!("  gossamer — Performance Benchmarks");
    println!("============================================================");
    println!();
    println!("=== Prompt-injection guard -- labelled corpus ===");
    println!(
        "    >> backend: {}",
        report
            .get("backend")
            .and_then(Value::as_str)
            .unwrap_or("stub detector (jailguard absent)")
    );

    if let Some(rows) = report.get("rows").and_then(Value::as_array) {
        for row in rows {
            let name = row.get("name").and_then(Value::as_str).unwrap_or("");
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
        println!(
            "    >> false-positive rate: {:.2}",
            f64_field(report, "false_positive_rate")
        );
    }
    if inject > 0 {
        println!(
            "    >> detection rate: {:.2}",
            f64_field(report, "detection_rate")
        );
    }
    println!(
        "    >> verdict: {}",
        report
            .get("verdict")
            .and_then(Value::as_str)
            .unwrap_or("suppressed -- stub backend: plumbing check, not detector accuracy")
    );
    println!();
    println!("============================================================");
    println!("  Done.");
    println!("============================================================");
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
    if args.len() != 1 || args[0] != "--corpus" {
        return fallback(&args, help_count);
    }

    let Ok(report) = benchmarks_rs::port::corpus_report() else {
        return fallback(&args, help_count);
    };
    print_corpus_report(&report);
    ExitCode::SUCCESS
}
