//! Glue for `gossamer audit-cargo`: parse Cargo.lock, query OSV, and format
//! the same report as `scripts/audit_cargo.py`.
//!
//! The OSV batch endpoint returns `{"results": [{"vulns": [...]}]}`. The
//! original Python script read a top-level `vulns` field, which silently
//! reported every lock as clean; both implementations now use the documented
//! per-package result shape.

use std::process::ExitCode;
use std::time::Duration;

use audit_cargo_rs::port::{format_osv_report, load_registry_packages, osv_request_body};
use serde_json::Value;

const OSV_BATCH_URL: &str = "https://api.osv.dev/v1/querybatch";
const OSV_TIMEOUT_SECONDS: u64 = 60;

fn python_os_error(error: &std::io::Error, path: &str) -> String {
    match error.raw_os_error() {
        Some(2) => format!("[Errno 2] No such file or directory: '{path}'"),
        Some(13) => format!("[Errno 13] Permission denied: '{path}'"),
        Some(21) => format!("[Errno 21] Is a directory: '{path}'"),
        _ => error.to_string(),
    }
}

pub fn run(argv: &[String]) -> ExitCode {
    let lock_path = argv.first().map(String::as_str).unwrap_or("Cargo.lock");

    let lock_text = match std::fs::read_to_string(lock_path) {
        Ok(text) => text,
        Err(error) => {
            pyeprintln!(
                "cannot read {lock_path}: {}",
                python_os_error(&error, lock_path)
            );
            return ExitCode::from(2);
        }
    };
    let Ok(packages) = load_registry_packages(lock_text) else {
        return ExitCode::from(2);
    };

    if packages.is_empty() {
        pyprintln!("no registry packages found in {lock_path}");
        return ExitCode::SUCCESS;
    }

    let body = osv_request_body(&packages);
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(OSV_TIMEOUT_SECONDS))
        .build()
    {
        Ok(client) => client,
        Err(error) => {
            pyeprintln!("OSV query failed: {error}");
            return ExitCode::from(2);
        }
    };
    let response = match client
        .post(OSV_BATCH_URL)
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .json(&body)
        .send()
        .and_then(reqwest::blocking::Response::error_for_status)
    {
        Ok(response) => response,
        Err(error) => {
            pyeprintln!("OSV query failed: {error}");
            return ExitCode::from(2);
        }
    };
    let result: Value = match response.json() {
        Ok(result) => result,
        Err(error) => {
            pyeprintln!("OSV query failed: {error}");
            return ExitCode::from(2);
        }
    };

    let (report, code) = format_osv_report(&result, &packages, lock_path);
    pyprint!("{report}");
    ExitCode::from(code)
}
