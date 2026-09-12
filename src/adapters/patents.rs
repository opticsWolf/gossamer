//! Adapter kernels: Patent kernels (PatentsView, EPO OPS, KIPRIS, Lens, Google Patents).

use pyo3::prelude::*;
use serde_json::Value;
use crate::pycompat::{char_head};
use crate::cite::py_value_repr;
use super::common::{attr_error, type_error_not_subscriptable, json_type, is_truthy, to_py_err, subscript_hits, py_str_value, slice_refs_len};

fn patentsview_row_impl(p: &Value) -> Result<Value, String> {
    let m = match p {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(p))),
    };
    // `str(p.get("patent_number", p.get("id", "")))`: the inner default
    // applies only when the outer key is missing.
    let number = match m.get("patent_number") {
        Some(v) => py_value_repr(v),
        None => match m.get("id") {
            Some(v) => py_value_repr(v),
            None => String::new(),
        },
    };
    // `p.get("patent_title", p.get("title", number))`: raw values; the
    // ultimate default is the rendered number string.
    let title = match m.get("patent_title") {
        Some(v) => v.clone(),
        None => match m.get("title") {
            Some(v) => v.clone(),
            None => Value::String(number.clone()),
        },
    };
    // `str(p.get("patent_date", p.get("date", "")))[:10]`.
    let date_raw = match m.get("patent_date") {
        Some(v) => py_value_repr(v),
        None => match m.get("date") {
            Some(v) => py_value_repr(v),
            None => String::new(),
        },
    };
    let date = char_head(date_raw.as_str(), 10).to_string();
    let url = if number.is_empty() {
        Value::String(String::new())
    } else {
        Value::String(format!("https://patents.google.com/patent/US{number}"))
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("patentsview".to_string()),
    );
    rec.insert("id".to_string(), Value::String(number.clone()));
    rec.insert("title".to_string(), title.clone());
    rec.insert("url".to_string(), url);
    rec.insert("published".to_string(), Value::String(date.clone()));
    rec.insert(
        "snippet".to_string(),
        Value::String(format!("{0} ({date})", py_value_repr(&title))),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "assignee".to_string(),
        match m.get("assignee_organization") {
            Some(v) => v.clone(),
            None => m
                .get("assignee")
                .cloned()
                .unwrap_or(Value::String(String::new())),
        },
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn patentsview_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // API-level errors surface before row building.
    if let Some(err) = obj.get("error") {
        if is_truthy(err) {
            return Err(format!(
                "RuntimeError: PatentsView error: {0}",
                py_value_repr(err)
            ));
        }
    }
    // `body.get("patents", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("patents") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for p in hits {
        out.push(patentsview_row_impl(p)?);
    }
    Ok(out)
}

pub fn patentsview_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // Non-dict bodies fall through both `isinstance` checks → [].
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Ok(Vec::new()),
    };
    // `body["patents"][0]`: truthy patents index; str → char (whose
    // `.get` raises), dict → KeyError 0, anything else TypeError.
    if let Some(patents) = obj.get("patents") {
        if is_truthy(patents) {
            let first = match patents {
                Value::Array(a) => match a.first() {
                    Some(v) => v,
                    None => return Ok(Vec::new()),
                },
                Value::String(s) => match s.chars().next() {
                    Some(_) => return Err(attr_error("str")),
                    None => return Ok(Vec::new()),
                },
                Value::Object(_) => return Err("KeyError: 0".to_string()),
                other => return Err(type_error_not_subscriptable(json_type(other))),
            };
            return Ok(vec![patentsview_row_impl(first)?]);
        }
    }
    if let Some(v) = obj.get("patent_number") {
        if is_truthy(v) {
            return Ok(vec![patentsview_row_impl(&body)?]);
        }
    }
    Ok(Vec::new())
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn patentsview_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    patentsview_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn patentsview_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    patentsview_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}



