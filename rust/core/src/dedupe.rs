//! Result-deduplication matching core.
//!
//! Pure port of the matching loop in the legacy PyO3 `src/dedupe.rs`. Python extracts raw identity
//! fields from live objects; this module computes keys and runs the first-seen collision loop.

use std::collections::{HashMap, HashSet};

use crate::urls::{canonical_url, content_hash};

/// Raw identity fields for one result; `None` means absent or empty.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DedupeItem {
    /// DOI identity signal.
    pub doi: Option<String>,
    /// URL identity signal.
    pub url: Option<String>,
    /// Title used by the weak hash signal.
    pub title: Option<String>,
    /// Snippet used by the weak hash signal.
    pub snippet: Option<String>,
    /// Fallback when `snippet` is empty.
    pub summary: Option<String>,
    /// Second fallback when `snippet` and `summary` are empty.
    pub description: Option<String>,
}

impl DedupeItem {
    /// Creates an item from its six optional identity fields.
    pub fn new(
        doi: Option<String>,
        url: Option<String>,
        title: Option<String>,
        snippet: Option<String>,
        summary: Option<String>,
        description: Option<String>,
    ) -> Self {
        Self {
            doi,
            url,
            title,
            snippet,
            summary,
            description,
        }
    }
}

fn nonempty(v: &Option<String>) -> Option<&str> {
    v.as_deref().filter(|s| !s.is_empty())
}

fn doi_key(item: &DedupeItem) -> Option<String> {
    nonempty(&item.doi)
        .map(|d| d.trim().to_lowercase())
        .filter(|d| !d.is_empty())
}

fn url_key(item: &DedupeItem, runtime_minor: Option<u32>) -> Option<String> {
    let url = nonempty(&item.url)?;
    canonical_url(url, "drop", runtime_minor).ok()
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
    Some(content_hash(Some(&format!(
        "{}||{}",
        title.trim(),
        snippet.trim()
    ))))
}

fn field_key(field: &str, item: &DedupeItem, runtime_minor: Option<u32>) -> Option<String> {
    match field {
        "doi" => doi_key(item),
        "url" => url_key(item, runtime_minor),
        "hash" => hash_key(item),
        _ => None,
    }
}

/// Runs the first-seen collision loop over pre-extracted items.
///
/// Returns `(kept_indices, dropped)` where each dropped entry is `(original_index, reason_field,
/// match_key)`. Kept indices ascend; dropped entries ascend by original position.
///
/// # Examples
///
/// ```
/// use gossamer::dedupe::{dedupe_plan, DedupeItem};
///
/// let items = vec![
///     DedupeItem::new(Some("10.1/ABC".into()), None, None, None, None, None),
///     DedupeItem::new(Some("10.1/abc".into()), None, None, None, None, None),
/// ];
/// let by = vec!["doi".to_string()];
/// let (kept, dropped) = dedupe_plan(&items, &by, None);
/// assert_eq!(kept, vec![0]);
/// assert_eq!(dropped[0].1, "doi");
/// ```
pub fn dedupe_plan(
    items: &[DedupeItem],
    by: &[String],
    runtime_minor: Option<u32>,
) -> (Vec<usize>, Vec<(usize, String, String)>) {
    let mut kept = Vec::new();
    let mut dropped = Vec::new();
    let mut seen: HashMap<String, HashSet<String>> = HashMap::new();
    for (i, item) in items.iter().enumerate() {
        let mut collision: Option<(String, String)> = None;
        for field in by.iter() {
            let key = match field_key(field, item, runtime_minor) {
                Some(k) => k,
                None => continue,
            };
            if seen
                .get(field.as_str())
                .is_some_and(|bucket| bucket.contains(&key))
            {
                collision = Some((field.clone(), key));
                break;
            }
        }
        match collision {
            Some((reason, matched)) => dropped.push((i, reason, matched)),
            None => {
                for field in by.iter() {
                    if let Some(key) = field_key(field, item, runtime_minor) {
                        seen.entry(field.clone()).or_default().insert(key);
                    }
                }
                kept.push(i);
            }
        }
    }
    (kept, dropped)
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
        DedupeItem::new(
            doi.map(str::to_string),
            url.map(str::to_string),
            title.map(str::to_string),
            snippet.map(str::to_string),
            None,
            None,
        )
    }

    fn default_by() -> Vec<String> {
        vec!["doi".into(), "url".into(), "hash".into()]
    }

    #[test]
    fn doi_beats_url_and_is_case_insensitive() {
        let items = vec![
            item(
                Some("10.1/ABC "),
                Some("https://a.example/1"),
                Some("T"),
                Some("S"),
            ),
            item(
                Some("10.1/abc"),
                Some("https://b.example/2"),
                Some("U"),
                Some("V"),
            ),
        ];
        let (kept, dropped) = dedupe_plan(&items, &default_by(), None);
        assert_eq!(kept, vec![0]);
        assert_eq!(dropped[0].1, "doi");
    }

    #[test]
    fn url_identity_ignores_tracking_and_case() {
        let items = vec![
            item(None, Some("https://www.Example.com/a"), None, None),
            item(None, Some("https://example.com/a?utm_x=1"), None, None),
        ];
        let (kept, dropped) = dedupe_plan(&items, &default_by(), None);
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
        let (kept, dropped) = dedupe_plan(&items, &default_by(), None);
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
        let (kept, dropped) = dedupe_plan(&items, &by, None);
        assert_eq!(kept, vec![0]);
        assert_eq!(dropped[0].1, "url");
        let by = vec!["bogus".to_string()];
        let (kept, dropped) = dedupe_plan(&items, &by, None);
        assert_eq!(kept, vec![0, 1]);
        assert!(dropped.is_empty());
    }
}
