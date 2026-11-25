#![forbid(unsafe_code)]
use gossamer::cite::{citation_input, citation_style};
use serde_json::Value;

pub fn service_citation_style(style: Option<String>) -> std::result::Result<String, String> {
    citation_style(style.as_deref()).map(str::to_string)
}

pub fn service_citation_input(value: String) -> Result<Value, String> {
    Ok(citation_input(&value))
}
