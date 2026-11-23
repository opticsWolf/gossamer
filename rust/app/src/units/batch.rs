//! Glue for `gossamer batch`: invalid-URL batches are answered in Rust; valid fetches fall back.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::document::prepare_url;
use serde_json::Value;

use super::jsonfmt::compact_record;

fn fallback(args: &[OsString], help_count: u8) -> ExitCode {
    let mut full: Vec<OsString> = Vec::with_capacity(args.len() + help_count as usize + 1);
    full.push(OsString::from("batch"));
    full.extend(args.iter().cloned());
    for _ in 0..help_count {
        full.push(OsString::from("--help"));
    }
    crate::original::run(&full)
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

    let mut urls: Vec<String> = Vec::new();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value == "--use-smart" {
            if index + 1 >= args.len() {
                return fallback(&args, help_count);
            }
            let strategy = args[index + 1].to_string_lossy().into_owned();
            if !matches!(strategy.as_str(), "auto" | "browser" | "static") {
                return fallback(&args, help_count);
            }
            index += 2;
            continue;
        }
        if value.starts_with('-') {
            return fallback(&args, help_count);
        }
        urls.push(value);
        index += 1;
    }
    if urls.is_empty() {
        return fallback(&args, help_count);
    }

    let mut records: Vec<Value> = Vec::with_capacity(urls.len());
    for url in &urls {
        match prepare_url(url, None) {
            Ok(_) => return fallback(&args, help_count),
            Err(error) => {
                if error.error_type() == "SsrfBlockedError" {
                    eprintln!(
                        "Blocked non-public URL {}: {}",
                        error.raw(),
                        error.message()
                    );
                }
                records.push(error.to_record());
            }
        }
    }
    let body = records
        .iter()
        .map(compact_record)
        .collect::<Vec<_>>()
        .join(", ");
    println!("[{body}]");
    ExitCode::SUCCESS
}
