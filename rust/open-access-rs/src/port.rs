#![forbid(unsafe_code)]
use gossamer::openaccess::{
    normalize_doi, oa_candidate, unpaywall_candidate, unpaywall_candidates,
};
use serde_json::Value;

pub fn service_normalize_doi(value: Value) -> Result<String, String> {
    let text = value
        .as_str()
        .ok_or_else(|| "ValueError: DOI must be a string".to_string())?;
    normalize_doi(text)
}

pub fn service_oa_candidate(
    location: Value,
    is_best: bool,
    work_is_oa: bool,
) -> Result<Option<Value>, String> {
    Ok(oa_candidate(&location, is_best, work_is_oa))
}

pub fn service_unpaywall_candidate(
    location: Value,
    is_best: bool,
) -> Result<Option<Value>, String> {
    Ok(unpaywall_candidate(&location, is_best))
}

pub fn service_locate_result(
    doi: String,
    records: Value,
    openalex_error: Option<Value>,
    fallback_candidates: Value,
    fallback_work: Value,
    unpaywall_error: Option<Value>,
    unpaywall_enabled: bool,
    include_unpaywall_error: bool,
    invalid_record_message: String,
) -> Result<Value, String> {
    let records = records.as_array().cloned().unwrap_or_default();
    let fallback_candidates = fallback_candidates.as_array().cloned().unwrap_or_default();
    Ok(gossamer::openaccess::locate_result(
        &doi,
        &records,
        openalex_error.as_ref(),
        &fallback_candidates,
        &fallback_work,
        unpaywall_error.as_ref(),
        unpaywall_enabled,
        include_unpaywall_error,
        &invalid_record_message,
    ))
}

pub fn service_unpaywall_candidates(payload: Value) -> Result<Value, String> {
    let (candidates, work) = unpaywall_candidates(&payload);
    Ok(serde_json::json!({"$tuple": [candidates, work]}))
}
