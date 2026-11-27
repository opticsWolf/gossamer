//! `gossamer extract`: argument validation for the extraction contract.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use clap::ArgMatches;
use gossamer::budget::truncate_text;
use gossamer::document::{
    document_page_range, jats_tables_allows, office_markdown, office_structured_payload,
    page_selection_error, pdf_markdown, prepare_url, spreadsheet_tables, structured_format_error,
    xml_feed_text,
};
use gossamer::downloader::download_bytes;
use gossamer::jats::{
    convert_jats_str, doi_from_pdf_path, doi_from_source_string, europepmc_fulltext_url,
    is_xml_body, parse_europepmc_search, plos_file_url,
};
use gossamer::jsonfmt::{pretty_record, py_dumps};
use gossamer::mcp_dispatch::extract_validation;
use gossamer::miscutils::sha256_bytes;
use gossamer::runtime::{RuntimeConfig, RuntimeState};
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

fn extraction_error(source: &str, detail: &str) -> ExitCode {
    eprintln!("Document extraction failed for {source}: {detail}");
    let payload = json!({"error": format!("Document extraction failed: {detail}")});
    println!("{}", py_dumps(&payload));
    ExitCode::SUCCESS
}

struct JatsHit {
    bytes: Vec<u8>,
    content: String,
    cache_hit: bool,
    resolved_doi: Value,
    jats_source: Value,
    jats_title: Value,
    n_formulas: Value,
    n_figures: Value,
    n_tables: Value,
    fetched_at: Value,
    http_status: Value,
    final_url: Value,
    content_type: Value,
}

#[expect(
    clippy::large_enum_variant,
    reason = "the hit carries the extracted document on a cold path"
)]
enum JatsOutcome {
    NotApplicable,
    Hit(JatsHit),
    Error(String),
}

fn jats_fetch_bytes(
    runtime: &RuntimeState,
    url: &str,
    now: f64,
) -> Result<(Vec<u8>, Value), String> {
    let checked = runtime
        .prepare_url(url)
        .map_err(|error| error.message().to_string())?;
    if !runtime.robots_allows(&checked, now) {
        return Err(format!("disallowed by robots.txt: {checked}"));
    }
    let sleep = runtime.throttle_gap(&checked, None, now);
    if sleep > 0.0 {
        std::thread::sleep(std::time::Duration::from_secs_f64(sleep));
    }
    let (bytes, status, final_url, content_type) = download_bytes(url, 20.0, 50_000_000)?;
    if status >= 400 {
        return Err(format!("HTTP {status}"));
    }
    Ok((
        bytes,
        json!({
            "fetched_at": timestamp(),
            "http_status": status,
            "final_url": final_url,
            "content_type": content_type,
        }),
    ))
}

fn jats_cache_key(runtime: &RuntimeState, source: &str) -> String {
    let base = if source.starts_with("http://") || source.starts_with("https://") {
        runtime.identity_key(source)
    } else {
        source.to_string()
    };
    gossamer::document::jats_cache_key(&base)
}

fn finish_jats(
    runtime: &RuntimeState,
    key: &str,
    doi: &str,
    label: &str,
    conv: &gossamer::jats::JatsOutput,
    prov: &Value,
    bytes: &[u8],
) -> JatsHit {
    let fetched_at = prov
        .get("fetched_at")
        .cloned()
        .unwrap_or_else(|| json!(timestamp()));
    let final_url = prov.get("final_url").cloned().unwrap_or(Value::Null);
    let cache_prov = json!({
        "resolved_doi": doi,
        "jats_source": label,
        "jats_title": conv.title,
        "n_formulas": conv.n_formulas,
        "n_figures": conv.n_figures,
        "n_tables": conv.n_tables,
        "final_url": final_url,
        "content_type": prov.get("content_type").cloned().unwrap_or(Value::Null),
        "http_status": prov.get("http_status").cloned().unwrap_or(Value::Null),
        "fetched_at": fetched_at,
    });
    runtime.cache().put(
        key,
        &serde_json::to_string(&json!({
            "markdown": conv.markdown,
            "prov": cache_prov,
        }))
        .unwrap_or_default(),
        runtime.now(),
    );
    JatsHit {
        bytes: bytes.to_vec(),
        content: conv.markdown.clone(),
        cache_hit: false,
        resolved_doi: json!(doi),
        jats_source: json!(label),
        jats_title: if conv.title.is_empty() {
            Value::Null
        } else {
            json!(conv.title)
        },
        n_formulas: json!(conv.n_formulas),
        n_figures: json!(conv.n_figures),
        n_tables: json!(conv.n_tables),
        fetched_at,
        http_status: prov.get("http_status").cloned().unwrap_or(Value::Null),
        final_url,
        content_type: prov.get("content_type").cloned().unwrap_or(Value::Null),
    }
}

