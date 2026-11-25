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



/// Serialize a crawl result with the page list capped to fit a budget.
pub fn shrink_crawl(result: &Value, budget: Option<i64>) -> Result<String, String> {
    let mut out = result.clone();
    if let Some(budget) = budget {
        let keep = (budget / 500).max(1) as usize;
        let omitted = out
            .get("pages")
            .and_then(Value::as_array)
            .map(|pages| pages.len().saturating_sub(keep))
            .unwrap_or(0);
        if omitted > 0 && let Some(pages) = out.get_mut("pages").and_then(Value::as_array_mut) {
            pages.truncate(keep);
        }
        if omitted > 0 && let Some(object) = out.as_object_mut() {
            object.insert("pages_omitted".to_string(), Value::from(omitted));
        }
    }
    serde_json::to_string_pretty(&out).map_err(|e| format!("ValueError: {e}"))
}



const STOPWORDS: &str = "a about above after again against all am an and any are as at be because been before below being between both but by can did do does doing down during each few for from further had has have having he her here hers him his how i if in into is it its just me more most my no nor not of off on once only or other our out over own same she should so some such than that the their them then there these they this those through to too under until up very was we were what when where which while who why will with you your yours";
const TOKEN_PATTERN: &str = r"[a-z0-9]+";
static TOKEN_RE: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
    regex::Regex::new(TOKEN_PATTERN)
        .unwrap_or_else(|e| panic!("invalid crawl token regex {TOKEN_PATTERN:?}: {e}"))
});

fn tokens(text: &str) -> Vec<String> {
    let lower = text.to_ascii_lowercase();
    TOKEN_RE.find_iter(&lower)
        .map(|m| m.as_str().to_string())
        .filter(|token| !STOPWORDS.split_whitespace().any(|word| word == token))
        .collect()
}

/// Lowercase alphanumeric content words with stopwords removed.
pub fn crawl_tokens(text: Option<&str>) -> Vec<String> {
    let mut values = tokens(text.unwrap_or(""));
    values.sort();
    values.dedup();
    values
}

/// Highest-frequency content words, capped and ordered by count then name.
pub fn crawl_topic_words(text: Option<&str>, limit: usize) -> Vec<String> {
    use std::collections::HashMap;
    let mut counts: HashMap<String, usize> = HashMap::new();
    for token in tokens(text.unwrap_or("")) {
        *counts.entry(token).or_insert(0) += 1;
    }
    let mut ranked: Vec<(String, usize)> = counts.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    ranked.into_iter().take(limit).map(|(token, _)| token).collect()
}



/// True when the URL path ends in a document extension.
pub fn crawl_is_document(url: &str) -> bool {
    let path = crate::robots::url_path(url, None).to_ascii_lowercase();
    let path = path.split(['?', '#']).next().unwrap_or(&path);
    [
        ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".xlsb",
        ".csv", ".txt", ".md", ".json", ".xml", ".rss", ".atom",
    ].iter().any(|ext| path.ends_with(ext))
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
