//! Adapter kernels: Scholarly kernels (Zenodo, BioRxiv, ChemRxiv, OpenAlex, Crossref, Open Library, DOAJ, arXiv, PubMed).

use pyo3::prelude::*;
use serde_json::Value;
use crate::pycompat::{char_head};
use crate::cite::py_value_repr;
use super::common::{attr_error, type_error_not_subscriptable, json_type, is_truthy, to_py_err, type_error_not_iterable, subscript_keyerror, sequence_item_error, subscript_hits, slice_refs, strip_tags_impl, py_str_value};

/// Mirror of `ZenodoAdapter._names`: falsy input → `""`; dicts via
/// `name` / `person_or_org.name`; anything else truthy stringified;
/// non-string join elements raise TypeError with the join index.
pub(crate) fn zenodo_names_impl(people: Option<&Value>) -> Result<String, String> {
    let people = match people {
        None => return Ok(String::new()),
        Some(v) if !is_truthy(v) => return Ok(String::new()),
        Some(v) => v,
    };
    // Iterate: lists item-wise; strings char-wise; dicts key-wise;
    // anything else raises TypeError (not iterable).
    if !matches!(people, Value::Array(_) | Value::String(_) | Value::Object(_)) {
        return Err(type_error_not_iterable(json_type(people)));
    }
    // (rendered_text, original_type) pairs: only originally-string
    // values survive `", ".join`; anything else raises TypeError.
    let mut out: Vec<(String, &'static str)> = Vec::new();
    if matches!(people, Value::Array(_)) {
        let items: Vec<&Value> = match people {
            Value::Array(a) => a.iter().collect(),
            _ => Vec::new(),
        };
        for a in items {
            match a {
                Value::Object(m) => {
                    // `a.get("name") or person_or_org-name or ""`.
                    let direct = m.get("name").filter(|v| is_truthy(v));
                    let via_poo = || -> Result<Option<(String, &'static str)>, String> {
                        let poo = match m.get("person_or_org") {
                            None => return Ok(None),
                            Some(v) if !is_truthy(v) => return Ok(None),
                            Some(Value::Object(pm)) => pm,
                            Some(other) => return Err(attr_error(json_type(other))),
                        };
                        Ok(match poo.get("name") {
                            None => None,
                            Some(v) if !is_truthy(v) => None,
                            Some(Value::String(s)) => Some((s.clone(), "str")),
                            Some(v) => Some((py_value_repr(v), json_type(v))),
                        })
                    };
                    let chosen: Option<(String, &'static str)> = match direct {
                        Some(Value::String(s)) => Some((s.clone(), "str")),
                        Some(v) => Some((py_value_repr(v), json_type(v))),
                        None => via_poo()?,
                    };
                    if let Some((text, t)) = chosen {
                        if !text.is_empty() {
                            out.push((text, t));
                        }
                    }
                }
                _ => {
                    // Falsy items skipped (`elif a:`); truthy rendered
                    // via `str(a)` (always a string afterwards).
                    if is_truthy(a) {
                        out.push((py_value_repr(a), "str"));
                    }
                }
            }
        }
    } else if let Value::String(st) = people {
        for c in st.chars() {
            out.push((c.to_string(), "str"));
        }
    } else if let Value::Object(m) = people {
        for k in m.keys() {
            // `elif "":` skips empty keys; every other key is truthy.
            if !k.is_empty() {
                out.push((k.clone(), "str"));
            }
        }
    }
    // `", ".join`: first non-string element raises with its index.
    let mut rendered: Vec<String> = Vec::with_capacity(out.len());
    for (i, (text, t)) in out.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        rendered.push(text.clone());
    }
    Ok(rendered.join(", "))
}

