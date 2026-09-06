//! Citation reconstruction and export (port of the pure parts of
//! `gossamer.citations`).
//!
//! Ported: `BibliographicRecord` (PyO3 class, get/set for every field),
//! record building from result JSON, and the BibTeX / CSL-JSON / APA‑approx
//! / MLA‑approx formatters. Deliberately NOT ported: `enrich_with_doi`
//! (injectable adapter object), `dedupe_records` (delegates to the shared
//! dedupe, already Rust), `format_citations` (style dispatch + enrichment
//! orchestration), and the citeproc-py branch (Python-only dependency).
//!
//! Boundary notes (documented divergences, unreachable from real adapter
//! output): scalar slots holding JSON containers (dict/list) read as
//! missing; a top-level non-str/list `authors` value renders via a
//! Python-`str()` spelling for scalars only. Adapter shapes are
//! str/list/dict/None throughout — the parity suite pins those.
//!
//! Pinned by `tests/test_rust_parity_citations.py`.

use pyo3::exceptions::PyAttributeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

use crate::pycompat::py_repr;
use crate::pycompat::py_strip;

#[pyclass]
pub struct BibliographicRecord {
    #[pyo3(get, set)]
    pub title: Option<String>,
    #[pyo3(get, set)]
    pub authors: Vec<String>,
    #[pyo3(get, set)]
    pub year: Option<String>,
    #[pyo3(get, set)]
    pub month: Option<String>,
    #[pyo3(get, set)]
    pub day: Option<String>,
    #[pyo3(get, set)]
    pub doi: Option<String>,
    #[pyo3(get, set)]
    pub url: Option<String>,
    #[pyo3(get, set)]
    pub venue: Option<String>,
    #[pyo3(get, set)]
    pub publisher: Option<String>,
    #[pyo3(get, set, name = "abstract")]
    pub abstract_text: Option<String>,
    #[pyo3(get, set)]
    pub extra: Py<PyAny>,
    #[pyo3(get, set)]
    pub id: Option<String>,
    #[pyo3(get, set)]
    pub kind: Option<String>,
}

impl Clone for BibliographicRecord {
    // Manual: `Py<PyAny>` is not Clone. Only called with the GIL held
    // (formatter arg marshaling inside `#[pyfunction]`s).
    fn clone(&self) -> Self {
        Python::with_gil(|py| Self {
            title: self.title.clone(),
            authors: self.authors.clone(),
            year: self.year.clone(),
            month: self.month.clone(),
            day: self.day.clone(),
            doi: self.doi.clone(),
            url: self.url.clone(),
            venue: self.venue.clone(),
            publisher: self.publisher.clone(),
            abstract_text: self.abstract_text.clone(),
            extra: self.extra.clone_ref(py),
            id: self.id.clone(),
            kind: self.kind.clone(),
        })
    }
}

/// Pure-Rust record: everything the formatters read. Kept separate from
/// the pyclass shell so kernels and unit tests never need an interpreter.
#[derive(Clone, Default)]
pub struct Record {
    pub title: Option<String>,
    pub authors: Vec<String>,
    pub year: Option<String>,
    pub month: Option<String>,
    pub day: Option<String>,
    pub doi: Option<String>,
    pub url: Option<String>,
    pub venue: Option<String>,
    pub publisher: Option<String>,
    pub abstract_text: Option<String>,
    pub id: Option<String>,
    pub kind: Option<String>,
}

impl BibliographicRecord {
    fn as_record(&self) -> Record {
        Record {
            title: self.title.clone(),
            authors: self.authors.clone(),
            year: self.year.clone(),
            month: self.month.clone(),
            day: self.day.clone(),
            doi: self.doi.clone(),
            url: self.url.clone(),
            venue: self.venue.clone(),
            publisher: self.publisher.clone(),
            abstract_text: self.abstract_text.clone(),
            id: self.id.clone(),
            kind: self.kind.clone(),
        }
    }
}

