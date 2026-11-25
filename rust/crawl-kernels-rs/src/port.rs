#![forbid(unsafe_code)]
use gossamer::service::crawl::{
    crawl_is_document, crawl_score, crawl_tokens, crawl_topic_words, shrink_crawl,
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