fn zenodo_hit_impl(h: &Value, fallback_id: &str) -> Result<Value, String> {
    let hm = match h {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(h))),
    };
    // `h.get("metadata", {}) or {}`: missing/falsy → None (= {}).
    // Truthy non-dicts raise on their first `.get` (resource_type read).
    let m: Option<&serde_json::Map<String, Value>> = match hm.get("metadata") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let links: Option<&serde_json::Map<String, Value>> = match hm.get("links") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let rec_id = match hm.get("id") {
        None => fallback_id.to_string(),
        Some(v) => py_value_repr(v),
    };
    // `m.get("resource_type", {})`, then dict-navigate or `.get("id")`.
    let rtype: Option<&Value> = match m {
        None => None,
        Some(mm) => match mm.get("resource_type") {
            None => None,
            Some(v) => Some(v),
        },
    };
    let rtype_s: Option<Value> = match rtype {
        None => None,
        // Dicts navigate: dict `title` -> its `en` (missing -> "");
        // missing/non-dict `title` -> `id` (missing -> "").
        // Non-dicts are kept as-is for the `or ""` below.
        Some(Value::Object(rm)) => match rm.get("title") {
            Some(Value::Object(tm)) => tm.get("en").cloned(),
            _ => rm.get("id").cloned(),
        },
        Some(v) => Some(v.clone()),
    };
    let rtype_out = match rtype_s {
        None => Value::String(String::new()),
        Some(v) if !is_truthy(&v) => Value::String(String::new()),
        Some(v) => v,
    };
    // Hmm: `rtype or ""` applies to the NAVIGATED value; falsy → "".
    // (Unreachable for missing (None→""), kept for symmetry.)
    let title = match m {
        None => Value::String(String::new()),
        Some(mm) => mm.get("title").cloned().unwrap_or(Value::String(String::new())),
    };
    let url = match links {
        Some(lm) => match lm.get("html") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => match lm.get("self_html") {
                Some(v) if is_truthy(v) => v.clone(),
                _ => {
                    if rec_id.is_empty() {
                        Value::String(String::new())
                    } else {
                        Value::String(format!("https://zenodo.org/records/{rec_id}"))
                    }
                }
            },
        },
        None => {
            if rec_id.is_empty() {
                Value::String(String::new())
            } else {
                Value::String(format!("https://zenodo.org/records/{rec_id}"))
            }
        }
    };
    let published = match m {
        None => Value::String(String::new()),
        Some(mm) => mm
            .get("publication_date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    let creators = match m {
        None => None,
        Some(mm) => mm
            .get("creators")
            .filter(|v| is_truthy(v))
            .or_else(|| mm.get("contributors").filter(|v| is_truthy(v)))
            .or_else(|| mm.get("authors").filter(|v| is_truthy(v))),
    };
    let authors = zenodo_names_impl(creators)?;
    let description = match m {
        None => Value::String(String::new()),
        Some(mm) => mm.get("description").cloned().unwrap_or(Value::String(String::new())),
    };
    let stripped = strip_tags_impl(&description)?;
    let snippet = char_head(&stripped, 240).to_string();
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("zenodo".to_string()));
    rec.insert("id".to_string(), Value::String(rec_id));
    rec.insert("title".to_string(), title);
    rec.insert("url".to_string(), url);
    rec.insert("published".to_string(), published);
    rec.insert("authors".to_string(), Value::String(authors));
    rec.insert("snippet".to_string(), Value::String(snippet));
    let mut inner = serde_json::Map::new();
    inner.insert("resource_type".to_string(), rtype_out);
    let mut fields = serde_json::Map::new();
    fields.insert("zenodo".to_string(), Value::Object(inner));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn zenodo_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("hits", {})` — missing → {}; non-dict body already
    // raised. Present (even falsy) kept; `.get("hits", [])` needs a dict.
    let outer = match obj.get("hits") {
        None => return Ok(Vec::new()),
        Some(v) => v,
    };
    let inner = match outer {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(outer))),
    };
    let hits: Vec<&Value> = match inner.get("hits") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for h in hits {
        out.push(zenodo_hit_impl(h, "")?);
    }
    Ok(out)
}

pub fn zenodo_parse_fetch_impl(
    response_json: &str,
    record_id_s: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    zenodo_hit_impl(&body, record_id_s)
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn zenodo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    zenodo_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn zenodo_parse_fetch(py: Python, response_json: &str, record_id_s: &str) -> PyResult<String> {
    zenodo_parse_fetch_impl(response_json, record_id_s)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

pub(crate) fn biorxiv_paper_impl(p: &Value, server: &str) -> Result<Value, String> {
    let m = match p {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(p))),
    };
    let doi = m
        .get("doi")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let url = if is_truthy(&doi) {
        Value::String(format!(
            "https://www.biorxiv.org/content/{0}",
            py_value_repr(&doi)
        ))
    } else {
        Value::String(String::new())
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("biorxiv".to_string()));
    rec.insert("id".to_string(), doi);
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        m.get("date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    let abstract_raw = m
        .get("abstract")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let stripped = strip_tags_impl(&abstract_raw)?;
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(stripped.as_str(), 240).to_string()),
    );
    rec.insert(
        "authors".to_string(),
        m.get("authors")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    // NB: flat `fields`, with the instance's server threaded through.
    let mut fields = serde_json::Map::new();
    fields.insert("server".to_string(), Value::String(server.to_string()));
    for key in ["category", "version", "type", "license"] {
        fields.insert(
            key.to_string(),
            m.get(key)
                .cloned()
                .unwrap_or(Value::String(String::new())),
        );
    }
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn biorxiv_parse_collection_impl(
    response_json: &str,
    max_results: i64,
    server: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("collection", [])`, then `papers[:max_results]`.
    let hits: Vec<&Value> = match obj.get("collection") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for p in hits {
        out.push(biorxiv_paper_impl(p, server)?);
    }
    Ok(out)
}

