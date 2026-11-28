//! `gossamer extract`: argument validation for the extraction contract.

use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use gossamer::budget::truncate_text;
use gossamer::cli_args::Parsed;
use gossamer::document::{
    document_page_range, jats_tables_allows, office_markdown, office_structured_payload,
    page_selection_error, pdf_markdown, pdf_structured_payload, prepare_url, spreadsheet_tables,
    xml_feed_text,
};
use gossamer::downloader::download_bytes;
use gossamer::jats::{
    convert_jats_str, doi_from_pdf_path, doi_from_source_string, europepmc_fulltext_url,
    is_xml_body, parse_europepmc_search, plos_file_url,
};
use gossamer::jsonfmt::{pretty_record, py_dumps_pretty, pydantic_json};
use gossamer::mcp_dispatch::extract_validation;
use gossamer::miscutils::sha256_bytes;
use gossamer::runtime::RuntimeState;
use gossamer::textlinks::scan_text_links;
use gossamer::tokens::count_tokens;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    pyeprintln!("gossamer: error: unsupported command shape");
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

fn unsupported_format(suffix: &str) -> String {
    match suffix {
        ".odt" => "Unsupported document format: .odt (convert the file to .docx).".to_string(),
        ".ods" => "Unsupported document format: .ods (convert the file to .xlsx).".to_string(),
        ".odp" => "Unsupported document format: .odp (convert the file to .pptx).".to_string(),
        ".rtf" => {
            "Unsupported document format: .rtf (convert the file to .docx or PDF).".to_string()
        }
        ".epub" => "Unsupported document format: .epub (convert the file to PDF).".to_string(),
        other => format!("Unsupported document format: {other}"),
    }
}

/// Extract readable text from local bytes using the full path for the suffix.
fn local_text(path: &Path, bytes: &[u8], tables_as: &str) -> Result<String, String> {
    let ext = path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let suffix = if ext.is_empty() {
        String::new()
    } else {
        format!(".{ext}")
    };
    let raw_text = || -> String {
        let text = String::from_utf8_lossy(bytes);
        text.strip_prefix('\u{feff}').unwrap_or(&text).to_string()
    };
    if matches!(ext.as_str(), "txt" | "md" | "csv") {
        return Ok(raw_text());
    }
    if ext == "pdf" {
        return pdf_markdown(bytes).map_err(|error| error.to_string());
    }
    if matches!(ext.as_str(), "xlsx" | "xls" | "xlsb") && matches!(tables_as, "csv" | "json") {
        return spreadsheet_tables(bytes, &ext, tables_as).map_err(|error| error.to_string());
    }
    if matches!(
        ext.as_str(),
        "docx" | "xlsx" | "pptx" | "doc" | "xls" | "ppt" | "xlsb"
    ) {
        return office_markdown(bytes, &ext).map_err(|error| error.to_string());
    }
    if ext == "json" {
        let raw = raw_text();
        return Ok(match serde_json::from_str::<Value>(&raw) {
            Ok(value) => gossamer::jsonfmt::py_dumps_pretty_utf8(&value),
            Err(_) => raw,
        });
    }
    if matches!(ext.as_str(), "xml" | "rss" | "atom") {
        let text = raw_text();
        return Ok(xml_feed_text(&text).unwrap_or(text));
    }
    Err(unsupported_format(&suffix))
}

/// Read local bytes with the same existence and size guards as `_extract_local`.
fn read_local_bytes(source: &str, path: &Path, max_bytes: u64) -> Result<Vec<u8>, String> {
    if !path.exists() {
        return Err(format!("File not found: {source}"));
    }
    if let Ok(metadata) = path.metadata()
        && metadata.len() > max_bytes
    {
        return Err(format!(
            "Document too large: {} bytes (cap {max_bytes})",
            metadata.len()
        ));
    }
    let bytes = std::fs::read(path).map_err(|error| error.to_string())?;
    if bytes.len() as u64 > max_bytes {
        return Err(format!("Document too large: exceeds {max_bytes} bytes"));
    }
    Ok(bytes)
}