#[pymethods]
impl BibliographicRecord {
    #[new]
    #[pyo3(signature = (title=None, authors=None, year=None, month=None, day=None, doi=None, url=None, venue=None, publisher=None, r#abstract=None, extra=None, id=None, kind=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python,
        title: Option<String>,
        authors: Option<Vec<String>>,
        year: Option<String>,
        month: Option<String>,
        day: Option<String>,
        doi: Option<String>,
        url: Option<String>,
        venue: Option<String>,
        publisher: Option<String>,
        r#abstract: Option<String>,
        extra: Option<Py<PyAny>>,
        id: Option<String>,
        kind: Option<String>,
    ) -> Self {
        let empty = PyDict::new(py).into_any().unbind();
        BibliographicRecord {
            title,
            authors: authors.unwrap_or_default(),
            year,
            month,
            day,
            doi,
            url,
            venue,
            publisher,
            abstract_text: r#abstract,
            extra: extra.unwrap_or(empty),
            id,
            kind,
        }
    }
}

/// Python-`str()` spelling for JSON scalars (bool None-case handled by
/// callers via truthiness, mirroring `str(v)`). Containers render via
/// `py_value_repr` at the call sites that can observe them.
fn py_scalar_str(v: &Value) -> Option<String> {
    match v {
        Value::Null => None,
        Value::Bool(true) => Some("True".to_string()),
        Value::Bool(false) => Some("False".to_string()),
        Value::Number(n) => Some(n.to_string()),
        Value::String(s) => Some(s.clone()),
        Value::Array(_) | Value::Object(_) => None,
    }
}

fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i != 0
            } else if let Some(u) = n.as_u64() {
                u != 0
            } else {
                n.as_f64().map(|f| f != 0.0).unwrap_or(false)
            }
        }
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `_first(d, *keys)`: first truthy value; non-empty lists contribute
/// `[0]` (which is itself truthiness-checked); missing keys skipped.
fn first_value<'a>(d: &'a serde_json::Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    for k in keys {
        let v = match d.get(*k) {
            Some(v) => v,
            None => continue,
        };
        let v = match v {
            Value::Array(a) if !a.is_empty() => &a[0],
            Value::Array(_) => continue,
            v => v,
        };
        if !is_truthy(v) {
            continue;
        }
        return Some(v);
    }
    None
}

/// Python `repr()` for nested values (single quotes, `True`/`False`/
/// `None` spellings). Used where the original stringifies containers
/// (`str(parts[i])`, `str(authors-dict)`); direct strings keep identity
/// via the caller (see `py_scalar_str`).
pub(crate) fn py_value_repr(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        Value::Array(a) => {
            let inner: Vec<String> = a.iter().map(py_repr_elem).collect();
            format!("[{0}]", inner.join(", "))
        }
        Value::Object(m) => {
            let inner: Vec<String> = m
                .iter()
                .map(|(k, val)| format!("{0}: {1}", py_repr(k), py_repr_elem(val)))
                .collect();
            format!("{{{0}}}", inner.join(", "))
        }
    }
}

fn py_repr_elem(v: &Value) -> String {
    match v {
        Value::String(s) => py_repr(s),
        _ => py_value_repr(v),
    }
}

fn first_text(d: &serde_json::Map<String, Value>, keys: &[&str]) -> Option<String> {
    first_value(d, keys).map(py_value_repr)
}

