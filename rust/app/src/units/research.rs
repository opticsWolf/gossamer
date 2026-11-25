//! `gossamer research`: taxonomy and validation envelopes in Rust.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::categories::classify_query;
use gossamer::discovery::fetch_text;
use gossamer::search::{duckduckgo_results, duckduckgo_search_url};
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
    let mut provider: Option<String> = None;
    let mut max_results = 5usize;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !KNOWN_FLAGS.contains(&value.as_str()) || index + 1 >= args.len() {
                return unsupported();
            }
            if value == "--category" {
                category = Some(args[index + 1].to_string_lossy().into_owned());
            } else if value == "--provider" {
                provider = Some(args[index + 1].to_string_lossy().into_owned());
            } else if value == "--max-results"
                && let Ok(parsed) = args[index + 1].to_string_lossy().parse::<usize>()
            {
                max_results = parsed;
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

    let names: Vec<String> = taxonomy
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|entry| entry.get("category").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    let category_name = if let Some(explicit) = category.as_deref() {
        explicit.to_string()
    } else if let Some(name) = provider.as_deref() {
        taxonomy
            .as_array()
            .into_iter()
            .flatten()
            .find(|entry| {
                entry
                    .get("providers")
                    .and_then(Value::as_array)
                    .is_some_and(|providers| {
                        providers.iter().any(|value| value.as_str() == Some(name))
                    })
            })
            .and_then(|entry| entry.get("category").and_then(Value::as_str))
            .unwrap_or("general")
            .to_string()
    } else {
        classify_query(Some(query.as_str()))
    };
    if !names.contains(&category_name) {
        let payload = json!({
            "query": query,
            "category": category_name,
            "provider": Value::Null,
            "available_categories": names,
            "error": format!(
                "unknown category '{}'; choose from: {}",
                category_name,
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
    let Some(entry) = taxonomy.as_array().into_iter().flatten().find(|entry| {
        entry.get("category").and_then(Value::as_str) == Some(category_name.as_str())
    }) else {
        return unsupported();
    };
    let default_provider = entry
        .get("default_provider")
        .and_then(Value::as_str)
        .unwrap_or("duckduckgo")
        .to_string();
    let selected = provider.unwrap_or(default_provider);
    let available = entry
        .get("providers")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if !available
        .iter()
        .any(|value| value.as_str() == Some(selected.as_str()))
    {
        let payload = json!({
            "query": query,
            "category": category_name,
            "provider": selected,
            "available_providers": available,
            "provider_kind": entry.get("provider_kind").cloned().unwrap_or(Value::Null),
            "error": format!(
                "provider '{}' is not available for category '{}'; choose from: {}",
                selected,
                category_name,
                available
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            "results": [],
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
        );
        return ExitCode::SUCCESS;
    }
    if selected != "duckduckgo" {
        return unsupported();
    }
    let url = duckduckgo_search_url(&query);
    let results = match fetch_text(&url, 20.0) {
        Ok((html, _)) => duckduckgo_results(&html, max_results),
        Err(error) => {
            eprintln!("DuckDuckGo search failed: {error}");
            eprintln!(
                "Provider DuckDuckGoProvider failed for '{}': {error} — trying next",
                query
            );
            let payload = json!({"error": format!("All search providers failed for: {query}")});
            println!(
                "{}",
                serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
            );
            return ExitCode::SUCCESS;
        }
    };
    let payload = json!({
        "query": query,
        "category": category_name,
        "provider": selected,
        "available_providers": available,
        "provider_kind": entry.get("provider_kind").cloned().unwrap_or(Value::Null),
        "description": entry.get("description").cloned().unwrap_or(Value::Null),
        "results": results,
    });
    println!(
        "{}",
        serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
    );
    ExitCode::SUCCESS
}