fn extraction_error(source: &str, detail: &str) -> ExitCode {
    pyeprintln!("Document extraction failed for {source}: {detail}");
    let payload = json!({"error": format!("Document extraction failed: {detail}")});
    pyprintln!("{}", py_dumps_pretty(&payload));
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

fn jats_store_stem(source: &str) -> String {
    let mut out = String::new();
    let mut gap = false;
    for ch in source.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
            gap = false;
        } else if !gap {
            out.push('_');
            gap = true;
        }
    }
    let trimmed = out.trim_matches('_');
    let stem: String = trimmed.chars().take(120).collect();
    if stem.is_empty() {
        "document".to_string()
    } else {
        stem
    }
}

fn content_type_format(content_type: &str) -> Option<&'static str> {
    match content_type
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "application/pdf" => Some("pdf"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" => Some("docx"),
        "application/vnd.msword" => Some("doc"),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" => Some("xlsx"),
        "application/vnd.ms-excel" => Some("xls"),
        "application/vnd.ms-excel.sheet.binary.macroenabled.12" => Some("xlsb"),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation" => Some("pptx"),
        "application/vnd.ms-powerpoint" => Some("ppt"),
        _ => None,
    }
}

fn text_like_kind(content_type: &str) -> Option<&'static str> {
    match content_type
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "text/plain" | "text/markdown" | "text/csv" => Some("text"),
        "application/json" => Some("json"),
        "text/xml" | "application/xml" | "application/rss+xml" | "application/atom+xml" => {
            Some("xml")
        }
        _ => None,
    }
}

fn parse_document_bytes(bytes: &[u8], format: &str, tables_as: &str) -> Result<String, String> {
    match format {
        "pdf" => pdf_markdown(bytes),
        "xlsx" | "xls" | "xlsb" if matches!(tables_as, "csv" | "json") => {
            spreadsheet_tables(bytes, format, tables_as)
        }
        "docx" | "xlsx" | "pptx" | "doc" | "xls" | "ppt" | "xlsb" => office_markdown(bytes, format),
        other => Err(format!("Unsupported document format: .{other}")),
    }
}

fn extract_url_body(
    bytes: &[u8],
    source: &str,
    content_type: &str,
    tables_as: &str,
) -> Result<String, String> {
    let suffix = Path::new(source)
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| format!(".{}", value.to_ascii_lowercase()))
        .unwrap_or_default();
    let raw = || {
        String::from_utf8_lossy(bytes)
            .trim_start_matches('\u{feff}')
            .to_string()
    };
    let direct = match suffix.as_str() {
        ".pdf" => pdf_markdown(bytes),
        ".xlsx" | ".xls" | ".xlsb" if matches!(tables_as, "csv" | "json") => {
            spreadsheet_tables(bytes, &suffix[1..], tables_as)
        }
        ".docx" | ".xlsx" | ".pptx" | ".doc" | ".xls" | ".ppt" | ".xlsb" => {
            office_markdown(bytes, &suffix[1..])
        }
        ".txt" | ".md" | ".csv" => Ok(raw()),
        ".json" => Ok(serde_json::from_slice::<Value>(bytes)
            .ok()
            .map(|value| gossamer::jsonfmt::py_dumps_pretty_utf8(&value))
            .unwrap_or_else(raw)),
        ".xml" | ".rss" | ".atom" => {
            let text = raw();
            Ok(xml_feed_text(&text).unwrap_or(text))
        }
        ".odt" => Err("Unsupported document format: .odt (convert the file to .docx).".to_string()),
        ".ods" => Err("Unsupported document format: .ods (convert the file to .xlsx).".to_string()),
        ".odp" => Err("Unsupported document format: .odp (convert the file to .pptx).".to_string()),
        ".rtf" => {
            Err("Unsupported document format: .rtf (convert the file to .docx or PDF).".to_string())
        }
        ".epub" => Err("Unsupported document format: .epub (convert the file to PDF).".to_string()),
        _ => Err(format!("Unsupported document format: {suffix}")),
    };
    if direct.is_ok() {
        return direct;
    }
    if let Some(format) = content_type_format(content_type) {
        return parse_document_bytes(bytes, format, tables_as);
    }
    match text_like_kind(content_type) {
        Some("json") => Ok(serde_json::from_slice::<Value>(bytes)
            .ok()
            .map(|value| gossamer::jsonfmt::py_dumps_pretty_utf8(&value))
            .unwrap_or_else(raw)),
        Some("xml") => {
            let text = raw();
            Ok(xml_feed_text(&text).unwrap_or(text))
        }
        Some(_) => Ok(raw()),
        None => direct,
    }
}

