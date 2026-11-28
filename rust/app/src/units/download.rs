//! `gossamer download`: argument/URL validation for the download contract.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use gossamer::cli_args::Parsed;
use gossamer::downloader::{
    candidate_list, download_bytes, download_range_bytes, finalize_candidates, http_error,
    validate_finished_file,
};
use gossamer::jsonfmt::py_dumps_pretty;
use gossamer::miscutils::sha256_bytes;
use gossamer::runtime::RuntimeState;
use gossamer::ssrf::check_url;
use gossamer::urls::normalize_url;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    pyeprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn pretty(value: &Value) -> String {
    py_dumps_pretty(value)
}

fn config_int(parsed: &Parsed, key: &str, env: &str, default: i64) -> i64 {
    crate::units::settings::int(parsed, key, env, default)
}

fn expand_user(path: &str) -> String {
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_default();
    if home.is_empty() {
        return path.to_string();
    }
    if path == "~" {
        return home;
    }
    if let Some(rest) = path.strip_prefix("~/").or_else(|| path.strip_prefix("~\\")) {
        return Path::new(&home).join(rest).to_string_lossy().into_owned();
    }
    path.to_string()
}

fn display_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    if cfg!(windows) {
        text.replace('/', "\\")
    } else {
        text.replace('\\', "/")
    }
}

fn parse_too_large_error(error: &str) -> Option<Value> {
    let rest = error.strip_prefix("too_large\u{1e}")?;
    let parts: Vec<&str> = rest.split('\u{1e}').collect();
    if parts.len() != 5 {
        return None;
    }
    let status = parts[0].parse::<i64>().ok()?;
    let amount = parts[3].parse::<i64>().ok()?;
    let limit = parts[4].parse::<i64>().ok()?;
    let message = if parts[2] == "declared" {
        format!("The server declared {amount} bytes; limit is {limit}.")
    } else {
        format!("The download exceeded the {limit}-byte limit.")
    };
    Some(json!({
        "code": "too_large",
        "message": message,
        "http_status": status,
        "url": parts[1],
    }))
}

fn emit_error_payload(source: &str, error: Value) -> ExitCode {
    pyprintln!(
        "{}",
        pretty(&json!({"source": source, "status": "error", "error": error}))
    );
    ExitCode::FAILURE
}

fn emit_error(source: &str, code: &str, message: &str) -> ExitCode {
    pyprintln!(
        "{}",
        pretty(&json!({
            "source": source,
            "status": "error",
            "error": {"code": code, "message": message},
        }))
    );
    ExitCode::FAILURE
}

fn emit_error_details(source: &str, code: &str, message: &str, details: &Value) -> ExitCode {
    let mut error = json!({"code": code, "message": message});
    if let (Some(object), Some(extra)) = (error.as_object_mut(), details.as_object()) {
        for (key, value) in extra {
            object.insert(key.clone(), value.clone());
        }
    }
    pyprintln!(
        "{}",
        pretty(&json!({"source": source, "status": "error", "error": error}))
    );
    ExitCode::FAILURE
}

fn resume_state_path(target: &Path) -> PathBuf {
    let name = target
        .file_name()
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_else(|| "download".to_string());
    target
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(format!("{name}.gossamer-resume.json"))
}

fn load_resume_validators(path: &Path) -> (Option<String>, Option<String>) {
    let Ok(text) = std::fs::read_to_string(path) else {
        return (None, None);
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return (None, None);
    };
    let read = |key: &str| {
        value
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|text| !text.is_empty())
            .map(str::to_string)
    };
    (read("etag"), read("last_modified"))
}

fn store_resume_validators(path: &Path, headers: &HashMap<String, String>) {
    let mut state = serde_json::Map::new();
    if let Some(value) = headers
        .get("etag")
        .map(|value| value.trim())
        .filter(|text| !text.is_empty())
    {
        state.insert("etag".to_string(), Value::String(value.to_string()));
    }
    if let Some(value) = headers
        .get("last-modified")
        .map(|value| value.trim())
        .filter(|text| !text.is_empty())
    {
        state.insert(
            "last_modified".to_string(),
            Value::String(value.to_string()),
        );
    }
    if state.is_empty() {
        return;
    }
    if let Some(parent) = path.parent()
        && std::fs::create_dir_all(parent).is_err()
    {
        return;
    }
    let _ = std::fs::write(path, serde_json::Value::Object(state).to_string());
}

fn clear_resume_state(path: &Path) {
    let _ = std::fs::remove_file(path);
}

