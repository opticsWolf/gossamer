//! DOI normalization kernels shared by open-access lookup and citation paths.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Value, json};

use crate::compat::py_repr;

/// Regex source mirroring the Python DOI shape check.
const DOI_PATTERN: &str = r"(?i)^10\.\d{4,9}/\S+$";

/// Compiled once for speed; this immutable cache carries no process state.
static DOI_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(DOI_PATTERN).unwrap_or_else(|e| panic!("invalid DOI regex {DOI_PATTERN:?}: {e}"))
});

/// Decode `%XX` escapes without treating `+` as a space.
pub fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = &value[index + 1..index + 3];
            if let Ok(byte) = u8::from_str_radix(hex, 16) {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Normalize a bare DOI, `doi:` value or DOI-resolver URL.
///
/// # Errors
///
/// Returns a `ValueError`-shaped message when a resolver URL carries query or
/// fragment components, or when the decoded DOI does not match the expected
/// `10.NNNN/suffix` shape.
pub fn normalize_doi(value: &str) -> Result<String, String> {
    let trimmed = value.trim();
    let mut doi = if trimmed.len() >= 4 && trimmed[..4].eq_ignore_ascii_case("doi:") {
        trimmed[4..].trim().to_string()
    } else {
        let lower = trimmed.to_ascii_lowercase();
        let scheme_end = lower.find("://");
        let is_http = scheme_end.is_some_and(|at| {
            let scheme = &lower[..at];
            scheme == "http" || scheme == "https"
        });
        if is_http {
            let rest = &trimmed[scheme_end.unwrap_or(0) + 3..];
            let authority_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
            let host = rest[..authority_end]
                .rsplit('@')
                .next()
                .unwrap_or("")
                .split(':')
                .next()
                .unwrap_or("")
                .to_ascii_lowercase();
            if matches!(host.as_str(), "doi.org" | "www.doi.org" | "dx.doi.org") {
                let tail = &rest[authority_end..];
                if tail.contains('?') || tail.contains('#') {
                    return Err(
                        "ValueError: DOI resolver URLs must not include a query or fragment"
                            .to_string(),
                    );
                }
                percent_decode(tail.trim_start_matches('/'))
            } else {
                trimmed.to_string()
            }
        } else {
            trimmed.to_string()
        }
    };
    if !DOI_RE.is_match(&doi) {
        return Err(format!(
            "ValueError: expected a DOI such as {}",
            py_repr("10.1234/example")
        ));
    }
    doi.make_ascii_lowercase();
    Ok(doi)
}

/// Map one OpenAlex location to the shared OA-candidate shape.
pub fn oa_candidate(location: &Value, is_best: bool, work_is_oa: bool) -> Option<Value> {
    let object = location.as_object()?;
    let location_is_oa = object.get("is_oa");
    if location_is_oa == Some(&Value::Bool(false)) || (location_is_oa.is_none() && !work_is_oa) {
        return None;
    }
    let pdf = object.get("pdf_url").cloned().unwrap_or(Value::Null);
    let landing = object
        .get("landing_page_url")
        .cloned()
        .unwrap_or(Value::Null);
    let url = if !pdf.is_null() {
        pdf.clone()
    } else {
        landing.clone()
    };
    if !url.as_str().is_some_and(|text| !text.trim().is_empty()) {
        return None;
    }
    let source = object.get("source");
    let (name, id) = match source {
        Some(Value::Object(map)) => (
            map.get("display_name").cloned().unwrap_or(Value::Null),
            map.get("id").cloned().unwrap_or(Value::Null),
        ),
        Some(Value::String(text)) => (Value::String(text.clone()), Value::Null),
        _ => (Value::Null, Value::Null),
    };
    Some(serde_json::json!({
        "url": url,
        "kind": if pdf.is_null() { "landing_page" } else { "pdf" },
        "pdf_url": pdf,
        "landing_page_url": landing,
        "source": name,
        "source_id": id,
        "is_oa": location_is_oa.cloned().unwrap_or(Value::Bool(work_is_oa)),
        "license": object.get("license").cloned().unwrap_or(Value::Null),
        "version": object.get("version").cloned().unwrap_or(Value::Null),
        "is_best": is_best,
    }))
}

/// Map one Unpaywall v2 location to the shared OA-candidate shape.
pub fn unpaywall_candidate(location: &Value, is_best: bool) -> Option<Value> {
    let object = location.as_object()?;
    let pdf = object
        .get("url_for_pdf")
        .or_else(|| object.get("pdf_url"))
        .cloned()
        .unwrap_or(Value::Null);
    let landing = object
        .get("url_for_landing_page")
        .or_else(|| object.get("landing_page_url"))
        .or_else(|| object.get("url"))
        .cloned()
        .unwrap_or(Value::Null);
    let url = if !pdf.is_null() {
        pdf.clone()
    } else {
        landing.clone()
    };
    if !url.as_str().is_some_and(|text| !text.trim().is_empty()) {
        return None;
    }
    let source = object.get("source");
    let mut name = match source {
        Some(Value::Object(map)) => map.get("display_name").cloned().unwrap_or(Value::Null),
        Some(Value::String(text)) => Value::String(text.clone()),
        _ => Value::Null,
    };
    if name.is_null() {
        name = object
            .get("endpoint_label")
            .or_else(|| object.get("host_type"))
            .cloned()
            .unwrap_or(Value::Null);
    }
    Some(serde_json::json!({
        "url": url,
        "kind": if pdf.is_null() { "landing_page" } else { "pdf" },
        "pdf_url": pdf,
        "landing_page_url": landing,
        "source": name,
        "source_id": Value::Null,
        "is_oa": true,
        "license": object.get("license").cloned().unwrap_or(Value::Null),
        "version": object.get("version").cloned().unwrap_or(Value::Null),
        "is_best": is_best,
    }))
}

/// Extract deduplicated OA candidates and work metadata from Unpaywall.
pub fn unpaywall_candidates(payload: &Value) -> (Vec<Value>, Value) {
    let mut ordered: Vec<(Value, bool)> = Vec::new();
    if let Some(best) = payload
        .get("best_oa_location")
        .filter(|value| value.is_object())
    {
        ordered.push((best.clone(), true));
    }
    if let Some(locations) = payload.get("oa_locations").and_then(Value::as_array) {
        ordered.extend(locations.iter().map(|item| (item.clone(), false)));
    }
    let mut candidates = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for (location, is_best) in ordered {
        let Some(candidate) = unpaywall_candidate(&location, is_best) else {
            continue;
        };
        let url = candidate
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if seen.contains(&url) {
            continue;
        }
        seen.push(url);
        candidates.push(candidate);
    }
    let is_oa = payload
        .get("is_oa")
        .and_then(Value::as_bool)
        .unwrap_or(!candidates.is_empty());
    let work = serde_json::json!({
        "id": Value::Null,
        "title": payload.get("title").cloned().unwrap_or(Value::Null),
        "url": payload.get("doi_url").cloned().unwrap_or(Value::Null),
        "published": payload.get("published_date").cloned().unwrap_or(Value::Null),
        "is_oa": is_oa,
        "oa_status": payload.get("oa_status").cloned().unwrap_or(Value::Null),
    });
    (candidates, work)
}

/// Builds the final `OpenAccessLocator.locate` envelope from caller-supplied
/// provider results. All network access stays in the caller.
#[expect(
    clippy::too_many_arguments,
    reason = "the locator envelope mirrors the Python call boundary"
)]
pub fn locate_result(
    doi: &str,
    records: &[Value],
    openalex_error: Option<&Value>,
    fallback_candidates: &[Value],
    fallback_work: &Value,
    unpaywall_error: Option<&Value>,
    unpaywall_enabled: bool,
    include_unpaywall_error: bool,
    invalid_record_message: &str,
) -> Value {
    let sources = if unpaywall_enabled {
        vec!["openalex", "unpaywall"]
    } else {
        vec!["openalex"]
    };
    let fallback_hit = |provider_errors: Option<Value>| {
        let mut out = json!({
            "doi": doi,
            "provider": "unpaywall",
            "sources": ["openalex", "unpaywall"],
            "status": "open_access",
            "work": fallback_work,
            "candidates": fallback_candidates,
        });
        if let Some(errors) = provider_errors {
            out["provider_errors"] = errors;
        }
        out
    };

    if let Some(error) = openalex_error {
        if !fallback_candidates.is_empty() {
            return fallback_hit(Some(json!([
                {"provider": "openalex", "message": error.get("message").and_then(Value::as_str).unwrap_or("")}
            ])));
        }
        let mut record = json!({
            "code": error.get("code").and_then(Value::as_str).unwrap_or("provider_error"),
            "message": error.get("message").and_then(Value::as_str).unwrap_or(""),
        });
        if include_unpaywall_error && let Some(unpaywall) = unpaywall_error {
            record["unpaywall"] = unpaywall.clone();
        }
        return json!({
            "doi": doi,
            "provider": "openalex",
            "sources": sources,
            "status": "error",
            "error": record,
            "candidates": [],
        });
    }

    let Some(record) = records.first() else {
        if !fallback_candidates.is_empty() {
            return fallback_hit(None);
        }
        return json!({
            "doi": doi,
            "provider": "openalex",
            "sources": sources,
            "status": "not_found",
            "candidates": [],
        });
    };

    let raw_text = record.get("raw").and_then(Value::as_str).unwrap_or("{}");
    let Ok(raw) = serde_json::from_str::<Value>(raw_text) else {
        return json!({
            "doi": doi,
            "provider": "openalex",
            "status": "error",
            "error": {
                "code": "invalid_provider_record",
                "message": invalid_record_message.to_string(),
            },
            "candidates": [],
        });
    };
    let open_access = raw.get("open_access").cloned().unwrap_or_else(|| json!({}));
    let work_is_oa = open_access
        .get("is_oa")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let best = raw.get("best_oa_location").cloned();
    let locations = raw
        .get("locations")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut ordered: Vec<(Value, bool)> = Vec::new();
    if let Some(best) = best {
        ordered.push((best, true));
    }
    ordered.extend(locations.into_iter().map(|location| (location, false)));
    let mut candidates = Vec::new();
    let mut seen = HashSet::new();
    for (location, is_best) in ordered {
        let Some(candidate) = oa_candidate(&location, is_best, work_is_oa) else {
            continue;
        };
        let url = candidate
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !seen.insert(url) {
            continue;
        }
        candidates.push(candidate);
    }
    let work = |fallback_work: Option<&Value>| {
        json!({
            "id": record.get("id").cloned().unwrap_or(Value::Null),
            "title": record
                .get("title")
                .cloned()
                .filter(|value| !value.is_null())
                .or_else(|| fallback_work.and_then(|value| value.get("title").cloned()))
                .unwrap_or(Value::Null),
            "url": record.get("url").cloned().unwrap_or(Value::Null),
            "published": record
                .get("published")
                .cloned()
                .filter(|value| !value.is_null())
                .or_else(|| {
                    fallback_work
                        .and_then(|value| value.get("published").cloned())
                })
                .unwrap_or(Value::Null),
            "is_oa": work_is_oa || fallback_work.is_some(),
            "oa_status": open_access
                .get("oa_status")
                .cloned()
                .filter(|value| !value.is_null())
                .or_else(|| {
                    fallback_work
                        .and_then(|value| value.get("oa_status").cloned())
                })
                .unwrap_or(Value::Null),
        })
    };
    if !candidates.is_empty() {
        return json!({
            "doi": doi,
            "provider": "openalex",
            "sources": ["openalex"],
            "status": "open_access",
            "work": work(None),
            "candidates": candidates,
        });
    }
    if !fallback_candidates.is_empty() {
        let mut merged = candidates;
        let mut urls = seen;
        for item in fallback_candidates {
            let url = item
                .get("url")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if urls.insert(url) {
                merged.push(item.clone());
            }
        }
        return json!({
            "doi": doi,
            "provider": "unpaywall",
            "sources": ["openalex", "unpaywall"],
            "status": "open_access",
            "work": work(Some(fallback_work)),
            "candidates": merged,
        });
    }
    let status = if work_is_oa {
        "open_access_no_location"
    } else {
        "closed_access"
    };
    json!({
        "doi": doi,
        "provider": "openalex",
        "sources": sources,
        "status": status,
        "work": work(None),
        "candidates": candidates,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn doi_forms() {
        assert_eq!(
            normalize_doi(" DOI:10.1234/Example ").ok().as_deref(),
            Some("10.1234/example")
        );
        assert_eq!(
            normalize_doi("https://doi.org/10.1234/Ab%2FC")
                .ok()
                .as_deref(),
            Some("10.1234/ab/c")
        );
        assert!(normalize_doi("https://doi.org/10.1234/x?q=1").is_err());
    }
}
