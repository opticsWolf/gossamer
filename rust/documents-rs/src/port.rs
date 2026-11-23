//! Thin bindings over `gossamer::documents` for the deterministic part of the
//! documents payload schema. Pydantic model classes stay in Python; only the
//! pure JSON-shaping helpers live here.

#![forbid(unsafe_code)]

use gossamer::documents::{
    absolutize_markdown_links as absolutize, follow_up_candidates, office_cell_text as cell_text,
    office_ir_tables as tables,
};
use serde_json::{Map, Value, json};

/// Port of `office_ir_tables` (`gossamer/structured_parser.py:77`).
///
/// # Errors
/// No error path: malformed IR yields an empty list, as in the reference.
pub fn office_ir_tables(ir: Value) -> Result<Vec<Value>, String> {
    Ok(tables(&ir)
        .into_iter()
        .map(|pair| json!({"$tuple": pair}))
        .collect())
}

/// Port of `_office_cell_text` (`gossamer/structured_parser.py:65`).
///
/// # Errors
/// No error path: non-object cells yield an empty string.
pub fn office_cell_text(cell: Value) -> Result<String, String> {
    Ok(cell_text(&cell))
}

/// Port of `build_follow_up_candidates` (`gossamer/structured_parser.py:212`).
///
/// `max_links` defaults to `0` in the source.
///
/// # Errors
/// No error path: items are stringified exactly as the reference does.
pub fn build_follow_up_candidates(
    anchored_links: Vec<Value>,
    max_links: i64,
) -> Result<Vec<Value>, String> {
    let decoded = anchored_links
        .into_iter()
        .map(|item| match item.get("$tuple").cloned() {
            Some(pair) => pair,
            None => item,
        })
        .collect();
    Ok(follow_up_candidates(&Value::Array(decoded), max_links)
        .into_iter()
        .map(|candidate| {
            let mut wrapper = Map::new();
            wrapper.insert(
                "$object".to_string(),
                Value::String("FollowUpCandidate".into()),
            );
            wrapper.insert("attrs".to_string(), candidate);
            Value::Object(wrapper)
        })
        .collect())
}

/// Port of `_absolutize_markdown_links` (`gossamer/models.py:392`).
///
/// # Errors
/// No error path: an unparsable base URL leaves the markdown unchanged.
pub fn absolutize_markdown_links(markdown: String, base_url: String) -> Result<String, String> {
    Ok(absolutize(&markdown, &base_url))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn office_table_ir_keeps_section_titles() {
        let ir = serde_json::json!({"sections": [
            {"title": "Sheet1", "elements": [{"type": "table", "rows": [
                {"cells": [{"content": [{"type": "paragraph", "content": [{"text": "A"}]}]}]}
            ]}]}
        ]});
        let tables = office_ir_tables(ir).expect("tables");
        assert_eq!(tables[0]["$tuple"][0], "Sheet1");
    }
}