/// `_text`: `" ".join(parts)[:400]` over `invention-title` / `title`
/// descendants in document order (the element itself included, as with
/// `element.iter()`). Only elements with truthy `.text` contribute —
/// whitespace-only texts strip to "" but are still appended — with a
/// `[lang]` prefix when set.
fn epo_title_text(doc: &crate::xmlatom::Node) -> String {
    let mut items: Vec<&crate::xmlatom::Node> = Vec::new();
    let mut st = vec![doc];
    while let Some(n) = st.pop() {
        items.push(n);
        st.extend(n.children.iter().rev());
    }
    let mut parts: Vec<String> = Vec::new();
    for el in items.iter() {
        if (el.local == "invention-title" || el.local == "title")
            && !el.text.is_empty()
        {
            let lang = el.attr("lang");
            let stripped =
                crate::pycompat::py_strip(el.text.as_str()).to_string();
            if lang.is_empty() {
                parts.push(stripped);
            } else {
                parts.push(format!("[{lang}] {stripped}"));
            }
        }
    }
    crate::pycompat::char_head(parts.join(" ").as_str(), 400).to_string()
}

fn epo_row_impl(doc: &crate::xmlatom::Node) -> Value {
    let mut number = String::new();
    let mut kind = String::new();
    let mut date = String::new();
    let mut applicants: Vec<String> = Vec::new();
    // `doc.iter()` in document order, self included; later
    // `document-id` hits overwrite earlier ones (no break).
    let mut items: Vec<&crate::xmlatom::Node> = Vec::new();
    let mut st = vec![doc];
    while let Some(n) = st.pop() {
        items.push(n);
        st.extend(n.children.iter().rev());
    }
    for el in items.iter() {
        if el.local == "document-id" && el.attr("document-id-type") == "epodoc" {
            for child in el.children.iter() {
                if child.local == "doc-number" {
                    number = crate::pycompat::py_strip(child.text.as_str()).to_string();
                } else if child.local == "kind" {
                    kind = crate::pycompat::py_strip(child.text.as_str()).to_string();
                } else if child.local == "date" {
                    date = crate::pycompat::char_head(
                        crate::pycompat::py_strip(child.text.as_str()),
                        10,
                    )
                    .to_string();
                }
            }
        } else if el.local == "applicant-name" || el.local == "inventor-name" {
            // `el.findtext(".//{*}name")`: first descendant `name`.
            let name = match el.descendant_text("name") {
                None => String::new(),
                Some(t) => crate::pycompat::py_strip(t.as_str()).to_string(),
            };
            if !name.is_empty() {
                applicants.push(name);
            }
        }
    }
    let epodoc = format!("{number}{kind}");
    let title = match epo_title_text(doc) {
        t if t.is_empty() => epodoc.clone(),
        t => t,
    };
    let mut fields = serde_json::Map::new();
    fields.insert(
        "publication_number".to_string(),
        Value::String(number.clone()),
    );
    fields.insert("kind".to_string(), Value::String(kind.clone()));
    fields.insert(
        "applicants".to_string(),
        Value::String(applicants.iter().take(5).cloned().collect::<Vec<String>>().join(", ")),
    );
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("epo".to_string()));
    rec.insert(
        "id".to_string(),
        Value::String(if epodoc.is_empty() {
            number.clone()
        } else {
            epodoc.clone()
        }),
    );
    rec.insert("title".to_string(), Value::String(title.clone()));
    rec.insert(
        "url".to_string(),
        Value::String(if epodoc.is_empty() {
            String::new()
        } else {
            format!("https://worldwide.espacenet.com/patent/search?q=pn%3D{epodoc}")
        }),
    );
    rec.insert("published".to_string(), Value::String(date.clone()));
    // `f"{title} — {applicants[:3]}".strip(" —")`: strip chars.
    let joined = applicants.iter().take(3).cloned().collect::<Vec<String>>().join(", ");
    let snippet = crate::pycompat::py_strip_chars(
        format!("{title} \u{2014} {joined}").as_str(),
        &[' ', '\u{2014}'],
    )
    .to_string();
    rec.insert("snippet".to_string(), Value::String(snippet));
    rec.insert("fields".to_string(), Value::Object(fields));
    // `raw` is `json.dumps({epodoc, title, date})`: the three cross
    // here (id/title/published reconstruct them) and the wrapper
    // re-dumps (see below).
    let mut raw = serde_json::Map::new();
    raw.insert("epodoc".to_string(), Value::String(epodoc));
    raw.insert("title".to_string(), Value::String(title));
    raw.insert("date".to_string(), Value::String(date));
    rec.insert("raw".to_string(), Value::Object(raw));
    Value::Object(rec)
}

