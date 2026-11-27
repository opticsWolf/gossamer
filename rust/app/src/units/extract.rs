//! `gossamer extract`: argument validation for the extraction contract.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use clap::ArgMatches;
use gossamer::document::{
    office_markdown, office_structured_payload, page_selection_error, pdf_markdown,
    spreadsheet_tables, structured_format_error, xml_feed_text,
};
use gossamer::downloader::download_bytes;
use gossamer::jsonfmt::py_dumps;
use gossamer::mcp_dispatch::extract_validation;
use gossamer::miscutils::sha256_bytes;
use gossamer::textlinks::scan_text_links;
use gossamer::tokens::count_tokens;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn timestamp() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let days = secs.div_euclid(86_400);
    let seconds = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    let year = year + i64::from(month <= 2);
    format!(
        "{year:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}+00:00",
        month,
        day,
        seconds / 3600,
        (seconds % 3600) / 60,
        seconds % 60,
        now.subsec_micros()
    )
}

fn local_text(path: &Path, bytes: &[u8], tables_as: &str) -> Option<String> {
    let ext = path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if matches!(ext.as_str(), "txt" | "md" | "csv") {
        return std::str::from_utf8(bytes).ok().map(str::to_string);
    }
    if ext == "pdf" {
        return pdf_markdown(bytes).ok();
    }
    if matches!(ext.as_str(), "xlsx" | "xls" | "xlsb") && matches!(tables_as, "csv" | "json") {
        return spreadsheet_tables(bytes, &ext, tables_as).ok();
    }
    if matches!(
        ext.as_str(),
        "docx" | "xlsx" | "pptx" | "doc" | "xls" | "ppt" | "xlsb"
    ) {
        return office_markdown(bytes, &ext).ok();
    }
    if ext == "json" {
        let value: Value = serde_json::from_slice(bytes).ok()?;
        return serde_json::to_string_pretty(&value).ok();
    }
    if matches!(ext.as_str(), "xml" | "rss" | "atom") {
        let text = std::str::from_utf8(bytes)
            .ok()?
            .trim_start_matches('\u{feff}');
        return Some(xml_feed_text(text).unwrap_or_else(|| text.to_string()));
    }
    None
}

