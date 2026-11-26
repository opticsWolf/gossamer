//! `gossamer research`: taxonomy and validation envelopes in Rust.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::adapters::scholar::{
    crossref_parse_search_impl, huggingface_papers_parse_search_impl,
    huggingface_parse_search_impl, openalex_parse_search_impl, zenodo_parse_search_impl,
};
use gossamer::categories::classify_query;
use gossamer::discovery::{fetch_text, fetch_text_with_user_agent};
use gossamer::jsonfmt::py_dumps;
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
    let results = if selected == "crossref" {
        let url = url::Url::parse_with_params(
            "https://api.crossref.org/works",
            &[("query", query.as_str())],
        )
        .map(|value| value.to_string())
        .unwrap_or_else(|_| format!("https://api.crossref.org/works?query={query}"));
        match fetch_text(&url, 20.0) {
            Ok((body, _)) => {
                let mut records =
                    crossref_parse_search_impl(&body, max_results as i64, None).unwrap_or_default();
                if let Ok(parsed) = serde_json::from_str::<Value>(&body) {
                    let items = parsed
                        .get("message")
                        .and_then(|value| value.get("items"))
                        .and_then(Value::as_array);
                    if let Some(items) = items {
                        for (record, item) in records.iter_mut().zip(items.iter().take(max_results))
                        {
                            if let Some(object) = record.as_object_mut() {
                                object.insert("raw".to_string(), Value::String(py_dumps(item)));
                            }
                        }
                    }
                }
                records
            }
            Err(error) => {
                eprintln!("Crossref search failed: {error}");
                let payload = json!({"error": format!("All search providers failed for: {query}")});
                println!(
                    "{}",
                    serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
                );
                return ExitCode::SUCCESS;
            }
        }
    } else if selected == "openalex" {
        let per_page = max_results.min(100).to_string();
        let url = url::Url::parse_with_params(
            "https://api.openalex.org/works",
            &[("search", query.as_str()), ("per_page", per_page.as_str())],
        )
        .map(|value| value.to_string())
        .unwrap_or_else(|_| format!("https://api.openalex.org/works?search={query}"));
        match fetch_text(&url, 15.0) {
            Ok((body, _)) => {
                let mut records =
                    openalex_parse_search_impl(&body, max_results as i64, None).unwrap_or_default();
                if let Ok(parsed) = serde_json::from_str::<Value>(&body) {
                    let items = parsed.get("results").and_then(Value::as_array);
                    if let Some(items) = items {
                        for (record, item) in records.iter_mut().zip(items.iter().take(max_results))
                        {
                            if let Some(object) = record.as_object_mut() {
                                object.insert("raw".to_string(), Value::String(py_dumps(item)));
                            }
                        }
                    }
                }
                records
            }
            Err(error) => {
                eprintln!("OpenAlex search failed: {error}");
                let payload = json!({"error": format!("All search providers failed for: {query}")});
                println!(
                    "{}",
                    serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
                );
                return ExitCode::SUCCESS;
            }
        }
    } else if selected == "huggingface" || selected == "huggingface-papers" {
        let limit = max_results.min(1000).to_string();
        let (path, params): (&str, Vec<(&str, &str)>) = if selected == "huggingface" {
            (
                "/models",
                vec![
                    ("search", query.as_str()),
                    ("sort", "downloads"),
                    ("direction", "-1"),
                    ("limit", limit.as_str()),
                ],
            )
        } else {
            (
                "/papers/search",
                vec![("q", query.as_str()), ("limit", limit.as_str())],
            )
        };
        let url = url::Url::parse_with_params(&format!("https://huggingface.co/api{path}"), params)
            .map(|value| value.to_string())
            .unwrap_or_else(|_| format!("https://huggingface.co/api{path}?q={query}"));
        match fetch_text(&url, 30.0) {
            Ok((body, _)) => {
                let parsed_body = serde_json::from_str::<Value>(&body).ok();
                let items = parsed_body.as_ref().and_then(Value::as_array);
                let mut records = if selected == "huggingface" {
                    huggingface_parse_search_impl(&body, max_results as i64, None)
                        .unwrap_or_default()
                } else {
                    huggingface_papers_parse_search_impl(&body, max_results as i64, None)
                        .unwrap_or_default()
                };
                if let Some(items) = items {
                    for (record, item) in records.iter_mut().zip(items.iter()) {
                        if let Some(object) = record.as_object_mut() {
                            object.insert("raw".to_string(), Value::String(py_dumps(item)));
                        }
                    }
                }
                records
            }
            Err(error) => {
                eprintln!("Hugging Face search failed: {error}");
                let payload = json!({"error": format!("All search providers failed for: {query}")});
                println!(
                    "{}",
                    serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
                );
                return ExitCode::SUCCESS;
            }
        }
    } else if selected == "zenodo" {
        let size = max_results.min(100).to_string();
        let url = url::Url::parse_with_params(
            "https://zenodo.org/api/records",
            &[
                ("q", query.as_str()),
                ("size", size.as_str()),
                ("page", "1"),
            ],
        )
        .map(|value| value.to_string())
        .unwrap_or_else(|_| format!("https://zenodo.org/api/records?q={query}"));
        let user_agent = format!("{}?email=", gossamer::USER_AGENT);
        match fetch_text_with_user_agent(&url, 20.0, &user_agent) {
            Ok((body, _)) => {
                let mut records =
                    zenodo_parse_search_impl(&body, max_results as i64, None).unwrap_or_default();
                if let Ok(parsed) = serde_json::from_str::<Value>(&body) {
                    let items = parsed
                        .get("hits")
                        .and_then(|value| value.get("hits"))
                        .and_then(Value::as_array);
                    if let Some(items) = items {
                        for (record, item) in records.iter_mut().zip(items.iter()) {
                            if let Some(object) = record.as_object_mut() {
                                object.insert("raw".to_string(), Value::String(py_dumps(item)));
                            }
                        }
                    }
                }
                records
            }
            Err(error) => {
                eprintln!("Zenodo search failed: {error}");
                let payload = json!({"error": format!("All search providers failed for: {query}")});
                println!(
                    "{}",
                    serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
                );
                return ExitCode::SUCCESS;
            }
        }
    } else if selected == "duckduckgo" {
        let url = duckduckgo_search_url(&query);
        match fetch_text(&url, 20.0) {
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
        }
    } else {
        return unsupported();
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
