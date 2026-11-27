//! `gossamer cite`: citation formatting over Rust citation kernels.

use std::collections::HashSet;
use std::ffi::OsString;
use std::fs;
use std::path::Path;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::cite::{
    Record, apa_approx, citation_input, citation_record_from_json, citation_style,
    find_doi_in_text, mla_approx, to_bibtex, to_csl_json,
};
use gossamer::document::pdf_markdown;
use gossamer::openaccess::normalize_doi;
use serde_json::{Value, json};

const KNOWN_FLAGS: [&str; 7] = [
    "--style",
    "--enrich",
    "--no-dedupe",
    "--from-pdf",
    "--cache-dir",
    "--config",
    "--keystore",
];

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
}

fn display_path(path: &str) -> String {
    if cfg!(windows) {
        path.replace('/', "\\")
    } else {
        path.to_string()
    }
}

fn doi_from_pdf(path: &str) -> Result<String, String> {
    let bytes = fs::read(path)
        .map_err(|error| format!("cannot read PDF {}: {error}", display_path(path)))?;
    let mut texts = Vec::new();
    if let Ok(markdown) = pdf_markdown(&bytes) {
        texts.push(markdown);
    }
    let latin = bytes.iter().map(|byte| *byte as char).collect::<String>();
    texts.push(latin);
    for text in &texts {
        if let Some(doi) = find_doi_in_text(Some(text)) {
            return Ok(normalize_doi(&doi).unwrap_or(doi));
        }
    }
    Err(format!(
        "no DOI found in PDF {}; pass a DOI explicitly",
        display_path(path)
    ))
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

    let mut style = "bibtex".to_string();
    let mut dedupe = true;
    let mut from_pdf = false;
    let mut results: Vec<String> = Vec::new();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if !KNOWN_FLAGS.contains(&value.as_str()) {
                return unsupported();
            }
            if value == "--enrich" {
                // Best-effort enrichment is a no-op without an injected adapter.
                index += 1;
                continue;
            }
            if value == "--from-pdf" {
                from_pdf = true;
                index += 1;
                continue;
            }
            if value == "--no-dedupe" {
                dedupe = false;
                index += 1;
                continue;
            }
            if index + 1 >= args.len() {
                return unsupported();
            }
            let flag_value = args[index + 1].to_string_lossy().into_owned();
            if value == "--style" {
                if !matches!(flag_value.as_str(), "bibtex" | "csl-json" | "apa" | "mla") {
                    return unsupported();
                }
                style = flag_value;
            }
            if matches!(value.as_str(), "--cache-dir" | "--config" | "--keystore") {
                // Accepted for CLI-shape compatibility; citation formatting is offline.
            }
            index += 2;
            continue;
        }
        results.push(value);
        index += 1;
    }

    let resolved = match citation_style(Some(&style)) {
        Ok(style) => style,
        Err(error) => {
            println!("{}", pretty(&json!({"error": error})));
            return ExitCode::SUCCESS;
        }
    };

    let mut records: Vec<Record> = Vec::new();
    let mut pdf_errors: Vec<String> = Vec::new();
    for item in results {
        let trimmed = item.trim();
        let wants_pdf = from_pdf
            || trimmed.to_ascii_lowercase().ends_with(".pdf") && Path::new(trimmed).is_file();
        if wants_pdf {
            if !Path::new(trimmed).is_file() {
                pdf_errors.push(format!("PDF not found: {item}"));
                continue;
            }
            if Path::new(trimmed)
                .extension()
                .and_then(|value| value.to_str())
                != Some("pdf")
            {
                pdf_errors.push(format!("not a PDF file: {item}"));
                continue;
            }
            match doi_from_pdf(trimmed) {
                Ok(doi) => {
                    let fields = citation_input(&doi);
                    match citation_record_from_json(&fields.to_string()) {
                        Ok(record) => records.push(record),
                        Err(error) => {
                            println!("{}", pretty(&json!({"error": error.message().to_string()})));
                            return ExitCode::SUCCESS;
                        }
                    }
                }
                Err(error) => pdf_errors.push(error),
            }
            continue;
        }
        let loaded = serde_json::from_str::<Value>(&item).ok();
        let record = if let Some(Value::Object(_)) = &loaded {
            match citation_record_from_json(&item) {
                Ok(record) => record,
                Err(error) => {
                    println!("{}", pretty(&json!({"error": error.message().to_string()})));
                    return ExitCode::SUCCESS;
                }
            }
        } else {
            let fields = citation_input(item.trim());
            match citation_record_from_json(&fields.to_string()) {
                Ok(record) => record,
                Err(error) => {
                    println!("{}", pretty(&json!({"error": error.message().to_string()})));
                    return ExitCode::SUCCESS;
                }
            }
        };
        records.push(record);
    }

    if !pdf_errors.is_empty() {
        println!(
            "{}",
            pretty(&json!({"error": pdf_errors.join("; "), "count": 0}))
        );
        return ExitCode::SUCCESS;
    }

    if dedupe {
        let mut seen_doi = HashSet::new();
        let mut seen_url = HashSet::new();
        records.retain(|record| {
            if let Some(doi) = &record.doi {
                return seen_doi.insert(doi.clone());
            }
            match &record.url {
                Some(url) => seen_url.insert(url.clone()),
                None => true,
            }
        });
    }

    let output = match resolved {
        "bibtex" => to_bibtex(&records),
        "csl-json" => match to_csl_json(&records) {
            Ok(text) => text,
            Err(error) => {
                println!("{}", pretty(&json!({"error": error.message().to_string()})));
                return ExitCode::SUCCESS;
            }
        },
        "apa" => apa_approx(&records),
        "mla" => mla_approx(&records),
        _ => unreachable!("citation_style validates the style"),
    };
    if output.trim().is_empty() {
        println!(
            "{}",
            pretty(&json!({
                "error": "no citable records: pass DOIs, URLs, or result dicts",
                "style": style,
                "count": 0,
            }))
        );
    } else {
        println!("{output}");
    }
    ExitCode::SUCCESS
}
