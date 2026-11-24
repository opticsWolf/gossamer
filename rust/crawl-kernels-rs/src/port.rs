#![forbid(unsafe_code)]
use gossamer::service::crawl::crawl_score;
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