fn document_download_error(error: &str) -> String {
    let Some(rest) = error.strip_prefix("too_large\u{1e}") else {
        return error.to_string();
    };
    let parts: Vec<&str> = rest.split('\u{1e}').collect();
    if parts.len() != 5 {
        return error.to_string();
    }
    let Ok(amount) = parts[3].parse::<u64>() else {
        return error.to_string();
    };
    let Ok(cap) = parts[4].parse::<u64>() else {
        return error.to_string();
    };
    if parts[2] == "declared" {
        format!("Document too large: declared {amount} bytes (cap {cap})")
    } else {
        format!("Document too large: exceeds {cap} bytes")
    }
}

fn page_range_failure(source: &str, detail: &str) -> ExitCode {
    pyeprintln!("Page-range extraction failed for {source}: {detail}");
    pyprintln!(
        "{}",
        py_dumps_pretty(&json!({
            "error": format!("Page-range extraction failed: {detail}")
        }))
    );
    ExitCode::SUCCESS
}

enum GuardedDocument {
    Delivered(String, Value),
    Withheld(Value),
}

fn apply_document_guard(content: &str, source: &str, parsed: &Parsed) -> GuardedDocument {
    let Some(config) = crate::units::guard::config(parsed) else {
        return GuardedDocument::Delivered(content.to_string(), Value::Null);
    };
    let pairs = [("document_text", content.to_string())];
    let Some(outcome) = crate::units::guard::evaluate(&config, &pairs, "document_text") else {
        return GuardedDocument::Delivered(content.to_string(), Value::Null);
    };
    if outcome.withheld {
        return GuardedDocument::Withheld(json!({
            "error": "content withheld by prompt-injection guard",
            "source": source,
            "guard": outcome.block,
        }));
    }
    let text = crate::units::guard::annotate(&config, &outcome, content, source);
    GuardedDocument::Delivered(text, outcome.block)
}