/// `_normalize_authors` over a JSON value.
fn normalize_authors(raw: Option<&Value>) -> Vec<String> {
    let raw = match raw {
        None => return Vec::new(),
        Some(v) => v,
    };
    match raw {
        Value::Array(items) => {
            let mut out = Vec::new();
            for a in items {
                match a {
                    Value::Object(m) => {
                        // `a.get("family") or a.get("name") or ""`
                        // (truthiness-filtered), then `f"{fam}, {giv}"`
                        // when `giv` is truthy, else bare `fam`.
                        let fam = m
                            .get("family")
                            .filter(|v| is_truthy(v))
                            .map(py_value_repr)
                            .filter(|s| !s.is_empty())
                            .or_else(|| {
                                m.get("name")
                                    .filter(|v| is_truthy(v))
                                    .map(py_value_repr)
                                    .filter(|s| !s.is_empty())
                            })
                            .unwrap_or_default();
                        let giv_raw = m.get("given");
                        let giv = giv_raw.map(py_value_repr).unwrap_or_default();
                        let giv_truthy = giv_raw.map(is_truthy).unwrap_or(false);
                        if giv_truthy {
                            out.push(py_strip(&format!("{fam}, {giv}")).to_string());
                        } else {
                            // `fam` unstripped when no given name (verbatim).
                            out.push(fam);
                        }
                    }
                    Value::Null => out.push("None".to_string()),
                    Value::Bool(true) => out.push("True".to_string()),
                    Value::Bool(false) => out.push("False".to_string()),
                    Value::Number(n) => out.push(n.to_string()),
                    Value::String(s) => {
                        let t = py_strip(s);
                        if !t.is_empty() {
                            out.push(t.to_string());
                        }
                    }
                    // Containers render via `str(a)` (verbatim port).
                    Value::Array(_) | Value::Object(_) => {
                        let t = py_strip(&py_value_repr(a)).to_string();
                        if !t.is_empty() {
                            out.push(t);
                        }
                    }
                }
            }
            out
        }
        Value::Null => Vec::new(),
        // Scalars and containers alike render via `str(raw)` and split
        // on commas (verbatim port of the `str` fallthrough).
        v => py_value_repr(v)
            .split(',')
            .map(|a| py_strip(a).to_string())
            .filter(|a| !a.is_empty())
            .collect(),
    }
}

fn date_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?").expect("date regex must compile")
    })
}

