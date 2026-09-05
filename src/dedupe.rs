//! Result-deduplication matching core (port of `gossamer.dedup.dedupe`).
//!
//! Split of responsibilities: Python extracts the six raw identity
//! fields from each item (dict keys or object attributes, first-non-empty
//! wins per group — trivially Python-side work over live objects) and
//! Rust computes keys and runs the first-seen collision loop. All
//! matching semantics (DOI normalization, canonical-URL identity,
//! title+snippet hashing, field priority, kept/dropped bookkeeping) live
//! here, pinned by `tests/test_rust_parity_dedupe.py`.

use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};

use crate::urls::{canonical_url_impl, content_hash_impl};

/// Raw identity fields for one item; all optional (absent/empty → None).
#[derive(FromPyObject)]
pub struct DedupeItem {
    #[pyo3(item)]
    doi: Option<String>,
    #[pyo3(item)]
    url: Option<String>,
    #[pyo3(item)]
    title: Option<String>,
    #[pyo3(item)]
    snippet: Option<String>,
    #[pyo3(item)]
    summary: Option<String>,
    #[pyo3(item)]
    description: Option<String>,
}

fn nonempty(v: &Option<String>) -> Option<&str> {
    v.as_deref().filter(|s| !s.is_empty())
}

fn doi_key(item: &DedupeItem) -> Option<String> {
    nonempty(&item.doi).map(|d| d.trim().to_lowercase()).filter(|d| !d.is_empty())
}

fn url_key(item: &DedupeItem) -> Option<String> {
    let url = nonempty(&item.url)?;
    canonical_url_impl(url, "drop").ok()
}

fn hash_key(item: &DedupeItem) -> Option<String> {
    let title = nonempty(&item.title).unwrap_or("");
    let snippet = nonempty(&item.snippet)
        .or_else(|| nonempty(&item.summary))
        .or_else(|| nonempty(&item.description))
        .unwrap_or("");
    if title.is_empty() && snippet.is_empty() {
        return None;
    }
    Some(content_hash_impl(Some(&format!(
        "{}||{}",
        title.trim(),
        snippet.trim()
    ))))
}

fn field_key(field: &str, item: &DedupeItem) -> Option<String> {
    match field {
        "doi" => doi_key(item),
        "url" => url_key(item),
        "hash" => hash_key(item),
        _ => None,
    }
}

/// Run the first-seen collision loop over pre-extracted items.
///
/// Returns `(kept_indices, dropped)` where each dropped entry is
/// `(original_index, reason_field, match_key)`. Order-preserving:
/// kept indices ascend, dropped entries ascend by original position.
pub fn dedupe_plan_impl(
    items: &[DedupeItem],
    by: &[String],
) -> (Vec<usize>, Vec<(usize, String, String)>) {
    let mut kept: Vec<usize> = Vec::new();
    let mut dropped: Vec<(usize, String, String)> = Vec::new();
    let mut seen: HashMap<String, HashSet<String>> = HashMap::new();
    for (i, item) in items.iter().enumerate() {
        let mut collision: Option<(String, String)> = None;
        for field in by.iter() {
            let key = match field_key(field, item) {
                Some(k) => k,
                None => continue,
            };
            if seen
                .get(field.as_str())
                .map(|bucket| bucket.contains(&key))
                .unwrap_or(false)
            {
                collision = Some((field.clone(), key));
                break;
            }
        }
        match collision {
            Some((reason, matched)) => dropped.push((i, reason, matched)),
            None => {
                for field in by.iter() {
                    if let Some(key) = field_key(field, item) {
                        seen.entry(field.clone()).or_default().insert(key);
                    }
                }
                kept.push(i);
            }
        }
    }
    (kept, dropped)
}

#[pyfunction]
#[pyo3(signature = (items, by = None))]
pub fn dedupe_plan(
    items: Vec<DedupeItem>,
    by: Option<Vec<String>>,
) -> (Vec<usize>, Vec<(usize, String, String)>) {
    let fields = by.unwrap_or_else(|| {
        vec!["doi".to_string(), "url".to_string(), "hash".to_string()]
    });
    dedupe_plan_impl(&items, &fields)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(
        doi: Option<&str>,
        url: Option<&str>,
        title: Option<&str>,
        snippet: Option<&str>,
    ) -> DedupeItem {
        DedupeItem {
            doi: doi.map(str::to_string),
            url: url.map(str::to_string),
            title: title.map(str::to_string),
            snippet: snippet.map(str::to_string),
            summary: None,
            description: None,
        }
    }

    fn default_by() -> Vec<String> {
        vec!["doi".into(), "url".into(), "hash".into()]
    }

    #[test]
    fn doi_beats_url_and_is_case_insensitive() {
        let items = vec![
            item(Some("10.1/ABC "), Some("https://a.example/1"), Some("T"), Some("S")),
            item(Some("10.1/abc"), Some("https://b.example/2"), Some("U"), Some("V")),
        ];
        let (kept, dropped) = dedupe_plan_impl(&items, &default_by());
        assert_eq!(kept, vec![0]);
        assert_eq!(dropped.len(), 1);
        assert_eq!(dropped[0].0, 1);
        assert_eq!(dropped[0].1, "doi");
    }

    #[test]
    fn url_identity_ignores_tracking_and_case() {
        let items = vec![
            item(None, Some("https://www.Example.com/a"), None, None),
            item(None, Some("https://example.com/a?utm_x=1"), None, None),
        ];
        let (kept, dropped) = dedupe_plan_impl(&items, &default_by());
        assert_eq!(kept, vec![0]);
        assert_eq!(dropped[0].1, "url");
    }

    #[test]
    fn hash_is_last_resort_and_empty_items_survive() {
        let items = vec![
            item(None, None, Some("Same"), Some("Body")),
            item(None, None, Some("Same"), Some("Body")),
            item(None, None, None, None),
        ];
        let (kept, dropped) = dedupe_plan_impl(&items, &default_by());
        assert_eq!(kept, vec![0, 2]);
        assert_eq!(dropped[0].1, "hash");
    }

    #[test]
    fn unknown_fields_skipped_and_by_respected() {
        let items = vec![
            item(None, Some("https://a.example/"), None, None),
            item(None, Some("https://a.example/"), None, None),
        ];
        let by = vec!["bogus".to_string(), "url".to_string()];
        let (kept, dropped) = dedupe_plan_impl(&items, &by);
        assert_eq!(kept, vec![0]);
        assert_eq!(dropped[0].1, "url");
        let by = vec!["bogus".to_string()];
        let (kept, dropped) = dedupe_plan_impl(&items, &by);
        assert_eq!(kept, vec![0, 1]);
        assert!(dropped.is_empty());
    }
}
