//! Static research taxonomy exposed by `research_categories()`.
//!
//! The JSON is generated from the Python `CATEGORIES` table once and checked in; category order,
//! descriptions, provider order, and Python pretty-printing are part of the compatibility contract.

/// Pretty-printed category payload returned by `gossamer categories`.
pub const CATEGORIES_JSON: &str = include_str!("taxonomy_data.json");
