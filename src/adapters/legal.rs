//! Adapter kernels: Law and government kernels (CourtListener, GovInfo, HUDOC, OLDP, Federal Register, Congress, Census, eCFR).

use pyo3::prelude::*;
use serde_json::Value;
use crate::pycompat::{char_head, py_strip};
use crate::cite::py_value_repr;
use super::common::{attr_error, attr_error_attr, type_error_not_subscriptable, json_type, is_truthy, to_py_err, type_error_not_iterable, subscript_keyerror, sequence_item_error, subscript_hits, slice_refs, strip_tags_impl, py_str_value};

pub(crate) fn courtlistener_row_impl(r: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    // `r.get("caseName") or r.get("caseNameFull", "")`.
    let title = match m.get("caseName") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => m
            .get("caseNameFull")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    // `_strip_tags(r.get("caseNameFull") or r.get("caseName") or "")[:240]`:
    // the or-chain feeds tag-stripping first (falsy → "", truthy
    // non-strings raise TypeError), and the slice applies to the
    // stripped string (chars, never raises).
    let raw_title = match m.get("caseNameFull") {
        Some(v) if is_truthy(v) => v.clone(),
        _ => match m.get("caseName") {
            Some(v) if is_truthy(v) => v.clone(),
            _ => Value::String(String::new()),
        },
    };
    let stripped_raw = strip_tags_impl(&raw_title)?;
    let stripped = char_head(stripped_raw.as_str(), 240).to_string();
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("courtlistener".to_string()),
    );
    rec.insert(
        "id".to_string(),
        Value::String(py_value_repr(
            m.get("cluster_id").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert("title".to_string(), title);
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://www.courtlistener.com{0}",
            py_value_repr(
                m.get("absolute_url")
                    .unwrap_or(&Value::String(String::new()))
            )
        )),
    );
    rec.insert(
        "published".to_string(),
        m.get("dateFiled")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("snippet".to_string(), Value::String(stripped));
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    for (key, field) in [
        ("court", "court"),
        ("court_citation", "court_citation_string"),
        ("docket_number", "docketNumber"),
        ("neutral_cite", "neutralCite"),
        ("cite_count", "citeCount"),
    ] {
        fields.insert(
            key.to_string(),
            m.get(field)
                .cloned()
                .unwrap_or(Value::String(String::new())),
        );
    }
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn courtlistener_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])`, then `results[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for r in hits {
        out.push(courtlistener_row_impl(r)?);
    }
    Ok(out)
}

pub fn courtlistener_parse_fetch_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    courtlistener_row_impl(&body)
}


fn govinfo_detail_url(pkg: &Value) -> Value {
    // `f"...{pkg}" if pkg else ""`: falsy package ids yield no URL.
    if is_truthy(pkg) {
        Value::String(format!(
            "https://www.govinfo.gov/app/details/{0}",
            py_value_repr(pkg)
        ))
    } else {
        Value::String(String::new())
    }
}