fn cached_jats(key: &str, runtime: &RuntimeState) -> Option<JatsHit> {
    let raw = runtime.cache().get(key, runtime.now())?;
    let envelope: Value = serde_json::from_str(&raw).ok()?;
    let content = envelope.get("markdown")?.as_str()?.to_string();
    let prov = envelope.get("prov").cloned().unwrap_or_else(|| json!({}));
    Some(JatsHit {
        bytes: Vec::new(),
        content,
        cache_hit: true,
        resolved_doi: prov.get("resolved_doi").cloned().unwrap_or(Value::Null),
        jats_source: prov
            .get("jats_source")
            .cloned()
            .unwrap_or_else(|| json!("cached")),
        jats_title: prov.get("jats_title").cloned().unwrap_or(Value::Null),
        n_formulas: prov.get("n_formulas").cloned().unwrap_or(Value::Null),
        n_figures: prov.get("n_figures").cloned().unwrap_or(Value::Null),
        n_tables: prov.get("n_tables").cloned().unwrap_or(Value::Null),
        fetched_at: prov.get("fetched_at").cloned().unwrap_or(Value::Null),
        http_status: prov.get("http_status").cloned().unwrap_or(Value::Null),
        final_url: prov.get("final_url").cloned().unwrap_or(Value::Null),
        content_type: prov.get("content_type").cloned().unwrap_or(Value::Null),
    })
}

fn plos_journal_for_doi(doi: &str) -> Option<String> {
    let rest = doi.strip_prefix("10.1371/journal.")?;
    let code = rest.split('.').next()?;
    // PLOS article DOI prefixes map to journal URL slugs.
    Some(match code {
        "pone" => "plosone".to_string(),
        "pbio" => "plosbiology".to_string(),
        "pgen" => "plosgenetics".to_string(),
        "pmed" => "plosmedicine".to_string(),
        "pcompbiol" => "ploscompbiol".to_string(),
        "ppat" => "plospathogens".to_string(),
        "pntd" => "plosntd".to_string(),
        other => other.to_string(),
    })
}

fn jats_resolvable_offline(source: &str, tables_as: &str) -> bool {
    if !jats_tables_allows(source, tables_as) {
        return false;
    }
    let path = Path::new(source);
    if path.is_file() {
        let ext = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if matches!(ext.as_str(), "xml" | "jats" | "nxml") {
            return true;
        }
        return ext == "pdf" && doi_from_pdf_path(source).ok().flatten().is_some();
    }
    doi_from_source_string(source).is_some()
}