pub fn epo_parse_search_impl(
    response_xml: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // `exchange-document` descendants in pre-order (root included),
    // append-then-break.
    let mut out = Vec::new();
    let mut stack: Vec<&crate::xmlatom::Node> = vec![&root];
    while let Some(el) = stack.pop() {
        if el.local == "exchange-document" {
            out.push(epo_row_impl(el));
            if out.len() as i64 >= max_results {
                break;
            }
        }
        stack.extend(el.children.iter().rev());
    }
    Ok(out)
}

pub fn epo_parse_fetch_impl(response_xml: &str) -> Result<Value, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // First `exchange-document` wins; none answers `[]` (as JSON null
    // here — the wrapper maps it back to an empty list).
    let mut stack: Vec<&crate::xmlatom::Node> = vec![&root];
    while let Some(el) = stack.pop() {
        if el.local == "exchange-document" {
            return Ok(epo_row_impl(el));
        }
        stack.extend(el.children.iter().rev());
    }
    Ok(Value::Null)
}


/// `_item_to_dict`: direct `{tag: stripped-text}` (later duplicates
/// overwrite earlier ones, keeping first position — as dicts do).
fn kipris_item_dict(item: &crate::xmlatom::Node) -> serde_json::Map<String, Value> {
    let mut out = serde_json::Map::new();
    for child in item.children.iter() {
        // NOTE: `child.text or ""` then `.strip()`.
        out.insert(
            child.local.clone(),
            Value::String(crate::pycompat::py_strip(child.text.as_str()).to_string()),
        );
    }
    out
}

fn kipris_row_impl(d: &serde_json::Map<String, Value>) -> Value {
    let empty = Value::String(String::new());
    // `d.get("applicationNumber", d.get("application_number", ""))`:
    // eager inner default (both evaluated regardless).
    let app_no = d
        .get("applicationNumber")
        .unwrap_or(d.get("application_number").unwrap_or(&empty))
        .clone();
    let title = d
        .get("inventionTitle")
        .unwrap_or(d.get("title").unwrap_or(&app_no))
        .clone();
    let published = d
        .get("publicationDate")
        .unwrap_or(d.get("registrationDate").unwrap_or(&empty))
        .clone();
    let applicant = d.get("applicantName").unwrap_or(&empty);
    let status = d
        .get("applicationStatus")
        .unwrap_or(d.get("registerStatus").unwrap_or(&empty))
        .clone();
    // `f"{title} — {applicant}".strip(" —")`: title/applicant render.
    let snippet = crate::pycompat::py_strip_chars(
        format!(
            "{0} \u{2014} {1}",
            py_str_value(&title),
            py_str_value(applicant)
        )
        .as_str(),
        &[' ', '\u{2014}'],
    )
    .to_string();
    let mut fields = serde_json::Map::new();
    fields.insert("applicant".to_string(), applicant.clone());
    fields.insert("status".to_string(), status);
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("kipris".to_string()));
    rec.insert("id".to_string(), app_no);
    rec.insert("title".to_string(), title);
    rec.insert("url".to_string(), Value::String(String::new()));
    rec.insert("published".to_string(), published);
    rec.insert("snippet".to_string(), Value::String(snippet));
    rec.insert("fields".to_string(), Value::Object(fields));
    // `raw` is `json.dumps(d, ensure_ascii=False)`: `d` crosses here
    // and the wrapper re-dumps with `ensure_ascii=False` (see below).
    rec.insert("raw".to_string(), Value::Object(d.clone()));
    Value::Object(rec)
}

pub fn kipris_parse_impl(response_xml: &str) -> Result<Vec<serde_json::Map<String, Value>>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // All `item` descendants in pre-order (root included), no limit.
    let mut out = Vec::new();
    let mut stack: Vec<&crate::xmlatom::Node> = vec![&root];
    while let Some(el) = stack.pop() {
        if el.local == "item" {
            out.push(kipris_item_dict(el));
        }
        stack.extend(el.children.iter().rev());
    }
    Ok(out)
}

pub fn kipris_parse_search_impl(
    response_xml: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let items = kipris_parse_impl(response_xml)?;
    // `items[:max_results]` slices (negatives clip, dicts impossible —
    // items is a fresh list).
    let end = slice_refs_len(items.len(), max_results);
    let mut out = Vec::new();
    for d in items.iter().take(end) {
        out.push(kipris_row_impl(d));
    }
    Ok(out)
}

pub fn kipris_parse_fetch_impl(response_xml: &str) -> Result<Value, String> {
    let items = kipris_parse_impl(response_xml)?;
    // `[self._row(items[0])] if items else []`.
    match items.first() {
        Some(d) => Ok(Value::Array(vec![kipris_row_impl(d)])),
        None => Ok(Value::Array(Vec::new())),
    }
}

