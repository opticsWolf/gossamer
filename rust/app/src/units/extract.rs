//! `gossamer extract`: argument validation for the extraction contract.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::mcp_dispatch::extract_validation;

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

    let mut tables_as = "json".to_string();
    let mut include_images = false;
    let mut structured = false;
    let mut store = false;
    let mut source: Option<String> = None;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        match value.as_str() {
            "--structured" => structured = true,
            "--store" => store = true,
            "--include-images" => include_images = true,
            "--prefer-jats" | "--no-prefer-jats" => {}
            "--pages" | "--store-dir" | "--cache-dir" | "--config" | "--keystore"
            | "--log-level" | "--fetch-mode" => {
                if index + 1 >= args.len() {
                    return unsupported();
                }
                index += 1;
            }
            "--tables-as" => {
                if index + 1 >= args.len() {
                    return unsupported();
                }
                tables_as = args[index + 1].to_string_lossy().into_owned();
                index += 1;
            }
            _ if value.starts_with('-') => return unsupported(),
            _ => {
                if source.is_some() {
                    return unsupported();
                }
                source = Some(value);
            }
        }
        index += 1;
    }
    if source.is_none() {
        return unsupported();
    }

    if let Some(error) = extract_validation(&tables_as, include_images, structured, store) {
        let text = serde_json::to_string_pretty(&error).unwrap_or_else(|_| error.to_string());
        println!("{text}");
        return ExitCode::SUCCESS;
    }
    unsupported()
}
