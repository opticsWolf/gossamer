//! Glue for `gossamer search`: unknown-provider errors are handled in Rust; live searches fall back.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::search::unknown_provider_error;

fn fallback(_args: &[OsString], _help_count: u8) -> ExitCode {
    unsupported()
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

    let mut provider: Option<String> = None;
    let mut search_only = false;
    let mut saw_query = false;
    let mut query = String::new();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        match value.as_str() {
            "--provider" => {
                if index + 1 >= args.len() {
                    return fallback(&args, help_count);
                }
                provider = Some(args[index + 1].to_string_lossy().into_owned());
                index += 2;
                continue;
            }
            "--search-only" => {
                search_only = true;
                index += 1;
                continue;
            }
            "--max-results" | "--depth" | "--max-tokens" | "--use-smart" | "--cache-dir"
            | "--config" | "--keystore" | "--log-level" | "--fetch-mode" => {
                if index + 1 >= args.len() {
                    return fallback(&args, help_count);
                }
                index += 2;
                continue;
            }
            _ if value.starts_with('-') => return fallback(&args, help_count),
            _ => {
                if saw_query {
                    return fallback(&args, help_count);
                }
                saw_query = true;
                query = value;
            }
        }
        index += 1;
    }
    if !saw_query {
        return fallback(&args, help_count);
    }

    let Some(provider) = provider else {
        return fallback(&args, help_count);
    };
    let available = ["duckduckgo"];
    if let Some(payload) = unknown_provider_error(&provider, &available) {
        if !search_only {
            return fallback(&args, help_count);
        }
        println!(
            "{}",
            serde_json::to_string_pretty(&payload).unwrap_or_else(|_| "{}".to_owned())
        );
        return ExitCode::SUCCESS;
    }
    if !search_only {
        // A non-search-only run would still fetch pages; keep it on the original.
        return fallback(&args, help_count);
    }
    if query.trim().is_empty() {
        eprintln!("DuckDuckGo search failed: query is mandatory.");
        eprintln!(
            "Provider DuckDuckGoProvider failed for '{}': query is mandatory. — trying next",
            query
        );
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "error": "All search providers failed for: ",
            }))
            .unwrap_or_else(|_| "{}".to_owned())
        );
        return ExitCode::SUCCESS;
    }
    fallback(&args, help_count)
}