#[pyfunction]
#[pyo3(signature = (response_xml, max_results = 5))]
pub fn epo_parse_search(
    py: Python,
    response_xml: &str,
    max_results: i64,
) -> PyResult<String> {
    epo_parse_search_impl(response_xml, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn epo_parse_fetch(py: Python, response_xml: &str) -> PyResult<String> {
    epo_parse_fetch_impl(response_xml)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, max_results = 5))]
pub fn kipris_parse_search(
    py: Python,
    response_xml: &str,
    max_results: i64,
) -> PyResult<String> {
    kipris_parse_search_impl(response_xml, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn kipris_parse_fetch(py: Python, response_xml: &str) -> PyResult<String> {
    kipris_parse_fetch_impl(response_xml)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

// ────────────────────────────────────────────────────────────────
// Lens (api.lens.org) — cross-office aggregator (WO/EP/DE/CN/US).
//
// Search envelope: `{"data": [...], "results": N, ...}` (`results`
// is the int hit count; `data` is the record array). Single fetch
// (`GET /patent/{lens_id}`) answers the record object directly
// (with `lens_id`). Both shapes are accepted by the fetch kernel.
// Record shape (docs.api.lens.org): top-level `lens_id` /
// `jurisdiction` / `doc_number` / `kind` / `date_published` /
// `doc_key`, nested `biblio.invention_title: [{text}]`,
// `biblio.parties.{applicants,inventors}: [{extracted_name:
// {value}}]`, `abstract: [{text}]`, `legal_status.patent_status`.
// ────────────────────────────────────────────────────────────────

/// `str` of a Lens text holder: plain string, `{text}`, or
/// `{value}` (party names); containers contribute "".
fn lens_text_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Object(m) => {
            if let Some(t) = m.get("text") {
                if let Value::String(s) = t {
                    return s.clone();
                }
                if !matches!(t, Value::Object(_) | Value::Array(_)) {
                    return py_str_value(t);
                }
            }
            if let Some(t) = m.get("value") {
                if let Value::String(s) = t {
                    return s.clone();
                }
                if !matches!(t, Value::Object(_) | Value::Array(_)) {
                    return py_str_value(t);
                }
            }
            String::new()
        }
        Value::Null => String::new(),
        _ => String::new(),
    }
}

/// First text of a Lens list-or-single field (`invention_title`,
/// `abstract`): array -> first item's text; single -> its text.
fn lens_first_text(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(a)) => match a.first() {
            None => String::new(),
            Some(first) => lens_text_value(first),
        },
        Some(Value::Object(_)) => lens_text_value(v.unwrap()),
        _ => String::new(),
    }
}

/// Party names (`applicants` / `inventors`): each item carries
/// `extracted_name: {value}` (or a bare string); fall back to
/// `name: {value}` for forward-compat shapes.
fn lens_party_names(parties: Option<&Value>, key: &str) -> Vec<String> {
    let arr = match parties {
        Some(Value::Object(m)) => match m.get(key) {
            Some(Value::Array(a)) => a,
            _ => return Vec::new(),
        },
        _ => return Vec::new(),
    };
    let mut out = Vec::new();
    for item in arr.iter() {
        let name = match item {
            Value::Object(im) => {
                let ex = im.get("extracted_name").or_else(|| im.get("name"));
                match ex {
                    Some(Value::String(s)) => s.clone(),
                    Some(Value::Object(em)) => match em.get("value") {
                        Some(Value::String(s)) => s.clone(),
                        Some(v) if !matches!(v, Value::Object(_) | Value::Array(_)) => {
                            py_str_value(v)
                        }
                        _ => continue,
                    },
                    Some(v) if !matches!(v, Value::Object(_) | Value::Array(_) | Value::Null) => {
                        py_str_value(v)
                    }
                    _ => continue,
                }
            }
            Value::String(s) => s.clone(),
            _ => continue,
        };
        let trimmed = crate::pycompat::py_strip(name.as_str()).to_string();
        if !trimmed.is_empty() {
            out.push(trimmed);
        }
    }
    out
}