pub fn biorxiv_parse_fetch_impl(
    response_json: &str,
    server: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `papers = ...get("collection", [])`; `if not papers: return []`.
    let papers = match obj.get("collection") {
        None => return Ok(Vec::new()),
        Some(v) if !is_truthy(v) => return Ok(Vec::new()),
        Some(v) => v,
    };
    // `papers[0]`: list (non-empty — falsy caught above), str (char),
    // dict (KeyError 0), anything else TypeError.
    let first = match papers {
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
    Ok(vec![biorxiv_paper_impl(first, server)?])
}



/// One `", ".join` element: whether it survives an `if n` truthiness
/// filter, its rendered text, and its original type for the join
/// `TypeError` (which reports the *surviving* index).
/// Dicts read raw `name` (missing → ""); anything else renders via
/// `str()` — always a string, whose truthiness is the text's.
pub(crate) fn chemrxiv_part(v: &Value) -> (bool, String, &'static str) {
    match v {
        Value::Object(m) => match m.get("name") {
            None => (false, String::new(), "str"),
            Some(n) => (is_truthy(n), py_value_repr(n), json_type(n)),
        },
        _ => {
            let s = py_value_repr(v);
            (!s.is_empty(), s, "str")
        }
    }
}

pub(crate) fn chemrxiv_join(parts: Vec<(bool, String, &'static str)>, filter: bool) -> Result<String, String> {
    let kept: Vec<(String, &'static str)> = parts
        .into_iter()
        .filter(|(keep, _, _)| !filter || *keep)
        .map(|(_, text, t)| (text, t))
        .collect();
    let mut out: Vec<String> = Vec::with_capacity(kept.len());
    for (i, (text, t)) in kept.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        out.push(text.clone());
    }
    Ok(out.join(", "))
}

/// `", ".join(...)` over authors (truthiness-filtered) or topics
/// (unfiltered) list elements: lists item-wise, dicts key-wise
/// (keys render as themselves), strings char-wise, anything else
/// raises TypeError (not iterable).
fn chemrxiv_names_impl(items: &Value, filter: bool) -> Result<String, String> {
    match items {
        Value::Array(a) => chemrxiv_join(a.iter().map(chemrxiv_part).collect(), filter),
        Value::Object(m) => chemrxiv_join(
            m.keys()
                .map(|k| {
                    let s = k.clone();
                    (!s.is_empty(), s, "str")
                })
                .collect(),
            filter,
        ),
        Value::String(s) => chemrxiv_join(
            s.chars()
                .map(|c| {
                    let t = c.to_string();
                    (true, t, "str")
                })
                .collect(),
            filter,
        ),
        other => Err(type_error_not_iterable(json_type(other))),
    }
}

fn chemrxiv_item_impl(it: &Value) -> Result<Value, String> {
    let m = match it {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(it))),
    };
    // `authors = it.get("authors", [])`: missing → []; lists render
    // per item (dicts read `name`); anything else — even a dict or a
    // falsy value — renders whole as one `str()`.
    let authors = match m.get("authors") {
        None => String::new(),
        Some(Value::Array(a)) => {
            chemrxiv_join(a.iter().map(chemrxiv_part).collect(), true)?
        }
        Some(v) => py_value_repr(v),
    };
    // `it.get("url", f"...{it.get('id', '')}")`.
    let url = match m.get("url") {
        Some(v) => v.clone(),
        None => Value::String(format!(
            "https://chemrxiv.org/engage/chemrxiv/public-article-details/{0}",
            py_value_repr(m.get("id").unwrap_or(&Value::String(String::new())))
        )),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("chemrxiv".to_string()));
    rec.insert(
        "id".to_string(),
        Value::String(py_value_repr(
            m.get("id").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        m.get("published_on")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    let abstract_raw = m
        .get("abstract")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let stripped = strip_tags_impl(&abstract_raw)?;
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(stripped.as_str(), 240).to_string()),
    );
    rec.insert("authors".to_string(), Value::String(authors));
    // `"topics": ", ".join(... for t in it.get("topics", []) or [])`.
    let topics = match m.get("topics") {
        None => String::new(),
        Some(v) if !is_truthy(v) => String::new(),
        Some(v) => chemrxiv_names_impl(v, false)?,
    };
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "doi".to_string(),
        m.get("doi")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert("topics".to_string(), Value::String(topics));
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn chemrxiv_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `body.get("data", []) if isinstance(body, dict) else body`,
    // then `items[:max_results]`.
    let hits: Vec<&Value> = match &body {
        Value::Object(m) => match m.get("data") {
            None => Vec::new(),
            Some(v) => subscript_hits(Some(v), max_results)?,
        },
        other => subscript_hits(Some(other), max_results)?,
    };
    let mut out = Vec::new();
    for it in hits {
        out.push(chemrxiv_item_impl(it)?);
    }
    Ok(out)
}

pub fn chemrxiv_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `item = body.get("data", body) if isinstance(body, dict) else
    // body`; falsy → []; lists index [0]; anything else rows as-is.
    let item: &Value = match &body {
        Value::Object(m) => m.get("data").unwrap_or(&body),
        _ => &body,
    };
    if !is_truthy(item) {
        return Ok(Vec::new());
    }
    match item {
        Value::Array(a) => match a.first() {
            Some(v) => Ok(vec![chemrxiv_item_impl(v)?]),
            None => Ok(Vec::new()),
        },
        _ => Ok(vec![chemrxiv_item_impl(item)?]),
    }
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, server = "biorxiv"))]
pub fn biorxiv_parse_collection(
    py: Python,
    response_json: &str,
    max_results: i64,
    server: &str,
) -> PyResult<String> {
    biorxiv_parse_collection_impl(response_json, max_results, server)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, server = "biorxiv"))]
pub fn biorxiv_parse_fetch(py: Python, response_json: &str, server: &str) -> PyResult<String> {
    biorxiv_parse_fetch_impl(response_json, server)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn chemrxiv_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    chemrxiv_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn chemrxiv_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    chemrxiv_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}


/// `a.get("author", {}).get("display_name", "")` for one authorship:
/// `a` must be a dict; `author` missing → ""; present values (even
/// falsy) read as-is; non-dicts raise on the inner `.get`.
fn openalex_author_name(a: &Value) -> Result<Value, String> {
    let m = match a {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(a))),
    };
    Ok(match m.get("author") {
        None => Value::String(String::new()),
        Some(Value::Object(am)) => am
            .get("display_name")
            .cloned()
            .unwrap_or(Value::String(String::new())),
        Some(other) => return Err(attr_error(json_type(other))),
    })
}

