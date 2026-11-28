//! `gossamer research`: taxonomy and validation envelopes in Rust.

use std::process::ExitCode;

use gossamer::adapters::finance::{
    alphavantage_parse_search_impl, bis_parse_impl, bundesbank_parse_impl,
    coingecko_parse_search_impl, eurostat_parse_cells_impl, frankfurter_parse_rates_impl,
    yahoo_parse_search_impl,
};
use gossamer::adapters::legal::{
    congress_parse_search_impl, courtlistener_parse_search_impl, ecfr_find_part_impl,
    ecfr_part_record_impl, ecfr_title_record_impl, fed_parse_search_impl, hudoc_parse_search_impl,
    oldp_parse_search_impl,
};
use gossamer::adapters::misc::{openmeteo_parse_search_impl, overpass_parse_search_impl};
use gossamer::adapters::patents::{
    epo_parse_search_impl, kipris_parse_search_impl, lens_parse_search_impl,
    patentsview_parse_search_impl,
};
use gossamer::adapters::scholar::{
    arxiv_entry_raw, arxiv_parse_search_impl, crossref_parse_search_impl,
    huggingface_papers_parse_search_impl, huggingface_parse_search_impl,
    openalex_parse_search_impl, semanticscholar_parse_search_impl, zenodo_parse_search_impl,
};
use gossamer::categories::classify_query;
use gossamer::cli_args::Parsed;
use gossamer::discovery::{
    fetch_text, fetch_text_with_status, fetch_text_with_status_and_user_agent,
    fetch_text_with_user_agent,
};
use gossamer::jsonfmt::py_dumps;
use gossamer::jsonfmt::py_dumps_pretty;
use gossamer::research_merge::merge_scholarly_records;
use gossamer::search::{duckduckgo_results, duckduckgo_search_url};
use gossamer::taxonomy::CATEGORIES_JSON;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

fn attach_raw(records: &mut [Value], items: Option<&Vec<Value>>) {
    if let Some(items) = items {
        for (record, item) in records.iter_mut().zip(items.iter()) {
            if let Some(object) = record.as_object_mut() {
                object.insert("raw".to_string(), Value::String(py_dumps(item)));
            }
        }
    }
}

