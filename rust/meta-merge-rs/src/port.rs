#![forbid(unsafe_code)]
use gossamer::metaextract::merge_into_document_metadata;
use serde_json::Value;

pub fn service_merge_into_document_metadata(
    html_meta: Value,
    base_metadata: Value,
) -> Result<Value, String> {
    Ok(merge_into_document_metadata(&html_meta, &base_metadata))
}
