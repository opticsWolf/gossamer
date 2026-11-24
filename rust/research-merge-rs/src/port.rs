#![forbid(unsafe_code)]
use gossamer::research_merge::merge_scholarly_records;
use serde_json::Value;

pub fn service_merge_scholarly_records(provider_records: Value) -> Result<Vec<Value>, String> {
    Ok(merge_scholarly_records(&provider_records))
}
