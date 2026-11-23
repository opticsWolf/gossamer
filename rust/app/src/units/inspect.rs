//! Glue for `gossamer inspect`: URL rejection is handled in Rust; successful fetches still fall back.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::document::prepare_url;

use gossamer::jsonfmt::pretty_record;

fn fallback(_args: &[OsString], _help_count: u8) -> ExitCode {
    unsupported()
}

fn known_flag(name: &str) -> bool {
    matches!(
        name,
        "--use-smart"
            | "--query"
            | "--offset"
            | "--max-chunks"
            | "--structured"
            | "--cache-dir"
            | "--config"
            | "--keystore"
            | "--log-level"
            | "--fetch-mode"
    )
}

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
    let help_count = matches.get_count("help");
    if help_count > 0 {
        return fallback(&args, help_count);
    }

    let mut url: Option<String> = None;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !known_flag(&value) || index + 1 >= args.len() {
                return fallback(&args, help_count);
            }
            if value == "--structured" {
                index += 1;
                continue;
            }
            let flag_value = args[index + 1].to_string_lossy().into_owned();
            if value == "--use-smart"
                && !matches!(flag_value.as_str(), "auto" | "browser" | "static")
            {
                return fallback(&args, help_count);
            }
            if matches!(value.as_str(), "--offset" | "--max-chunks")
                && flag_value.parse::<i64>().is_err()
            {
                return fallback(&args, help_count);
            }
            index += 2;
            continue;
        }
        if url.is_some() {
            return fallback(&args, help_count);
        }
        url = Some(value);
        index += 1;
    }
    let Some(url) = url else {
        return fallback(&args, help_count);
    };

    match prepare_url(&url, None) {
        Ok(_) => fallback(&args, help_count),
        Err(error) => {
            if error.error_type() == "SsrfBlockedError" {
                eprintln!(
                    "Blocked non-public URL {}: {}",
                    error.raw(),
                    error.message()
                );
            }
            println!("{}", pretty_record(&error.to_record()));
            ExitCode::SUCCESS
        }
    }
}
