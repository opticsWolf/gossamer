//! Deterministic crawl frontier scoring kernels.

use serde_json::Value;

const QUERY_WEIGHT: f64 = 0.7;
const CONTEXT_WEIGHT: f64 = 0.3;
const EXPANSION_WEIGHT: f64 = 0.5;
const IDF_MIN_CORPUS: i64 = 3;

fn idf_value(idf: Option<&Value>, term: &str) -> f64 {
    idf.and_then(Value::as_object)
        .and_then(|map| map.get(term))
        .and_then(Value::as_f64)
        .unwrap_or(1.0)
}

fn contains_any(haystack: &[String], needle: &[String]) -> usize {
    haystack.iter().filter(|item| needle.contains(item)).count()
}

/// Score one crawl frontier candidate.
pub fn crawl_score(
    label: &[String],
    query_terms: &[String],
    page_terms: &[String],
    idf: Option<&Value>,
    base_terms: Option<&[String]>,
    corpus_n: i64,
    path_prior: f64,
) -> f64 {
    if idf.is_none() {
        let mut score = 0.0;
        if !query_terms.is_empty() {
            score += QUERY_WEIGHT * contains_any(label, query_terms) as f64 / query_terms.len() as f64;
        }
        if !label.is_empty() && !page_terms.is_empty() {
            score += CONTEXT_WEIGHT * contains_any(label, page_terms) as f64 / label.len() as f64;
        }
        return score;
    }
    let base = base_terms.unwrap_or(query_terms);
    let weight_query = |term: &str| {
        let value = idf_value(idf, term);
        if base.iter().any(|item| item == term) {
            value
        } else {
            EXPANSION_WEIGHT * value
        }
    };
    let q_weight: f64 = query_terms.iter().map(|term| weight_query(term)).sum();
    let mut score = 0.0;
    if !query_terms.is_empty() && q_weight > 0.0 {
        let hits: f64 = label
            .iter()
            .filter(|term| query_terms.contains(term))
            .map(|term| weight_query(term))
            .sum();
        score += QUERY_WEIGHT * hits / q_weight;
    }
    let l_weight: f64 = label.iter().map(|term| idf_value(idf, term)).sum();
    if !label.is_empty() && !page_terms.is_empty() && l_weight > 0.0 {
        let hits: f64 = label
            .iter()
            .filter(|term| page_terms.contains(term))
            .map(|term| idf_value(idf, term))
            .sum();
        score += CONTEXT_WEIGHT * hits / l_weight;
    }
    if corpus_n >= IDF_MIN_CORPUS {
        score *= path_prior;
    }
    score
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_score() {
        let label = vec!["rust".to_string(), "lang".to_string()];
        let query = vec!["rust".to_string()];
        let page = vec!["lang".to_string()];
        assert!((crawl_score(&label, &query, &page, None, None, 0, 1.0) - 0.85).abs() < 1e-9);
    }
}
