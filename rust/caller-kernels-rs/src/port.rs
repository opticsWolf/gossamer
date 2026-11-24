#![forbid(unsafe_code)]
use gossamer::{mcp_dispatch, providers::base, service::search};
use serde_json::Value;
pub fn service_inspect_request(
    url: String,
    use_smart: String,
    query: Option<String>,
    offset: i64,
    max_chunks: i64,
    structured: bool,
    store_dir: Option<String>,
) -> Result<Value, String> {
    Ok(mcp_dispatch::inspect_request(
        &url,
        &use_smart,
        query.as_deref(),
        offset,
        max_chunks,
        structured,
        store_dir.as_deref(),
    ))
}
pub fn service_extract_validation(
    tables_as: String,
    include_images: bool,
    structured: bool,
    store: bool,
) -> Result<Option<Value>, String> {
    Ok(mcp_dispatch::extract_validation(
        &tables_as,
        include_images,
        structured,
        store,
    ))
}
pub fn service_batch_plan(
    urls: Option<Value>,
    cached_keys: Value,
    visited_keys: Value,
    strategy: String,
) -> Result<Value, String> {
    Ok(mcp_dispatch::batch_plan(
        urls.as_ref(),
        &cached_keys,
        &visited_keys,
        &strategy,
    ))
}
pub fn service_normalize_rate_limit(
    delay: Option<Value>,
    fetch_delay: Option<f64>,
    jitter: Option<f64>,
) -> Result<Value, String> {
    Ok(base::normalize_rate_limit(
        delay.as_ref(),
        fetch_delay,
        jitter,
    ))
}
pub fn service_web_search_route(search_only: bool) -> Result<String, String> {
    Ok(search::web_search_route(search_only).to_string())
}
pub fn service_search_cache_key(
    query: Option<String>,
    max_results: i64,
    provider: Option<String>,
    merge: bool,
) -> Result<String, String> {
    Ok(search::search_cache_key(
        query.as_deref(),
        max_results,
        provider.as_deref(),
        merge,
    ))
}
pub fn service_doc_extract_validation(
    tables_as: String,
    include_images: bool,
    structured: bool,
    store: bool,
) -> Result<Option<Value>, String> {
    Ok(mcp_dispatch::extract_validation(
        &tables_as,
        include_images,
        structured,
        store,
    ))
}
