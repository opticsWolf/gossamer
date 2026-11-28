//! `gossamer cite`: citation formatting over Rust citation kernels.

use std::collections::HashSet;
use std::fs;
use std::path::Path;
use std::process::ExitCode;

use gossamer::cite::{
    Record, apa_approx, citation_input, citation_record_from_json, citation_style,
    find_doi_in_text, mla_approx, to_bibtex, to_csl_json,
};
use gossamer::cli_args::Parsed;
use gossamer::document::pdf_markdown;
use gossamer::openaccess::normalize_doi;
use serde_json::{Value, json};

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

pub fn run(parsed: &Parsed) -> ExitCode {
    let style = parsed.value("style").unwrap_or("bibtex").to_string();
    let dedupe = !parsed.flag("no_dedupe");
    let from_pdf = parsed.flag("from_pdf");
    let results: Vec<String> = parsed.positionals("results").to_vec();

    let resolved = match citation_style(Some(&style)) {
        Ok(style) => style,
        Err(error) => {
            pyprintln!("{}", pretty(&json!({"error": error})));
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
                            pyprintln!(
                                "{}",
                                pretty(&json!({"error": error.message().to_string()}))
                            );
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
                    pyprintln!("{}", pretty(&json!({"error": error.message().to_string()})));
                    return ExitCode::SUCCESS;
                }
            }
        } else {
            let fields = citation_input(item.trim());
            match citation_record_from_json(&fields.to_string()) {
                Ok(record) => record,
                Err(error) => {
                    pyprintln!("{}", pretty(&json!({"error": error.message().to_string()})));
                    return ExitCode::SUCCESS;
                }
            }
        };
        records.push(record);
    }

    if !pdf_errors.is_empty() {
        pyprintln!(
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
                pyprintln!("{}", pretty(&json!({"error": error.message().to_string()})));
                return ExitCode::SUCCESS;
            }
        },
        "apa" => apa_approx(&records),
        "mla" => mla_approx(&records),
        _ => unreachable!("citation_style validates the style"),
    };
    if output.trim().is_empty() {
        pyprintln!(
            "{}",
            pretty(&json!({
                "error": "no citable records: pass DOIs, URLs, or result dicts",
                "style": style,
                "count": 0,
            }))
        );
    } else {
        pyprintln!("{output}");
    }
    ExitCode::SUCCESS
}
