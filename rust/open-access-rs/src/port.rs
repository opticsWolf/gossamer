#![forbid(unsafe_code)]
use gossamer::openaccess::normalize_doi;
use serde_json::Value;

pub fn service_normalize_doi(value: Value) -> Result<String, String> {
    let text = value
        .as_str()
        .ok_or_else(|| "ValueError: DOI must be a string".to_string())?;
    normalize_doi(text)
}