/// Python type name for `AttributeError`/`TypeError` messages.
fn json_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_i64() || n.is_u64() {
                "int"
            } else {
                "float"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// `_g(i)` over a truthy `parts` element: lists index (missing →
/// None), strings index by character, dicts raise `KeyError(i)`
/// (JSON dicts never hold int keys), other scalars raise TypeError
/// (`len()`), elements render via `str()`.
fn date_from_element(elem: &Value) -> Result<(Option<String>, Option<String>, Option<String>), String> {
    if !is_truthy(elem) {
        return Ok((None, None, None));
    }
    match elem {
        Value::Array(parts) => {
            let g = |i: usize| {
                parts.get(i).and_then(|v| {
                    if !is_truthy(v) {
                        None
                    } else {
                        Some(py_value_repr(v))
                    }
                })
            };
            Ok((g(0), g(1), g(2)))
        }
        Value::String(s) => {
            let chars: Vec<char> = s.chars().collect();
            let g = |i: usize| chars.get(i).map(|c| c.to_string());
            Ok((g(0), g(1), g(2)))
        }
        Value::Object(_) => Err("KeyError: 0".to_string()),
        _ => Err(format!(
            "TypeError: object of type '{}' has no len()",
            json_type_name(elem)
        )),
    }
}

/// `date-parts` itself is a truthy string: `parts` is its first character.
fn date_from_str_parts(s: &str) -> (Option<String>, Option<String>, Option<String>) {
    (s.chars().next().map(|c| c.to_string()), None, None)
}

fn extract_date(result: &serde_json::Map<String, Value>) -> Result<(Option<String>, Option<String>, Option<String>), String> {
    let published = result
        .get("published")
        .filter(|v| is_truthy(v))
        .or_else(|| result.get("publication_date").filter(|v| is_truthy(v)));
    let published = match published {
        None => return Ok((None, None, None)),
        Some(v) => v,
    };
    if let Value::Object(m) = published {
        // Verbatim port of `(published.get("date-parts") or [])[0] or []`
        // plus `_g(i)`: missing/falsy date-parts → IndexError; [0] of a
        // non-indexable → TypeError; string parts index by character;
        // elements render via `str()` (containers included).
        let dp_missing_or_falsy = match m.get("date-parts") {
            None => true,
            Some(v) => !is_truthy(v),
        };
        if dp_missing_or_falsy {
            return Err("IndexError: list index out of range".to_string());
        }
        let dp = m.get("date-parts").unwrap();
        let elem0: &Value = match dp {
            Value::Array(a) => a.first().unwrap(),
            Value::String(s) => {
                return Ok(date_from_str_parts(s));
            }
            _ => {
                return Err(format!(
                    "TypeError: '{}' object is not subscriptable",
                    json_type_name(dp)
                ));
            }
        };
        return date_from_element(elem0);
    }
    // Scalar: `str(published).strip()` then anchored match. (Arrays and
    // objects cannot match — their renderings start with `[` / `{`.)
    let text = match published {
        Value::String(s) => py_strip(s).to_string(),
        Value::Null => return Ok((None, None, None)),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => py_strip(&n.to_string()).to_string(),
        Value::Array(_) | Value::Object(_) => return Ok((None, None, None)),
    };
    match date_re().captures(&text) {
        None => Ok((None, None, None)),
        Some(caps) => Ok((
            caps.get(1).map(|m| m.as_str().to_string()),
            caps.get(2).map(|m| m.as_str().to_string()),
            caps.get(3).map(|m| m.as_str().to_string()),
        )),
    }
}

fn non_empty(s: Option<String>) -> Option<String> {
    s.filter(|v| !v.is_empty())
}

/// `_venue_from_raw`: `None` on unparseable JSON; `AttributeError` when
/// the payload parses to a non-object (mirrors `r.get` raising).
fn venue_from_raw(raw: Option<&Value>) -> Result<Option<String>, String> {
    venue_or_abstract_from_raw(raw, true)
}

fn abstract_from_raw(raw: Option<&Value>) -> Result<Option<String>, String> {
    venue_or_abstract_from_raw(raw, false)
}

fn venue_or_abstract_from_raw(
    raw: Option<&Value>,
    want_venue: bool,
) -> Result<Option<String>, String> {
    let raw = match raw {
        None => return Ok(None),
        Some(v) => v,
    };
    let text = match raw {
        Value::String(s) => s.clone(),
        _ => return Ok(None), // `raw` is a JSON string in practice; other
                              // shapes read as missing (see module docs).
    };
    let parsed: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return Ok(None),
    };
    let root = match parsed.as_object() {
        Some(m) => m,
        None => return Err(attr_error(&parsed)),
    };
    let msg = match root.get("message") {
        None => root,
        Some(Value::Object(m)) => m,
        Some(Value::Null) => {
            return Err("TypeError: argument of type 'NoneType' is not iterable".to_string());
        }
        // Non-object `message`: out of contract for real providers
        // (always an object when present); read as missing.
        Some(_) => return Ok(None),
    };
    if want_venue {
        if let Some(v) = first_text(msg, &["container_title", "journal_title"]) {
            if !v.is_empty() {
                return Ok(Some(v));
            }
        }
        if let Some(Value::Object(pl)) = msg.get("primary_location") {
            if let Some(Value::Object(jour)) = pl.get("journal") {
                if let Some(Value::String(v)) = jour.get("display_name") {
                    if !v.is_empty() {
                        return Ok(Some(v.clone()));
                    }
                }
            }
        }
        return Ok(None);
    }
    if let Some(ab) = msg.get("abstract") {
        match ab {
            Value::String(s) if !s.trim().is_empty() => {
                return Ok(Some(s.trim().to_string()));
            }
            Value::Object(m) => {
                for k in ["#text", "a"] {
                    if let Some(Value::String(t)) = m.get(k) {
                        if !t.trim().is_empty() {
                            return Ok(Some(t.trim().to_string()));
                        }
                    }
                }
            }
            _ => {}
        }
    }
    if let Some(Value::Object(pl)) = msg.get("primary_location") {
        if let Some(Value::Object(inner)) = pl.get("abstract") {
            if let Some(Value::String(txt)) = inner.get("text") {
                if !txt.trim().is_empty() {
                    return Ok(Some(txt.trim().to_string()));
                }
            }
        }
    }
    Ok(None)
}

/// Python type name for `AttributeError` messages (`r.get` on non-objects).
fn attr_error(parsed: &Value) -> String {
    let t = match parsed {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_i64() || n.is_u64() {
                "int"
            } else {
                "float"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    };
    format!("AttributeError: '{t}' object has no attribute 'get'")
}

fn opt_str(v: Option<String>) -> Option<String> {
    v.filter(|s| !s.is_empty())
}
fn alnum_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^A-Za-z0-9]").expect("alnum regex"))
}