pub(crate) fn govinfo_row_impl(r: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    let pkg = m
        .get("packageId")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let granule = m
        .get("granuleId")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `r.get("download", {}) or {}`: missing/falsy → none; truthy
    // non-dicts raise on their first `.get` (txtLink read).
    let dl: Option<&serde_json::Map<String, Value>> = match m.get("download") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(Value::Object(mm)) => Some(mm),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let txt = dl.and_then(|mm| mm.get("txtLink").filter(|v| is_truthy(v)));
    let pdf = dl.and_then(|mm| mm.get("pdfLink").filter(|v| is_truthy(v)));
    let url = match txt.or(pdf) {
        Some(v) => v.clone(),
        None => govinfo_detail_url(&pkg),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("govinfo".to_string()));
    // `granule or pkg`: raw values, first truthy wins.
    rec.insert(
        "id".to_string(),
        if is_truthy(&granule) {
            granule.clone()
        } else {
            pkg.clone()
        },
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
        Value::String(py_value_repr(
            m.get("dateIssued")
                .unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            py_strip(
                format!(
                    "{0} {1}",
                    py_value_repr(
                        m.get("collectionCode")
                            .unwrap_or(&Value::String(String::new()))
                    ),
                    py_value_repr(&pkg)
                )
                .as_str(),
            )
            .to_string(),
        ),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "collection".to_string(),
        m.get("collectionCode")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert("package_id".to_string(), pkg);
    fields.insert("granule_id".to_string(), granule);
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn govinfo_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])[:max_results]` (no `or []`).
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for r in hits {
        out.push(govinfo_row_impl(r)?);
    }
    Ok(out)
}

pub fn govinfo_parse_fetch_impl(
    response_json: &str,
    rid: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let m = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `body.get("download", {}).get("txtLink", "")`: missing download
    // → ""; present values (even falsy) read as-is; non-dicts raise.
    let txt = match m.get("download") {
        None => Value::String(String::new()),
        Some(Value::Object(dm)) => dm
            .get("txtLink")
            .cloned()
            .unwrap_or(Value::String(String::new())),
        Some(other) => return Err(attr_error(json_type(other))),
    };
    let url = if is_truthy(&txt) {
        txt
    } else {
        Value::String(format!("https://www.govinfo.gov/app/details/{rid}"))
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("govinfo".to_string()));
    rec.insert(
        "id".to_string(),
        m.get("packageId").cloned().unwrap_or(Value::String(
            rid.to_string(),
        )),
    );
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(rid.to_string())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        Value::String(py_value_repr(
            m.get("dateIssued")
                .unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(py_value_repr(
            m.get("collectionCode")
                .unwrap_or(&Value::String(String::new())),
        )),
    );
    // NB: flat `fields` here too (search rows carry package/granule).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "collection".to_string(),
        m.get("collectionCode")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}


fn hudoc_row_impl(columns: &Value) -> Result<Value, String> {
    let m = match columns {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(columns))),
    };
    let itemid = m
        .get("itemid")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let url = if is_truthy(&itemid) {
        Value::String(format!(
            "https://hudoc.echr.coe.int/eng?i={0}",
            py_value_repr(&itemid)
        ))
    } else {
        Value::String(String::new())
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("hudoc".to_string()));
    rec.insert("id".to_string(), itemid);
    rec.insert(
        "title".to_string(),
        m.get("docname")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("url".to_string(), url);
    rec.insert(
        "published".to_string(),
        Value::String(
            char_head(
                py_value_repr(
                    m.get("kpdate").unwrap_or(&Value::String(String::new()))
                )
                .as_str(),
                10,
            )
            .to_string(),
        ),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            py_strip(
                format!(
                    "application no. {0}",
                    py_value_repr(
                        m.get("appno").unwrap_or(&Value::String(String::new()))
                    )
                )
                .as_str(),
            )
            .to_string(),
        ),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "appno".to_string(),
        m.get("appno")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "ecli".to_string(),
        m.get("ecli")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn hudoc_parse_search_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // NB: no `[:max_results]` here — every result row is built.
    // `body.get("results", [])`: missing → []; present values iterate
    // (`for r in ...` — no truthiness check, so only ""/[]/{} iterate
    // empty while None/0/False raise TypeError).
    let results = match obj.get("results") {
        None => return Ok(Vec::new()),
        Some(v) => v,
    };
    // Materialize iteration exactly like `for r in results`.
    if !matches!(
        results,
        Value::Array(_) | Value::String(_) | Value::Object(_)
    ) {
        return Err(type_error_not_iterable(json_type(results)));
    }
    let items: Vec<&Value> = match results {
        Value::Array(a) => a.iter().collect(),
        _ => Vec::new(),
    };
    let mut out = Vec::new();
    if matches!(results, Value::Array(_)) {
        for r in items {
            // `r.get("columns", {})`: missing → {}; non-dict rows raise.
            let columns = match r {
                Value::Object(rm) => rm
                    .get("columns")
                    .cloned()
                    .unwrap_or(Value::Object(serde_json::Map::new())),
                _ => return Err(attr_error(json_type(r))),
            };
            out.push(hudoc_row_impl(&columns)?);
        }
    } else if let Value::String(s) = results {
        // Iterating a string yields chars; `.get` on a char raises.
        if !s.is_empty() {
            return Err(attr_error("str"));
        }
    } else if let Value::Object(m) = results {
        // Iterating a dict yields keys; `.get` on a key raises.
        if let Some(k) = m.keys().next() {
            let _ = k;
            return Err(attr_error("str"));
        }
    }
    Ok(out)
}

pub fn hudoc_parse_fetch_impl(response_json: &str) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `results = ...get("results", [])`; `if not results: return []`.
    let results = match obj.get("results") {
        None => return Ok(Vec::new()),
        Some(v) if !is_truthy(v) => return Ok(Vec::new()),
        Some(v) => v,
    };
    // `results[0]`: list (non-empty — falsy caught above), str (char),
    // dict (KeyError 0), anything else TypeError.
    let first = match results {
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
    // `.get("columns", {})` on the element (must be a dict).
    let columns = match first {
        Value::Object(m) => m
            .get("columns")
            .cloned()
            .unwrap_or(Value::Object(serde_json::Map::new())),
        _ => return Err(attr_error(json_type(first))),
    };
    Ok(vec![hudoc_row_impl(&columns)?])
}


#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn courtlistener_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    courtlistener_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn courtlistener_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    courtlistener_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn govinfo_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    govinfo_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn govinfo_parse_fetch(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    govinfo_parse_fetch_impl(response_json, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json))]
pub fn hudoc_parse_search(py: Python, response_json: &str) -> PyResult<String> {
    hudoc_parse_search_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn hudoc_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    hudoc_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

fn oldp_court_name(court: Option<&Value>) -> String {
    match court {
        // Dicts read `name` (missing → ""); anything else renders
        // `str(court or "")` — falsy → "", truthy → Python-`str()`.
        Some(Value::Object(m)) => match m.get("name") {
            None => String::new(),
            Some(v) => py_value_repr(v),
        },
        Some(v) if is_truthy(v) => py_value_repr(v),
        _ => String::new(),
    }
}

pub(crate) fn oldp_snippet(snippets: Option<&Value>) -> Result<String, String> {
    // `(c.get("snippets") or [])[:3]`, each `str(s)[:200]`, joined with
    // " … ". Unlike the row-list helpers, iterating here cannot fail
    // (`str(s)` accepts chars), so strings slice to chars; dicts raise
    // KeyError on slicing, anything else TypeError.
    let render = |s: &Value| char_head(py_value_repr(s).as_str(), 200).to_string();
    let parts: Vec<String> = match snippets {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => slice_refs(a, 3).iter().map(|s| render(s)).collect(),
        Some(Value::String(s)) => {
            // `str(char)` is the char itself; `[:200]` is a no-op.
            let chars: Vec<char> = s.chars().collect();
            let n = chars.len();
            chars[..3.min(n)].iter().map(|c| c.to_string()).collect()
        }
        Some(Value::Object(_)) => return Err(subscript_keyerror(3)),
        Some(other) => return Err(type_error_not_subscriptable(json_type(other))),
    };
    Ok(parts.join(" … "))
}

fn oldp_case_row_impl(c: &Value) -> Result<Value, String> {
    let m = match c {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(c))),
    };
    let court = oldp_court_name(m.get("court"));
    let file_no = m
        .get("file_number")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `f"{court} {file_no}".strip() or c.get("slug", "")`.
    let headed = py_strip(
        format!("{court} {0}", py_value_repr(&file_no)).as_str(),
    )
    .to_string();
    let title = if headed.is_empty() {
        m.get("slug")
            .cloned()
            .unwrap_or(Value::String(String::new()))
    } else {
        Value::String(headed)
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("oldp".to_string()));
    rec.insert(
        "id".to_string(),
        Value::String(py_value_repr(
            m.get("id").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert("title".to_string(), title);
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://de.openlegaldata.io/case/{0}",
            py_value_repr(m.get("slug").unwrap_or(&Value::String(String::new())))
        )),
    );
    rec.insert(
        "published".to_string(),
        Value::String(py_value_repr(
            m.get("date").unwrap_or(&Value::String(String::new())),
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(oldp_snippet(m.get("snippets"))?),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert("court".to_string(), Value::String(court));
    fields.insert("file_number".to_string(), file_no);
    fields.insert(
        "ecli".to_string(),
        m.get("ecli")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "decision_type".to_string(),
        m.get("decision_type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn oldp_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `resp.json().get("results", [])`, then `hits[:max_results]`.
    let hits: Vec<&Value> = match obj.get("results") {
        None => Vec::new(),
        Some(v) => subscript_hits(Some(v), max_results)?,
    };
    let mut out = Vec::new();
    for c in hits {
        out.push(oldp_case_row_impl(c)?);
    }
    Ok(out)
}

pub fn oldp_parse_law_impl(response_json: &str, rid: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let m = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("oldp".to_string()));
    rec.insert("id".to_string(), Value::String(rid.to_string()));
    rec.insert(
        "title".to_string(),
        m.get("title")
            .cloned()
            .unwrap_or(Value::String(rid.to_string())),
    );
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://de.openlegaldata.io/law/{0}",
            py_value_repr(m.get("slug").unwrap_or(&Value::String(String::new())))
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            char_head(
                py_value_repr(m.get("text").unwrap_or(&Value::String(String::new())))
                    .as_str(),
                400,
            )
            .to_string(),
        ),
    );
    let mut fields = serde_json::Map::new();
    fields.insert(
        "book".to_string(),
        m.get("book")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "section".to_string(),
        m.get("section")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}


pub(crate) fn fed_doc_impl(d: &Value) -> Result<Value, String> {
    let m = match d {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(d))),
    };
    // `d.get("agency", {}) or {}`: missing/falsy → none. The dict
    // check stays lazy: `agency.get("name", "")` runs at fields time
    // (after the snippet), so a truthy non-dict must not raise here.
    let agency: Option<&Value> = match m.get("agency") {
        None => None,
        Some(v) if !is_truthy(v) => None,
        Some(v) => Some(v),
    };
    // `d.get("html_url", d.get("text_url", ""))`: the inner default
    // applies only when the outer key is missing.
    let url = match m.get("html_url") {
        Some(v) => v.clone(),
        None => m
            .get("text_url")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("federalregister".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("document_number")
            .cloned()
            .unwrap_or(Value::String(String::new())),
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
        m.get("doc_date")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    // `_strip_tags(d.get("abstract", d.get("excerpt", "")))[:240]`.
    let abstract_raw = match m.get("abstract") {
        Some(v) => v.clone(),
        None => m
            .get("excerpt")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    };
    let stripped = strip_tags_impl(&abstract_raw)?;
    rec.insert(
        "snippet".to_string(),
        Value::String(char_head(stripped.as_str(), 240).to_string()),
    );
    // NB: flat `fields` (no adapter-namespaced sub-object here).
    let mut fields = serde_json::Map::new();
    fields.insert(
        "document_type".to_string(),
        m.get("document_type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "type".to_string(),
        m.get("type")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    fields.insert(
        "agency".to_string(),
        match agency {
            None => Value::String(String::new()),
            Some(Value::Object(am)) => am
                .get("name")
                .cloned()
                .unwrap_or(Value::String(String::new())),
            Some(other) => return Err(attr_error(json_type(other))),
        },
    );
    fields.insert(
        "document_number".to_string(),
        m.get("document_number")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn fed_parse_search_impl(
    response_json: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let obj = match &body {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&body))),
    };
    // `body.get("results", body.get("documents", [])) or []`: both
    // `.get`s always run; missing results → documents (missing → []);
    // present values (even falsy) pass through the `or []` fold.
    let raw: Value = match obj.get("results") {
        None => obj
            .get("documents")
            .cloned()
            .unwrap_or(Value::Array(Vec::new())),
        Some(v) => v.clone(),
    };
    let folded: Value = if is_truthy(&raw) {
        raw
    } else {
        Value::Array(Vec::new())
    };
    let hits: Vec<&Value> = subscript_hits(Some(&folded), max_results)?;
    let mut out = Vec::new();
    for d in hits {
        out.push(fed_doc_impl(d)?);
    }
    Ok(out)
}

pub fn fed_parse_fetch_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    fed_doc_impl(&body)
}


#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn oldp_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    oldp_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

pub fn oldp_parse_case_impl(response_json: &str) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    oldp_case_row_impl(&body)
}

#[pyfunction]
pub fn oldp_parse_case(py: Python, response_json: &str) -> PyResult<String> {
    oldp_parse_case_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn oldp_parse_law(py: Python, response_json: &str, rid: &str) -> PyResult<String> {
    oldp_parse_law_impl(response_json, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn fed_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    fed_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
pub fn fed_parse_fetch(py: Python, response_json: &str) -> PyResult<String> {
    fed_parse_fetch_impl(response_json)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

/// The dead `loc` computation (`r.get("state", "")` dance) is skipped:
/// it never reaches the record and cannot raise anything the first
/// field read does not already raise identically.
fn congress_row_impl(r: &Value, fallback: &Value) -> Result<Value, String> {
    let m = match r {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(r))),
    };
    let mut cg = serde_json::Map::new();
    cg.insert(
        "chamber".to_string(),
        m.get("chamber")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    cg.insert(
        "party".to_string(),
        m.get("party").cloned().unwrap_or(Value::String(String::new())),
    );
    cg.insert(
        "state".to_string(),
        m.get("state").cloned().unwrap_or(Value::String(String::new())),
    );
    let mut fields = serde_json::Map::new();
    fields.insert("congress".to_string(), Value::Object(cg));
    let mut rec = serde_json::Map::new();
    rec.insert(
        "source".to_string(),
        Value::String("congress".to_string()),
    );
    rec.insert(
        "id".to_string(),
        m.get("cgi_id").cloned().unwrap_or(fallback.clone()),
    );
    rec.insert(
        "title".to_string(),
        m.get("display_name").cloned().unwrap_or(fallback.clone()),
    );
    rec.insert(
        "url".to_string(),
        m.get("url").cloned().unwrap_or(Value::String(String::new())),
    );
    // `f"{title} {party} — {state}{district}"`: every slot renders
    // raw (missing → "").
    let empty = Value::String(String::new());
    rec.insert(
        "snippet".to_string(),
        Value::String(format!(
            "{0} {1} \u{2014} {2}{3}",
            py_str_value(m.get("title").unwrap_or(&empty)),
            py_str_value(m.get("party").unwrap_or(&empty)),
            py_str_value(m.get("state").unwrap_or(&empty)),
            py_str_value(m.get("district").unwrap_or(&empty))
        )),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    Ok(Value::Object(rec))
}

pub fn congress_parse_search_impl(
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
    let empty = Value::String(String::new());
    let mut out = Vec::new();
    for r in hits {
        out.push(congress_row_impl(r, &empty)?);
    }
    Ok(out)
}

pub fn congress_parse_fetch_impl(
    response_json: &str,
    fallback: &Value,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    congress_row_impl(&body, fallback)
}


/// `[h.lower().replace(" ", "_") for h in rows[0]]`: `rows[0]`
/// indexes directly (anything but a list/dict/str raises TypeError);
/// the row itself then iterates (non-iterables raise TypeError);
/// dicts iterate their keys; members must be strings (`.lower`
/// raises otherwise — chars/keys included).
fn census_header(first: &Value) -> Result<Vec<String>, String> {
    match first {
        Value::Array(a) => {
            let owned: Vec<Value> = a.to_vec();
            census_header_owned(&owned)
        }
        // Dicts iterate keys (always strings in JSON).
        Value::Object(mm) => {
            let owned: Vec<Value> = mm
                .keys()
                .map(|k| Value::String(k.clone()))
                .collect();
            census_header_owned(&owned)
        }
        Value::String(s) => {
            // Chars carry no owned storage; rebuild as owned Values.
            let chars: Vec<Value> = s
                .chars()
                .map(|c| Value::String(c.to_string()))
                .collect();
            census_header_owned(&chars)
        }
        // Iterating the header row (not indexing it).
        other => Err(type_error_not_iterable(json_type(other))),
    }
}

fn census_header_owned(items: &[Value]) -> Result<Vec<String>, String> {
    let mut out = Vec::with_capacity(items.len());
    for h in items.iter() {
        match h {
            Value::String(s) => out.push(s.to_lowercase().replace(' ', "_")),
            // `.lower` names the method (not `.get`).
            _ => return Err(attr_error_attr(json_type(h), "lower")),
        }
    }
    Ok(out)
}

/// `{header[i]: row[i] for i in range(len(header)) if i < len(row)}`:
/// `len(row)` raises first for unsized rows; dict rows raise KeyError
/// on `row[0]`; anything else indexes.
fn census_record(header: &[String], row: &Value) -> Result<serde_json::Map<String, Value>, String> {
    // An empty header never touches the row (neither `len` nor `[i]`).
    if header.is_empty() {
        return Ok(serde_json::Map::new());
    }
    match row {
        Value::Array(a) => {
            let mut rec = serde_json::Map::new();
            for (i, h) in header.iter().enumerate() {
                if i < a.len() {
                    rec.insert(h.clone(), a[i].clone());
                }
            }
            Ok(rec)
        }
        Value::String(s) => {
            let chars: Vec<Value> =
                s.chars().map(|c| Value::String(c.to_string())).collect();
            let mut rec = serde_json::Map::new();
            for (i, h) in header.iter().enumerate() {
                if i < chars.len() {
                    rec.insert(h.clone(), chars[i].clone());
                }
            }
            Ok(rec)
        }
        // Dict rows raise KeyError on `row[0]` — but only when a slot
        // passes the `i < len(row)` filter (non-empty row); empty dicts
        // yield no pairs without ever indexing.
        Value::Object(rm) => {
            if rm.is_empty() {
                Ok(serde_json::Map::new())
            } else {
                Err("KeyError: 0".to_string())
            }
        }
        other => Err(format!(
            "TypeError: object of type '{0}' has no len()",
            json_type(other)
        )),
    }
}

fn census_search_row_impl(
    header: &[String],
    row: &Value,
    dataset: &str,
) -> Result<Value, String> {
    let rec = census_record(header, row)?;
    // `key = rec.get("state", rec.get("geographic_unit", ""))`:
    // eager default, always dict-safe here.
    let geo = rec
        .get("geographic_unit")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let key = rec.get("state").cloned().unwrap_or(geo);
    // Title renders the first three pairs; the snippet renders slots
    // 1.. (both raw-rendered, so joining never raises).
    let keys: Vec<&String> = rec.keys().collect();
    let title_pairs: Vec<String> = keys
        .iter()
        .take(3)
        .map(|k| format!("{k}={0}", py_str_value(&rec[*k])))
        .collect();
    // `f"{header[i]}={row[i]}" for i in range(1, len(header)) if i <
    // len(row)`: header names pair with the RAW row slots.
    let row_len: usize = match row {
        Value::Array(a) => a.len(),
        Value::String(s) => s.chars().count(),
        _ => 0,
    };
    let mut snip: Vec<String> = Vec::new();
    for (i, h) in header.iter().enumerate().skip(1) {
        if i < row_len {
            let v = match row {
                Value::Array(a) => a[i].clone(),
                Value::String(s) => Value::String(
                    s.chars().nth(i).map(|c| c.to_string()).unwrap_or_default(),
                ),
                _ => Value::Null,
            };
            snip.push(format!("{h}={0}", py_str_value(&v)));
        }
    }
    let mut cf = serde_json::Map::new();
    cf.insert("dataset".to_string(), Value::String(dataset.to_string()));
    let mut fields = serde_json::Map::new();
    fields.insert("census".to_string(), Value::Object(cf));
    let mut out = serde_json::Map::new();
    out.insert("source".to_string(), Value::String("census".to_string()));
    out.insert("id".to_string(), Value::String(py_str_value(&key)));
    out.insert(
        "title".to_string(),
        Value::String(title_pairs.join(", ")),
    );
    out.insert(
        "url".to_string(),
        Value::String(format!("https://data.census.gov/?g={dataset}")),
    );
    out.insert("snippet".to_string(), Value::String(snip.join(", ")));
    out.insert("fields".to_string(), Value::Object(fields));
    // `raw` is `json.dumps(rec)`: the rebuilt record crosses here and
    // the wrapper attaches it verbatim (see below).
    out.insert("raw".to_string(), Value::Object(rec));
    Ok(Value::Object(out))
}