fn platform_display(path: &Path) -> String {
    let text = path.to_string_lossy();
    if cfg!(windows) {
        text.replace('/', "\\")
    } else {
        text.replace('\\', "/")
    }
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
    let mut store_dir: Option<String> = None;
    let mut pages: Option<String> = None;
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
                if value == "--pages" {
                    pages = Some(args[index + 1].to_string_lossy().into_owned());
                } else if value == "--store-dir" {
                    store_dir = Some(args[index + 1].to_string_lossy().into_owned());
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

    let Some(source) = source else {
        return unsupported();
    };
    if let Some(pages) = pages.as_deref()
        && !pages.trim().is_empty()
    {
        if store {
            let payload = json!({"error": "store=True cannot be combined with pages=...; store the full document instead"});
            println!(
                "{}",
                serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
            );
            return ExitCode::SUCCESS;
        }
        let is_url = source.starts_with("http://") || source.starts_with("https://");
        if let Some(error) = page_selection_error(&source, is_url) {
            let payload = json!({"error": error});
            println!(
                "{}",
                serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
            );
            return ExitCode::SUCCESS;
        }
        return unsupported();
    }
    // Pages, store and structured extraction keep their existing contract;
    // this slice owns the flat local-file path plus spreadsheet renderings.
    let is_url = source.starts_with("http://") || source.starts_with("https://");
    if structured {
        if let Some(error) = structured_format_error(&source, is_url) {
            let display = source.replace('\\', "/");
            eprintln!("Structured extraction failed for {display}: {error}");
            let payload =
                json!({"error": format!("Structured document extraction failed: {error}")});
            println!(
                "{}",
                serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
            );
            return ExitCode::SUCCESS;
        }
        let suffix = Path::new(&source)
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !is_url
            && matches!(
                suffix.as_str(),
                "docx" | "xlsx" | "pptx" | "doc" | "xls" | "ppt" | "xlsb"
            )
        {
            let raw_path = PathBuf::from(&source);
            let path = if raw_path.is_absolute() {
                raw_path
            } else {
                std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join(raw_path)
            };
            let Ok(bytes) = std::fs::read(&path) else {
                return unsupported();
            };
            let file_name = path
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_else(|| "document".to_string());
            let payload =
                match office_structured_payload(&bytes, &suffix, &file_name, bytes.len() as u64) {
                    Ok(payload) => payload,
                    Err(_) => return unsupported(),
                };
            println!(
                "{}",
                serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
            );
            return ExitCode::SUCCESS;
        }
        return unsupported();
    }
    let url_suffix = Path::new(&source)
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if is_url
        && !url_suffix.is_empty()
        && !matches!(
            url_suffix.as_str(),
            "txt"
                | "md"
                | "csv"
                | "json"
                | "xml"
                | "rss"
                | "atom"
                | "pdf"
                | "docx"
                | "xlsx"
                | "pptx"
                | "doc"
                | "xls"
                | "ppt"
                | "xlsb"
        )
    {
        let payload = json!({"error": format!("Document extraction failed: Unsupported document format: .{url_suffix}")});
        println!("{}", py_dumps(&payload));
        return ExitCode::SUCCESS;
    }
    let (display, bytes, content, http_status, final_url, content_type) = if is_url {
        let (bytes, status, final_url, content_type) =
            match download_bytes(&source, 20.0, 50_000_000) {
                Ok(value) => value,
                Err(error) => {
                    let payload = json!({"error": format!("Document extraction failed: {error}")});
                    println!("{}", py_dumps(&payload));
                    return ExitCode::SUCCESS;
                }
            };
        if status >= 400 {
            let reason = match status {
                400 => "Bad Request",
                401 => "Unauthorized",
                403 => "Forbidden",
                404 => "Not Found",
                405 => "Method Not Allowed",
                408 => "Request Timeout",
                429 => "Too Many Requests",
                500 => "Internal Server Error",
                502 => "Bad Gateway",
                503 => "Service Unavailable",
                504 => "Gateway Timeout",
                _ => "HTTP Error",
            };
            let message = format!(
                "Client error '{status} {reason}' for url '{source}'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{status}"
            );
            let payload = json!({"error": format!("Document extraction failed: {message}")});
            println!("{}", py_dumps(&payload));
            return ExitCode::SUCCESS;
        }
        let raw = String::from_utf8_lossy(&bytes)
            .trim_start_matches('\u{feff}')
            .to_string();
        let content = if url_suffix == "json" {
            serde_json::from_slice::<Value>(&bytes)
                .ok()
                .and_then(|value| serde_json::to_string_pretty(&value).ok())
                .unwrap_or(raw)
        } else if matches!(url_suffix.as_str(), "xml" | "rss" | "atom") {
            xml_feed_text(&raw).unwrap_or(raw)
        } else if url_suffix == "pdf" {
            pdf_markdown(&bytes).unwrap_or(raw)
        } else if matches!(url_suffix.as_str(), "xlsx" | "xls" | "xlsb")
            && matches!(tables_as.as_str(), "csv" | "json")
        {
            spreadsheet_tables(&bytes, &url_suffix, &tables_as).unwrap_or(raw)
        } else if matches!(
            url_suffix.as_str(),
            "docx" | "xlsx" | "pptx" | "doc" | "xls" | "ppt" | "xlsb"
        ) {
            office_markdown(&bytes, &url_suffix).unwrap_or(raw)
        } else {
            raw
        };
        (
            source.clone(),
            bytes,
            content,
            json!(status),
            json!(final_url),
            json!(content_type),
        )
    } else {
        let raw_path = PathBuf::from(&source);
        let path = if raw_path.is_absolute() {
            raw_path
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(raw_path)
        };
        let Ok(bytes) = std::fs::read(&path) else {
            return unsupported();
        };
        let Some(content) = local_text(&path, &bytes, &tables_as) else {
            return unsupported();
        };
        (
            source.replace('\\', "/"),
            bytes,
            content,
            Value::Null,
            Value::Null,
            Value::Null,
        )
    };
    let stored = if store {
        let directory = PathBuf::from(store_dir.unwrap_or_else(|| "stored_documents".to_string()));
        if std::fs::create_dir_all(&directory).is_err() {
            return unsupported();
        }
        let raw_path = PathBuf::from(&source);
        let stem = raw_path
            .file_stem()
            .map(|value| value.to_string_lossy().into_owned())
            .unwrap_or_else(|| "document".to_string());
        let ext = raw_path
            .extension()
            .map(|value| format!(".{}", value.to_string_lossy()))
            .unwrap_or_default();
        let original_path = directory.join(format!("{stem}{ext}"));
        let markdown_path = directory.join(format!("{stem}.md"));
        let resources_dir = directory.join(format!("{stem}.files"));
        if std::fs::write(&original_path, &bytes).is_err()
            || std::fs::write(&markdown_path, content.as_bytes()).is_err()
        {
            return unsupported();
        }
        json!({
            "directory": platform_display(&directory),
            "original": platform_display(&original_path),
            "markdown": platform_display(&markdown_path),
            "original_bytes": bytes.len(),
            "markdown_chars": content.chars().count(),
            "content_type": content_type,
            "resources": {
                "dir": platform_display(&resources_dir),
                "files": [],
                "referenced": 0,
                "embedded": 0,
            },
        })
    } else {
        Value::Null
    };
    let payload = json!({
        "source": display,
        "content": content,
        "content_tokens": count_tokens(&content, "gpt-4o").unwrap_or(0),
        "cache_hit": false,
        "page_range": Value::Null,
        "page_start": 0,
        "page_end": 0,
        "total_pages": 0,
        "fetched_at": timestamp(),
        "http_status": http_status,
        "final_url": final_url,
        "content_type": content_type,
        "content_hash": sha256_bytes(content.as_bytes()),
        "links": scan_text_links(&content, 50)
            .into_iter()
            .map(Value::String)
            .collect::<Vec<_>>(),
        "guard": Value::Null,
        "stored": stored,
        "resolved_doi": Value::Null,
        "jats_source": Value::Null,
        "jats_title": Value::Null,
        "n_formulas": Value::Null,
        "n_figures": Value::Null,
        "n_tables": Value::Null,
    });
    println!("{}", py_dumps(&payload));
    ExitCode::SUCCESS
}