fn openalex_work_impl(w: &Value, full: bool) -> Result<Value, String> {
    let m = match w {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(w))),
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("openalex".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("id").cloned().unwrap_or(Value::String(String::new())),
    );
    // `w.get("title") or ""`: falsy titles fold to "".
    rec.insert(
        "title".to_string(),
        match m.get("title") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => Value::String(String::new()),
        },
    );
    // `w.get("doi") or w.get("id")`: no defaults — both may be None.
    rec.insert(
        "url".to_string(),
        match m.get("doi") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => m.get("id").cloned().unwrap_or(Value::Null),
        },
    );
    rec.insert(
        "doi".to_string(),
        m.get("doi").cloned().unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "published".to_string(),
        m.get("publication_date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    if full {
        // Authors render with the *filtered* join index on TypeError.
        // `w.get("authorships", [])`: missing → []; lists item-wise;
        // dicts/strings iterate (keys/chars) and fail per item;
        // anything else raises TypeError.
        let raw_authors: Vec<Value> = match m.get("authorships") {
            None => Vec::new(),
            Some(Value::Array(a)) => a
                .iter()
                .map(openalex_author_name)
                .collect::<Result<Vec<Value>, String>>()?,
            Some(Value::String(s)) => s
                .chars()
                .map(|c| openalex_author_name(&Value::String(c.to_string())))
                .collect::<Result<Vec<Value>, String>>()?,
            Some(Value::Object(mm)) => mm
                .keys()
                .map(|k| openalex_author_name(&Value::String(k.clone())))
                .collect::<Result<Vec<Value>, String>>()?,
            Some(other) => return Err(type_error_not_iterable(json_type(other))),
        };
        let mut kept: Vec<(String, &'static str)> = Vec::new();
        for name in raw_authors.iter() {
            if !is_truthy(name) {
                continue;
            }
            kept.push((py_value_repr(name), json_type(name)));
        }
        let mut authors: Vec<String> = Vec::with_capacity(kept.len());
        for (i, (text, t)) in kept.iter().enumerate() {
            if *t != "str" {
                return Err(sequence_item_error(i, t));
            }
            authors.push(text.clone());
        }
        let joined = authors.join(", ");
        rec.insert("authors".to_string(), Value::String(joined.clone()));
        rec.insert(
            "citations".to_string(),
            m.get("cited_by_count").cloned().unwrap_or(Value::from(0)),
        );
        rec.insert("snippet".to_string(), Value::String(joined));
    }
    Ok(Value::Object(rec))
}

pub fn openalex_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for w in hits {
        out.push(openalex_work_impl(w, true)?);
    }
    Ok(out)
}

pub fn openalex_parse_fetch_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    openalex_work_impl(&body, false)
}


/// `(w.get("title") or [""])[0]`: falsy titles fold to `[""]`;
/// truthy lists index (empty impossible — falsy caught); truthy
/// strings yield their first char; anything else raises TypeError.
fn crossref_title(title: Option<&Value>) -> Result<Value, String> {
    let t = match title {
        None => return Ok(Value::String(String::new())),
        Some(v) if !is_truthy(v) => return Ok(Value::String(String::new())),
        Some(v) => v,
    };
    match t {
        Value::Array(a) => match a.first() {
            Some(v) => Ok(v.clone()),
            None => Ok(Value::String(String::new())),
        },
        Value::String(s) => Ok(Value::String(
            s.chars().next().map(|c| c.to_string()).unwrap_or_default(),
        )),
        other => Err(type_error_not_subscriptable(json_type(other))),
    }
}

/// `(w.get("published", {}) or {}).get("date-parts", [[""]])[0]`:
/// missing/falsy published folds to `{}`; truthy non-dicts raise on
/// `.get`; the date-parts default applies only when missing.
fn crossref_published(published: Option<&Value>) -> Result<Value, String> {
    let m = match published {
        None => return Ok(Value::Array(vec![Value::String(String::new())])),
        Some(v) if !is_truthy(v) => {
            return Ok(Value::Array(vec![Value::String(String::new())]))
        }
        Some(Value::Object(m)) => m,
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let parts = match m.get("date-parts") {
        None => Value::Array(vec![Value::Array(vec![Value::String(String::new())])]),
        Some(v) => v.clone(),
    };
    // `[0]`: lists (empty → IndexError), strings (chars; empty →
    // IndexError), dicts (KeyError 0), anything else TypeError.
    match &parts {
        Value::Array(a) => match a.first() {
            Some(v) => Ok(v.clone()),
            None => Err("IndexError: list index out of range".to_string()),
        },
        Value::String(s) => match s.chars().next() {
            Some(c) => Ok(Value::String(c.to_string())),
            None => Err("IndexError: string index out of range".to_string()),
        },
        Value::Object(_) => Err("KeyError: 0".to_string()),
        other => Err(type_error_not_subscriptable(json_type(other))),
    }
}

/// `[a.get("family", a.get("name", "")) for a in ...]`: `a` must be a
/// dict; the `name` default applies only when `family` is missing.
fn crossref_author_name(a: &Value) -> Result<Value, String> {
    let m = match a {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(a))),
    };
    Ok(match m.get("family") {
        Some(v) => v.clone(),
        None => m
            .get("name")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    })
}

