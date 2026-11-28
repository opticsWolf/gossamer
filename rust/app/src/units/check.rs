//! `gossamer check`: deterministic liveness envelopes over Rust HTTP probes.

use std::process::ExitCode;

use gossamer::cli_args::Parsed;
use gossamer::jsonfmt::py_dumps_pretty;
use gossamer::service::liveness::{liveness_result, probe_liveness};
use gossamer::ssrf::check_url;
use serde_json::{Value, json};

fn blocked(url: &str, message: &str) -> Value {
    json!({
        "url": url,
        "status": "blocked",
        "alive": false,
        "http_status": Value::Null,
        "error": format!("SsrfBlockedError: {message}"),
    })
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let mode = parsed.value("mode").unwrap_or("status").to_string();
    let urls: Vec<String> = parsed.positionals("urls").to_vec();

    let probe = if mode == "content" { "get" } else { "head" };
    let mut results = Vec::with_capacity(urls.len());
    for url in &urls {
        if let Err(error) = check_url(url, gossamer::ssrf::env_allow_private(), None) {
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
    pyprintln!("{}", py_dumps_pretty(&payload));
    ExitCode::SUCCESS
}