fn fetch_provider_records(
    provider: &str,
    query: &str,
    max_results: usize,
) -> Result<Vec<Value>, String> {
    let limit = max_results.to_string();
    match provider {
        "crossref" => {
            let url =
                url::Url::parse_with_params("https://api.crossref.org/works", &[("query", query)])
                    .map(|value| value.to_string())
                    .unwrap_or_else(|_| format!("https://api.crossref.org/works?query={query}"));
            let (body, _) = fetch_text(&url, 20.0)?;
            let mut records = crossref_parse_search_impl(&body, max_results as i64, None)
                .map_err(|error| error.to_string())?;
            if let Ok(parsed) = serde_json::from_str::<Value>(&body) {
                let items = parsed
                    .get("message")
                    .and_then(|value| value.get("items"))
                    .and_then(Value::as_array);
                attach_raw(&mut records, items);
            }
            Ok(records)
        }
        "openalex" => {
            let per_page = max_results.min(100).to_string();
            let url = url::Url::parse_with_params(
                "https://api.openalex.org/works",
                &[("search", query), ("per_page", per_page.as_str())],
            )
            .map(|value| value.to_string())
            .unwrap_or_else(|_| format!("https://api.openalex.org/works?search={query}"));
            let (body, _) = fetch_text(&url, 15.0)?;
            let mut records = openalex_parse_search_impl(&body, max_results as i64, None)
                .map_err(|error| error.to_string())?;
            if let Ok(parsed) = serde_json::from_str::<Value>(&body) {
                attach_raw(
                    &mut records,
                    parsed.get("results").and_then(Value::as_array),
                );
            }
            Ok(records)
        }
        "zenodo" => {
            let size = max_results.min(100).to_string();
            let url = url::Url::parse_with_params(
                "https://zenodo.org/api/records",
                &[("q", query), ("size", size.as_str()), ("page", "1")],
            )
            .map(|value| value.to_string())
            .unwrap_or_else(|_| format!("https://zenodo.org/api/records?q={query}"));
            let user_agent = format!("{}?email=", gossamer::USER_AGENT);
            let (body, _) = fetch_text_with_user_agent(&url, 20.0, &user_agent)?;
            let mut records = zenodo_parse_search_impl(&body, max_results as i64, None)
                .map_err(|error| error.to_string())?;
            if let Ok(parsed) = serde_json::from_str::<Value>(&body) {
                let items = parsed
                    .get("hits")
                    .and_then(|value| value.get("hits"))
                    .and_then(Value::as_array);
                attach_raw(&mut records, items);
            }
            Ok(records)
        }
        "huggingface" | "huggingface-papers" => {
            let total_limit = max_results.min(1000).to_string();
            let (path, params): (&str, Vec<(&str, &str)>) = if provider == "huggingface" {
                (
                    "/models",
                    vec![
                        ("search", query),
                        ("sort", "downloads"),
                        ("direction", "-1"),
                        ("limit", total_limit.as_str()),
                    ],
                )
            } else {
                (
                    "/papers/search",
                    vec![("q", query), ("limit", total_limit.as_str())],
                )
            };
            let url =
                url::Url::parse_with_params(&format!("https://huggingface.co/api{path}"), params)
                    .map(|value| value.to_string())
                    .unwrap_or_else(|_| format!("https://huggingface.co/api{path}?q={query}"));
            let (body, _) = fetch_text(&url, 30.0)
                .map_err(|error| format!("huggingface search failed: {error}"))?;
            let items = serde_json::from_str::<Value>(&body)
                .ok()
                .and_then(|value| value.as_array().cloned());
            let mut records = if provider == "huggingface" {
                huggingface_parse_search_impl(&body, max_results as i64, None)
            } else {
                huggingface_papers_parse_search_impl(&body, max_results as i64, None)
            }
            .map_err(|error| error.to_string())?;
            attach_raw(&mut records, items.as_ref());
            Ok(records)
        }
        "arxiv" => {
            let url = url::Url::parse_with_params(
                "https://export.arxiv.org/api/query",
                &[
                    ("search_query", query),
                    ("start", "0"),
                    ("max_results", limit.as_str()),
                ],
            )
            .map(|value| value.to_string())
            .unwrap_or_else(|_| format!("https://export.arxiv.org/api/query?search_query={query}"));
            let user_agent = format!(
                "{} (+https://github.com/opticsWolf/gossamer)",
                gossamer::USER_AGENT
            );
            let (body, _) = fetch_text_with_user_agent(&url, 20.0, &user_agent)
                .map_err(|error| format!("arxiv search failed: {error}"))?;
            let mut records = arxiv_parse_search_impl(&body, max_results as i64)
                .map_err(|error| error.to_string())?;
            for (index, record) in records.iter_mut().enumerate() {
                if let (Some(object), Some(raw)) =
                    (record.as_object_mut(), arxiv_entry_raw(&body, index))
                {
                    object.insert("raw".to_string(), Value::String(raw));
                }
            }
            Ok(records)
        }
        "semanticscholar" => {
            let fields = "paperId,title,abstract,year,publicationDate,authors,citationCount,referenceCount,venue,externalIds,url,openAccessPdf,fieldsOfStudy,publicationTypes";
            let url = url::Url::parse_with_params(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                &[
                    ("query", query),
                    ("fields", fields),
                    ("offset", "0"),
                    ("limit", limit.as_str()),
                ],
            )
            .map(|value| value.to_string())
            .unwrap_or_else(|_| {
                format!("https://api.semanticscholar.org/graph/v1/paper/search?query={query}")
            });
            let (body, _, status) = fetch_text_with_status(&url, 30.0)
                .map_err(|error| format!("semanticscholar search failed: {error}"))?;
            if status == 429 {
                return Err("Semantic Scholar returned HTTP 429 for a keyless request; unauthenticated traffic shares a rate-limit pool. Set GOSSAMER_SEMANTICSCHOLAR_API_KEY for an individual one-request-per-second allowance, or wait before retrying.".to_string());
            }
            if status >= 400 {
                return Err(format!("Semantic Scholar returned HTTP {status}"));
            }
            let items = serde_json::from_str::<Value>(&body)
                .ok()
                .and_then(|value| value.get("data").and_then(Value::as_array).cloned());
            let mut records = semanticscholar_parse_search_impl(&body, max_results as i64, None)
                .map_err(|error| error.to_string())?;
            attach_raw(&mut records, items.as_ref());
            Ok(records)
        }
        other => Err(format!("unknown provider: {other}")),
    }
}

fn env_key(name: &str) -> String {
    std::env::var(name).unwrap_or_default()
}

fn bounded_limit(value: usize, lower: usize, upper: usize) -> usize {
    if value < lower {
        lower
    } else if value > upper {
        upper
    } else {
        value
    }
}

fn with_raw(records: &mut [Value], items: Option<&Vec<Value>>) {
    attach_raw(records, items);
}

fn http_reason(status: u16) -> &'static str {
    match status {
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "Unknown Status",
    }
}

fn fetch_provider_text(url: &str, timeout: f64) -> Result<String, String> {
    let (body, _, status) = fetch_text_with_status(url, timeout)?;
    if status >= 400 {
        return Err(format!(
            "Client error '{} {}' for url '{}'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{}",
            status,
            http_reason(status),
            url,
            status
        ));
    }
    Ok(body)
}