fn crossref_work_impl(w: &Value, fallback_id: &Value) -> Result<Value, String> {
    let m = match w {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(w))),
    };
    // Evaluation order matches the original: title, then the author
    // list, then published, then the abstract — an early field's
    // error precedes any later field's.
    let title = crossref_title(m.get("title"))?;
    // `w.get("author", [])`: missing → []; present values iterate
    // (lists item-wise, dicts key-wise, strings char-wise — failing
    // per item); anything else raises TypeError.
    let author_items: Vec<Value> = match m.get("author") {
        None => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut raw_names: Vec<Value> = Vec::with_capacity(author_items.len());
    for a in author_items.iter() {
        raw_names.push(crossref_author_name(a)?);
    }
    // The author *join* runs after `published` in the original dict
    // literal, so it is deferred until then.
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("crossref".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("DOI").cloned().unwrap_or(fallback_id.clone()),
    );
    rec.insert("title".to_string(), title);
    // `w.get("URL")`: no default — missing reads None.
    rec.insert(
        "url".to_string(),
        m.get("URL").cloned().unwrap_or(Value::Null),
    );
    rec.insert(
        "doi".to_string(),
        m.get("DOI")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("published".to_string(), crossref_published(m.get("published"))?);
    // `", ".join(a for a in authors if a)`: falsy dropped, then
    // strict (runs after `published` in the dict literal).
    let mut kept: Vec<(String, &'static str)> = Vec::new();
    for name in raw_names.iter() {
        if !is_truthy(name) {
            continue;
        }
        kept.push((py_value_repr(name), json_type(name)));
    }
    let mut authors: Vec<String> = Vec::with_capacity(kept.len());
    for (i, (text, t)) in kept.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        authors.push(text.clone());
    }
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    // `(w.get("abstract") or "")[:240]`: falsy folds; truthy lists
    // slice (kept as lists!); truthy strings head; else TypeError.
    let abstract_folded = match m.get("abstract") {
        None => Value::String(String::new()),
        Some(v) if !is_truthy(v) => Value::String(String::new()),
        Some(v) => v.clone(),
    };
    rec.insert(
        "snippet".to_string(),
        match &abstract_folded {
            Value::String(s) => Value::String(char_head(s, 240).to_string()),
            Value::Array(a) => {
                Value::Array(a.iter().take(240).cloned().collect())
            }
            Value::Object(_) => return Err(subscript_keyerror(240)),
            other => return Err(type_error_not_subscriptable(json_type(other))),
        },
    );
    Ok(Value::Object(rec))
}

pub fn crossref_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `msg = resp.json().get("message", {})` (missing → {}; present
    // values kept as-is), then `msg.get("items", [])[:max_results]`.
    let msg = match obj.get("message") {
        None => Value::Object(serde_json::Map::new()),
        Some(v) => v.clone(),
    };
    let msg_map = match &msg {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&msg))),
    };
    let hits: Vec<&Value> = match msg_map.get("items") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for w in hits {
        out.push(crossref_work_impl(w, &Value::String(String::new()))?);
    }
    Ok(out)
}

pub fn crossref_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `w = resp.json()["message"]`: direct indexing (missing key →
    // KeyError; lists/strings index by integer only; anything else
    // is not subscriptable).
    let w = match &body {
        Value::Object(m) => match m.get("message") {
            Some(v) => v.clone(),
            None => return Err("KeyError: message".to_string()),
        },
        Value::Array(_) => {
            return Err(
                "TypeError: list indices must be integers or slices, not str".to_string(),
            );
        }
        Value::String(_) => {
            return Err("TypeError: string indices must be integers, not 'str'".to_string());
        }
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    crossref_work_impl(&w, fallback)
}


fn openlibrary_search_row_impl(d: &Value, base: &str) -> Result<Value, String> {
    let m = match d {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(d))),
    };
    let key = m
        .get("key")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `", ".join(d.get("author", []) or [])`: falsy folds; truthy
    // values iterate strictly (lists item-wise, dicts key-wise,
    // strings char-wise — non-strings raise at the raw index).
    let author_vals: Vec<Value> = match m.get("author") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut authors: Vec<String> = Vec::with_capacity(author_vals.len());
    for (i, a) in author_vals.iter().enumerate() {
        match a {
            Value::String(s) => authors.push(s.clone()),
            _ => return Err(sequence_item_error(i, json_type(a))),
        }
    }
    // `", ".join(d.get("isbn", []) or [])[:120]`: strict join, then
    // a char slice of the joined string (never raises).
    let isbn_vals: Vec<Value> = match m.get("isbn") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut isbns: Vec<String> = Vec::with_capacity(isbn_vals.len());
    for (i, v) in isbn_vals.iter().enumerate() {
        match v {
            Value::String(s) => isbns.push(s.clone()),
            _ => return Err(sequence_item_error(i, json_type(v))),
        }
    }
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("openlibrary".to_string()),
    );
    rec.insert("id".to_string(), key.clone());
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "url".to_string(),
        if is_truthy(&key) {
            Value::String(format!("{base}{0}", py_value_repr(&key)))
        } else {
            Value::String(String::new())
        },
    );
    rec.insert(
        "published".to_string(),
        m.get("first_publish_year")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(isbns.join(", ").as_str(), 120).to_string()),
    );
    Ok(Value::Object(rec))
}