/// Shared response prologue: falsy bodies fold to []; truthy bodies
/// index `[0]` (dicts → KeyError 0) and slice `[1:]` (strings slice
/// by char; anything else raises TypeError).
fn census_rows_impl(body: &Value) -> Result<(Vec<String>, Vec<Value>), String> {
    if !is_truthy(body) {
        return Ok((Vec::new(), Vec::new()));
    }
    let first: Value = match body {
        Value::Array(a) => match a.first() {
            Some(v) => v.clone(),
            None => return Ok((Vec::new(), Vec::new())),
        },
        Value::String(s) => Value::String(
            s.chars().next().map(|c| c.to_string()).unwrap_or_default(),
        ),
        Value::Object(_) => return Err("KeyError: 0".to_string()),
        other => return Err(type_error_not_subscriptable(json_type(other))),
    };
    // `"".rows[0]` on an empty string: `"a"[0]` of `""`… unreachable
    // (empty strings are falsy and folded above).
    let header = census_header(&first)?;
    let tail: Vec<Value> = match body {
        Value::Array(a) => a.iter().skip(1).cloned().collect(),
        Value::String(s) => s
            .chars()
            .skip(1)
            .map(|c| Value::String(c.to_string()))
            .collect(),
        _ => Vec::new(),
    };
    Ok((header, tail))
}

