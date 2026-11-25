#![forbid(unsafe_code)]
use gossamer::cite::{citation_input, citation_style, enrichment_plan, find_doi_in_text};
use serde_json::Value;

pub fn service_citation_style(style: Option<String>) -> std::result::Result<String, String> {
    citation_style(style.as_deref()).map(str::to_string)
}

pub fn service_citation_input(value: String) -> Result<Value, String> {
    Ok(citation_input(&value))
}

pub fn service_find_doi_in_text(text: Value) -> Result<Option<String>, String> {
    Ok(find_doi_in_text(text.as_str()))
}

pub fn service_enrichment_plan(record: Value, hits: Value) -> Result<Value, String> {
    let hits = hits.as_array().cloned().unwrap_or_default();
    let (venue, abstract_text) = enrichment_plan(&record, Some(&hits));
    Ok(serde_json::json!({"$tuple": [venue, abstract_text]}))
}