fn citation_key(r: &Record) -> String {
    let mut name = String::new();
    if let Some(first) = r.authors.first() {
        if !first.is_empty() {
            let after_comma = first.split(',').last().unwrap_or("");
            let token = after_comma.split_whitespace().last().unwrap_or("");
            let stripped: String = alnum_re().replace_all(token, "").to_string();
            name = stripped.chars().take(8).collect();
        }
    }
    let year = opt_str(r.year.clone()).unwrap_or_else(|| "0000".to_string());
    if name.is_empty() {
        name = "unknown".to_string();
    }
    let lowered = alnum_re().replace_all(&name, "").to_lowercase();
    let key = format!("{lowered}{year}");
    if key.is_empty() {
        format!("item{year}")
    } else {
        key
    }
}
fn author_last_first(author: &str) -> (String, String) {
    if let Some(i) = author.find(',') {
        return (
            author[..i].trim().to_string(),
            author[i + 1..].trim().to_string(),
        );
    }
    let parts: Vec<&str> = author.split(' ').collect();
    if parts.len() > 1 {
        (
            parts[parts.len() - 1].to_string(),
            parts[..parts.len() - 1].join(" "),
        )
    } else {
        (author.to_string(), String::new())
    }
}

fn bibtex_escape(text: &str) -> String {
    let mut out = text.replace('{', "\\{");
    out = out.replace('}', "\\}");
    out = out.replace('&', "\\&");
    out = out.replace('_', "\\_");
    out = out.replace('#', "\\#");
    out
}
fn record_from_object(
    result: &serde_json::Map<String, Value>,
    raw: Option<&Value>,
) -> Result<Record, String> {
    let date = extract_date(result)?;
    let doi = result.get("doi").and_then(|v| {
        if !is_truthy(v) {
            None
        } else {
            Some(py_strip(&py_value_repr(v)).to_string()).filter(|s| !s.is_empty())
        }
    });
    let title = first_text(result, &["title"]);
    let authors_raw = result
        .get("authors")
        .filter(|v| is_truthy(v))
        .or_else(|| result.get("author").filter(|v| is_truthy(v)));
    let url = result
        .get("url")
        .filter(|v| is_truthy(v))
        .map(py_value_repr);
    let venue = match result
        .get("venue")
        .filter(|v| is_truthy(v))
        .map(py_value_repr)
    {
        Some(v) => Some(v),
        None => venue_from_raw(raw)?,
    };
    let publisher = first_text(result, &["publisher"]);
    let abstract_text = match result
        .get("abstract")
        .filter(|v| is_truthy(v))
        .map(py_value_repr)
    {
        Some(v) => Some(v),
        None => abstract_from_raw(raw)?,
    };
    let id = result
        .get("id")
        .filter(|v| is_truthy(v))
        .map(py_value_repr);
    let kind = result
        .get("kind")
        .filter(|v| is_truthy(v))
        .map(py_value_repr);
    Ok(Record {
        title,
        authors: normalize_authors(authors_raw),
        year: date.0,
        month: date.1,
        day: date.2,
        doi,
        url,
        venue,
        publisher,
        abstract_text,
        id,
        kind,
    })
}
fn raise_mapped(err: String) -> PyErr {
    use pyo3::exceptions::PyIndexError;
    use pyo3::exceptions::PyKeyError;
    use pyo3::exceptions::PyTypeError;
    if let Some(msg) = err.strip_prefix("AttributeError: ") {
        PyAttributeError::new_err(msg.to_string())
    } else if let Some(msg) = err.strip_prefix("TypeError: ") {
        PyTypeError::new_err(msg.to_string())
    } else if let Some(key) = err.strip_prefix("KeyError: ") {
        match key.parse::<i64>() {
            Ok(n) => PyKeyError::new_err(n),
            Err(_) => PyKeyError::new_err(key.to_string()),
        }
    } else if let Some(msg) = err.strip_prefix("IndexError: ") {
        PyIndexError::new_err(msg.to_string())
    } else {
        PyValueError::new_err(err)
    }
}
fn to_bibtex_impl(records: &[Record]) -> String {
    if records.is_empty() {
        return String::new();
    }
    let mut out = Vec::new();
    for r in records {
        let kind = r.kind.clone().unwrap_or_else(|| "article-journal".to_string());
        let entry = if kind.contains("proceed") {
            "inproceedings"
        } else {
            "article"
        };
        let mut lines = vec![format!("@{entry}{{{},", citation_key(r))];
        if !r.authors.is_empty() {
            let joined = r
                .authors
                .iter()
                .map(|a| bibtex_escape(a))
                .collect::<Vec<_>>()
                .join(" and ");
            lines.push(format!("  author={},", bibtex_escape(&joined)));
        }
        if let Some(t) = opt_str(r.title.clone()) {
            lines.push(format!("  title={},", bibtex_escape(&t)));
        }
        if let Some(v) = opt_str(r.venue.clone()) {
            lines.push(format!("  journal={},", bibtex_escape(&v)));
        } else if let Some(p) = opt_str(r.publisher.clone()) {
            lines.push(format!("  publisher={},", bibtex_escape(&p)));
        }
        if let Some(y) = opt_str(r.year.clone()) {
            lines.push(format!("  year={y},"));
        }
        if let Some(d) = opt_str(r.doi.clone()) {
            lines.push(format!("  doi={},", bibtex_escape(&d)));
        }
        if let Some(u) = opt_str(r.url.clone()) {
            lines.push(format!("  url={},", bibtex_escape(&u)));
        }
        out.push(lines.join("\n") + "\n}");
    }
    out.join("\n\n")
}
fn ws_collapse_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s+").expect("ws regex"))
}