pub fn census_parse_search_impl(
    response_json: &str,
    dataset: &str,
    max_results: i64,
) -> Result<Vec<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    // `if not rows: return []` folds falsy bodies before any indexing.
    if !is_truthy(&body) {
        return Ok(Vec::new());
    }
    let (header, tail) = census_rows_impl(&body)?;
    // `rows[1:][:max_results]`: slice, then slice again.
    let mut out = Vec::new();
    for row in slice_refs(&tail, max_results) {
        out.push(census_search_row_impl(&header, row, dataset)?);
    }
    Ok(out)
}

pub fn census_parse_fetch_impl(
    response_json: &str,
    dataset: &str,
    rid_s: &str,
) -> Result<Value, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    if !is_truthy(&body) {
        return Ok(Value::Array(Vec::new()));
    }
    let (header, tail) = census_rows_impl(&body)?;
    for row in tail.iter() {
        let rec = census_record(&header, row)?;
        // `str(rec.get("state", rec.get("geographic_unit", ""))) ==
        // str(record_id)`: both sides Python-str rendered.
        let geo = rec
            .get("geographic_unit")
            .cloned()
            .unwrap_or(Value::String(String::new()));
        let key = rec.get("state").cloned().unwrap_or(geo);
        if py_str_value(&key) == rid_s {
            // Title renders slots 1..; snippet reads `state` raw.
            let row_len: usize = match row {
                Value::Array(a) => a.len(),
                Value::String(s) => s.chars().count(),
                _ => 0,
            };
            let mut title: Vec<String> = Vec::new();
            for (i, h) in header.iter().enumerate().skip(1) {
                if i < row_len {
                    let v = match row {
                        Value::Array(a) => a[i].clone(),
                        Value::String(s) => Value::String(
                            s.chars().nth(i).map(|c| c.to_string()).unwrap_or_default(),
                        ),
                        _ => Value::Null,
                    };
                    title.push(format!("{h}={0}", py_str_value(&v)));
                }
            }
            let mut cf = serde_json::Map::new();
            cf.insert("dataset".to_string(), Value::String(dataset.to_string()));
            let mut fields = serde_json::Map::new();
            fields.insert("census".to_string(), Value::Object(cf));
            let mut out = serde_json::Map::new();
            out.insert("source".to_string(), Value::String("census".to_string()));
            out.insert("id".to_string(), Value::String(rid_s.to_string()));
            out.insert("title".to_string(), Value::String(title.join(", ")));
            out.insert(
                "url".to_string(),
                Value::String(format!("https://data.census.gov/?g={dataset}")),
            );
            out.insert(
                "snippet".to_string(),
                rec.get("state")
                    .cloned()
                    .unwrap_or(Value::String(String::new())),
            );
            out.insert("fields".to_string(), Value::Object(fields));
            out.insert("raw".to_string(), Value::Object(rec));
            return Ok(Value::Array(vec![Value::Object(out)]));
        }
    }
    Ok(Value::Array(Vec::new()))
}

