//! Identifier-based merge for explicitly requested scholarly providers.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value};

use crate::{
    config::py_str_value,
    openaccess::{normalize_doi, percent_decode},
};

const CONFLICT_FIELDS: [&str; 5] = ["title", "authors", "published", "url", "citations"];

/// Compiled once for speed; immutable and carries no process state.
static ARXIV_VERSION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)v\d+$").unwrap_or_else(|e| panic!(r"invalid arXiv version regex v\d+$: {e}"))
});

fn arxiv_id(value: Option<&Value>) -> Option<String> {
    let text = match value? {
        Value::String(text) => text.trim().to_string(),
        _ => return None,
    };
    if text.is_empty() {
        return None;
    }
    let mut ident = percent_decode(&text);
    let lowered = ident.to_ascii_lowercase();
    for prefix in [
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
        "arxiv:",
    ] {
        if lowered.starts_with(prefix) {
            ident = ident[prefix.len()..].to_string();
            break;
        }
    }
    if let Some((_, tail)) = ident.rsplit_once('/') {
        ident = tail.to_string();
    }
    if ident.to_ascii_lowercase().ends_with(".pdf") {
        ident.truncate(ident.len() - 4);
    }
    ident = ARXIV_VERSION.replace(&ident, "").into_owned();
    let ident = ident.trim().to_ascii_lowercase();
    if ident.is_empty() { None } else { Some(ident) }
}

fn raw_record(record: &Value) -> Option<Map<String, Value>> {
    match record.get("raw")? {
        Value::Object(map) => Some(map.clone()),
        Value::String(text) => serde_json::from_str::<Value>(text)
            .ok()
            .and_then(|value| value.as_object().cloned()),
        _ => None,
    }
}

fn identifier_key(record: &Value) -> Option<String> {
    let doi_value = record
        .get("doi")
        .filter(|v| !v.is_null())
        .or_else(|| record.get("DOI").filter(|v| !v.is_null()))
        .or_else(|| record.get("url").filter(|v| !v.is_null()));
    if let Some(value) = doi_value
        && let Ok(normalized) = normalize_doi(&py_str_value(value))
    {
        return Some(format!("doi:{normalized}"));
    }
    let raw = raw_record(record).unwrap_or_default();
    if let Some(external) = raw.get("externalIds").and_then(Value::as_object) {
        let arxiv = external.get("ArXiv").or_else(|| external.get("arxiv"));
        if let Some(id) = arxiv_id(arxiv) {
            return Some(format!("arxiv:{id}"));
        }
    }
    if let Some(ids) = raw.get("ids").and_then(Value::as_object)
        && let Some(id) = arxiv_id(ids.get("arxiv"))
    {
        return Some(format!("arxiv:{id}"));
    }
    if let Some(id) = arxiv_id(record.get("arxiv_id")) {
        return Some(format!("arxiv:{id}"));
    }
    if py_str_value(record.get("source").unwrap_or(&Value::Null)).eq_ignore_ascii_case("arxiv")
        && let Some(id) = arxiv_id(record.get("id").or_else(|| record.get("url")))
    {
        return Some(format!("arxiv:{id}"));
    }
    None
}

fn pairs(value: &Value) -> Vec<(String, Vec<Value>)> {
    let array = value
        .as_array()
        .or_else(|| value.get("$tuple").and_then(Value::as_array));
    array
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let pair = item
                        .as_array()
                        .or_else(|| item.get("$tuple").and_then(Value::as_array))?;
                    let provider = pair.first()?.as_str()?.to_string();
                    let records = pair.get(1)?.as_array()?.clone();
                    Some((provider, records))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Merge provider records on DOI or arXiv identifier only.
pub fn merge_scholarly_records(provider_records: &Value) -> Vec<Value> {
    let mut index: HashMap<String, usize> = HashMap::new();
    let mut ordered: Vec<Value> = Vec::new();
    for (provider, records) in pairs(provider_records) {
        for record in records {
            let key = identifier_key(&record);
            if key.is_none() {
                ordered.push(serde_json::json!({
                    "key": Value::Null,
                    "canonical": record,
                    "sources": [provider],
                    "source_records": [{"provider": provider, "record": record}],
                    "conflicts": {},
                }));
                continue;
            }
            let key = key.unwrap_or_default();
            let position = match index.get(&key) {
                Some(position) => *position,
                None => {
                    ordered.push(serde_json::json!({
                        "key": key,
                        "canonical": record,
                        "sources": [],
                        "source_records": [],
                        "conflicts": {},
                    }));
                    let position = ordered.len() - 1;
                    index.insert(key, position);
                    position
                }
            };
            if let Some(merged) = ordered.get_mut(position) {
                if let Some(sources) = merged.get_mut("sources").and_then(Value::as_array_mut)
                    && !sources
                        .iter()
                        .any(|value| value.as_str() == Some(provider.as_str()))
                {
                    sources.push(Value::String(provider.clone()));
                }
                if let Some(items) = merged
                    .get_mut("source_records")
                    .and_then(Value::as_array_mut)
                {
                    items.push(serde_json::json!({"provider": provider, "record": record}));
                }
            }
        }
    }
    for merged in &mut ordered {
        let mut by_field: Vec<(String, Vec<Value>)> = Vec::new();
        if let Some(items) = merged.get("source_records").and_then(Value::as_array) {
            for item in items {
                let provider = item.get("provider").cloned().unwrap_or(Value::Null);
                let record = item.get("record").cloned().unwrap_or(Value::Null);
                for field in CONFLICT_FIELDS {
                    let value = record.get(field).cloned().unwrap_or(Value::Null);
                    if value.is_null() || value.as_str() == Some("") {
                        continue;
                    }
                    let entry = serde_json::json!({"provider": provider, "value": value});
                    if let Some(position) = by_field.iter().position(|(name, _)| name == field) {
                        let entries = &mut by_field[position].1;
                        if !entries
                            .iter()
                            .any(|item| item.get("value") == entry.get("value"))
                        {
                            entries.push(entry);
                        }
                    } else {
                        by_field.push((field.to_string(), vec![entry]));
                    }
                }
            }
        }
        let conflicts: Map<String, Value> = by_field
            .into_iter()
            .filter(|(_, values)| values.len() > 1)
            .map(|(field, values)| (field, Value::Array(values)))
            .collect();
        if let Some(slot) = merged.get_mut("conflicts") {
            *slot = Value::Object(conflicts);
        }
    }
    ordered
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn arxiv_keys() {
        assert_eq!(
            arxiv_id(Some(&Value::String("2401.01234v2".to_string()))).as_deref(),
            Some("2401.01234")
        );
        assert_eq!(
            arxiv_id(Some(&Value::String(
                "https://arxiv.org/abs/2401.01234v1".to_string()
            )))
            .as_deref(),
            Some("2401.01234")
        );
        assert_eq!(
            identifier_key(&serde_json::json!({"source":"arxiv","id":"2401.01234v2"})).as_deref(),
            Some("arxiv:2401.01234")
        );
    }
}