fn parse_py_int(s: &str) -> Result<i64, String> {
    // Mirrors `int(s)`: ASCII digits with optional sign; anything else
    // raises with CPython's exact message.
    let t = s.trim();
    let digits = t.strip_prefix('+').or_else(|| t.strip_prefix('-')).unwrap_or(t);
    if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
        if let Ok(n) = t.parse::<i64>() {
            return Ok(n);
        }
    }
    Err(format!(
        "ValueError: invalid literal for int() with base 10: {}",
        py_repr(s)
    ))
}

fn issued_value(r: &Record) -> Result<Option<serde_json::Map<String, Value>>, String> {
    let year = match opt_str(r.year.clone()) {
        None => return Ok(None),
        Some(y) => y,
    };
    let mut parts = vec![parse_py_int(&year)?];
    if let Some(m) = opt_str(r.month.clone()) {
        let head: String = m.chars().take(2).collect();
        parts.push(parse_py_int(&head)?);
    }
    let mut issued = serde_json::Map::new();
    issued.insert(
        "date-parts".to_string(),
        Value::Array(vec![Value::Array(
            parts.into_iter().map(Value::from).collect(),
        )]),
    );
    Ok(Some(issued))
}

fn to_csl_json_impl(records: &[Record]) -> Result<String, String> {
    if records.is_empty() {
        return Ok("[]".to_string());
    }
    let mut items = Vec::new();
    for r in records {
        let mut item = serde_json::Map::new();
        item.insert(
            "type".to_string(),
            Value::String(
                r.kind
                    .clone()
                    .unwrap_or_else(|| "article-journal".to_string()),
            ),
        );
        item.insert("ID".to_string(), Value::from(items.len() as i64 + 1));
        item.insert(
            "title".to_string(),
            Value::String(r.title.clone().unwrap_or_default()),
        );
        if let Some(d) = opt_str(r.doi.clone()) {
            item.insert("DOI".to_string(), Value::String(d));
        }
        if let Some(u) = opt_str(r.url.clone()) {
            item.insert("URL".to_string(), Value::String(u));
        }
        if !r.authors.is_empty() {
            let authors: Vec<Value> = r
                .authors
                .iter()
                .map(|a| {
                    let (fam, giv) = author_last_first(a);
                    let mut m = serde_json::Map::new();
                    m.insert("family".to_string(), Value::String(fam));
                    if !giv.is_empty() {
                        m.insert("given".to_string(), Value::String(giv));
                    }
                    Value::Object(m)
                })
                .collect();
            item.insert("author".to_string(), Value::Array(authors));
        }
        if let Some(v) = opt_str(r.venue.clone()) {
            item.insert(
                "container-title".to_string(),
                Value::Array(vec![Value::String(v)]),
            );
        }
        if let Some(p) = opt_str(r.publisher.clone()) {
            item.insert("publisher".to_string(), Value::String(p));
        }
        if r.year.is_some() {
            if let Some(issued) = issued_value(r)? {
                item.insert("issued".to_string(), Value::Object(issued));
            }
        }
        if let Some(a) = opt_str(r.abstract_text.clone()) {
            let collapsed = py_strip(ws_collapse_re().replace_all(&a, " ").as_ref()).to_string();
            item.insert("abstract".to_string(), Value::String(collapsed));
        }
        items.push(Value::Object(item));
    }
    serde_json::to_string_pretty(&Value::Array(items)).map_err(|e| e.to_string())
}
fn apa_author(author: &str) -> String {
    let (fam, giv) = author_last_first(author);
    let initials: Vec<String> = giv
        .split_whitespace()
        .filter(|p| !p.is_empty())
        .map(|p| format!("{}.", p.chars().next().unwrap_or('?')))
        .collect();
    if initials.is_empty() {
        fam
    } else {
        format!("{fam}, {}", initials.join(" "))
    }
}