fn lens_row_impl(p: &Value) -> Result<Value, String> {
    let m = match p {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(p))),
    };
    let lens_id = match m.get("lens_id") {
        Some(v) if !matches!(v, Value::Null) => py_str_value(v),
        _ => String::new(),
    };
    let jurisdiction = match m.get("jurisdiction") {
        Some(v) if !matches!(v, Value::Null) => py_str_value(v),
        _ => String::new(),
    };
    let doc_number = match m.get("doc_number") {
        Some(v) if !matches!(v, Value::Null) => py_str_value(v),
        _ => String::new(),
    };
    let kind = match m.get("kind") {
        Some(v) if !matches!(v, Value::Null) => py_str_value(v),
        _ => String::new(),
    };
    let doc_key = match m.get("doc_key") {
        Some(v) if !matches!(v, Value::Null) => py_str_value(v),
        _ => String::new(),
    };
    let date_raw = match m.get("date_published") {
        Some(v) if !matches!(v, Value::Null) => py_str_value(v),
        _ => String::new(),
    };
    let date = char_head(date_raw.as_str(), 10).to_string();
    // Title: `biblio.invention_title` (list/single/string), else a
    // readable publication ref, else the id itself.
    let biblio = m.get("biblio");
    let mut title = String::new();
    if let Some(Value::Object(bm)) = biblio {
        title = lens_first_text(bm.get("invention_title"));
    }
    if title.is_empty() {
        let pubref = format!("{jurisdiction} {doc_number} {kind}");
        let pubref = crate::pycompat::py_strip(pubref.as_str()).to_string();
        title = if pubref.is_empty() {
            if !doc_key.is_empty() {
                doc_key.clone()
            } else if !lens_id.is_empty() {
                lens_id.clone()
            } else {
                String::new()
            }
        } else {
            pubref
        };
    }
    let parties = biblio.and_then(|b| match b {
        Value::Object(bm) => bm.get("parties"),
        _ => None,
    });
    let applicants = lens_party_names(parties, "applicants");
    let inventors = lens_party_names(parties, "inventors");
    let abstract_text = lens_first_text(m.get("abstract"));
    let legal_status = match m.get("legal_status") {
        Some(Value::Object(lm)) => match lm.get("patent_status") {
            Some(v) if !matches!(v, Value::Null) => py_str_value(v),
            _ => String::new(),
        },
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    };
    let id = if !lens_id.is_empty() {
        lens_id.clone()
    } else if !doc_key.is_empty() {
        doc_key.clone()
    } else {
        format!("{jurisdiction}{doc_number}{kind}")
    };
    let url = if lens_id.is_empty() {
        Value::String(String::new())
    } else {
        Value::String(format!("https://www.lens.org/lens/patent/{lens_id}"))
    };
    // EPO-style snippet: `title — applicants[:3]`.
    let joined = applicants.iter().take(3).cloned().collect::<Vec<String>>().join(", ");
    let snippet = crate::pycompat::py_strip_chars(
        format!("{title} \u{2014} {joined}").as_str(),
        &[' ', '\u{2014}'],
    )
    .to_string();
    let mut fields = serde_json::Map::new();
    fields.insert("jurisdiction".to_string(), Value::String(jurisdiction.clone()));
    fields.insert("doc_number".to_string(), Value::String(doc_number.clone()));
    fields.insert("kind".to_string(), Value::String(kind.clone()));
    fields.insert("doc_key".to_string(), Value::String(doc_key.clone()));
    fields.insert("lens_id".to_string(), Value::String(lens_id.clone()));
    fields.insert(
        "applicants".to_string(),
        Value::String(applicants.iter().take(5).cloned().collect::<Vec<String>>().join(", ")),
    );
    fields.insert(
        "inventors".to_string(),
        Value::String(inventors.iter().take(5).cloned().collect::<Vec<String>>().join(", ")),
    );
    fields.insert("legal_status".to_string(), Value::String(legal_status));
    fields.insert(
        "abstract".to_string(),
        Value::String(crate::pycompat::char_head(abstract_text.as_str(), 240).to_string()),
    );
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("lens".to_string()));
    rec.insert("id".to_string(), Value::String(id));
    rec.insert("title".to_string(), Value::String(title));
    rec.insert("url".to_string(), url);
    rec.insert("published".to_string(), Value::String(date));
    rec.insert("snippet".to_string(), Value::String(snippet));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