fn fetch_provider_text_with_user_agent(
    url: &str,
    timeout: f64,
    user_agent: &str,
) -> Result<String, String> {
    let (body, _, status) = fetch_text_with_status_and_user_agent(url, timeout, user_agent)?;
    if status >= 400 {
        return Err(format!(
            "Client error '{} {}' for url '{}'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{}",
            status,
            http_reason(status),
            url,
            status
        ));
    }
    Ok(body)
}

fn generic_provider_records(
    provider: &str,
    query: &str,
    max_results: usize,
) -> Result<Vec<Value>, String> {
    let limit = max_results as i64;
    let minor = Some(13u32);
    match provider {
        "courtlistener" => {
            let url = url::Url::parse_with_params(
                "https://www.courtlistener.com/api/rest/v4/search/",
                &[
                    ("q", if query.trim().is_empty() { "*" } else { query }),
                    ("per_page", &max_results.min(100).to_string()),
                    ("format", "json"),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            let mut records = courtlistener_parse_search_impl(&body, limit, minor)
                .map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("results").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "open-meteo" => {
            let url = url::Url::parse_with_params(
                "https://geocoding-api.open-meteo.com/v1/search",
                &[
                    ("name", query),
                    ("count", &max_results.to_string()),
                    ("language", "en"),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 15.0).map_err(|error| error.to_string())?;
            let mut records = openmeteo_parse_search_impl(
                &body,
                limit,
                "https://api.open-meteo.com/v1/forecast",
                minor,
            )
            .map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("results").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "yahoo" => {
            let url = url::Url::parse_with_params(
                "https://query2.finance.yahoo.com/v1/finance/search",
                &[
                    ("q", query),
                    ("quotesCount", &max_results.min(20).to_string()),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            let mut records =
                yahoo_parse_search_impl(&body, limit, minor).map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("quotes").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "frankfurter" => {
            let base = query.trim().to_uppercase();
            let url = format!("https://api.frankfurter.dev/v2/rates?base={base}");
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            let mut records = frankfurter_parse_rates_impl(&body, &base, None, limit)
                .map_err(|error| error.to_string())?;
            for record in &mut records {
                if let Some(fields) = record.get("fields") {
                    let raw = json!({
                        "base": fields.get("base").cloned().unwrap_or(Value::Null),
                        "quote": fields.get("quote").cloned().unwrap_or(Value::Null),
                        "rate": fields.get("rate").cloned().unwrap_or(Value::Null),
                        "date": fields.get("date").cloned().unwrap_or(Value::Null),
                    });
                    if let Some(object) = record.as_object_mut() {
                        object.insert("raw".to_string(), Value::String(py_dumps(&raw)));
                    }
                }
            }
            Ok(records)
        }
        "eurostat" => {
            let code = query.trim();
            if code.is_empty() {
                return Err("EurostatAdapter needs a dataset code".to_string());
            }
            let url = format!(
                "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{code}?lang=en&format=JSON"
            );
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            eurostat_parse_cells_impl(&body, code, limit).map_err(|error| error.to_string())
        }
        "coingecko" => {
            let url = url::Url::parse_with_params(
                "https://api.coingecko.com/api/v3/search",
                &[("query", query)],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text_with_user_agent(&url, 20.0, "python-httpx/0.28.1")
                .map_err(|error| error.to_string())?;
            let mut records = coingecko_parse_search_impl(&body, limit, minor)
                .map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("coins").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "oldp" => {
            let url = url::Url::parse_with_params(
                "https://de.openlegaldata.io/api/cases/search/",
                &[
                    ("text", query),
                    ("page_size", &max_results.min(100).to_string()),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            let mut records =
                oldp_parse_search_impl(&body, limit, minor).map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("results").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "hudoc" => {
            let url = url::Url::parse_with_params(
                "https://hudoc.echr.coe.int/app/query/results",
                &[
                    ("query", query),
                    ("select", "itemid,title,docname,date,doctype"),
                    ("sort", "itemid Ascending"),
                    ("start", "0"),
                    ("length", &max_results.min(100).to_string()),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 25.0).map_err(|error| error.to_string())?;
            let mut records = hudoc_parse_search_impl(&body).map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("results").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "federalregister" => {
            let url = url::Url::parse_with_params(
                "https://www.federalregister.gov/api/v1/documents.json",
                &[
                    ("conditions[term]", query),
                    ("per_page", &max_results.min(100).to_string()),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            let mut records =
                fed_parse_search_impl(&body, limit, minor).map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            let items = parsed.as_ref().and_then(|value| {
                value
                    .get("results")
                    .and_then(Value::as_array)
                    .or_else(|| value.get("documents").and_then(Value::as_array))
            });
            with_raw(&mut records, items);
            Ok(records)
        }
        "overpass" => {
            let text = query.trim();
            if text.is_empty() {
                return Err("OverpassAdapter requires an Overpass QL query string".to_string());
            }
            let data = if text.starts_with("[out:") {
                text.to_string()
            } else {
                format!("[out:json]{text}")
            };
            let url = url::Url::parse_with_params(
                "https://overpass.kumi.systems/api/interpreter",
                &[("data", data.as_str()), ("format", "json")],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 60.0).map_err(|error| error.to_string())?;
            let mut records = overpass_parse_search_impl(&body, limit, minor)
                .map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("elements").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "ecfr" => {
            let text = query.trim();
            let (title, part) = if let Some((left, right)) = text.split_once('/') {
                (left.trim().to_string(), right.trim().to_string())
            } else if text.to_uppercase().contains("CFR") {
                let digits: Vec<&str> = text
                    .split(|c: char| !c.is_ascii_digit())
                    .filter(|v| !v.is_empty())
                    .collect();
                (
                    digits.first().copied().unwrap_or("").to_string(),
                    digits.get(1).copied().unwrap_or("").to_string(),
                )
            } else if let Some((left, right)) = text.split_once('.') {
                (left.trim().to_string(), right.trim().to_string())
            } else {
                (text.to_string(), String::new())
            };
            if title.is_empty() {
                return Err(format!("Could not parse an eCFR citation from {query:?}"));
            }
            let titles =
                fetch_provider_text("https://www.ecfr.gov/api/versioner/v1/titles.json", 20.0)
                    .map_err(|error| format!("eCFR search failed: {error}"))?;
            let parsed =
                serde_json::from_str::<Value>(&titles).map_err(|error| error.to_string())?;
            let date = parsed
                .get("titles")
                .and_then(Value::as_array)
                .and_then(|items| {
                    items.iter().find(|item| {
                        item.get("number").and_then(Value::as_str) == Some(title.as_str())
                    })
                })
                .and_then(|item| item.get("latest_issue_date").and_then(Value::as_str))
                .ok_or_else(|| format!("eCFR has no title {title:?}"))?;
            let tree_url = format!(
                "https://www.ecfr.gov/api/versioner/v1/structure/{date}/title-{title}.json"
            );
            let (tree, _) = fetch_text(&tree_url, 30.0)
                .map_err(|error| format!("eCFR search failed: {error}"))?;
            if part.is_empty() {
                let mut record = ecfr_title_record_impl(&tree, &title, minor)
                    .map_err(|error| error.to_string())?;
                let value = serde_json::from_str::<Value>(&tree).unwrap_or(Value::Null);
                let raw = json!({
                    "identifier": value.get("identifier").cloned().unwrap_or(Value::Null),
                    "label": value.get("label").cloned().unwrap_or(Value::Null),
                    "type": value.get("type").cloned().unwrap_or(Value::Null),
                });
                if let Some(object) = record.as_object_mut() {
                    object.insert("raw".to_string(), Value::String(py_dumps(&raw)));
                }
                return Ok(vec![record]);
            }
            let node = ecfr_find_part_impl(&tree, &part)
                .map_err(|error| error.to_string())?
                .ok_or_else(|| format!("eCFR title {title} has no part {part:?}"))?;
            let node_json = py_dumps(&node);
            let mut record = ecfr_part_record_impl(&node_json, &title, &part)
                .map_err(|error| error.to_string())?;
            if let Some(object) = record.as_object_mut() {
                object.insert("raw".to_string(), Value::String(node_json));
            }
            Ok(vec![record])
        }
        "alphavantage" => {
            let key = env_key("GOSSAMER_ALPHA_VANTAGE_KEY");
            if key.is_empty() {
                return Err("AlphaVantageAdapter needs GOSSAMER_ALPHA_VANTAGE_KEY".to_string());
            }
            let url = url::Url::parse_with_params(
                "https://www.alphavantage.co/query",
                &[
                    ("function", "SEARCH"),
                    ("keywords", query),
                    ("apikey", key.as_str()),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            alphavantage_parse_search_impl(&body, &json!(query), limit, minor)
                .map_err(|error| error.to_string())
        }
        "congress" => {
            let key = env_key("GOSSAMER_CONGRESS_KEY");
            if key.is_empty() {
                return Err("CongressAdapter needs GOSSAMER_CONGRESS_KEY".to_string());
            }
            let url = url::Url::parse_with_params(
                "https://api.data.gov/congress/v1/members/search",
                &[
                    ("q", query),
                    ("api_key", key.as_str()),
                    ("limit", &max_results.min(50).to_string()),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            let mut records = congress_parse_search_impl(&body, limit, minor)
                .map_err(|error| error.to_string())?;
            let parsed = serde_json::from_str::<Value>(&body).ok();
            with_raw(
                &mut records,
                parsed
                    .as_ref()
                    .and_then(|value| value.get("results").and_then(Value::as_array)),
            );
            Ok(records)
        }
        "bundesbank" => {
            let (flow, key) = query.trim().split_once('/').unwrap_or((query.trim(), ""));
            if flow.is_empty() || key.is_empty() {
                return Err("BundesbankAdapter needs a \"FLOW/KEY\" spec".to_string());
            }
            let url = format!(
                "https://api.statistiken.bundesbank.de/rest/data/{flow}/{key}?format=csvfile"
            );
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            bundesbank_parse_impl(&body, flow, key, limit).map_err(|error| error.to_string())
        }
        "bis" => {
            let (flow, key) = query.trim().split_once('/').unwrap_or((query.trim(), ""));
            if flow.is_empty() {
                return Err("BisAdapter needs a \"FLOW[/KEY]\" spec".to_string());
            }
            let url = format!("https://stats.bis.org/api/v1/data/{flow}/{key}?format=csvfile");
            let body = fetch_provider_text(&url, 20.0).map_err(|error| error.to_string())?;
            bis_parse_impl(&body, flow, key, limit).map_err(|error| error.to_string())
        }
        "epo" => {
            let key = env_key("GOSSAMER_EPO_KEY");
            let secret = env_key("GOSSAMER_EPO_SECRET");
            if key.is_empty() || secret.is_empty() {
                return Err(
                    "EpoOpsAdapter needs GOSSAMER_EPO_KEY and GOSSAMER_EPO_SECRET".to_string(),
                );
            }
            let url = url::Url::parse_with_params(
                "https://ops.epo.org/3.2/rest-services/published-data/search",
                &[
                    ("q", query),
                    (
                        "Range",
                        &format!("1-{}", bounded_limit(max_results, 1, 100)),
                    ),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let (body, _) = fetch_text_with_user_agent(&url, 30.0, "gossamer")
                .map_err(|error| error.to_string())?;
            epo_parse_search_impl(&body, limit).map_err(|error| error.to_string())
        }
        "kipris" => {
            let key = env_key("GOSSAMER_KIPRIS_KEY");
            if key.is_empty() {
                return Err("KiprisAdapter needs GOSSAMER_KIPRIS_KEY".to_string());
            }
            let url = url::Url::parse_with_params(
                "http://kipo-api.kipi.or.kr/openapi/service/patUtliInfoSearchService/getWordSearch",
                &[
                    ("serviceKey", key.as_str()),
                    ("numOfRows", &max_results.min(100).to_string()),
                    ("word", query),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let body = fetch_provider_text(&url, 25.0).map_err(|error| error.to_string())?;
            kipris_parse_search_impl(&body, limit).map_err(|error| error.to_string())
        }
        "patentsview" => {
            let key = env_key("GOSSAMER_PATENTSVIEW_API_KEY");
            if key.is_empty() {
                return Err("PatentsViewAdapter needs GOSSAMER_PATENTSVIEW_API_KEY".to_string());
            }
            let q = json!({"_text_all": {"patent_title": query}}).to_string();
            let fields = json!(["patent_number", "patent_title", "patent_date"]).to_string();
            let options = json!({"per_page": max_results.min(100)}).to_string();
            let url = url::Url::parse_with_params(
                "https://search.patentsview.org/api/v1/patents/",
                &[
                    ("q", q.as_str()),
                    ("f", fields.as_str()),
                    ("o", options.as_str()),
                ],
            )
            .map_err(|error| error.to_string())?
            .to_string();
            let user_agent = format!("gossamer api-key:{key}");
            let (body, _) = fetch_text_with_user_agent(&url, 25.0, &user_agent)
                .map_err(|error| error.to_string())?;
            patentsview_parse_search_impl(&body, limit, minor).map_err(|error| error.to_string())
        }
        "lens" => {
            let key = env_key("GOSSAMER_LENS_API_KEY");
            if key.is_empty() {
                return Err("LensAdapter needs GOSSAMER_LENS_API_KEY".to_string());
            }
            let user_agent = format!("Bearer {key}");
            let url = format!(
                "https://api.lens.org/patent/search?size={}",
                bounded_limit(max_results, 1, 100)
            );
            let (body, _) = fetch_text_with_user_agent(&url, 25.0, &user_agent)
                .map_err(|error| error.to_string())?;
            lens_parse_search_impl(&body, limit, minor).map_err(|error| error.to_string())
        }
        "govinfo" => Err("GovInfoAdapter search requires GOSSAMER_GOVINFO_KEY".to_string()),
        "google-patents" => {
            Err("GooglePatentsAdapter needs a publication number (e.g. US5000575A)".to_string())
        }
        other => Err(format!("unknown provider: {other}")),
    }
}

fn taxonomy_entries(taxonomy: &Value) -> Vec<Value> {
    taxonomy.as_array().cloned().unwrap_or_default()
}

fn all_providers(taxonomy: &Value) -> Vec<String> {
    taxonomy_entries(taxonomy)
        .iter()
        .flat_map(|entry| {
            entry
                .get("providers")
                .and_then(Value::as_array)
                .map(|providers| {
                    providers
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default()
        })
        .collect()
}

fn find_category(taxonomy: &Value, name: &str) -> Option<Value> {
    taxonomy_entries(taxonomy)
        .into_iter()
        .find(|entry| entry.get("category").and_then(Value::as_str) == Some(name))
}

fn owner_category(taxonomy: &Value, provider: &str) -> Option<Value> {
    taxonomy_entries(taxonomy).into_iter().find(|entry| {
        entry
            .get("providers")
            .and_then(Value::as_array)
            .is_some_and(|providers| {
                providers
                    .iter()
                    .any(|value| value.as_str() == Some(provider))
            })
    })
}

fn entry_list(entry: &Value) -> Value {
    entry.get("providers").cloned().unwrap_or_else(|| json!([]))
}

fn entry_kind(entry: &Value) -> Value {
    entry.get("provider_kind").cloned().unwrap_or(Value::Null)
}

fn entry_description(entry: &Value) -> Value {
    entry.get("description").cloned().unwrap_or(Value::Null)
}

fn provider_names(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn print_failure(payload: &Value) -> ExitCode {
    println!("{}", py_dumps_pretty(payload));
    ExitCode::FAILURE
}

fn empty_provider_error(query: &str, message: String) -> ExitCode {
    print_failure(&json!({"query": query, "results": [], "error": message}))
}

fn scholarly_failure(
    query: &str,
    category_name: &str,
    selected: &str,
    available: &Value,
    entry: &Value,
    message: &str,
) -> ExitCode {
    print_failure(&json!({
        "query": query,
        "category": category_name,
        "provider": selected,
        "available_providers": available,
        "provider_kind": entry_kind(entry),
        "description": entry_description(entry),
        "results": [],
        "error": format!("{selected} search failed: {message}"),
    }))
}

fn openalex_records(
    query: &str,
    max_results: usize,
    filter: Option<&str>,
    select: Option<&str>,
    title: Option<&str>,
    author: Option<&str>,
) -> Result<Vec<Value>, String> {
    if let Some(select) = select
        && select.trim().is_empty()
    {
        return Err("OpenAlex select must not be blank".to_string());
    }
    let mut clauses: Vec<String> = Vec::new();
    if let Some(filter) = filter {
        if filter.trim().is_empty() {
            return Err("OpenAlex filter must not be blank".to_string());
        }
        clauses.push(filter.trim().to_string());
    }
    if let Some(title) = title {
        if title.trim().is_empty() {
            return Err("OpenAlex title must not be blank".to_string());
        }
        if title.contains(',') {
            return Err(
                "OpenAlex title must not contain a comma; use filter= for advanced syntax"
                    .to_string(),
            );
        }
        clauses.push(format!("title.search:{}", title.trim()));
    }
    if let Some(author) = author {
        if author.trim().is_empty() {
            return Err("OpenAlex author must not be blank".to_string());
        }
        if author.contains(',') {
            return Err(
                "OpenAlex author must not contain a comma; use filter= for advanced syntax"
                    .to_string(),
            );
        }
        clauses.push(format!("raw_author_name.search:{}", author.trim()));
    }
    let per_page = max_results.min(100).to_string();
    let mut params: Vec<(&str, String)> =
        vec![("search", query.to_string()), ("per_page", per_page)];
    if !clauses.is_empty() {
        params.push(("filter", clauses.join(",")));
    }
    if let Some(select) = select {
        params.push(("select", select.to_string()));
    }
    let email = std::env::var("GOSSAMER_OPENALEX_EMAIL")
        .unwrap_or_default()
        .trim()
        .to_string();
    if !email.is_empty() {
        params.push(("mailto", email.clone()));
    }
    let key = std::env::var("GOSSAMER_OPENALEX_KEY").unwrap_or_default();
    if !key.is_empty() {
        params.push(("api_key", key));
    }
    let pairs: Vec<(&str, &str)> = params
        .iter()
        .map(|(name, value)| (*name, value.as_str()))
        .collect();
    let url = url::Url::parse_with_params("https://api.openalex.org/works", pairs)
        .map_err(|error| error.to_string())?
        .to_string();
    let user_agent = if email.is_empty() {
        gossamer::USER_AGENT.to_string()
    } else {
        format!("{} (mailto:{email})", gossamer::USER_AGENT)
    };
    let (body, _) = fetch_text_with_user_agent(&url, 15.0, &user_agent)?;
    let mut records = openalex_parse_search_impl(&body, max_results as i64, None)
        .map_err(|error| error.to_string())?;
    if let Ok(parsed) = serde_json::from_str::<Value>(&body) {
        attach_raw(
            &mut records,
            parsed.get("results").and_then(Value::as_array),
        );
    }
    Ok(records)
}

fn run_providers(query: &str, requested: &[String], max_results: usize) -> ExitCode {
    let taxonomy: Value = serde_json::from_str(CATEGORIES_JSON).unwrap_or_else(|_| json!([]));
    if requested.iter().any(|name| name.is_empty()) {
        return empty_provider_error(
            query,
            "providers= entries must be non-empty provider ids".to_string(),
        );
    }
    let mut unique = std::collections::HashSet::new();
    if requested.iter().any(|name| !unique.insert(name)) {
        return empty_provider_error(
            query,
            "providers= cannot contain duplicate provider ids".to_string(),
        );
    }
    let owners: Vec<Option<Value>> = requested
        .iter()
        .map(|name| owner_category(&taxonomy, name))
        .collect();
    let unknown: Vec<&str> = requested
        .iter()
        .zip(owners.iter())
        .filter_map(|(name, owner)| owner.is_none().then_some(name.as_str()))
        .collect();
    if !unknown.is_empty() {
        return print_failure(&json!({
            "query": query,
            "results": [],
            "providers": requested,
            "error": format!("unknown provider(s): {}", unknown.join(", ")),
        }));
    }
    let owner_names: std::collections::HashSet<String> = owners
        .iter()
        .flatten()
        .filter_map(|entry| entry.get("category").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    if owner_names.len() != 1 {
        return print_failure(&json!({
            "query": query,
            "results": [],
            "providers": requested,
            "error": "all providers= entries must belong to the same category",
        }));
    }
    let owner_name = owner_names.iter().next().cloned().unwrap_or_default();
    let Some(entry) = find_category(&taxonomy, &owner_name) else {
        return unsupported();
    };
    if owner_name != "scholarly"
        || entry.get("provider_kind").and_then(Value::as_str) != Some("adapter")
    {
        return print_failure(&json!({
            "query": query,
            "results": [],
            "category": owner_name,
            "providers": requested,
            "error": "multi-provider merge is currently supported only for scholarly",
        }));
    }
    let available = entry_list(&entry);
    let unavailable: Vec<&str> = requested
        .iter()
        .filter(|name| {
            !provider_names(&available)
                .iter()
                .any(|known| known == *name)
        })
        .map(String::as_str)
        .collect();
    if !unavailable.is_empty() {
        return print_failure(&json!({
            "query": query,
            "results": [],
            "category": owner_name,
            "providers": requested,
            "available_providers": available,
            "error": format!(
                "provider(s) unavailable for scholarly: {}",
                unavailable.join(", ")
            ),
        }));
    }
    let mut pairs = Vec::new();
    let mut errors = Vec::new();
    for name in requested {
        match fetch_provider_records(name, query, max_results) {
            Ok(records) => pairs.push(json!([name, records])),
            Err(message) => errors.push(json!({"provider": name, "message": message})),
        }
    }
    let merged = merge_scholarly_records(&Value::Array(pairs.clone()));
    let mut payload = json!({
        "query": query,
        "category": "scholarly",
        "provider": Value::Null,
        "providers": requested,
        "available_providers": available,
        "provider_kind": entry_kind(&entry),
        "description": entry_description(&entry),
        "results": merged,
    });
    if !errors.is_empty() {
        let message = if pairs.is_empty() {
            "All requested providers failed"
        } else {
            "One or more requested providers failed"
        };
        if let Some(object) = payload.as_object_mut() {
            object.insert("provider_errors".to_string(), Value::Array(errors));
            object.insert("error".to_string(), Value::String(message.to_string()));
        }
    }
    println!("{}", py_dumps_pretty(&payload));
    if payload.get("error").is_some() {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let Some(query) = parsed.positional("query").map(str::to_string) else {
        return unsupported();
    };
    let category = parsed.value("category").map(str::to_string);
    let provider = parsed.value("provider").map(str::to_string);
    let providers: Vec<String> = parsed.values("providers").to_vec();
    let filter = parsed.value("filter").map(str::to_string);
    let select = parsed.value("select").map(str::to_string);
    let title = parsed.value("title").map(str::to_string);
    let author = parsed.value("author").map(str::to_string);
    let max_results = parsed.int("max_results").unwrap_or(5).max(0) as usize;
    let has_options = filter.is_some() || select.is_some() || title.is_some() || author.is_some();

    let taxonomy: Value = match serde_json::from_str(CATEGORIES_JSON) {
        Ok(value) => value,
        Err(_) => return unsupported(),
    };
    if query.trim().is_empty() {
        if has_options || !providers.is_empty() {
            return empty_provider_error(
                &query,
                "filter/select/title/author/providers options require a research query".to_string(),
            );
        }
        println!("{}", py_dumps_pretty(&taxonomy));
        return ExitCode::SUCCESS;
    }
    if provider.is_some() && !providers.is_empty() {
        return empty_provider_error(
            &query,
            "use either provider= or providers=, not both".to_string(),
        );
    }
    if !providers.is_empty() {
        if has_options {
            return empty_provider_error(
                &query,
                "OpenAlex filter/select/title/author options cannot be combined with providers="
                    .to_string(),
            );
        }
        if let Some(explicit) = category.as_deref()
            && find_category(&taxonomy, explicit).is_none()
        {
            return print_failure(&json!({
                "query": query,
                "results": [],
                "category": explicit,
                "error": format!("unknown category '{explicit}'"),
            }));
        }
        return run_providers(&query, &providers, max_results);
    }

    let entry = if let Some(explicit) = category.as_deref() {
        match find_category(&taxonomy, explicit) {
            Some(entry) => entry,
            None => {
                return print_failure(&json!({
                    "query": query,
                    "category": explicit,
                    "provider": provider,
                    "available_categories": taxonomy_entries(&taxonomy)
                        .iter()
                        .filter_map(|entry| entry.get("category").and_then(Value::as_str))
                        .collect::<Vec<_>>(),
                    "error": format!(
                        "unknown category '{explicit}'; choose from: {}",
                        taxonomy_entries(&taxonomy)
                            .iter()
                            .filter_map(|entry| entry.get("category").and_then(Value::as_str))
                            .collect::<Vec<_>>()
                            .join(", ")
                    ),
                    "results": [],
                }));
            }
        }
    } else if let Some(name) = provider.as_deref() {
        match owner_category(&taxonomy, name) {
            Some(entry) => entry,
            None => {
                let available = all_providers(&taxonomy);
                return print_failure(&json!({
                    "query": query,
                    "category": Value::Null,
                    "provider": name,
                    "available_providers": available,
                    "error": format!(
                        "unknown provider '{name}'; choose from: {}",
                        available.join(", ")
                    ),
                    "results": [],
                }));
            }
        }
    } else {
        let name = classify_query(Some(query.as_str()));
        match find_category(&taxonomy, &name) {
            Some(entry) => entry,
            None => return unsupported(),
        }
    };

    let category_name = entry
        .get("category")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let default_provider = entry
        .get("default_provider")
        .and_then(Value::as_str)
        .unwrap_or("duckduckgo")
        .to_string();
    let selected = provider.unwrap_or(default_provider);
    let available = entry_list(&entry);
    if !provider_names(&available)
        .iter()
        .any(|known| known == &selected)
    {
        return print_failure(&json!({
            "query": query,
            "category": category_name,
            "provider": selected,
            "available_providers": available,
            "provider_kind": entry_kind(&entry),
            "error": format!(
                "provider '{}' is not available for category '{}'; choose from: {}",
                selected,
                category_name,
                provider_names(&available).join(", ")
            ),
            "results": [],
        }));
    }
    if has_options && selected != "openalex" {
        return print_failure(&json!({
            "query": query,
            "category": category_name,
            "provider": selected,
            "available_providers": available,
            "provider_kind": entry_kind(&entry),
            "error": "OpenAlex filter/select/title/author options require provider='openalex'",
            "results": [],
        }));
    }
    let fetched: Result<Vec<Value>, String> = if selected == "openalex" {
        openalex_records(
            &query,
            max_results,
            filter.as_deref(),
            select.as_deref(),
            title.as_deref(),
            author.as_deref(),
        )
    } else if matches!(
        selected.as_str(),
        "crossref" | "arxiv" | "zenodo" | "semanticscholar" | "huggingface" | "huggingface-papers"
    ) {
        fetch_provider_records(&selected, &query, max_results)
    } else if selected == "duckduckgo" {
        match fetch_text(&duckduckgo_search_url(&query), 20.0) {
            Ok((body, _)) => Ok(duckduckgo_results(&body, max_results)),
            Err(error) => Err(error),
        }
    } else {
        generic_provider_records(&selected, &query, max_results)
    };
    let results = match fetched {
        Ok(results) => results,
        Err(message) => {
            return scholarly_failure(
                &query,
                &category_name,
                &selected,
                &available,
                &entry,
                &message,
            );
        }
    };
    let payload = json!({
        "query": query,
        "category": category_name,
        "provider": selected,
        "available_providers": available,
        "provider_kind": entry.get("provider_kind").cloned().unwrap_or(Value::Null),
        "description": entry.get("description").cloned().unwrap_or(Value::Null),
        "results": results,
    });
    println!("{}", py_dumps_pretty(&payload));
    ExitCode::SUCCESS
}
