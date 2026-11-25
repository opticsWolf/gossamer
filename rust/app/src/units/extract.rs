//! `gossamer extract`: argument validation for the extraction contract.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use clap::ArgMatches;
use gossamer::downloader::download_bytes;
use gossamer::jsonfmt::py_dumps;
use gossamer::mcp_dispatch::extract_validation;
use gossamer::miscutils::sha256_bytes;
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

fn local_text(path: &Path, bytes: &[u8]) -> Option<String> {
    let ext = path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if matches!(ext.as_str(), "txt" | "md" | "csv") {
        return std::str::from_utf8(bytes).ok().map(str::to_string);
    }
    if ext == "json" {
        let value: Value = serde_json::from_slice(bytes).ok()?;
        return serde_json::to_string_pretty(&value).ok();
    }
    None
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

    let Some(source) = source else {
        return unsupported();
    };
    // Pages, store and structured extraction keep their existing contract;
    // this slice owns the text-like flat local-file path.
    if structured || store || !tables_as.eq_ignore_ascii_case("json") {
        return unsupported();
    }
    let is_url = source.starts_with("http://") || source.starts_with("https://");
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
                "Client error '{status} {reason}' for url '{source}'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{status}"
            );
            let payload = json!({"error": format!("Document extraction failed: {message}")});
            println!("{}", py_dumps(&payload));
            return ExitCode::SUCCESS;
        }
        let Ok(text) = String::from_utf8(bytes.clone()) else {
            return unsupported();
        };
        (
            source.clone(),
            bytes,
            text.to_string(),
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
        let Some(content) = local_text(&path, &bytes) else {
            return unsupported();
        };
        (
            path.to_string_lossy().replace('\\', "/"),
            bytes,
            content,
            Value::Null,
            Value::Null,
            Value::Null,
        )
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
        "content_hash": sha256_bytes(&bytes),
        "links": [],
        "guard": Value::Null,
        "stored": Value::Null,
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