pub fn openlibrary_parse_search_impl(
    response_json: &str,
    max_results: i64,
    base: &str,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("docs", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("docs") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for d in hits {
        out.push(openlibrary_search_row_impl(d, base)?);
    }
    Ok(out)
}

fn openlibrary_fetch_authors_impl(book: &serde_json::Map<String, Value>) -> Result<Vec<Value>, String> {
    // `for a in book.get("authors", []) or []`: falsy folds; truthy
    // values iterate (lists item-wise, dicts key-wise, strings
    // char-wise); anything else raises TypeError. Strings append
    // as-is; dicts append `name`, else `author.key` when that is a
    // dict (missing → ""); anything else is silently skipped.
    let items: Vec<Value> = match book.get("authors") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let mut out = Vec::new();
    for a in items.iter() {
        match a {
            Value::String(s) => out.push(Value::String(s.clone())),
            Value::Object(am) => {
                if let Some(name) = am.get("name") {
                    out.push(name.clone());
                } else if let Some(Value::Object(sub)) = am.get("author") {
                    out.push(
                        sub.get("key")
                            .cloned()
                            .unwrap_or(Value::String(String::new())),
                    );
                }
            }
            _ => {}
        }
    }
    Ok(out)
}

pub fn openlibrary_parse_fetch_impl(
    response_json: &str,
    key_s: &str,
    base: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let book = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `key = book.get("key", key)`: missing → the request key.
    let key = book
        .get("key")
        .cloned()
        .unwrap_or(Value::String(key_s.to_string()));
    // `", ".join(a for a in authors if a)`: falsy dropped, then
    // strict at the surviving index.
    let mut kept: Vec<(String, &'static str)> = Vec::new();
    for a in openlibrary_fetch_authors_impl(book)?.iter() {
        if !is_truthy(a) {
            continue;
        }
        kept.push((py_value_repr(a), json_type(a)));
    }
    let mut authors: Vec<String> = Vec::with_capacity(kept.len());
    for (i, (text, t)) in kept.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        authors.push(text.clone());
    }
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("openlibrary".to_string()),
    );
    rec.insert("id".to_string(), key.clone());
    rec.insert(
        "title".to_string(),
        book.get("title")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    // Unconditional: `f"{BASE}{key}"` (no truthiness check here,
    // unlike search).
    rec.insert(
        "url".to_string(),
        Value::String(format!("{base}{0}", py_value_repr(&key))),
    );
    // `book.get("first_publish_year") or ... or ""`: no defaults —
    // missing reads propagate as None through the chain.
    let published = match book.get("first_publish_year") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => match book.get("first_publish_date") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => match book.get("publish_date") {
                Some(v) if is_truthy(v) => v.clone(),
                _ => Value::String(String::new()),
            },
        },
    };
    rec.insert("published".to_string(), published);
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    Ok(Value::Object(rec))
}

fn doaj_row_impl(r: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    // `bib = r.get("bibjson", {}) or {}`: falsy folds; truthy
    // non-dicts raise on the first `.get` (identifier read below —
    // nothing raisable precedes it, so eager is exact).
    let bib: Option<&serde_json::Map<String, Value>> = match m.get("bibjson") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(bm)) => Some(bm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    // DOI hunt: non-dict identifier items are silently skipped;
    // `id` reads raw (missing → None!). Present non-list identifiers
    // iterate harmlessly (keys/chars never match the dict filter).
    let mut dois: Vec<Value> = Vec::new();
    if let Some(bm) = bib {
        match bm.get("identifier") {
            None => {}
            Some(Value::Array(a)) => {
                for i in a.iter() {
                    if let Value::Object(im) = i {
                        if matches!(im.get("type"), Some(Value::String(t)) if t == "doi")
                        {
                            dois.push(im.get("id").cloned().unwrap_or(Value::Null));
                        }
                    }
                }
            }
            Some(Value::Object(_)) | Some(Value::String(_)) => {}
            Some(other) => return Err(type_error_not_iterable(json_type(other))),
        }
    }
    // `bib.get("author", [])` only when it is a list (double `.get`,
    // same result — no side effects either way).
    let author_items: Vec<&Value> = match bib {
        None => Vec::new(),
        Some(bm) => match bm.get("author") {
            Some(Value::Array(a)) => a.iter().collect(),
            _ => Vec::new(),
        },
    };
    // Strict join at the raw index (no truthiness filter here).
    let mut parts: Vec<(String, &'static str)> = Vec::new();
    for a in author_items.iter() {
        match a {
            Value::Object(am) => match am.get("name") {
                None => parts.push((String::new(), "str")),
                Some(Value::String(s)) => parts.push((s.clone(), "str")),
                Some(v) => parts.push((py_value_repr(v), json_type(v))),
            },
            _ => parts.push((py_value_repr(a), "str")),
        }
    }
    let mut author_names: Vec<String> = Vec::with_capacity(parts.len());
    for (i, (text, t)) in parts.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        author_names.push(text.clone());
    }
    let title = match bib {
        None => Value::String(String::new()),
        Some(bm) => match bm.get("title") {
            None => Value::String(String::new()),
            Some(v) if !is_truthy(v) => Value::String(String::new()),
            Some(v) => v.clone(),
        },
    };
    let abstract_folded = match bib {
        None => Value::String(String::new()),
        Some(bm) => match bm.get("abstract") {
            None => Value::String(String::new()),
            Some(v) if !is_truthy(v) => Value::String(String::new()),
            Some(v) => v.clone(),
        },
    };
    let snippet = match &abstract_folded {
        Value::String(s) => Value::String(char_head(s, 240).to_string()),
        Value::Array(a) => Value::Array(a.iter().take(240).cloned().collect()),
        Value::Object(_) => return Err(subscript_keyerror(240)),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("doaj".to_string()));
    rec.insert(
        "id".to_string(),
        m.get("id").cloned().unwrap_or(Value::String(String::new())),
    );
    rec.insert("title".to_string(), title);
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://doaj.org/article/{0}",
            py_value_repr(m.get("id").unwrap_or(&Value::String(String::new())))
        )),
    );
    // `dois[0] if dois else ""`: the first hit stays raw (even None).
    rec.insert(
        "doi".to_string(),
        dois.into_iter().next().unwrap_or(Value::String(String::new())),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(author_names.join(", ")),
    );
    rec.insert("snippet".to_string(), snippet);
    Ok(Value::Object(rec))
}

pub fn doaj_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for r in hits {
        out.push(doaj_row_impl(r)?);
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn openalex_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    openalex_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn openalex_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    openalex_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn crossref_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    crossref_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn crossref_parse_fetch(py: Python, response_json: &str, fallback_json: &str) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    crossref_parse_fetch_impl(response_json, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5, base = "https://openlibrary.org"))]
pub fn openlibrary_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
    base: &str,
) -> PyResult<String> {
    openlibrary_parse_search_impl(response_json, max_results, base)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, key = "", base = "https://openlibrary.org"))]
