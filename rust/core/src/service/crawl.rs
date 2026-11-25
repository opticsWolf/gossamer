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
            score +=
                QUERY_WEIGHT * contains_any(label, query_terms) as f64 / query_terms.len() as f64;
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
        if omitted > 0
            && let Some(pages) = out.get_mut("pages").and_then(Value::as_array_mut)
        {
            pages.truncate(keep);
        }
        if omitted > 0
            && let Some(object) = out.as_object_mut()
        {
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
    TOKEN_RE
        .find_iter(&lower)
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
    ranked
        .into_iter()
        .take(limit)
        .map(|(token, _)| token)
        .collect()
}

/// True when the URL path ends in a document extension.
pub fn crawl_is_document(url: &str) -> bool {
    let path = crate::robots::url_path(url, None).to_ascii_lowercase();
    let path = path.split(['?', '#']).next().unwrap_or(&path);
    [
        ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".xlsb", ".csv", ".txt", ".md",
        ".json", ".xml", ".rss", ".atom",
    ]
    .iter()
    .any(|ext| path.ends_with(ext))
}

fn ranked_tokens(text: &str, limit: usize) -> Vec<String> {
    use std::collections::HashMap;
    let mut counts: HashMap<String, usize> = HashMap::new();
    for token in tokens(text) {
        *counts.entry(token).or_insert(0) += 1;
    }
    let mut ranked: Vec<(String, usize)> = counts.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    ranked
        .into_iter()
        .take(limit)
        .map(|(token, _)| token)
        .collect()
}

/// Topic words near an anchor in rendered markdown.
pub fn crawl_anchor_context(
    md: Option<&str>,
    anchor: Option<&str>,
    chars: usize,
    cap: usize,
) -> Vec<String> {
    let text = (md.unwrap_or("")).to_ascii_lowercase();
    let needle = (anchor.unwrap_or("")).trim().to_ascii_lowercase();
    if needle.is_empty() {
        return Vec::new();
    }
    let Some(start) = text.find(&needle) else {
        return Vec::new();
    };
    let from = start.saturating_sub(chars);
    let to = (start + needle.len() + chars).min(text.len());
    // Character slicing keeps parity with Python for non-ASCII markdown.
    let window: String = text
        .chars()
        .skip(from)
        .take(to.saturating_sub(from))
        .collect();
    ranked_tokens(&window, cap)
}

/// Mild topic prior from a URL path.
pub fn crawl_path_prior(url: &str) -> f64 {
    let path = crate::robots::url_path(url, None).to_ascii_lowercase();
    let path = path.split(['?', '#']).next().unwrap_or(&path);
    if [
        "/docs/",
        "/guide/",
        "/guides/",
        "/blog/",
        "/api/",
        "/changelog/",
        "/reference/",
    ]
    .iter()
    .any(|prefix| path.starts_with(prefix))
    {
        return 1.15;
    }
    if ["/pricing", "/careers", "/contact", "/about"]
        .iter()
        .any(|prefix| path.starts_with(prefix))
    {
        return 0.85;
    }
    1.0
}

/// Count occurrences of terms in a token stream.
pub fn crawl_term_hits(text: Option<&str>, terms: &[String]) -> usize {
    if terms.is_empty() {
        return 0;
    }
    tokens(text.unwrap_or(""))
        .into_iter()
        .filter(|token| terms.contains(token))
        .count()
}

/// Densest keyword window of a text, with Python-style ellipses.
pub fn crawl_excerpt(
    text: Option<&str>,
    terms: &[String],
    window: usize,
    step: usize,
) -> Option<String> {
    let chars: Vec<char> = text.unwrap_or("").chars().collect();
    if terms.is_empty() || chars.is_empty() || step == 0 {
        return None;
    }
    let mut best: Option<(usize, usize)> = None;
    let mut start = 0usize;
    while start < chars.len() {
        let end = (start + window).min(chars.len());
        let slice: String = chars[start..end].iter().collect();
        let density = crawl_term_hits(Some(&slice), terms);
        if best.is_none_or(|(value, _)| density > value) {
            best = Some((density, start));
        }
        start += step;
    }
    let (density, start) = best?;
    if density == 0 {
        return None;
    }
    let end = (start + window).min(chars.len());
    let excerpt: String = chars[start..end].iter().collect();
    let prefix = if start > 0 { "…" } else { "" };
    let suffix = if end < chars.len() { "…" } else { "" };
    Some(format!("{prefix}{excerpt}{suffix}"))
}

/// Expand base terms with ordered synonym clusters, capped at the base size.
pub fn crawl_expand_query(base_terms: &[String], clusters: &Value) -> (Vec<String>, usize) {
    let mut seen: Vec<String> = base_terms.to_vec();
    seen.sort();
    seen.dedup();
    if seen.is_empty() {
        return (Vec::new(), 0);
    }
    let cap = seen.len();
    let mut added = 0usize;
    for term in base_terms
        .iter()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>()
    {
        let Some(groups) = clusters.as_array() else {
            break;
        };
        for group in groups {
            let Some(members) = group.as_array() else {
                continue;
            };
            if !members
                .iter()
                .any(|member| member.as_str() == Some(term.as_str()))
            {
                continue;
            }
            for member in members.iter().filter_map(Value::as_str) {
                if seen.iter().any(|existing| existing == member) {
                    continue;
                }
                seen.push(member.to_string());
                added += 1;
                if added >= cap {
                    seen.sort();
                    return (seen, added);
                }
            }
        }
    }
    seen.sort();
    (seen, added)
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