#[pyfunction]
#[pyo3(signature = (response_json, max_results = 5))]
pub fn congress_parse_search(
    py: Python,
    response_json: &str,
    max_results: i64,
) -> PyResult<String> {
    congress_parse_search_impl(response_json, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, fallback_json = "null"))]
pub fn congress_parse_fetch(
    py: Python,
    response_json: &str,
    fallback_json: &str,
) -> PyResult<String> {
    let fallback: Value =
        serde_json::from_str(fallback_json).unwrap_or(Value::Null);
    congress_parse_fetch_impl(response_json, &fallback)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, dataset = "", max_results = 5))]
pub fn census_parse_search(
    py: Python,
    response_json: &str,
    dataset: &str,
    max_results: i64,
) -> PyResult<String> {
    census_parse_search_impl(response_json, dataset, max_results)
        .and_then(|v| serde_json::to_string(&Value::Array(v)).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (response_json, dataset = "", rid = ""))]
pub fn census_parse_fetch(
    py: Python,
    response_json: &str,
    dataset: &str,
    rid: &str,
) -> PyResult<String> {
    census_parse_fetch_impl(response_json, dataset, rid)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}


/// `str(value).lstrip("0")`: Python-`str()` render, then strip all
/// leading zeros ("000" folds to "").
fn ecfr_ident_key(v: Option<&Value>) -> String {
    match v {
        None => String::new(),
        Some(x) => py_str_value(x).trim_start_matches('0').to_string(),
    }
}