fn apa_approx_impl(records: &[Record]) -> String {
    let mut lines = Vec::new();
    for r in records {
        let author_part = r
            .authors
            .iter()
            .map(|a| apa_author(a))
            .collect::<Vec<_>>()
            .join(", ");
        let year = opt_str(r.year.clone())
            .map(|y| format!("({y})"))
            .unwrap_or_default();
        let title = r.title.clone().unwrap_or_default();
        let venue = opt_str(r.venue.clone())
            .map(|v| format!("*{v}*"))
            .unwrap_or_default();
        let link = if let Some(d) = opt_str(r.doi.clone()) {
            format!(" https://doi.org/{d}")
        } else if let Some(u) = opt_str(r.url.clone()) {
            format!(" {u}")
        } else {
            String::new()
        };
        lines.push(
            py_strip(&format!("{author_part} {year}. {title}. {venue}{link}")).to_string(),
        );
    }
    lines.join("\n\n")
}

fn mla_approx_impl(records: &[Record]) -> String {
    let mut lines = Vec::new();
    for r in records {
        let author = r.authors.join(", ");
        let author = author.trim_end_matches('.').to_string();
        let title = opt_str(r.title.clone())
            .map(|t| format!("\"{t}\""))
            .unwrap_or_default();
        let venue = opt_str(r.venue.clone())
            .map(|v| format!("*{v}*"))
            .unwrap_or_default();
        let year = r.year.clone().unwrap_or_default();
        let link = if let Some(d) = opt_str(r.doi.clone()) {
            format!(" doi.org/{d}")
        } else if let Some(u) = opt_str(r.url.clone()) {
            format!(" {u}")
        } else {
            String::new()
        };
        lines.push(
            py_strip(&format!("{author}. {title}. {venue}, {year}.{link}")).to_string(),
        );
    }
    lines.join("\n\n")
}
#[pyfunction]
pub fn citation_record_from_json(
    py: Python,
    result_json: &str,
) -> PyResult<BibliographicRecord> {
    let parsed: Value = serde_json::from_str(result_json)
        .map_err(|e: serde_json::Error| PyValueError::new_err(e.to_string()))?;
    let result = parsed.as_object().ok_or_else(|| {
        PyValueError::new_err("citation record needs a JSON object")
    })?;
    let raw = result.get("raw");
    let rec = record_from_object(result, raw).map_err(raise_mapped)?;
    let empty = PyDict::new(py).into_any().unbind();
    Ok(BibliographicRecord {
        title: rec.title,
        authors: rec.authors,
        year: rec.year,
        month: rec.month,
        day: rec.day,
        doi: rec.doi,
        url: rec.url,
        venue: rec.venue,
        publisher: rec.publisher,
        abstract_text: rec.abstract_text,
        extra: empty,
        id: rec.id,
        kind: rec.kind,
    })
}

