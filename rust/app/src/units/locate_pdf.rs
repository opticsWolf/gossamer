//! `gossamer locate-pdf`: open-access candidate reporting over Rust services.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::adapters::scholar::openalex_parse_fetch_impl;
use gossamer::discovery::fetch_text;
use gossamer::openaccess::{normalize_doi, oa_candidate};
use serde_json::{Value, json};

const KNOWN_FLAGS: [&str; 5] = [
    "--cache-dir",
    "--config",
    "--keystore",
    "--log-level",
    "--fetch-mode",
];

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
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
    let mut positional = Vec::new();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !KNOWN_FLAGS.contains(&value.as_str()) || index + 1 >= args.len() {
                return unsupported();
            }
            if value == "--fetch-mode"
                && !matches!(
                    args[index + 1].to_string_lossy().as_ref(),
                    "auto" | "browser" | "static"
                )
            {
                return unsupported();
            }
            index += 2;
            continue;
        }
        positional.push(value);
        index += 1;
    }
    if positional.len() != 1 {
        return unsupported();
    }
    let raw = &positional[0];
    let doi = match normalize_doi(raw) {
        Ok(doi) => doi,
        Err(error) => {
            println!(
                "{}",
                pretty(&json!({
                    "doi": raw,
                    "status": "error",
                    "error": {"code": "invalid_doi", "message": error.strip_prefix("ValueError: ").unwrap_or(&error)},
                    "candidates": [],
                }))
            );
            return ExitCode::SUCCESS;
        }
    };

    let url = format!(
        "https://api.openalex.org/works?filter=doi%3Ahttps%3A%2F%2Fdoi.org%2F{doi}&per_page=1"
    );
    let payload = match fetch_text(&url, 15.0) {
        Ok((body, _)) => body,
        Err(error) => {
            println!(
                "{}",
                pretty(&json!({
                    "doi": doi,
                    "provider": "openalex",
                    "status": "error",
                    "error": {"code": "provider_error", "message": error.strip_prefix("ValueError: ").unwrap_or(&error)},
                    "candidates": [],
                }))
            );
            return ExitCode::SUCCESS;
        }
    };
    let parsed: Value = match serde_json::from_str(&payload) {
        Ok(value) => value,
        Err(error) => {
            println!(
                "{}",
                pretty(&json!({
                    "doi": doi,
                    "provider": "openalex",
                    "status": "error",
                    "error": {"code": "provider_error", "message": error.to_string()},
                    "candidates": [],
                }))
            );
            return ExitCode::SUCCESS;
        }
    };
    let work = parsed
        .get("results")
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .cloned()
        .unwrap_or(Value::Null);
    if work.is_null() {
        println!(
            "{}",
            pretty(&json!({
                "doi": doi,
                "provider": "openalex",
                "status": "error",
                "error": {"code": "provider_error", "message": "no OpenAlex records returned"},
                "candidates": [],
            }))
        );
        return ExitCode::SUCCESS;
    }
    let record = match openalex_parse_fetch_impl(&work.to_string()) {
        Ok(record) => record,
        Err(error) => {
            println!(
                "{}",
                pretty(&json!({
                    "doi": doi,
                    "provider": "openalex",
                    "status": "error",
                    "error": {"code": "invalid_provider_record", "message": error.message().to_string()},
                    "candidates": [],
                }))
            );
            return ExitCode::SUCCESS;
        }
    };
    let open_access = work.get("open_access").cloned().unwrap_or(json!({}));
    let work_is_oa = open_access
        .get("is_oa")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut ordered: Vec<(Value, bool)> = Vec::new();
    if let Some(best) = work
        .get("best_oa_location")
        .filter(|value| value.is_object())
    {
        ordered.push((best.clone(), true));
    }
    if let Some(locations) = work.get("locations").and_then(Value::as_array) {
        ordered.extend(locations.iter().map(|item| (item.clone(), false)));
    }
    let mut candidates = Vec::new();
    let mut seen = Vec::new();
    for (location, is_best) in ordered {
        let Some(candidate) = oa_candidate(&location, is_best, work_is_oa) else {
            continue;
        };
        let candidate_url = candidate
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if seen.contains(&candidate_url) {
            continue;
        }
        seen.push(candidate_url);
        candidates.push(candidate);
    }
    let work_payload = json!({
        "id": record.get("id").cloned().unwrap_or(Value::Null),
        "title": record.get("title").cloned().unwrap_or(Value::Null),
        "url": record.get("url").cloned().unwrap_or(Value::Null),
        "published": record.get("published").cloned().unwrap_or(Value::Null),
        "is_oa": work_is_oa,
        "oa_status": open_access.get("oa_status").cloned().unwrap_or(Value::Null),
    });
    let status = if !candidates.is_empty() {
        "open_access"
    } else if work_is_oa {
        "open_access_no_location"
    } else {
        "closed_access"
    };
    let payload = json!({
        "doi": doi,
        "provider": "openalex",
        "sources": ["openalex"],
        "status": status,
        "work": work_payload,
        "candidates": candidates,
    });
    println!("{}", pretty(&payload));
    ExitCode::SUCCESS
}