/// `_find_part` one pass: iterative DFS (the stack pops from the end
/// while children extend reversed); identifiers compare after
/// zero-stripping; `only_parts` gates on `type == "part"`.
fn ecfr_walk(node: &Value, want: &str, only_parts: bool) -> Result<Option<Value>, String> {
    let mut stack: Vec<Value> = vec![node.clone()];
    while let Some(current) = stack.pop() {
        let m = match &current {
            Value::Object(m) => m,
            _ => return Err(attr_error(json_type(&current))),
        };
        if ecfr_ident_key(m.get("identifier")) == want
            && (!only_parts
                || matches!(m.get("type"), Some(Value::String(t)) if t == "part"))
        {
            return Ok(Some(current));
        }
        // `(current.get("children", []) or [])`, reversed: falsy
        // folds; lists reverse item-wise; strings/dicts reverse
        // (chars/keys — failing later per item); anything else raises
        // TypeError.
        let kids: Vec<Value> = match m.get("children") {
            None => Vec::new(),
            Some(v) if !is_truthy(v) => Vec::new(),
            Some(Value::Array(a)) => a.iter().rev().cloned().collect(),
            Some(Value::String(s)) => s
                .chars()
                .rev()
                .map(|c| Value::String(c.to_string()))
                .collect(),
            Some(Value::Object(mm)) => mm
                .keys()
                .rev()
                .map(|k| Value::String(k.clone()))
                .collect(),
            Some(other) => {
                return Err(format!(
                    "TypeError: '{0}' object is not reversible",
                    json_type(other)
                ));
            }
        };
        stack.extend(kids);
    }
    Ok(None)
}