fn lens_api_error(obj: &serde_json::Map<String, Value>) -> Option<String> {
    if let Some(err) = obj.get("error") {
        if is_truthy(err) {
            return Some(format!("RuntimeError: Lens error: {0}", py_value_repr(err)));
        }
    }
    // `{"message": ..., "status": 4xx}` without `data`/`lens_id` is
    // an error envelope, not a record (records never carry `message`).
    if obj.get("data").is_none() && obj.get("lens_id").is_none() {
        if let Some(msg) = obj.get("message") {
            if is_truthy(msg) {
                return Some(format!("RuntimeError: Lens error: {0}", py_value_repr(msg)));
            }
        }
    }
    None
}

pub fn lens_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    if let Some(err) = lens_api_error(obj) {
        return Err(err);
    }
    // `body.get("data", [])[:max_results]` (`results` is the int hit
    // count, not the rows).
    let hits: Vec<&Value> = match obj.get("data") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for p in hits {
        out.push(lens_row_impl(p)?);
    }
    Ok(out)
}

pub fn lens_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Ok(Vec::new()),
    };
    if let Some(err) = lens_api_error(obj) {
        return Err(err);
    }
    // Single-record answer (`GET /patent/{lens_id}`) carries `lens_id`.
    if obj.get("lens_id").is_some() {
        return Ok(vec![lens_row_impl(&body)?]);
    }
    // Envelope answer (`POST /patent/search`): first of `data`.
    match obj.get("data") {
        Some(Value::Array(a)) => match a.first() {
            Some(first) => Ok(vec![lens_row_impl(first)?]),
            None => Ok(Vec::new()),
        },
        Some(Value::String(s)) if !s.is_empty() => Err(attr_error("str")),
        Some(Value::Object(_)) => Err("KeyError: 0".to_string()),
        Some(other) if is_truthy(other) => {
            Err(type_error_not_subscriptable(json_type(other)))
        }
        _ => Ok(Vec::new()),
    }
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn lens_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    lens_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn lens_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    lens_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

// ────────────────────────────────────────────────────────────────
// Google Patents (patents.google.com) — keyless lookup.
//
// robots.txt Allows `/patent/` (detail pages) but Disallows `/`
// (search), so there is deliberately NO search-page scraper here:
// `search()` resolves publication numbers (FredAdapter pattern) and
// `fetch()` reads the server-rendered detail HTML — Dublin Core /
// citation meta tags plus `itemprop` abstract/claims sections.
// Free-text discovery stays on `site:patents.google.com` web search.
// ────────────────────────────────────────────────────────────────

use std::collections::HashMap;
use std::sync::OnceLock;

/// Minimal HTML-entity decode for meta/section text (the five
/// predefined entities plus decimal/hex character references).
fn gp_unescape(s: &str) -> String {
    let mut out = s
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'");
    // Numeric references: `&#123;` / `&#x1F;` (invalid ones pass through).
    while let Some(start) = out.find("&#") {
        let rest = &out[start + 2..];
        let end = match rest.find(';') {
            Some(i) if i <= 8 => i,
            _ => break,
        };
        let body = &rest[..end];
        let ch = if let Some(hex) = body.strip_prefix('x').or_else(|| body.strip_prefix('X')) {
            u32::from_str_radix(hex, 16).ok().and_then(char::from_u32)
        } else {
            body.parse::<u32>().ok().and_then(char::from_u32)
        };
        match ch {
            Some(c) => out.replace_range(start..start + 2 + end + 1, &c.to_string()),
            None => break,
        }
    }
    out
}

/// Collapse + strip + unescape: one readable string out of raw HTML text.
fn gp_text(raw: &str) -> String {
    crate::pycompat::py_collapse_ws(gp_unescape(raw).as_str())
}

fn gp_regex(pattern: &str) -> regex::Regex {
    regex::Regex::new(pattern).expect("google-patents kernel regex")
}

/// One attribute out of a single tag string (order-tolerant).
fn gp_attr(tag: &str, key: &str) -> String {
    static COMPILED: OnceLock<HashMap<&'static str, regex::Regex>> = OnceLock::new();
    let map = COMPILED.get_or_init(|| {
        let mut m = HashMap::new();
        for k in ["name", "content", "scheme", "href"] {
            let p = match k {
                "name" => "(?s)\\bname\\s*=\\s*\"([\\s\\S]*?)\"",
                "content" => "(?s)\\bcontent\\s*=\\s*\"([\\s\\S]*?)\"",
                "scheme" => "(?s)\\bscheme\\s*=\\s*\"([\\s\\S]*?)\"",
                _ => "(?s)\\bhref\\s*=\\s*\"([\\s\\S]*?)\"",
            };
            m.insert(k, gp_regex(p));
        }
        m
    });
    map.get(key)
        .and_then(|re| re.captures(tag))
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_string())
        .unwrap_or_default()
}