pub fn openlibrary_parse_fetch(
    py: Python,
    response_json: &str,
    key: &str,
    base: &str,
) -> PyResult<String> {
    openlibrary_parse_fetch_impl(response_json, key, base)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn doaj_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    doaj_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}


/// `_bare_arxiv_id`: reduce an id or abs URL to the bare identifier.
fn arxiv_bare_id(value: &str) -> String {
    let mut ident = value.to_string();
    for sep in ["arxiv.org/abs/", "arxiv.org/abs", "arxiv.org/"] {
        if let Some((_, rest)) = ident.split_once(sep) {
            ident = rest.to_string();
        }
    }
    ident.trim_matches('/').to_string()
}

/// `_entry_field`: collapsed text of the first direct child with this
/// local name ("" when absent).
fn arxiv_field(entry: &crate::xmlatom::Node, name: &str) -> String {
    match entry.child(name) {
        None => String::new(),
        Some(c) => crate::pycompat::py_collapse_ws(&c.text),
    }
}

fn arxiv_entry_impl(entry: &crate::xmlatom::Node) -> Value {
    let entry_id = arxiv_field(entry, "id");
    let bare_id = arxiv_bare_id(&entry_id);
    // DOI: the extension field, else the rel=related title=doi link.
    let mut doi = arxiv_field(entry, "doi");
    if doi.is_empty() {
        for link in entry.children_named("link") {
            if link.attr("title") == "doi" {
                let href = link.attr("href").trim().to_string();
                doi = match href.split_once("doi.org/") {
                    Some((_, rest)) => rest.trim().to_string(),
                    None => href,
                };
                break;
            }
        }
    }
    // Primary category: the extension, else the arxiv-scheme category.
    let mut primary = String::new();
    for pc in entry.children_named("primary_category") {
        primary = pc.attr("term").to_string();
        if !primary.is_empty() {
            break;
        }
    }
    if primary.is_empty() {
        for cat in entry.children_named("category") {
            if cat.attr("scheme").ends_with("schemas/atom") {
                primary = cat.attr("term").to_string();
                break;
            }
        }
    }
    let mut authors: Vec<String> = Vec::new();
    for a in entry.children_named("author") {
        let name = arxiv_field(a, "name");
        if !name.is_empty() {
            authors.push(name);
        }
    }
    let mut fields = serde_json::Map::new();
    fields.insert(
        "primary_category".to_string(),
        Value::String(primary),
    );
    let mut arxiv = serde_json::Map::new();
    arxiv.insert("arxiv".to_string(), Value::Object(fields));
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("arxiv".to_string()));
    rec.insert("id".to_string(), Value::String(bare_id.clone()));
    rec.insert(
        "title".to_string(),
        Value::String(arxiv_field(entry, "title")),
    );
    rec.insert(
        "url".to_string(),
        Value::String(if entry_id.is_empty() {
            bare_id
        } else {
            entry_id
        }),
    );
    rec.insert("doi".to_string(), Value::String(doi));
    rec.insert(
        "published".to_string(),
        Value::String(arxiv_field(entry, "published")),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            crate::pycompat::char_head(
                crate::pycompat::py_collapse_ws(&arxiv_field(entry, "summary")).as_str(),
                240,
            )
            .to_string(),
        ),
    );
    // `raw` is `ET.tostring(entry)`: re-attached by the wrapper from
    // its own ElementTree parse (see below).
    rec.insert("fields".to_string(), Value::Object(arxiv));
    Value::Object(rec)
}

pub fn arxiv_parse_search_impl(
    response_xml: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // Append-then-break: any entries at all yield at least one record,
    // even for `max_results <= 0`.
    let mut out = Vec::new();
    for entry in root.children_named("entry") {
        out.push(arxiv_entry_impl(entry));
        if out.len() as i64 >= max_results {
            break;
        }
    }
    Ok(out)
}

pub fn arxiv_parse_fetch_impl(
    response_xml: &str,
    ident: &str,
    url_fallback: &Value,
) -> Result<Value, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    let entries = root.children_named("entry");
    // A valid entry's <id> contains 'abs/'; otherwise (or when empty)
    // answer the error record with the entry summary ("" when empty).
    let entry = entries.first().copied();
    let valid = match entry {
        Some(e) => arxiv_field(e, "id").contains("abs/"),
        None => false,
    };
    if !valid {
        let summary = match entry {
            Some(e) => arxiv_field(e, "summary"),
            None => String::new(),
        };
        let mut fields = serde_json::Map::new();
        fields.insert(
            "primary_category".to_string(),
            Value::String(String::new()),
        );
        let mut arxiv = serde_json::Map::new();
        arxiv.insert("arxiv".to_string(), Value::Object(fields));
        let mut rec = serde_json::Map::new();
        rec.insert("source".to_string(), Value::String("arxiv".to_string()));
        rec.insert("id".to_string(), Value::String(ident.to_string()));
        rec.insert("title".to_string(), Value::String(String::new()));
        rec.insert("url".to_string(), url_fallback.clone());
        rec.insert("doi".to_string(), Value::String(String::new()));
        rec.insert("published".to_string(), Value::String(String::new()));
        rec.insert("authors".to_string(), Value::String(String::new()));
        rec.insert("snippet".to_string(), Value::String(summary));
        rec.insert("fields".to_string(), Value::Object(arxiv));
        // `raw` is "": attached by the wrapper.
        return Ok(Value::Object(rec));
    }
    Ok(arxiv_entry_impl(entry.unwrap()))
}


