//! `gossamer research`: taxonomy and validation envelopes in Rust.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::taxonomy::CATEGORIES_JSON;
use serde_json::{Value, json};

const KNOWN_FLAGS: [&str; 10] = [
    "--max-results",
    "--category",
    "--provider",
    "--filter",
    "--select",
    "--title",
    "--author",
    "--providers",
    "--cache-dir",
    "--config",
];

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

    let mut query: Option<String> = None;
    let mut category: Option<String> = None;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !KNOWN_FLAGS.contains(&value.as_str()) || index + 1 >= args.len() {
                return unsupported();
            }
            if value == "--category" {
                category = Some(args[index + 1].to_string_lossy().into_owned());
            }
            index += 2;
            continue;
        }
        if query.is_some() {
            return unsupported();
        }
        query = Some(value);
        index += 1;
    }
    let Some(query) = query else {
        return unsupported();
    };

    let taxonomy: Value = match serde_json::from_str(CATEGORIES_JSON) {
        Ok(value) => value,
        Err(_) => return unsupported(),
    };
    if query.trim().is_empty() {
        println!(
            "{}",
            serde_json::to_string_pretty(&taxonomy).unwrap_or_else(|_| taxonomy.to_string())
        );
        return ExitCode::SUCCESS;
    }

    let Some(category) = category else {
        return unsupported();
    };
    let names: Vec<String> = taxonomy
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|entry| entry.get("category").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    if !names.contains(&category) {
        let payload = json!({
            "query": query,
            "category": category,
            "provider": Value::Null,
            "available_categories": names,
            "error": format!(
                "unknown category '{}'; choose from: {}",
                category,
                names.join(", ")
            ),
            "results": [],
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
        );
        return ExitCode::SUCCESS;
    }
    unsupported()
}