/// All `<meta ...>` tags as `(name, content, scheme)`; missing attrs
/// read as "". `content` may span lines (lazy match).
fn gp_metas(html: &str) -> Vec<(String, String, String)> {
    static TAG: OnceLock<regex::Regex> = OnceLock::new();
    let tag = TAG.get_or_init(|| gp_regex("<meta\\b[^<>]*>"));
    tag.find_iter(html)
        .map(|m| {
            let t = m.as_str();
            (gp_attr(t, "name"), gp_attr(t, "content"), gp_attr(t, "scheme"))
        })
        .collect()
}

/// First capture of `pattern` over `html`, text-cleaned.
fn gp_first(html: &str, pattern: &str) -> String {
    gp_text(
        gp_regex(pattern)
            .captures(html)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str())
            .unwrap_or_default(),
    )
}

/// Strip tags + collapse: readable text out of an HTML fragment.
fn gp_fragment_text(fragment: &str) -> String {
    let no_tags = super::common::strip_tags_re().replace_all(fragment, " ");
    crate::pycompat::py_collapse_ws(gp_unescape(no_tags.as_ref()).as_str())
}

fn google_patents_row_impl(html: &str, rid: &str) -> Result<(Value, Value), String> {
    let metas = gp_metas(html);
    let mut title = String::new();
    let mut filing = String::new();
    let mut issue = String::new();
    let mut app_no = String::new();
    let mut pdf = String::new();
    let mut cite_no = String::new();
    let mut inventors: Vec<String> = Vec::new();
    let mut assignees: Vec<String> = Vec::new();
    let mut refs_count: u64 = 0;
    for (name, content, scheme) in metas.iter() {
        match (name.as_str(), scheme.as_str()) {
            ("DC.title", _) if title.is_empty() => title = gp_text(content),
            ("DC.date", "dateSubmitted") if filing.is_empty() => {
                filing = crate::pycompat::char_head(content.as_str(), 10).to_string()
            }
            ("DC.date", _) if issue.is_empty() && scheme != "dateSubmitted" => {
                issue = crate::pycompat::char_head(content.as_str(), 10).to_string()
            }
            ("citation_patent_application_number", _) if app_no.is_empty() => {
                app_no = gp_text(content)
            }
            ("citation_pdf_url", _) if pdf.is_empty() => pdf = content.trim().to_string(),
            ("citation_patent_number", _) if cite_no.is_empty() => {
                cite_no = gp_text(content)
            }
            ("DC.contributor", "inventor") => {
                let n = gp_text(content);
                if !n.is_empty() {
                    inventors.push(n);
                }
            }
            ("DC.contributor", "assignee") => {
                let n = gp_text(content);
                if !n.is_empty() {
                    assignees.push(n);
                }
            }
            ("DC.relation", "references") => refs_count += 1,
            _ => {}
        }
    }
    let pubnum = gp_first(
        html,
        "(?s)<dd\\b[^<>]*\\bitemprop=\"publicationNumber\"[^<>]*>([^<>]*)</dd>",
    );
    let kind = gp_first(
        html,
        "(?s)<meta\\b[^<>]*\\bitemprop=\"kindCode\"[^<>]*\\bcontent=\"([^\"]*)\"",
    );
    // Canonical link (order-tolerant: find the tag, then its href).
    let mut canonical = String::new();
    {
        static TAG: OnceLock<regex::Regex> = OnceLock::new();
        let tag = TAG.get_or_init(|| gp_regex("<link\\b[^<>]*>"));
        for m in tag.find_iter(html) {
            let t = m.as_str();
            if t.contains("rel=\"canonical\"") {
                canonical = gp_attr(t, "href").trim().to_string();
                break;
            }
        }
    }
    let abstract_text = {
        static RE: OnceLock<regex::Regex> = OnceLock::new();
        let re = RE.get_or_init(|| {
            gp_regex("(?s)<section\\b[^<>]*\\bitemprop=\"abstract\"[\\s\\S]*?<div\\s+class=\"abstract\">([\\s\\S]*?)</div>")
        });
        gp_fragment_text(
            re.captures(html)
                .and_then(|c| c.get(1))
                .map(|m| m.as_str())
                .unwrap_or_default(),
        )
    };
    let claims_count: u64 = {
        static RE: OnceLock<regex::Regex> = OnceLock::new();
        let re = RE.get_or_init(|| {
            gp_regex("(?s)<section\\b[^<>]*\\bitemprop=\"claims\"[\\s\\S]*?<span\\s+itemprop=\"count\">(\\d+)</span>")
        });
        re.captures(html)
            .and_then(|c| c.get(1))
            .and_then(|m| m.as_str().parse::<u64>().ok())
            .unwrap_or(0)
    };
    // Not-found: a 200 with none of the biblio markers (soft 404).
    if pubnum.is_empty() && title.is_empty() && cite_no.is_empty() {
        let mut raw = serde_json::Map::new();
        raw.insert("id".to_string(), Value::String(rid.to_string()));
        raw.insert("not_found".to_string(), Value::Bool(true));
        return Ok((Value::Null, Value::Object(raw)));
    }
    let id = if !pubnum.is_empty() {
        pubnum.clone()
    } else if !cite_no.is_empty() {
        cite_no.clone()
    } else {
        rid.to_string()
    };
    if title.is_empty() {
        title = id.clone();
    }
    let url = if !canonical.is_empty() {
        canonical.clone()
    } else if !id.is_empty() {
        format!("https://patents.google.com/patent/{id}/en")
    } else {
        String::new()
    };
    // EPO-style snippet: `title — inventors/assignee[:3]`.
    let parties = if !inventors.is_empty() {
        &inventors
    } else {
        &assignees
    };
    let joined = parties.iter().take(3).cloned().collect::<Vec<String>>().join(", ");
    let snippet = crate::pycompat::py_strip_chars(
        format!("{title} \u{2014} {joined}").as_str(),
        &[' ', '\u{2014}'],
    )
    .to_string();
    let mut fields = serde_json::Map::new();
    fields.insert("publication_number".to_string(), Value::String(pubnum.clone()));
    fields.insert("kind".to_string(), Value::String(kind));
    fields.insert("application_number".to_string(), Value::String(app_no));
    fields.insert("filing_date".to_string(), Value::String(filing));
    fields.insert(
        "inventors".to_string(),
        Value::String(inventors.iter().take(5).cloned().collect::<Vec<String>>().join(", ")),
    );
    fields.insert(
        "assignee".to_string(),
        Value::String(assignees.iter().take(5).cloned().collect::<Vec<String>>().join(", ")),
    );
    fields.insert("pdf_url".to_string(), Value::String(pdf.clone()));
    fields.insert("claims_count".to_string(), Value::Number(claims_count.into()));
    fields.insert("refs_count".to_string(), Value::Number(refs_count.into()));
    fields.insert(
        "abstract".to_string(),
        Value::String(crate::pycompat::char_head(abstract_text.as_str(), 240).to_string()),
    );
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("google-patents".to_string()));
    rec.insert("id".to_string(), Value::String(id.clone()));
    rec.insert("title".to_string(), Value::String(title.clone()));
    rec.insert("url".to_string(), Value::String(url));
    rec.insert("published".to_string(), Value::String(issue.clone()));
    rec.insert("snippet".to_string(), Value::String(snippet));
    rec.insert("fields".to_string(), Value::Object(fields));
    let mut raw = serde_json::Map::new();
    raw.insert("publication_number".to_string(), Value::String(pubnum));
    raw.insert("title".to_string(), Value::String(title));
    raw.insert("date".to_string(), Value::String(issue));
    raw.insert("pdf_url".to_string(), Value::String(pdf));
    Ok((Value::Object(rec), Value::Object(raw)))
}

pub fn google_patents_parse_fetch_impl(html: &str, rid: &str) -> Result<(Value, Value), String> {
    google_patents_row_impl(html, rid)
}

#[pyfunction]
pub fn google_patents_parse_fetch(py: Python, html: &str, rid: &str) -> PyResult<String> {
    google_patents_parse_fetch_impl(html, rid)
        .and_then(|(rec, meta)| {
            let mut both = serde_json::Map::new();
            both.insert("record".to_string(), rec);
            both.insert("meta".to_string(), meta);
            serde_json::to_string(&Value::Object(both)).map_err(|e| e.to_string())
        })
        .map_err(|e| to_py_err(py, e))
}