fn fit_structured_document(payload_json: &str, source: &str, parsed: &Parsed) -> String {
    let max_chars = config_int(
        parsed,
        "max_markdown_chars",
        "GOSSAMER_MAX_MARKDOWN_CHARS",
        8000,
    );
    let max_tokens = config_int(parsed, "max_tokens", "GOSSAMER_MAX_TOKENS", 0);
    let model = config_text(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
    let fits = |text: &str| {
        gossamer::budget::json_fits(text, max_chars, max_tokens, &model).unwrap_or(false)
    };
    if fits(payload_json) {
        return payload_json.to_string();
    }
    let mut budget = if max_chars > 0 {
        max_chars
    } else {
        payload_json.chars().count() as i64
    };
    while budget > 240 {
        budget /= 2;
        if let Ok(candidate) = gossamer::budget::shrink_payload(payload_json, Some(budget as usize))
            && fits(&candidate)
        {
            return candidate;
        }
    }
    pyeprintln!(
        "Payload could not be shrunk into the output budget; returning an overflow envelope instead of invalid JSON"
    );
    py_dumps_pretty(&json!({
        "source": source,
        "error": "document too large for the output budget",
        "hint": "narrow the read with the pages parameter",
    }))
}

fn structured_failure(source: &str, detail: &str) -> ExitCode {
    pyeprintln!("Structured extraction failed for {source}: {detail}");
    pyprintln!(
        "{}",
        py_dumps_pretty(&json!({
            "error": format!("Structured document extraction failed: {detail}")
        }))
    );
    ExitCode::SUCCESS
}

fn structured_parse(
    bytes: &[u8],
    suffix: &str,
    file_name: &str,
    file_size_bytes: u64,
) -> Result<Value, String> {
    match suffix {
        "pdf" => pdf_structured_payload(bytes, file_name, file_size_bytes).map_err(|error| {
            error.replacen("Failed to open PDF from bytes: ", "Failed to open PDF: ", 1)
        }),
        "xlsx" | "xls" | "xlsb" | "ods" | "docx" | "doc" | "pptx" | "ppt" => {
            office_structured_payload(bytes, suffix, file_name, file_size_bytes)
        }
        _ => Err(format!(
            "Unsupported file format '.{suffix}'. Supported: pdf, docx, xlsx, pptx, xls, ppt, xlsb, ods, doc."
        )),
    }
}

fn structured_extract(source: &str, is_url: bool, parsed: &Parsed) -> ExitCode {
    if !is_url {
        let raw_path = PathBuf::from(source);
        let path = if raw_path.is_absolute() {
            raw_path
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(raw_path)
        };
        if !path.exists() {
            return structured_failure(
                source,
                &format!(
                    "Source file not found at: {}",
                    platform_display(Path::new(source))
                ),
            );
        }
        let bytes = match std::fs::read(&path) {
            Ok(bytes) => bytes,
            Err(error) => return structured_failure(source, &error.to_string()),
        };
        let suffix = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        let file_name = path
            .file_name()
            .map(|value| value.to_string_lossy().into_owned())
            .unwrap_or_else(|| "document".to_string());
        return match structured_parse(&bytes, &suffix, &file_name, bytes.len() as u64) {
            Ok(payload) => {
                let rendered =
                    serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string());
                pyprintln!("{}", fit_structured_document(&rendered, source, parsed));
                ExitCode::SUCCESS
            }
            Err(detail) => structured_failure(source, &detail),
        };
    }

    let cache_dir = crate::units::settings::cache_dir(parsed);
    let runtime = match RuntimeState::new(crate::units::settings::runtime(parsed, &cache_dir)) {
        Ok(runtime) => runtime,
        Err(error) => {
            pyeprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
    if !runtime.robots_allows(source, runtime.now()) {
        pyprintln!(
            "{}",
            py_dumps_pretty(&json!({
                "error": format!("Document fetch disallowed by robots.txt: {source}")
            }))
        );
        return ExitCode::SUCCESS;
    }
    let (bytes, status, _, _) = match download_bytes(source, 30.0, 200_000_000) {
        Ok(value) => value,
        Err(error) => return structured_failure(source, &error),
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
        let detail = format!(
            "Client error '{status} {reason}' for url '{source}'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{status}"
        );
        return structured_failure(source, &detail);
    }
    let suffix = Path::new(source)
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "pdf".to_string());
    let file_name = Path::new(source)
        .file_name()
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_else(|| format!("document.{suffix}"));
    match structured_parse(&bytes, &suffix, &file_name, bytes.len() as u64) {
        Ok(payload) => {
            let rendered =
                serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string());
            pyprintln!("{}", fit_structured_document(&rendered, source, parsed));
            ExitCode::SUCCESS
        }
        Err(detail) => structured_failure(source, &detail),
    }
}

fn config_int(parsed: &Parsed, key: &str, env: &str, default: i64) -> i64 {
    crate::units::settings::int(parsed, key, env, default)
}