fn content_range_matches(value: Option<&String>, want_start: usize) -> bool {
    let Some(value) = value else {
        return false;
    };
    let text = value.trim().to_ascii_lowercase();
    let Some(rest) = text.strip_prefix("bytes ") else {
        return false;
    };
    let (range_part, _total) = rest.split_once('/').unwrap_or((rest, ""));
    let Some((start_text, end_text)) = range_part.split_once('-') else {
        return false;
    };
    let Ok(start) = start_text.trim().parse::<usize>() else {
        return false;
    };
    end_text.trim().parse::<usize>().is_ok() && start == want_start
}

fn download_one(
    url: &str,
    target: &PathBuf,
    cap: usize,
    min_bytes: i64,
    expect_pdf: bool,
    target_existed: bool,
) -> Value {
    let (bytes, status, final_url, content_type) = match download_bytes(url, 30.0, cap) {
        Ok(value) => value,
        Err(error) => {
            if let Some(payload) = parse_too_large_error(&error) {
                return json!({"source": url, "status": "error", "error": payload});
            }
            return json!({
                "source": url,
                "status": "error",
                "error": {"code": "network_error", "message": error},
            });
        }
    };
    if status >= 400 {
        let payload = http_error(
            status as i64,
            content_type.as_deref().unwrap_or(""),
            Some(&bytes),
            &final_url,
            None,
        );
        return json!({"source": url, "status": "error", "error": payload});
    }
    if let Some(payload) = validate_finished_file(
        Some(&bytes[..bytes.len().min(5)]),
        Some(&bytes),
        content_type.as_deref(),
        bytes.len() as i64,
        min_bytes,
        expect_pdf,
        status as i64,
        &final_url,
    ) {
        return json!({"source": url, "status": "error", "error": payload});
    }
    if target_existed {
        return json!({
            "source": url,
            "status": "error",
            "error": {
                "code": "file_exists",
                "message": format!("Output already exists: {}", target.display()),
                "output_path": display_path(target),
            },
        });
    }
    if std::fs::write(target, &bytes).is_err() {
        return json!({
            "source": url,
            "status": "error",
            "error": {"code": "local_write_error", "message": "could not write output file"},
        });
    }
    json!({
        "source": url,
        "final_url": final_url,
        "output_path": display_path(target),
        "bytes": bytes.len(),
        "sha256": sha256_bytes(&bytes),
        "content_type": content_type,
        "http_status": status,
        "status": "downloaded",
    })
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let Some(source) = parsed.positional("source").map(str::to_string) else {
        return unsupported();
    };
    let Some(output) = parsed.value("output_path").map(str::to_string) else {
        return unsupported();
    };
    let overwrite = parsed.flag("overwrite");
    let min_bytes = parsed.int("min_bytes").unwrap_or(1);
    let max_bytes_raw = parsed.int("max_bytes").unwrap_or(0);
    let mut expect_pdf = parsed.value("expect_format").unwrap_or("auto") == "pdf";
    let resume = parsed.flag("resume");
    let mirrors: Vec<String> = parsed.values("try_mirrors").to_vec();

    let url = if mirrors.is_empty() {
        match normalize_url(Some(&source), None, None) {
            Ok(url) => url,
            Err(error) => {
                return emit_error(
                    &source,
                    "invalid_url",
                    error
                        .message()
                        .strip_prefix("ValueError: ")
                        .unwrap_or(error.message()),
                );
            }
        }
    } else {
        source.clone()
    };
    if mirrors.is_empty()
        && let Err(error) = check_url(&url, gossamer::ssrf::env_allow_private(), None)
    {
        pyeprintln!("Blocked non-public URL {}: {}", url, error.message());
        return emit_error(
            &url,
            "url_rejected",
            error
                .message()
                .strip_prefix("SsrfBlockedError: ")
                .unwrap_or(error.message()),
        );
    }
    let cache_dir = crate::units::settings::cache_dir(parsed);
    if mirrors.is_empty() {
        let runtime = match RuntimeState::new(crate::units::settings::runtime(parsed, &cache_dir)) {
            Ok(runtime) => runtime,
            Err(error) => {
                pyeprintln!("gossamer: error: {error}");
                return ExitCode::FAILURE;
            }
        };
        if !runtime.robots_allows(&url, runtime.now()) {
            return emit_error(
                &url,
                "robots_disallowed",
                "The URL is disallowed by robots.txt; no request was made.",
            );
        }
        let sleep = runtime.throttle_gap(&url, None, runtime.now());
        if sleep > 0.0 {
            std::thread::sleep(std::time::Duration::from_secs_f64(sleep));
        }
    }
    let expanded = expand_user(&output);
    let name = Path::new(&expanded)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("");
    if name.is_empty() || matches!(name, "." | "..") {
        return emit_error(&url, "invalid_argument", "output_path must name a file");
    }
    let target = PathBuf::from(&expanded);
    let target = if target.is_absolute() {
        target
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(target)
    };
    expect_pdf |= target
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| ext.eq_ignore_ascii_case("pdf"));
    let configured_cap = config_int(
        parsed,
        "max_response_bytes",
        "GOSSAMER_MAX_RESPONSE_BYTES",
        5 * 1024 * 1024,
    );
    let cap_i64 = if max_bytes_raw == 0 {
        configured_cap
    } else {
        max_bytes_raw
    };
    if min_bytes < 0 {
        return emit_error(&url, "invalid_argument", "min_bytes must be >= 0");
    }
    if cap_i64 < 1 {
        return emit_error(&url, "invalid_argument", "max_bytes must be >= 1");
    }
    if min_bytes > cap_i64 {
        return emit_error(
            &url,
            "invalid_argument",
            "min_bytes cannot exceed max_bytes",
        );
    }
    let cap = cap_i64 as usize;
    let resumable_file = resume && target.is_file();
    if target.exists() && !overwrite && !resumable_file {
        return emit_error_details(
            &url,
            "file_exists",
            &format!("Output already exists: {}", target.display()),
            &json!({"output_path": display_path(&target)}),
        );
    }
    let target_existed = target.exists() && !overwrite;
    if let Some(parent) = target.parent()
        && std::fs::create_dir_all(parent).is_err()
    {
        return emit_error(
            &url,
            "local_write_error",
            "could not create output directory",
        );
    }
    let state_path = resume_state_path(&target);
    if resume && !overwrite && target.is_file() {
        let resume_offset = match std::fs::metadata(&target) {
            Ok(metadata) => metadata.len() as usize,
            Err(error) => {
                return emit_error(&url, "local_write_error", &error.to_string());
            }
        };
        if resume_offset > 0 {
            let (etag, last_modified) = load_resume_validators(&state_path);
            let result = download_range_bytes(
                &url,
                resume_offset,
                etag.as_deref(),
                last_modified.as_deref(),
                30.0,
                cap,
            );
            let (status, final_url, content_type, headers, body) = match result {
                Ok(value) => value,
                Err(error) => {
                    if let Some(payload) = parse_too_large_error(&error) {
                        return emit_error_payload(&url, payload);
                    }
                    return emit_error(&url, "network_error", &error);
                }
            };
            if status == 416 {
                return emit_error_details(
                    &url,
                    "resume_unsatisfiable",
                    "The server rejected the resume offset; the partial file was preserved.",
                    &json!({
                        "http_status": status,
                        "url": final_url,
                        "output_path": display_path(&target),
                        "resume_offset": resume_offset,
                    }),
                );
            }
            if status == 206 {
                if !content_range_matches(headers.get("content-range"), resume_offset) {
                    return emit_error_details(
                        &url,
                        "resume_mismatch",
                        "The server's Content-Range did not match the resume offset; the partial file was preserved.",
                        &json!({
                            "http_status": status,
                            "url": final_url,
                            "output_path": display_path(&target),
                            "resume_offset": resume_offset,
                        }),
                    );
                }
                if resume_offset + body.len() > cap {
                    return emit_error_details(
                        &url,
                        "too_large",
                        &format!("The download exceeded the {cap}-byte limit."),
                        &json!({"http_status": status, "url": final_url}),
                    );
                }
                let existing = match std::fs::read(&target) {
                    Ok(bytes) => bytes,
                    Err(error) => return emit_error(&url, "local_write_error", &error.to_string()),
                };
                let mut combined = existing;
                combined.extend_from_slice(&body);
                let sample = combined[..combined.len().min(1024)].to_vec();
                if let Some(payload) = validate_finished_file(
                    Some(&combined[..combined.len().min(5)]),
                    Some(&sample),
                    content_type.as_deref(),
                    combined.len() as i64,
                    min_bytes,
                    expect_pdf,
                    status as i64,
                    &final_url,
                ) {
                    return emit_error_payload(&url, payload);
                }
                if std::fs::write(&target, &combined).is_err() {
                    return emit_error(&url, "local_write_error", "could not write output file");
                }
                store_resume_validators(&state_path, &headers);
                clear_resume_state(&state_path);
                pyprintln!(
                    "{}",
                    pretty(&json!({
                        "source": url,
                        "final_url": final_url,
                        "output_path": display_path(&target),
                        "bytes": combined.len(),
                        "sha256": sha256_bytes(&combined),
                        "content_type": content_type,
                        "http_status": status,
                        "status": "downloaded",
                        "resumed": true,
                        "resume_offset": resume_offset,
                    }))
                );
                return ExitCode::SUCCESS;
            }
            if status == 200 {
                if let Some(payload) = validate_finished_file(
                    Some(&body[..body.len().min(5)]),
                    Some(&body[..body.len().min(1024)]),
                    content_type.as_deref(),
                    body.len() as i64,
                    min_bytes,
                    expect_pdf,
                    status as i64,
                    &final_url,
                ) {
                    return emit_error_payload(&url, payload);
                }
                if std::fs::write(&target, &body).is_err() {
                    return emit_error(&url, "local_write_error", "could not write output file");
                }
                store_resume_validators(&state_path, &headers);
                clear_resume_state(&state_path);
                pyprintln!(
                    "{}",
                    pretty(&json!({
                        "source": url,
                        "final_url": final_url,
                        "output_path": display_path(&target),
                        "bytes": body.len(),
                        "sha256": sha256_bytes(&body),
                        "content_type": content_type,
                        "http_status": status,
                        "status": "downloaded",
                        "resumed": false,
                        "resume_restarted": true,
                    }))
                );
                return ExitCode::SUCCESS;
            }
            let payload = http_error(
                status as i64,
                content_type.as_deref().unwrap_or(""),
                Some(&body),
                &final_url,
                None,
            );
            return emit_error_payload(&url, payload);
        }
    }
    if !mirrors.is_empty() {
        let list = match candidate_list(
            Some(&Value::String(source.clone())),
            Some(&Value::Array(
                mirrors.iter().cloned().map(Value::String).collect(),
            )),
        ) {
            Ok(list) => list,
            Err(error) => {
                let (code, message) = error
                    .split_once('\0')
                    .unwrap_or(("invalid_argument", &error));
                return emit_error(&source, code, message);
            }
        };
        let mut attempts = Vec::new();
        let mut results = Vec::new();
        for candidate in &list {
            let candidate_url = match normalize_url(Some(candidate), None, None) {
                Ok(value) => value,
                Err(error) => {
                    let result = json!({
                        "source": candidate,
                        "status": "error",
                        "error": {
                            "code": "invalid_url",
                            "message": error.message().strip_prefix("ValueError: ").unwrap_or(error.message()),
                        },
                    });
                    attempts.push(
                        json!({"url": candidate, "status": "error", "error": result["error"]}),
                    );
                    results.push(result);
                    continue;
                }
            };
            if let Err(error) = check_url(&candidate_url, gossamer::ssrf::env_allow_private(), None)
            {
                pyeprintln!(
                    "Blocked non-public URL {}: {}",
                    candidate_url,
                    error.message()
                );
                let message = error
                    .message()
                    .strip_prefix("SsrfBlockedError: ")
                    .unwrap_or(error.message());
                let result = json!({
                    "source": candidate_url,
                    "status": "error",
                    "error": {"code": "url_rejected", "message": message},
                });
                attempts
                    .push(json!({"url": candidate, "status": "error", "error": result["error"]}));
                results.push(result);
                continue;
            }
            let result = download_one(
                &candidate_url,
                &target,
                cap,
                min_bytes,
                expect_pdf,
                target_existed,
            );
            let terminal = result.get("status").and_then(Value::as_str) == Some("downloaded")
                || result
                    .get("error")
                    .and_then(Value::as_object)
                    .and_then(|error| error.get("code"))
                    .and_then(Value::as_str)
                    .is_some_and(|code| matches!(code, "invalid_argument" | "file_exists"));
            attempts.push(json!({
                "url": candidate,
                "status": result.get("status").cloned().unwrap_or(Value::Null),
                "error": result.get("error").cloned().unwrap_or(Value::Null),
            }));
            results.push(result);
            if terminal {
                break;
            }
        }
        let payload = finalize_candidates(&source, &list, &attempts, &results);
        pyprintln!("{}", pretty(&payload));
        return if payload.get("error").is_some() {
            ExitCode::FAILURE
        } else {
            ExitCode::SUCCESS
        };
    }
    let payload = download_one(&url, &target, cap, min_bytes, expect_pdf, target_existed);
    pyprintln!("{}", pretty(&payload));
    if payload.get("error").is_some() {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}
