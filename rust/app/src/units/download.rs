//! `gossamer download`: argument/URL validation for the download contract.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
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
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if matches!(value.as_str(), "--overwrite" | "--resume") {
                index += 1;
                continue;
            }
            if matches!(value.as_str(), "-o" | "--output") {
                if index + 1 >= args.len() {
                    return unsupported();
                }
                output = Some(args[index + 1].to_string_lossy().into_owned());
                index += 2;
                continue;
            }
            if matches!(
                value.as_str(),
                "--min-bytes"
                    | "--max-bytes"
                    | "--expect-format"
                    | "--try-mirrors"
                    | "--cache-dir"
                    | "--config"
                    | "--keystore"
                    | "--log-level"
                    | "--fetch-mode"
            ) {
                if index + 1 >= args.len() {
                    return unsupported();
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
    unsupported()
}