fn borrow_records(records: Vec<Bound<BibliographicRecord>>) -> Vec<Record> {
    records.iter().map(|r| r.borrow().as_record()).collect()
}

#[pyfunction]
pub fn cite_bibtex(records: Vec<Bound<BibliographicRecord>>) -> String {
    to_bibtex_impl(&borrow_records(records))
}

#[pyfunction]
pub fn cite_csl_json(records: Vec<Bound<BibliographicRecord>>) -> PyResult<String> {
    to_csl_json_impl(&borrow_records(records)).map_err(raise_mapped)
}

#[pyfunction]
pub fn cite_apa_approx(records: Vec<Bound<BibliographicRecord>>) -> String {
    apa_approx_impl(&borrow_records(records))
}

#[pyfunction]
pub fn cite_mla_approx(records: Vec<Bound<BibliographicRecord>>) -> String {
    mla_approx_impl(&borrow_records(records))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec() -> Record {
        Record {
            title: Some("A & B _C #D {E}".to_string()),
            authors: vec!["Doe, Jane".to_string(), "John Smith".to_string()],
            year: Some("2020".to_string()),
            month: None,
            day: None,
            doi: Some("10.1/xyz".to_string()),
            url: Some("https://example.com/p".to_string()),
            venue: Some("J Test".to_string()),
            publisher: None,
            abstract_text: None,
            id: None,
            kind: None,
        }
    }

    #[test]
    fn bibtex_shape_and_escapes() {
        let out = to_bibtex_impl(&[rec()]);
        assert!(out.starts_with("@article{jane2020,"));
        assert!(out.contains("author=Doe, Jane and John Smith,"));
        assert!(out.contains(r"title=A \& B \_C \#D \{E\},"));
    }

    #[test]
    fn csl_shape_and_key_order() {
        let out = to_csl_json_impl(&[rec()]).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        let item = &v[0];
        assert_eq!(item["ID"], 1);
        assert_eq!(item["DOI"], "10.1/xyz");
        assert_eq!(item["author"][0]["family"], "Doe");
        assert_eq!(item["author"][1]["family"], "Smith");
        assert_eq!(item["author"][1]["given"], "John");
        // Insertion order preserved (type, ID, title first).
        let keys: Vec<&str> = item.as_object().unwrap().keys().map(|s| s.as_str()).collect();
        assert_eq!(&keys[..3], &["type", "ID", "title"]);
    }

    #[test]
    fn apa_mla_approx_shapes() {
        let apa = apa_approx_impl(&[rec()]);
        assert!(apa.contains("Doe, J., Smith, J."));
        assert!(apa.contains("(2020)."));
        assert!(apa.contains("*J Test*"));
        let mla = mla_approx_impl(&[rec()]);
        assert!(mla.contains("\"A & B _C #D {E}\""));
    }

    #[test]
    fn date_extraction_shapes() {
        let mut m = serde_json::Map::new();
        m.insert(
            "published".to_string(),
            Value::String("2011-03-04".to_string()),
        );
        assert_eq!(
            extract_date(&m).unwrap(),
            (
                Some("2011".to_string()),
                Some("03".to_string()),
                Some("04".to_string())
            )
        );
        let mut m2 = serde_json::Map::new();
        m2.insert(
            "published".to_string(),
            serde_json::json!({"date-parts": [[2020, 9, 24]]}),
        );
        assert_eq!(
            extract_date(&m2).unwrap(),
            (
                Some("2020".to_string()),
                Some("9".to_string()),
                Some("24".to_string())
            )
        );
    }
}

#[pyfunction]
pub fn cite_venue_from_raw(raw: Option<&str>) -> PyResult<Option<String>> {
    let value = raw.map(|s| Value::String(s.to_string()));
    venue_from_raw(value.as_ref()).map_err(raise_mapped)
}

#[pyfunction]
pub fn cite_abstract_from_raw(raw: Option<&str>) -> PyResult<Option<String>> {
    let value = raw.map(|s| Value::String(s.to_string()));
    abstract_from_raw(value.as_ref()).map_err(raise_mapped)
}
