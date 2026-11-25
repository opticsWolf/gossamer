//! `gossamer check`: deterministic liveness envelopes over Rust HTTP probes.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::service::liveness::{liveness_result, probe_liveness};
use gossamer::ssrf::check_url;
use serde_json::{Value, json};

const KNOWN_FLAGS: [&str; 6] = [
    "--mode",
    "--cache-dir",
    "--config",
    "--keystore",
    "--log-level",
    "--fetch-mode",
];

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn blocked(url: &str, message: &str) -> Value {
    json!({
        "url": url,
        "status": "blocked",
        "alive": false,
        "http_status": Value::Null,
        "error": format!("SsrfBlockedError: {message}"),
    })
}

pub fn run(command: &str, matches: &ArgMatches) -> ExitCode {
    if let Some(code) = super::cli::maybe_run(command, matches) {
        return code;
    }

    let args: Vec<OsString> = matches
        .get_many::<OsString>("args")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    if matches.get_count("help") > 0 {
        return unsupported();
    }

    let mut mode = "status".to_string();
    let mut urls: Vec<String> = Vec::new();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !KNOWN_FLAGS.contains(&value.as_str()) || index + 1 >= args.len() {
                return unsupported();
            }
            let flag_value = args[index + 1].to_string_lossy().into_owned();
            if value == "--mode" {
                if !matches!(flag_value.as_str(), "status" | "content") {
                    return unsupported();
                }
                mode = flag_value.clone();
            }
            if value == "--fetch-mode"
                && !matches!(flag_value.as_str(), "auto" | "browser" | "static")
            {
                return unsupported();
            }
            index += 2;
            continue;
        }
        urls.push(value);
        index += 1;
    }
    if urls.is_empty() {
        return unsupported();
    }

    let probe = if mode == "content" { "get" } else { "head" };
    let mut results = Vec::with_capacity(urls.len());
    for url in &urls {
        if let Err(error) = check_url(url, false, None) {
            results.push(blocked(url, error.message()));
            continue;
        }
        let outcome = probe_liveness(url, 10.0, probe);
        let status = outcome
            .get("http_status")
            .and_then(Value::as_u64)
            .map(|value| value as i64);
        let error = outcome.get("error").and_then(Value::as_str);
        results.push(liveness_result(url, status, error));
    }

    let mut summary = json!({"ok": 0, "unreachable": 0, "blocked": 0, "error": 0});
    for result in &results {
        let status = result
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("error");
        let entry = summary.as_object_mut().and_then(|map| map.get_mut(status));
        if let Some(entry) = entry
            && let Some(count) = entry.as_u64()
        {
            *entry = json!(count + 1);
        }
    }

    let payload = json!({
        "tool": "check_sources",
        "count": results.len(),
        "mode": mode,
        "summary": summary,
        "results": results,
    });
    println!(
        "{}",
        serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
    );
    ExitCode::SUCCESS
}
