//! `gossamer download`: argument/URL validation for the download contract.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::downloader::{download_bytes, http_error, validate_finished_file};
use gossamer::miscutils::sha256_bytes;
use gossamer::ssrf::check_url;
use gossamer::urls::normalize_url;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
}

fn emit_error(source: &str, code: &str, message: &str) -> ExitCode {
    println!(
        "{}",
        pretty(&json!({
            "source": source,
            "status": "error",
            "error": {"code": code, "message": message},
        }))
    );
    ExitCode::SUCCESS
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
    let mut source: Option<String> = None;
    let mut output: Option<String> = None;
    let mut overwrite = false;
    let mut min_bytes = 1i64;
    let mut max_bytes = 0usize;
    let mut expect_pdf = false;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if value == "--overwrite" {
                overwrite = true;
                index += 1;
                continue;
            }
            if value == "--resume" {
                return unsupported();
            }
            if matches!(value.as_str(), "-o" | "--output") {
                if index + 1 >= args.len() {
                    return unsupported();
                }
                output = Some(args[index + 1].to_string_lossy().into_owned());
                index += 2;
                continue;
            }
            if value == "--try-mirrors" {
                return unsupported();
            }
            if matches!(
                value.as_str(),
                "--min-bytes"
                    | "--max-bytes"
                    | "--expect-format"
                    | "--cache-dir"
                    | "--config"
                    | "--keystore"
                    | "--log-level"
                    | "--fetch-mode"
            ) {
                if index + 1 >= args.len() {
                    return unsupported();
                }
                let flag_value = args[index + 1].to_string_lossy().into_owned();
                if value == "--min-bytes"
                    && let Ok(parsed) = flag_value.parse::<i64>()
                {
                    min_bytes = parsed;
                }
                if value == "--max-bytes"
                    && let Ok(parsed) = flag_value.parse::<usize>()
                {
                    max_bytes = parsed;
                }
                if value == "--expect-format" && flag_value == "pdf" {
                    expect_pdf = true;
                }
                index += 2;
                continue;
            }
            return unsupported();
        }
        if source.is_some() {
            return unsupported();
        }
        source = Some(value);
        index += 1;
    }
    let (Some(source), Some(output)) = (source, output) else {
        return unsupported();
    };
    if output.trim().is_empty() {
        return emit_error(&source, "invalid_argument", "output_path must name a file");
    }

    let url = match normalize_url(Some(&source), None, None) {
        Ok(url) => url,
        Err(error) => {
            return emit_error(
                &source,
                "invalid_url",
                error
                    .message()
                    .strip_prefix("ValueError: ")
                    .unwrap_or(error.message()),
            );
        }
    };
    if let Err(error) = check_url(&url, false, None) {
        eprintln!("Blocked non-public URL {}: {}", url, error.message());
        return emit_error(
            &url,
            "url_rejected",
            error
                .message()
                .strip_prefix("SsrfBlockedError: ")
                .unwrap_or(error.message()),
        );
    }
    let target = PathBuf::from(&output);
    let target = if target.is_absolute() {
        target
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(target)
    };
    expect_pdf |= target
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| ext.eq_ignore_ascii_case("pdf"));
    if target.exists() && !overwrite {
        return emit_error(
            &url,
            "file_exists",
            &format!("Output already exists: {}", target.display()),
        );
    }
    if let Some(parent) = target.parent()
        && std::fs::create_dir_all(parent).is_err()
    {
        return emit_error(
            &url,
            "local_write_error",
            "could not create output directory",
        );
    }

    let cap = if max_bytes == 0 {
        50_000_000
    } else {
        max_bytes
    };
    let (bytes, status, final_url, content_type) = match download_bytes(&url, 30.0, cap) {
        Ok(value) => value,
        Err(error) => return emit_error(&url, "download_error", &error),
    };
    if status >= 400 {
        let payload = http_error(
            status as i64,
            content_type.as_deref().unwrap_or(""),
            Some(&bytes),
            &final_url,
            None,
        );
        let code = payload
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or("http_error");
        let message = payload
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("HTTP download failed");
        return emit_error(&final_url, code, message);
    }
    if let Some(payload) = validate_finished_file(
        Some(&bytes[..bytes.len().min(5)]),
        Some(&bytes),
        content_type.as_deref(),
        bytes.len() as i64,
        min_bytes,
        expect_pdf,
        status as i64,
        &final_url,
    ) {
        let code = payload
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or("invalid_file");
        let message = payload
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("invalid downloaded file");
        return emit_error(&final_url, code, message);
    }
    if std::fs::write(&target, &bytes).is_err() {
        return emit_error(
            &final_url,
            "local_write_error",
            "could not write output file",
        );
    }
    let payload = json!({
        "source": url,
        "final_url": final_url,
        "output_path": target.to_string_lossy(),
        "bytes": bytes.len(),
        "sha256": sha256_bytes(&bytes),
        "content_type": content_type,
        "http_status": status,
        "status": "downloaded",
    });
    println!("{}", pretty(&payload));
    ExitCode::SUCCESS
}
