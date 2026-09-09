//! Adapter kernels: Patent kernels (PatentsView, EPO OPS, KIPRIS).

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
