#![forbid(unsafe_code)]
use gossamer::service::crawl::{
    crawl_anchor_context, crawl_excerpt, crawl_expand_query, crawl_is_document, crawl_path_prior,
    crawl_score, crawl_term_hits, crawl_tokens, crawl_topic_words, shrink_crawl,
};
use serde_json::Value;

pub fn service_crawl_score(
    label: Vec<String>,
    query_terms: Vec<String>,
    page_terms: Vec<String>,
    idf: Option<Value>,
    base_terms: Option<Value>,
    corpus_n: i64,
    path_prior: f64,
) -> Result<f64, String> {
    let base = base_terms.as_ref().and_then(Value::as_array).map(|items| {
        items
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect::<Vec<_>>()
    });
    Ok(crawl_score(
        &label,
        &query_terms,
        &page_terms,
        idf.as_ref(),
        base.as_deref(),
        corpus_n,
        path_prior,
    ))
}

pub fn service_shrink_crawl(result: Value, budget: Option<i64>) -> Result<String, String> {
    shrink_crawl(&result, budget)
}

pub fn service_crawl_tokens(text: Option<String>) -> Result<Vec<String>, String> {
    Ok(crawl_tokens(text.as_deref()))
}

pub fn service_crawl_topic_words(text: Option<String>, limit: i64) -> Result<Vec<String>, String> {
    Ok(crawl_topic_words(text.as_deref(), limit.max(0) as usize))
}

pub fn service_crawl_is_document(url: String) -> Result<bool, String> {
    Ok(crawl_is_document(&url))
}

pub fn service_crawl_anchor_context(
    md: Option<String>,
    anchor: Option<String>,
    chars: i64,
    cap: i64,
) -> Result<Vec<String>, String> {
    Ok(crawl_anchor_context(
        md.as_deref(),
        anchor.as_deref(),
        chars.max(0) as usize,
        cap.max(0) as usize,
    ))
}
pub fn service_crawl_path_prior(url: String) -> Result<f64, String> {
    Ok(crawl_path_prior(&url))
}
pub fn service_crawl_term_hits(text: Option<String>, terms: Vec<String>) -> Result<usize, String> {
    Ok(crawl_term_hits(text.as_deref(), &terms))
}

pub fn service_crawl_excerpt(
    text: Option<String>,
    terms: Vec<String>,
    window: Option<i64>,
    step: Option<i64>,
) -> Result<Option<String>, String> {
    Ok(crawl_excerpt(
        text.as_deref(),
        &terms,
        window.unwrap_or(300).max(0) as usize,
        step.unwrap_or(100).max(1) as usize,
    ))
}
pub fn service_crawl_expand_query(
    base_terms: Vec<String>,
    clusters: Option<Value>,
) -> Result<Value, String> {
    let (terms, added) = crawl_expand_query(&base_terms, clusters.as_ref().unwrap_or(&Value::Null));
    Ok(serde_json::json!({"$tuple": [terms, added]}))
}
