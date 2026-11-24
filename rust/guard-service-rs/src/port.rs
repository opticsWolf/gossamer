#![forbid(unsafe_code)]
use gossamer::guard;
use serde_json::Value;
pub fn service_guard_pairs(
    mode: Option<String>,
    scopes: Value,
    scope_texts: Value,
    main_scope: String,
) -> Result<Value, String> {
    Ok(guard::prepare_scope_pairs(
        mode.as_deref(),
        &scopes,
        &scope_texts,
        &main_scope,
    ))
}
pub fn service_merge_reports(reports: Value) -> Result<Value, String> {
    Ok(guard::merge_reports(&reports))
}
