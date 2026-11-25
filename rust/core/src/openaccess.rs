//! DOI normalization kernels shared by open-access lookup and citation paths.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

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
            let authority_end = rest
                .find(['/', '?', '#'])
                .unwrap_or(rest.len());
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
                    return Err("ValueError: DOI resolver URLs must not include a query or fragment".to_string());
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
    if location_is_oa == Some(&Value::Bool(false))
        || (location_is_oa.is_none() && !work_is_oa)
    { return None; }
    let pdf = object.get("pdf_url").cloned().unwrap_or(Value::Null);
    let landing = object.get("landing_page_url").cloned().unwrap_or(Value::Null);
    let url = if !pdf.is_null() { pdf.clone() } else { landing.clone() };
    if !url.as_str().is_some_and(|text| !text.trim().is_empty()) { return None; }
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
    let pdf = object.get("url_for_pdf").or_else(|| object.get("pdf_url")).cloned().unwrap_or(Value::Null);
    let landing = object.get("url_for_landing_page")
        .or_else(|| object.get("landing_page_url"))
        .or_else(|| object.get("url"))
        .cloned().unwrap_or(Value::Null);
    let url = if !pdf.is_null() { pdf.clone() } else { landing.clone() };
    if !url.as_str().is_some_and(|text| !text.trim().is_empty()) { return None; }
    let source = object.get("source");
    let mut name = match source {
        Some(Value::Object(map)) => map.get("display_name").cloned().unwrap_or(Value::Null),
        Some(Value::String(text)) => Value::String(text.clone()),
        _ => Value::Null,
    };
    if name.is_null() {
        name = object.get("endpoint_label").or_else(|| object.get("host_type")).cloned().unwrap_or(Value::Null);
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
    if let Some(best) = payload.get("best_oa_location").filter(|value| value.is_object()) {
        ordered.push((best.clone(), true));
    }
    if let Some(locations) = payload.get("oa_locations").and_then(Value::as_array) {
        ordered.extend(locations.iter().map(|item| (item.clone(), false)));
    }
    let mut candidates = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for (location, is_best) in ordered {
        let Some(candidate) = unpaywall_candidate(&location, is_best) else { continue; };
        let url = candidate.get("url").and_then(Value::as_str).unwrap_or("").to_string();
        if seen.contains(&url) { continue; }
        seen.push(url);
        candidates.push(candidate);
    }
    let is_oa = payload.get("is_oa").and_then(Value::as_bool).unwrap_or(!candidates.is_empty());
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn doi_forms() {
        assert_eq!(normalize_doi(" DOI:10.1234/Example ").ok().as_deref(), Some("10.1234/example"));
        assert_eq!(normalize_doi("https://doi.org/10.1234/Ab%2FC").ok().as_deref(), Some("10.1234/ab/c"));
        assert!(normalize_doi("https://doi.org/10.1234/x?q=1").is_err());
    }
}
