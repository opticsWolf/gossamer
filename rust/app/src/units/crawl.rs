//! `gossamer crawl`: root-URL validation for the crawl command.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::document::prepare_url;
use serde_json::json;

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
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

    let mut root: Option<String> = None;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if matches!(
                value.as_str(),
                "--same-host" | "--excerpts" | "--search-prior"
            ) {
                index += 1;
                continue;
            }
            if matches!(
                value.as_str(),
                "--query"
                    | "--max-depth"
                    | "--max-pages"
                    | "--min-score"
                    | "--seed-urls"
                    | "--use-smart"
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
        if root.is_some() {
            return unsupported();
        }
        root = Some(value);
        index += 1;
    }
    let Some(root) = root else {
        return unsupported();
    };

    match prepare_url(&root, None) {
        Ok(_) => unsupported(),
        Err(error) => {
            if error.error_type() == "SsrfBlockedError" {
                eprintln!(
                    "Blocked non-public URL {}: {}",
                    error.raw(),
                    error.message()
                );
            }
            let message = error.message();
            let message = message
                .strip_prefix("ValueError: ")
                .or_else(|| message.strip_prefix("SsrfBlockedError: "))
                .unwrap_or(message);
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({"error": format!("crawl: {message}")}))
                    .unwrap_or_default()
            );
            ExitCode::SUCCESS
        }
    }
}