pub fn ecfr_find_part_impl(response_json: &str, part: &str) -> Result<Option<Value>, String> {
    let body: Value = serde_json::from_str(response_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let want = part.trim_start_matches('0').to_string();
    // First pass prefers `type == "part"` hits; the second accepts any
    // identifier hit. (`walk(True) or walk(False)` — an error aborts.)
    match ecfr_walk(&body, &want, true)? {
        Some(n) => Ok(Some(n)),
        None => ecfr_walk(&body, &want, false),
    }
}

/// `_sections`: `f"{identifier} {label}".strip()` over direct
/// `type == "section"` children (falsy children fold; truthy non-lists
/// raise TypeError on iteration), capped at 12.
fn ecfr_sections(node: &serde_json::Map<String, Value>) -> Result<Vec<String>, String> {
    let kids: Vec<Value> = match node.get("children") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        // Dicts/strings iterate (keys/chars — failing per item on
        // `.get` below); anything else raises TypeError.
        Some(Value::String(s)) => {
            s.chars().map(|c| Value::String(c.to_string())).collect()
        }
        Some(Value::Object(mm)) => {
            mm.keys().map(|k| Value::String(k.clone())).collect()
        }
        Some(other) => return Err(type_error_not_iterable(json_type(other))),
    };
    let empty = Value::String(String::new());
    let mut out = Vec::new();
    for child in kids.iter() {
        let cm = match child {
            Value::Object(m) => m,
            _ => return Err(attr_error(json_type(child))),
        };
        if matches!(cm.get("type"), Some(Value::String(t)) if t == "section") {
            // `.get` defaults ("") then f-string render, stripped
            // (Python-`strip()` set via pycompat).
            let ident = py_str_value(cm.get("identifier").unwrap_or(&empty));
            let label = py_str_value(cm.get("label").unwrap_or(&empty));
            out.push(
                crate::pycompat::py_strip(&format!("{ident} {label}")).to_string(),
            );
            if out.len() >= 12 {
                break;
            }
        }
    }
    Ok(out)
}