fn config_text(parsed: &Parsed, key: &str, env: &str, default: &str) -> String {
    crate::units::settings::text(parsed, key, env, default)
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let tables_as = parsed.value("tables_as").unwrap_or("json").to_string();
    let include_images = parsed.flag("include_images");
    let structured = parsed.flag("structured");
    let store = parsed.flag("store");
    let store_dir = parsed.value("store_dir").map(str::to_string);
    let pages = parsed.value("pages").map(str::to_string);
    let cache_dir = crate::units::settings::cache_dir(parsed);
    let prefer_jats = parsed.switch("prefer_jats").unwrap_or(true);
    let Some(source) = parsed.positional("source").map(str::to_string) else {
        return unsupported();
    };

    if let Some(error) = extract_validation(&tables_as, include_images, structured, store) {
        pyprintln!("{}", py_dumps_pretty(&error));
        return ExitCode::SUCCESS;
    }

    if let Some(pages) = pages.as_deref()
        && !pages.trim().is_empty()
    {
        if store {
            let payload = json!({"error": "store=True cannot be combined with pages=...; store the full document instead"});
            pyprintln!("{}", py_dumps_pretty(&payload));
            return ExitCode::SUCCESS;
        }
        if prefer_jats && jats_resolvable_offline(&source, &tables_as) {
            let payload = json!({
                "error": format!(
                    "pages='{pages}' is not supported for structured-source (JATS) extraction; re-run without pages or with --no-prefer-jats"
                )
            });
            pyprintln!("{}", py_dumps_pretty(&payload));
            return ExitCode::SUCCESS;
        }
    }
    // Pages, store and structured extraction keep their existing contract;
    // this slice owns the flat local-file path plus spreadsheet renderings.
    let is_url = source.starts_with("http://") || source.starts_with("https://");
    if is_url && let Err(error) = prepare_url(&source, None) {
        if error.error_type() == "SsrfBlockedError" {
            pyeprintln!(
                "Blocked non-public URL {}: {}",
                error.raw(),
                error.message()
            );
        }
        pyprintln!("{}", pretty_record(&error.to_record()));
        return ExitCode::SUCCESS;
    }
    if structured {
        return structured_extract(&source, is_url, parsed);
    }
    let runtime = match RuntimeState::new(crate::units::settings::runtime(parsed, &cache_dir)) {
        Ok(value) => value,
        Err(error) => {
            pyeprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
    if is_url {
        let now = runtime.now();
        if !runtime.robots_allows(&source, now) {
            pyprintln!(
                "{}",
                py_dumps_pretty(&json!({
                    "error": format!("Document fetch disallowed by robots.txt: {}", source)
                }))
            );
            return ExitCode::SUCCESS;
        }
    }
    if pages.as_deref().is_some_and(|spec| !spec.trim().is_empty())
        && let Some(error) = page_selection_error(&source, is_url)
    {
        pyprintln!("{}", py_dumps_pretty(&json!({"error": error})));
        return ExitCode::SUCCESS;
    }
    let mut jats_resolved_doi = Value::Null;
    let mut fetched_at: Value = json!(timestamp());
    let mut jats_source = Value::Null;
    let mut jats_title = Value::Null;
    let mut n_formulas = Value::Null;
    let mut n_figures = Value::Null;
    let mut n_tables = Value::Null;
    let jats_hit = if prefer_jats && jats_resolvable_offline(&source, &tables_as) {
        match jats_route(&runtime, &source, &tables_as) {
            JatsOutcome::Hit(hit) => Some(hit),
            JatsOutcome::Error(error) => {
                let payload = json!({"error": format!("Document extraction failed: {error}")});
                pyprintln!("{}", py_dumps_pretty(&payload));
                return ExitCode::SUCCESS;
            }
            JatsOutcome::NotApplicable => None,
        }
    } else {
        None
    };
    let pages_requested = pages.as_deref().is_some_and(|spec| !spec.trim().is_empty());
    let had_jats = jats_hit.is_some();
    let local_jats_candidate = !is_url
        && prefer_jats
        && jats_tables_allows(&source, &tables_as)
        && matches!(
            Path::new(&source)
                .extension()
                .and_then(|ext| ext.to_str())
                .unwrap_or("")
                .to_ascii_lowercase()
                .as_str(),
            "xml" | "jats" | "nxml"
        );
    let base_document_key = if is_url {
        runtime.identity_key(&source)
    } else {
        source.clone()
    };
    let document_key = if tables_as == "markdown" {
        base_document_key.clone()
    } else {
        format!("{base_document_key}#tables={tables_as}")
    };
    let page_key = pages
        .as_deref()
        .filter(|spec| !spec.trim().is_empty())
        .map(|spec| format!("{base_document_key}#pages={spec}"));
    let cached_document = if had_jats || pages_requested || local_jats_candidate {
        None
    } else {
        runtime.document_cache_get(&document_key)
    };
    let cached_hit = cached_document.is_some();
    let max_document_bytes = config_int(
        parsed,
        "max_response_bytes",
        "GOSSAMER_MAX_RESPONSE_BYTES",
        runtime.max_response_bytes() as i64,
    )
    .max(0) as u64;
    if let Some(key) = page_key.as_deref()
        && let Some(cached) = runtime.document_cache_get(key)
    {
        let char_limit = config_int(
            parsed,
            "max_markdown_chars",
            "GOSSAMER_MAX_MARKDOWN_CHARS",
            8000,
        )
        .max(0) as usize;
        let token_limit = config_int(parsed, "max_tokens", "GOSSAMER_MAX_TOKENS", 0);
        let model = config_text(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
        let delivered = truncate_text(&cached, char_limit, token_limit, &model)
            .unwrap_or_else(|_| cached.clone());
        let (delivered, guard) = match apply_document_guard(&delivered, &source, parsed) {
            GuardedDocument::Delivered(text, block) => (text, block),
            GuardedDocument::Withheld(error) => {
                pyprintln!("{}", py_dumps_pretty(&error));
                return ExitCode::SUCCESS;
            }
        };
        let spec = pages.as_deref().unwrap_or("");
        let payload = json!({
            "source": source,
            "content": delivered,
            "content_tokens": count_tokens(&delivered, &model).unwrap_or(0),
            "cache_hit": true,
            "page_range": spec,
            "page_start": 0,
            "page_end": 0,
            "total_pages": 0,
            "fetched_at": Value::Null,
            "http_status": Value::Null,
            "final_url": Value::Null,
            "content_type": Value::Null,
            "content_hash": sha256_bytes(cached.as_bytes()),
            "links": [],
            "guard": guard,
            "stored": Value::Null,
            "resolved_doi": Value::Null,
            "jats_source": Value::Null,
            "jats_title": Value::Null,
            "n_formulas": Value::Null,
            "n_figures": Value::Null,
            "n_tables": Value::Null,
        });
        pyprintln!("{}", pydantic_json(&payload));
        return ExitCode::SUCCESS;
    }
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
        let content = full.clone();
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
    } else if let Some(cached) = cached_document.clone() {
        cache_hit = true;
        fetched_at = Value::Null;
        let bytes = if store {
            if is_url {
                match download_bytes(&source, 20.0, max_document_bytes as usize) {
                    Ok((bytes, _, _, _)) => bytes,
                    Err(error) => {
                        return extraction_error(&source, &document_download_error(&error));
                    }
                }
            } else {
                match read_local_bytes(&source, Path::new(&source), max_document_bytes) {
                    Ok(bytes) => bytes,
                    Err(detail) => return extraction_error(&source, &detail),
                }
            }
        } else {
            Vec::new()
        };
        (
            source.clone(),
            bytes,
            cached,
            Value::Null,
            Value::Null,
            Value::Null,
        )
    } else if is_url {
        let (bytes, status, final_url, content_type) =
            match download_bytes(&source, 20.0, max_document_bytes as usize) {
                Ok(value) => value,
                Err(error) => {
                    let detail = document_download_error(&error);
                    return if pages_requested {
                        page_range_failure(&source, &detail)
                    } else {
                        extraction_error(&source, &detail)
                    };
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
            return if pages_requested {
                page_range_failure(&source, &message)
            } else {
                extraction_error(&source, &message)
            };
        }
        let content = match extract_url_body(
            &bytes,
            &source,
            content_type.as_deref().unwrap_or(""),
            &tables_as,
        ) {
            Ok(content) => content,
            Err(error) => return extraction_error(&source, &error),
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
        let bytes = match read_local_bytes(&source, &path, max_document_bytes) {
            Ok(bytes) => bytes,
            Err(detail) => {
                return if pages_requested {
                    page_range_failure(&source, &detail)
                } else {
                    extraction_error(&source, &detail)
                };
            }
        };
        let ext = path
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        let jats_text = Some(
            String::from_utf8_lossy(&bytes)
                .trim_start_matches('\u{feff}')
                .to_string(),
        );
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
            match local_text(&path, &bytes, &tables_as) {
                Ok(content) => content,
                Err(detail) => return extraction_error(&source, &detail),
            }
        };
        (
            source.clone(),
            bytes,
            content,
            Value::Null,
            Value::Null,
            Value::Null,
        )
    };
    let mut full_content = jats_full_content.unwrap_or_else(|| content.clone());
    let local_jats_hit = local_jats_candidate && jats_source == json!("local");
    if !had_jats && !cached_hit && !pages_requested && !local_jats_hit {
        runtime.document_cache_put(&document_key, &full_content);
    }
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
                if let Some(key) = page_key.as_deref() {
                    runtime.document_cache_put(key, &content);
                }
            }
            Err(error) => {
                if error.starts_with("Invalid page range")
                    || error.starts_with("Page range ")
                    || error.starts_with("Document has no pages")
                    || error.starts_with("Page selection is supported")
                {
                    pyprintln!("{}", py_dumps_pretty(&json!({"error": error})));
                } else {
                    return page_range_failure(&source, &error);
                }
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
        if jats_source != Value::Null {
            let stem_source = jats_resolved_doi
                .as_str()
                .filter(|value| !value.is_empty())
                .unwrap_or(&source);
            let stem = jats_store_stem(stem_source);
            let original_path = directory.join(format!("{stem}.xml"));
            let markdown_path = directory.join(format!("{stem}.md"));
            if std::fs::write(&original_path, &bytes).is_err()
                || std::fs::write(&markdown_path, full_content.as_bytes()).is_err()
            {
                return unsupported();
            }
            let mut resources = json!({
                "dir": platform_display(&directory),
                "files": [],
                "referenced": 0,
                "embedded": 0,
            });
            if include_images && let Some(map) = resources.as_object_mut() {
                map.insert(
                    "note".to_string(),
                    json!("JATS figures are remote hrefs, not saved"),
                );
            }
            json!({
                "directory": platform_display(&directory),
                "original": platform_display(&original_path),
                "markdown": platform_display(&markdown_path),
                "original_bytes": bytes.len(),
                "markdown_chars": full_content.chars().count(),
                "content_type": content_type,
                "resources": resources,
            })
        } else {
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
        }
    } else {
        Value::Null
    };
    let char_limit = config_int(
        parsed,
        "max_markdown_chars",
        "GOSSAMER_MAX_MARKDOWN_CHARS",
        8000,
    )
    .max(0) as usize;
    let token_limit = config_int(parsed, "max_tokens", "GOSSAMER_MAX_TOKENS", 0);
    let model = config_text(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
    let delivered = truncate_text(&full_content, char_limit, token_limit, &model)
        .unwrap_or_else(|_| full_content.clone());
    let (delivered, guard) = match apply_document_guard(&delivered, &display, parsed) {
        GuardedDocument::Delivered(text, block) => (text, block),
        GuardedDocument::Withheld(error) => {
            pyprintln!("{}", py_dumps_pretty(&error));
            return ExitCode::SUCCESS;
        }
    };
    let delivered_hash = if pages_requested {
        sha256_bytes(delivered.as_bytes())
    } else {
        sha256_bytes(full_content.as_bytes())
    };
    let delivered_links: Vec<Value> = if pages_requested {
        Vec::new()
    } else {
        scan_text_links(&full_content, 50)
            .into_iter()
            .map(Value::String)
            .collect()
    };
    let payload = json!({
        "source": display,
        "content": delivered,
        "content_tokens": count_tokens(&delivered, &model).unwrap_or(0),
        "cache_hit": cache_hit,
        "page_range": page_range,
        "page_start": page_start,
        "page_end": page_end,
        "total_pages": total_pages,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "final_url": final_url,
        "content_type": content_type,
        "content_hash": delivered_hash,
        "links": delivered_links,
        "guard": guard,
        "stored": stored,
        "resolved_doi": jats_resolved_doi,
        "jats_source": jats_source,
        "jats_title": jats_title,
        "n_formulas": n_formulas,
        "n_figures": n_figures,
        "n_tables": n_tables,
    });
    pyprintln!("{}", pydantic_json(&payload));
    ExitCode::SUCCESS
}