fn jats_route(runtime: &RuntimeState, source: &str, tables_as: &str) -> JatsOutcome {
    if !jats_tables_allows(source, tables_as) {
        return JatsOutcome::NotApplicable;
    }
    let path = Path::new(source);
    let is_file = path.is_file();
    let doi = if is_file {
        let ext = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext != "pdf" {
            return JatsOutcome::NotApplicable;
        }
        let Ok(Some(doi)) = doi_from_pdf_path(source) else {
            return JatsOutcome::NotApplicable;
        };
        doi
    } else {
        let Some(doi) = doi_from_source_string(source) else {
            return JatsOutcome::NotApplicable;
        };
        doi
    };
    let key = jats_cache_key(runtime, source);
    if let Some(hit) = cached_jats(&key, runtime) {
        return JatsOutcome::Hit(hit);
    }

    let email = std::env::var("GOSSAMER_OPENALEX_EMAIL").unwrap_or_default();
    let encoded_doi: String = url::form_urlencoded::byte_serialize(doi.as_bytes()).collect();
    let mut search_url = format!(
        "{}?query=DOI:{}&format=json",
        gossamer::jats::EUROPEPMC_SEARCH_URL,
        encoded_doi
    );
    let email = email.trim();
    if !email.is_empty() {
        let encoded_email: String =
            url::form_urlencoded::byte_serialize(email.as_bytes()).collect();
        search_url.push_str(&format!("&email={encoded_email}"));
    }
    let picked = jats_fetch_bytes(runtime, &search_url, runtime.now())
        .ok()
        .and_then(|(bytes, _)| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|payload| parse_europepmc_search(&payload));
    if let Some(hit) = picked {
        let mut candidates = vec![europepmc_fulltext_url(&hit.source, &hit.id)];
        if !hit.source.eq_ignore_ascii_case("PMC") {
            candidates.push(europepmc_fulltext_url("PMC", &hit.pmcid));
        }
        for candidate in candidates {
            let Ok((bytes, prov)) = jats_fetch_bytes(runtime, &candidate, runtime.now()) else {
                continue;
            };
            if !is_xml_body(prov.get("content_type").and_then(Value::as_str), &bytes) {
                continue;
            }
            let text = String::from_utf8_lossy(&bytes);
            match convert_jats_str(&text) {
                Ok(conv) if !conv.markdown.trim().is_empty() => {
                    return JatsOutcome::Hit(finish_jats(
                        runtime,
                        &key,
                        &doi,
                        "europepmc",
                        &conv,
                        &prov,
                        &bytes,
                    ));
                }
                Ok(_) => continue,
                Err(error) if error.message().contains("not a JATS article") => continue,
                Err(error) => return JatsOutcome::Error(error.message().to_string()),
            }
        }
    }

    let journal = plos_journal_for_doi(&doi);
    if let Some(journal) = journal {
        let plos_url = plos_file_url(&journal, &doi);
        if let Ok((bytes, prov)) = jats_fetch_bytes(runtime, &plos_url, runtime.now())
            && is_xml_body(prov.get("content_type").and_then(Value::as_str), &bytes)
        {
            let text = String::from_utf8_lossy(&bytes);
            if let Ok(conv) = convert_jats_str(&text)
                && !conv.markdown.trim().is_empty()
            {
                return JatsOutcome::Hit(finish_jats(
                    runtime, &key, &doi, "plos", &conv, &prov, &bytes,
                ));
            }
        }
    }
    JatsOutcome::NotApplicable
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
    let mut cache_dir =
        std::env::var("GOSSAMER_CACHE_DIR").unwrap_or_else(|_| ".gossamer_cache".into());
    let mut prefer_jats = true;
    let mut source: Option<String> = None;
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        match value.as_str() {
            "--structured" => structured = true,
            "--store" => store = true,
            "--include-images" => include_images = true,
            "--prefer-jats" => prefer_jats = true,
            "--no-prefer-jats" => prefer_jats = false,
            "--pages" | "--store-dir" | "--cache-dir" | "--config" | "--keystore"
            | "--log-level" | "--fetch-mode" => {
                if index + 1 >= args.len() {
                    return unsupported();
                }
                if value == "--pages" {
                    pages = Some(args[index + 1].to_string_lossy().into_owned());
                } else if value == "--store-dir" {
                    store_dir = Some(args[index + 1].to_string_lossy().into_owned());
                } else if value == "--cache-dir" {
                    cache_dir = args[index + 1].to_string_lossy().into_owned();
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
        if prefer_jats && jats_resolvable_offline(&source, &tables_as) {
            let payload = json!({
                "error": format!(
                    "pages='{pages}' is not supported for structured-source (JATS) extraction; re-run without pages or with --no-prefer-jats"
                )
            });
            println!("{}", py_dumps(&payload));
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
    }
    // Pages, store and structured extraction keep their existing contract;
    // this slice owns the flat local-file path plus spreadsheet renderings.
    let is_url = source.starts_with("http://") || source.starts_with("https://");
    if is_url && let Err(error) = prepare_url(&source, None) {
        if error.error_type() == "SsrfBlockedError" {
            eprintln!(
                "Blocked non-public URL {}: {}",
                error.raw(),
                error.message()
            );
        }
        println!("{}", pretty_record(&error.to_record()));
        return ExitCode::SUCCESS;
    }
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
        && !jats_resolvable_offline(&source, &tables_as)
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
        return extraction_error(
            &source,
            &format!("Unsupported document format: .{url_suffix}"),
        );
    }
    let mut jats_resolved_doi = Value::Null;
    let mut fetched_at: Value = json!(timestamp());
    let mut jats_source = Value::Null;
    let mut jats_title = Value::Null;
    let mut n_formulas = Value::Null;
    let mut n_figures = Value::Null;
    let mut n_tables = Value::Null;
    let runtime = match RuntimeState::new(RuntimeConfig::from_cache_dir(PathBuf::from(&cache_dir)))
    {
        Ok(value) => value,
        Err(error) => {
            eprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
    let jats_hit = if prefer_jats && jats_resolvable_offline(&source, &tables_as) {
        match jats_route(&runtime, &source, &tables_as) {
            JatsOutcome::Hit(hit) => Some(hit),
            JatsOutcome::Error(error) => {
                let payload = json!({"error": format!("Document extraction failed: {error}")});
                println!("{}", py_dumps(&payload));
                return ExitCode::SUCCESS;
            }
            JatsOutcome::NotApplicable => None,
        }
    } else {
        None
    };
    let mut cache_hit = false;
    let mut jats_full_content: Option<String> = None;
    let (display, bytes, mut content, http_status, final_url, content_type) = if let Some(hit) =
        jats_hit
    {
        jats_resolved_doi = hit.resolved_doi;
        jats_source = hit.jats_source;
        jats_title = hit.jats_title;
        n_formulas = hit.n_formulas;
        n_figures = hit.n_figures;
        n_tables = hit.n_tables;
        cache_hit = hit.cache_hit;
        fetched_at = hit.fetched_at;
        let full = hit.content;
        let content = truncate_text(&full, 8000, 0, "gpt-4o").unwrap_or_else(|_| full.clone());
        jats_full_content = Some(full);
        // Local XML JATS uses its source spelling; URL/DOI hits use the source too.
        let display = source.clone();
        (
            display,
            hit.bytes,
            content,
            hit.http_status,
            hit.final_url,
            hit.content_type,
        )
    } else if is_url {
        let (bytes, status, final_url, content_type) =
            match download_bytes(&source, 20.0, 50_000_000) {
                Ok(value) => value,
                Err(error) => return extraction_error(&source, &error),
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
            return extraction_error(&source, &message);
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
        let ext = path
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        let jats_text = std::str::from_utf8(&bytes)
            .ok()
            .map(|text| text.trim_start_matches('\u{feff}').to_string());
        let jats_conv = if prefer_jats
            && jats_tables_allows(&source, &tables_as)
            && matches!(ext.as_str(), "xml" | "jats" | "nxml")
        {
            jats_text
                .as_deref()
                .and_then(|text| convert_jats_str(text).ok())
        } else {
            None
        };
        let content = if let Some(conv) = jats_conv {
            jats_source = json!("local");
            jats_title = if conv.title.is_empty() {
                Value::Null
            } else {
                json!(conv.title)
            };
            n_formulas = json!(conv.n_formulas);
            n_figures = json!(conv.n_figures);
            n_tables = json!(conv.n_tables);
            conv.markdown
        } else {
            let Some(content) = local_text(&path, &bytes, &tables_as) else {
                return unsupported();
            };
            content
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
    let mut full_content = jats_full_content.unwrap_or_else(|| content.clone());
    let mut page_range = Value::Null;
    let mut page_start = 0i64;
    let mut page_end = 0i64;
    let mut total_pages = 0i64;
    if let Some(spec) = pages.as_deref()
        && !spec.trim().is_empty()
    {
        let plain = if is_url {
            source.split(['?', '#']).next().unwrap_or(&source)
        } else {
            &source
        };
        let suffix = Path::new(plain)
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        match document_page_range(&bytes, &suffix, spec, &source) {
            Ok(range) => {
                content = range.content;
                page_range = Value::String(spec.to_string());
                page_start = range.page_start;
                page_end = range.page_end;
                total_pages = range.total_pages;
            }
            Err(error) => {
                let payload = json!({"error": error});
                println!("{}", py_dumps(&payload));
                return ExitCode::SUCCESS;
            }
        }
        full_content = content.clone();
    }
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
            || std::fs::write(&markdown_path, full_content.as_bytes()).is_err()
        {
            return unsupported();
        }
        json!({
            "directory": platform_display(&directory),
            "original": platform_display(&original_path),
            "markdown": platform_display(&markdown_path),
            "original_bytes": bytes.len(),
            "markdown_chars": full_content.chars().count(),
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
        "cache_hit": cache_hit,
        "page_range": page_range,
        "page_start": page_start,
        "page_end": page_end,
        "total_pages": total_pages,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "final_url": final_url,
        "content_type": content_type,
        "content_hash": sha256_bytes(full_content.as_bytes()),
        "links": scan_text_links(&full_content, 50)
            .into_iter()
            .map(Value::String)
            .collect::<Vec<_>>(),
        "guard": Value::Null,
        "stored": stored,
        "resolved_doi": jats_resolved_doi,
        "jats_source": jats_source,
        "jats_title": jats_title,
        "n_formulas": n_formulas,
        "n_figures": n_figures,
        "n_tables": n_tables,
    });
    println!("{}", py_dumps(&payload));
    ExitCode::SUCCESS
}