pub fn ecfr_part_record_impl(
    node_json: &str,
    title_s: &str,
    part_s: &str,
) -> Result<Value, String> {
    let node: Value = serde_json::from_str(node_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let m = match &node {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&node))),
    };
    // Evaluation order matches the dict build: label/desc reads, then
    // sections, then the children-length count.
    let label = m
        .get("label")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let desc = m
        .get("label_description")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let sections = ecfr_sections(m)?;
    // `len(node.get("children", []) or [])`: falsy folds to 0; strs and
    // dicts measure by length; anything else raises TypeError.
    let section_count = match m.get("children") {
        None => 0,
        Some(v) if !is_truthy(v) => 0,
        Some(Value::Array(a)) => a.len(),
        Some(Value::String(s)) => s.chars().count(),
        Some(Value::Object(mm)) => mm.len(),
        Some(other) => {
            return Err(format!(
                "TypeError: object of type '{0}' has no len()",
                json_type(other)
            ));
        }
    };
    // `" ".join(s for s in [desc, f"Sections: ..."] if s)[:400]`:
    // desc stays raw (falsy dropped, non-strings raise at join time).
    let mut kept: Vec<(String, &'static str)> = Vec::new();
    for v in [desc, Value::String(format!("Sections: {0}", sections.join("; ")))] {
        if !is_truthy(&v) {
            continue;
        }
        kept.push((py_value_repr(&v), json_type(&v)));
    }
    let mut parts: Vec<String> = Vec::with_capacity(kept.len());
    for (i, (text, t)) in kept.iter().enumerate() {
        if *t != "str" {
            return Err(sequence_item_error(i, t));
        }
        parts.push(text.clone());
    }
    let mut fields = serde_json::Map::new();
    fields.insert("title_no".to_string(), Value::String(title_s.to_string()));
    fields.insert("part".to_string(), Value::String(part_s.to_string()));
    fields.insert("label".to_string(), label.clone());
    fields.insert("section_count".to_string(), Value::from(section_count));
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("ecfr".to_string()));
    rec.insert(
        "id".to_string(),
        Value::String(format!("{title_s}/{part_s}")),
    );
    rec.insert(
        "title".to_string(),
        Value::String(if is_truthy(&label) {
            format!("Title {title_s}: {0}", py_str_value(&label))
        } else {
            format!("Title {title_s} part {part_s}")
        }),
    );
    rec.insert(
        "url".to_string(),
        Value::String(format!(
            "https://www.ecfr.gov/current/title-{title_s}/part-{part_s}"
        )),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            crate::pycompat::char_head(parts.join(" ").as_str(), 400).to_string(),
        ),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    // `raw` is `json.dumps(node)`: re-attached by the wrapper.
    Ok(Value::Object(rec))
}

pub fn ecfr_title_record_impl(
    tree_json: &str,
    title_s: &str,
) -> Result<Value, String> {
    let tree: Value = serde_json::from_str(tree_json)
        .map_err(|e| format!("ValueError: {e}"))?;
    let m = match &tree {
        Value::Object(m) => m,
        _ => return Err(attr_error(json_type(&tree))),
    };
    let label = m
        .get("label")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    // `[str(c.get("label", "")) for c in children[:8]]`: falsy folds;
    // lists slice; strings slice per char; dicts raise KeyError(slice);
    // anything else raises TypeError.
    let kids: Vec<&Value> = match m.get("children") {
        None => Vec::new(),
        Some(v) if !is_truthy(v) => Vec::new(),
        Some(Value::Array(a)) => slice_refs(a, 8),
        Some(Value::String(s)) => {
            let chars: Vec<char> = s.chars().collect();
            let end = 8.min(chars.len());
            // Chars fail per item (`.get` on a str) unless sliced away.
            if end == 0 {
                Vec::new()
            } else {
                return Err(attr_error("str"));
            }
        }
        Some(Value::Object(_)) => return Err(subscript_keyerror(8)),
        Some(other) => return Err(type_error_not_subscriptable(json_type(other))),
    };
    let empty = Value::String(String::new());
    let mut descs: Vec<String> = Vec::with_capacity(kids.len());
    for c in kids.iter() {
        match c {
            Value::Object(cm) => {
                descs.push(py_str_value(cm.get("label").unwrap_or(&empty)));
            }
            _ => return Err(attr_error(json_type(c))),
        }
    }
    // `"; ".join(d for d in descs if d)[:400]`: all strings by
    // construction (str() applied above), so joining never raises.
    let kept: Vec<&str> = descs.iter().map(|s| s.as_str()).filter(|s| !s.is_empty()).collect();
    let mut fields = serde_json::Map::new();
    fields.insert("title_no".to_string(), Value::String(title_s.to_string()));
    let mut rec = serde_json::Map::new();
    rec.insert("source".to_string(), Value::String("ecfr".to_string()));
    rec.insert("id".to_string(), Value::String(title_s.to_string()));
    rec.insert(
        "title".to_string(),
        if is_truthy(&label) {
            label.clone()
        } else {
            Value::String(format!("Title {title_s}"))
        },
    );
    rec.insert(
        "url".to_string(),
        Value::String(format!("https://www.ecfr.gov/current/title-{title_s}")),
    );
    rec.insert(
        "snippet".to_string(),
        Value::String(
            crate::pycompat::char_head(kept.join("; ").as_str(), 400).to_string(),
        ),
    );
    rec.insert("fields".to_string(), Value::Object(fields));
    // `raw` is the identifier/label/type subset: attached by the wrapper.
    Ok(Value::Object(rec))
}

#[pyfunction]
#[pyo3(signature = (response_json, part = ""))]
pub fn ecfr_find_part(
    py: Python,
    response_json: &str,
    part: &str,
) -> PyResult<String> {
    ecfr_find_part_impl(response_json, part)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (node_json, title_s = "", part_s = ""))]
pub fn ecfr_part_record(
    py: Python,
    node_json: &str,
    title_s: &str,
    part_s: &str,
) -> PyResult<String> {
    ecfr_part_record_impl(node_json, title_s, part_s)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

#[pyfunction]
#[pyo3(signature = (tree_json, title_s = ""))]
pub fn ecfr_title_record(
    py: Python,
    tree_json: &str,
    title_s: &str,
) -> PyResult<String> {
    ecfr_title_record_impl(tree_json, title_s)
        .and_then(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .map_err(|e| to_py_err(py, e))
}