pub fn pubmed_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `resp.json()["esearchresult"]`: direct indexing (missing key →
    // KeyError; lists/strings index by integer only; anything else is
    // not subscriptable).
    let esearch = match &body {
        Value::Object(m) => match m.get("esearchresult") {
            Some(v) => v.clone(),
            None => return Err("KeyError: esearchresult".to_string()),
        },
        Value::Array(_) => {
            return Err(
                "TypeError: list indices must be integers or slices, not str".to_string(),
            );
        }
        Value::String(_) => {
            return Err("TypeError: string indices must be integers, not 'str'".to_string());
        }
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let emap = match &esearch {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&esearch))),
    };
    // `.get("idlist", [])[:max_results]`: missing → []; lists slice;
    // strings slice per char (uids render, never `.get`); dicts raise
    // `KeyError(slice)`; anything else raises TypeError.
    let hits: Vec<Value> = match emap.get("idlist") {
        None => Vec::new(),
        Some(Value::Array(a)) => slice_refs(a, max_results)
            .into_iter()
            .cloned()
            .collect(),
        Some(Value::String(s)) => {
            let chars: Vec<char> = s.chars().collect();
            let n = chars.len() as i64;
            let end = (if max_results < 0 {
                (n + max_results).max(0)
            } else {
                max_results.min(n)
            }) as usize;
            chars[..end]
                .iter()
                .map(|c| Value::String(c.to_string()))
                .collect()
        }
        Some(Value::Object(_)) => return Err(subscript_keyerror(max_results)),
        Some(other) => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let mut out = Vec::new();
    for uid in hits.iter() {
        let uid_s = py_str_value(uid);
        let mut rec = serde_json::Map::new();
        rec.insert("source".to_string(), Value::String("pubmed".to_string()));
        rec.insert("id".to_string(), uid.clone());
        rec.insert("title".to_string(), Value::String(String::new()));
        rec.insert(
            "url".to_string(),
            Value::String(format!("https://pubmed.ncbi.nlm.nih.gov/{uid_s}/")),
        );
        rec.insert(
            "snippet".to_string(),
            Value::String(format!("PMID {uid_s}")),
        );
        out.push(Value::Object(rec));
    }
    Ok(out)
}

pub fn pubmed_parse_fetch_impl(
    response_xml: &str,
    rid_s: &str,
) -> Result<Option<Value>, String> {
    let root = crate::xmlatom::parse_document(response_xml)?;
    // `root.find("PubmedArticle")`: None answers the bare record in
    // the wrapper (which owns the ET lookup); the kernel returns None.
    let entry = match root.child("PubmedArticle") {
        Some(e) => e,
        None => return Ok(None),
    };
    // `entry.find("MedlineCitation")`: None raises AttributeError on
    // the first `.findtext` below.
    let mc = entry.child("MedlineCitation");
    let pmid = match mc {
        None => return Err(attr_error("NoneType")),
        Some(m) => m.child("PMID").map(|p| p.text.clone()),
    };
    // `article.findtext("ArticleTitle")`: article None raises here.
    let article = mc.unwrap().child("Article");
    let title = match article {
        None => return Err(attr_error("NoneType")),
        Some(a) => match a.child("ArticleTitle") {
            None => String::new(),
            Some(t) => crate::pycompat::py_collapse_ws(&t.text),
        },
    };
    let mut authors: Vec<String> = Vec::new();
    if let Some(alist) = article.unwrap().child("AuthorList") {
        for a in alist.children_named("Author") {
            let last = a.child("LastName").map(|n| n.text.as_str());
            let fore = a.child("ForeName").map(|n| n.text.as_str());
            // `" ".join(x for x in (ln, fn) if x).strip()`: raw parts
            // (uncollapsed), single-space join, ends stripped.
            let mut parts: Vec<&str> = Vec::new();
            if let Some(x) = last {
                if !x.is_empty() {
                    parts.push(x);
                }
            }
            if let Some(x) = fore {
                if !x.is_empty() {
                    parts.push(x);
                }
            }
            let name = parts.join(" ").trim().to_string();
            if !name.is_empty() {
                authors.push(name);
            }
        }
    }
    // `mc.findtext("PMID") or str(record_id)`: falsy PMIDs fold.
    let uid = match pmid {
        Some(t) if !t.is_empty() => t,
        _ => rid_s.to_string(),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("pubmed".to_string()));
    rec.insert("id".to_string(), Value::String(uid.clone()));
    rec.insert("title".to_string(), Value::String(title.clone()));
    rec.insert(
        "url".to_string(),
        Value::String(format!("https://pubmed.ncbi.nlm.nih.gov/{uid}/")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(crate::pycompat::char_head(title.as_str(), 240).to_string()),
    );
    rec.insert(
        "authors".to_string(),
        Value::String(authors.join(", ")),
    );
    // `raw` is `resp.text` verbatim: attached by the wrapper.
    Ok(Some(Value::Object(rec)))
}

#[pyfunction]
#[pyo3(signature = (response_xml, max_results = 5))]
pub fn arxiv_parse_search(
    py: Python,
    response_xml: &str,
    max_results: i64,
) -> PyResult<String> {
    arxiv_parse_search_impl(response_xml, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, ident = "", url_fallback_json = "null"))]
pub fn arxiv_parse_fetch(
    py: Python,
    response_xml: &str,
    ident: &str,
    url_fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(url_fallback_json).unwrap_or(Value::Null);
    arxiv_parse_fetch_impl(response_xml, ident, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn pubmed_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    pubmed_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_xml, rid = ""))]
pub fn pubmed_parse_fetch(
    py: Python,
    response_xml: &str,
    rid: &str,
) -> PyResult<String> {
    pubmed_parse_fetch_impl(response_xml, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

