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

pub fn service_unpaywall_candidates(payload: Value) -> Result<Value, String> {
    let (candidates, work) = unpaywall_candidates(&payload);
    Ok(serde_json::json!({"$tuple": [candidates, work]}))
}
