//! Deterministic research-result kernels.

use crate::urls::canonical_url;
use serde_json::Value;

/// Deduplicate research records by canonical URL, preserving first occurrence.
pub fn normalize_records(records: &Value, max_results: i64) -> Vec<Value> {
    let mut out = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let Some(items) = records.as_array() else {
        return out;
    };
    for item in items {
        if !item.is_object() {
            continue;
        }
        if let Some(url) = item.get("url").and_then(Value::as_str)
            && let Ok(key) = canonical_url(url, "keep", None)
        {
            if seen.iter().any(|value| value == &key) {
                continue;
            }
            seen.push(key);
        }
        out.push(item.clone());
        if max_results > 0 && out.len() as i64 >= max_results {
            break;
        }
    }
    out
}

/// Order provider names with the preferred canonical provider first.
pub fn provider_order(providers: &Value, preferred: Option<&str>) -> Vec<String> {
    let names: Vec<String> = providers
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect();
    let Some(name) = preferred.filter(|v| !v.is_empty()) else {
        return names;
    };
    let canonical = crate::search::resolve_provider_name(name)
        .unwrap_or(name)
        .to_lowercase();
    let mut out: Vec<String> = names
        .iter()
        .filter(|n| n.to_lowercase() == canonical)
        .cloned()
        .collect();
    out.extend(names.into_iter().filter(|n| n.to_lowercase() != canonical));
    out
}

/// Normalize a research request before search fan-out.
pub fn research_plan(topic: &str, depth: i64, max_results: Option<i64>, max_pages: i64) -> Value {
    let topic = topic.trim();
    if topic.is_empty() {
        return serde_json::json!({"error": "research: topic is required"});
    }
    let depth = depth.clamp(1, max_pages);
    let plan_results = max_results
        .map(|value| value.clamp(1, 20))
        .unwrap_or_else(|| (depth * 2).clamp(1, 20));
    serde_json::json!({"topic": topic, "depth": depth, "plan_results": plan_results})
}

/// Resolve a research provider id to its closed import path.
pub fn provider_factory_path(provider: &str) -> Option<&'static str> {
    const FACTORIES: [(&str, &str); 27] = [
        ("openalex", "gossamer.research_providers.OpenAlexAdapter"),
        ("crossref", "gossamer.research_providers.CrossrefAdapter"),
        ("arxiv", "gossamer.research_providers.ArxivAdapter"),
        (
            "courtlistener",
            "gossamer.research_providers.CourtListenerAdapter",
        ),
        ("ecfr", "gossamer.research_providers.EcfrAdapter"),
        (
            "federalregister",
            "gossamer.research_providers.FederalRegisterAdapter",
        ),
        (
            "alphavantage",
            "gossamer.research_providers.AlphaVantageAdapter",
        ),
        ("yahoo", "gossamer.research_providers.YahooFinanceAdapter"),
        (
            "frankfurter",
            "gossamer.research_providers.FrankfurterAdapter",
        ),
        ("eurostat", "gossamer.research_providers.EurostatAdapter"),
        (
            "bundesbank",
            "gossamer.research_providers.BundesbankAdapter",
        ),
        ("bis", "gossamer.research_providers.BisAdapter"),
        ("coingecko", "gossamer.research_providers.CoinGeckoAdapter"),
        ("zenodo", "gossamer.research_providers.ZenodoAdapter"),
        (
            "semanticscholar",
            "gossamer.research_providers.SemanticScholarAdapter",
        ),
        (
            "huggingface",
            "gossamer.research_providers.HuggingFaceAdapter",
        ),
        (
            "huggingface-papers",
            "gossamer.research_providers.HuggingFacePapersAdapter",
        ),
        ("overpass", "gossamer.research_providers.OverpassAdapter"),
        ("oldp", "gossamer.research_providers.OldpAdapter"),
        ("hudoc", "gossamer.research_providers.HudocAdapter"),
        ("govinfo", "gossamer.research_providers.GovInfoAdapter"),
        ("epo", "gossamer.research_providers.EpoOpsAdapter"),
        ("kipris", "gossamer.research_providers.KiprisAdapter"),
        (
            "patentsview",
            "gossamer.research_providers.PatentsViewAdapter",
        ),
        ("lens", "gossamer.research_providers.LensAdapter"),
        (
            "google-patents",
            "gossamer.research_providers.GooglePatentsAdapter",
        ),
        ("open-meteo", "gossamer.research_providers.OpenMeteoAdapter"),
    ];
    FACTORIES
        .iter()
        .find(|(id, _)| *id == provider)
        .map(|(_, path)| *path)
}

/// Canonical error text for an unregistered research provider.
pub fn unknown_provider_message(provider: &str) -> String {
    format!("no adapter registered for provider '{provider}'")
}
