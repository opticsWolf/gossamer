//! HTML metadata extraction via the `meta_oxide` Rust crate (M22).
//!
//! Replaces the `meta-oxide` Python bridge
//! (`meta-oxide @ git+...`, which made gossamer un-publishable to
//! PyPI). Same extraction code (fork rev `81bdb53`), reached through
//! its C-ABI section functions; outputs pass through `sparse()` so
//! they match the Python `to_py_dict` shape (absent `None`s, no empty
//! vecs). Two documented refinements vs the old bridge: `twitter`
//! uses the with-fallback mapping everywhere (as `extract_all`
//! always did — the crate's own FFI agrees), and `microformats`
//! uses the combined parser (any `h-*` class, the crate's C-API
//! shape) instead of the 9 named-field extractors, whose modules are
//! not public API. NUL bytes: HTML NULs are pre-replaced with
//! U+FFFD (what the HTML tokenizer does with them anyway); a
//! NUL-bearing `base_url` is passed as NULL.

use std::borrow::Cow;
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

use scraper::Selector;
use serde_json::{Map, Value};

use meta_oxide::ffi;

type SectionFn = unsafe extern "C" fn(*const c_char, *const c_char) -> *mut c_char;

/// NUL cannot cross a C string: the HTML tokenizer replaces NUL with
/// U+FFFD anyway, so pre-replacing is output-identical.
fn sanitize_html(html: &str) -> Cow<'_, str> {
    if html.contains('\0') {
        Cow::Owned(html.replace('\0', "\u{FFFD}"))
    } else {
        Cow::Borrowed(html)
    }
}

/// A NUL-bearing base URL is unusable (and unparseable as a URL);
/// pass NULL instead of a silently-truncated prefix.
fn sanitize_base(base: Option<&str>) -> Option<&str> {
    match base {
        Some(b) if b.contains('\0') => None,
        b => b,
    }
}

/// Call one C-ABI section extractor; `None` on any failure
/// (extractor error, NUL, invalid UTF-8, bad JSON).
fn section(f: SectionFn, html: &str, base: Option<&str>) -> Option<Value> {
    let html = sanitize_html(html);
    let base = sanitize_base(base);
    let c_html = CString::new(html.as_ref()).ok()?;
    let c_base = match base {
        Some(b) => Some(CString::new(b).ok()?),
        None => None,
    };
    let base_ptr = c_base
        .as_ref()
        .map(|c| c.as_ptr())
        .unwrap_or(std::ptr::null());
    // SAFETY: both pointers are valid NUL-terminated strings (or NULL
    // for base, which the callee accepts); the returned pointer is
    // freed exactly once below.
    let raw = unsafe { f(c_html.as_ptr(), base_ptr) };
    if raw.is_null() {
        return None;
    }
    let text = unsafe { CStr::from_ptr(raw) }.to_str().ok()?.to_owned();
    unsafe { ffi::meta_oxide_string_free(raw) };
    serde_json::from_str(&text).ok()
}

/// Dublin Core takes no base URL.
fn section_no_base(
    f: unsafe extern "C" fn(*const c_char) -> *mut c_char,
    html: &str,
) -> Option<Value> {
    let html = sanitize_html(html);
    let c_html = CString::new(html.as_ref()).ok()?;
    // SAFETY: as in `section`.
    let raw = unsafe { f(c_html.as_ptr()) };
    if raw.is_null() {
        return None;
    }
    let text = unsafe { CStr::from_ptr(raw) }.to_str().ok()?.to_owned();
    unsafe { ffi::meta_oxide_string_free(raw) };
    serde_json::from_str(&text).ok()
}

/// Mirror the Python `to_py_dict` shape: drop `null` object values
/// and empty arrays (plain-`Vec` fields are only set when non-empty),
/// recurse everywhere, keep empty objects (unconditionally-set
/// sections stay present as `{}`).
fn sparse(v: Value) -> Value {
    match v {
        Value::Object(map) => Value::Object(
            map.into_iter()
                .filter_map(|(k, v)| {
                    let s = sparse(v);
                    match &s {
                        Value::Null => None,
                        Value::Array(a) if a.is_empty() => None,
                        _ => Some((k, s)),
                    }
                })
                .collect(),
        ),
        Value::Array(items) => {
            Value::Array(items.into_iter().map(sparse).collect())
        }
        v => v,
    }
}

/// Untag one microformats `PropertyValue`: `Text`/`Url` already
/// serialize as bare strings; `{"Nested": obj}` becomes the
/// normalized item (mirrors `to_python`).
fn mf_value(v: Value) -> Value {
    match v {
        Value::Object(mut m) if m.len() == 1 => match m.remove("Nested") {
            Some(inner) => mf_item(inner),
            None => Value::Object(m),
        },
        v => v,
    }
}

/// Normalize one combined-parser microformat item to the
/// `to_py_dict` shape: `type_` back to `type`, property values
/// untagged, children normalized.
fn mf_item(v: Value) -> Value {
    let Value::Object(mut m) = v else {
        return v;
    };
    let mut out = Map::new();
    if let Some(t) = m.remove("type_") {
        out.insert("type".to_string(), t);
    }
    if let Some(Value::Object(props)) = m.remove("properties") {
        let mapped: Map<String, Value> = props
            .into_iter()
            .map(|(k, vals)| {
                let mapped = match vals {
                    Value::Array(a) => {
                        Value::Array(a.into_iter().map(mf_value).collect())
                    }
                    other => mf_value(other),
                };
                (k, mapped)
            })
            .collect();
        out.insert("properties".to_string(), Value::Object(mapped));
    }
    if let Some(Value::Array(kids)) = m.remove("children") {
        out.insert(
            "children".to_string(),
            Value::Array(kids.into_iter().map(mf_item).collect()),
        );
    }
    // `children: None` serializes as null but `to_py_dict` skips it;
    // anything else unexpected passes through untouched.
    for (k, v) in m {
        if v.is_null() {
            continue;
        }
        out.insert(k, v);
    }
    Value::Object(out)
}

/// Normalize one microdata/RDFa item: scalar header keys kept
/// verbatim, flattened property arrays unwrapped when singular
/// (mirrors `to_py_dict`; nested-item and typed-literal serde
/// shapes already match, so only the single/multi rule applies).
fn flat_item(v: Value, scalar_keys: &[&str]) -> Value {
    let Value::Object(m) = v else {
        return v;
    };
    let mut out = Map::new();
    for (k, vals) in m {
        if scalar_keys.contains(&k.as_str()) {
            out.insert(k, vals);
            continue;
        }
        let unwrapped = match vals {
            Value::Array(mut a) if a.len() == 1 => {
                let first = a.pop().unwrap();
                match first {
                    Value::Object(_) => flat_item(first, scalar_keys),
                    other => other,
                }
            }
            Value::Array(a) => Value::Array(
                a.into_iter()
                    .map(|e| match e {
                        Value::Object(_) => flat_item(e, scalar_keys),
                        other => other,
                    })
                    .collect(),
            ),
            other => other,
        };
        out.insert(k, unwrapped);
    }
    Value::Object(out)
}

/// `format: Json|Xml` (serde) vs `json|xml` (`to_py_dict`).
fn fix_oembed_endpoint(mut ep: Map<String, Value>) -> Map<String, Value> {
    if let Some(Value::String(f)) = ep.remove("format") {
        let lower = match f.as_str() {
            "Json" => "json",
            "Xml" => "xml",
            other => other,
        }
        .to_string();
        ep.insert("format".to_string(), Value::String(lower));
    }
    ep
}

/// Upstream infinite-recursion guard: an element carrying BOTH
/// `property` and `typeof` sends `extract_item_with_context` +
/// `extract_property_value_with_context` into unbounded mutual
/// recursion (process-killing stack overflow in the old bridge too).
/// Any such element is always reached by the RDFa walk (it bears
/// `typeof`, so it is a root or nested under one), hence
/// document-wide presence ⟺ crash. Screen cheaply, confirm with
/// one selector parse, and skip the section when present.
fn rdfa_cycle_present(html: &str) -> bool {
    let lower = html.to_lowercase();
    if !(lower.contains("typeof") && lower.contains("property")) {
        return false;
    }
    let Ok(doc) = std::panic::catch_unwind(|| scraper::Html::parse_document(html))
    else {
        return true; // unparseable: stay safe
    };
    let Ok(sel) = Selector::parse("[typeof][property]") else {
        return false; // static selector: unreachable
    };
    doc.select(&sel).next().is_some()
}

fn is_nonempty_array(v: &Value) -> bool {
    matches!(v, Value::Array(a) if !a.is_empty())
}

fn is_nonempty_object(v: &Value) -> bool {
    matches!(v, Value::Object(m) if !m.is_empty())
}

/// `meta_extractor.extract_all`: the 11 sections with the Python
/// `extract_all` presence rules (meta/opengraph/twitter/dublin_core
/// whenever their extractor succeeds; the rest only when non-empty;
/// manifest only with an `href`; oembed only with endpoints).
pub fn meta_extract_all_impl(html: &str, base_url: Option<&str>) -> String {
    let mut out = Map::new();
    if let Some(v) = section(ffi::meta_oxide_extract_meta, html, base_url) {
        out.insert("meta".to_string(), sparse(v));
    }
    if let Some(v) = section(ffi::meta_oxide_extract_open_graph, html, base_url)
    {
        out.insert("opengraph".to_string(), sparse(v));
    }
    if let Some(v) = section(ffi::meta_oxide_extract_twitter, html, base_url) {
        out.insert("twitter".to_string(), sparse(v));
    }
    if let Some(v) = section(ffi::meta_oxide_extract_json_ld, html, base_url) {
        let s = sparse(v);
        if is_nonempty_array(&s) {
            out.insert("jsonld".to_string(), s);
        }
    }
    if let Some(v) = section(ffi::meta_oxide_extract_microdata, html, base_url)
    {
        // Bespoke mapper (no generic sparse: it would drop a
        // hypothetical `Some([])` that `to_py_dict` keeps).
        if let Value::Array(items) = v {
            let mapped: Vec<Value> = items
                .into_iter()
                .map(|e| flat_item(e, &["type", "id"]))
                .collect();
            if !mapped.is_empty() {
                out.insert("microdata".to_string(), Value::Array(mapped));
            }
        }
    }
    // The combined parser aborts wholesale on an unresolvable URL
    // with `Some(base)` (even `Some("")`), while the per-format
    // extractors keep the raw URL. Retry baseless to reproduce their
    // tolerance; a residual corner (unparseable absolute URL with a
    // good base) stays absent instead of partial.
    let mf = section(ffi::meta_oxide_extract_microformats, html, base_url)
        .or_else(|| {
            if base_url.is_some() {
                section(ffi::meta_oxide_extract_microformats, html, None)
            } else {
                None
            }
        });
    if let Some(v) = mf {
        if let Value::Object(formats) = sparse(v) {
            let mapped: Map<String, Value> = formats
                .into_iter()
                .map(|(k, items)| {
                    let mapped = match items {
                        Value::Array(a) => Value::Array(
                            a.into_iter().map(mf_item).collect(),
                        ),
                        other => other,
                    };
                    (k, mapped)
                })
                .collect();
            if !mapped.is_empty() {
                out.insert("microformats".to_string(), Value::Object(mapped));
            }
        }
    }
    if !rdfa_cycle_present(html) {
        if let Some(Value::Array(items)) =
            section(ffi::meta_oxide_extract_rdfa, html, base_url)
        {
            let mapped: Vec<Value> = items
                .into_iter()
                .map(|e| flat_item(e, &["type", "about", "vocab"]))
                .collect();
            if !mapped.is_empty() {
                out.insert("rdfa".to_string(), Value::Array(mapped));
            }
        }
    }
    if let Some(v) = section_no_base(ffi::meta_oxide_extract_dublin_core, html) {
        out.insert("dublin_core".to_string(), sparse(v));
    }
    if let Some(v) = section(ffi::meta_oxide_extract_manifest, html, base_url) {
        let s = sparse(v);
        if s.get("href").is_some() {
            out.insert("manifest".to_string(), s);
        }
    }
    if let Some(v) = section(ffi::meta_oxide_extract_oembed, html, base_url) {
        if let Value::Object(mut m) = sparse(v) {
            for key in ["json_endpoints", "xml_endpoints"] {
                if let Some(Value::Array(eps)) = m.remove(key) {
                    let fixed: Vec<Value> = eps
                        .into_iter()
                        .map(|e| match e {
                            Value::Object(o) => {
                                Value::Object(fix_oembed_endpoint(o))
                            }
                            other => other,
                        })
                        .collect();
                    m.insert(key.to_string(), Value::Array(fixed));
                }
            }
            let has = m
                .get("json_endpoints")
                .is_some_and(is_nonempty_array)
                || m.get("xml_endpoints").is_some_and(is_nonempty_array);
            if has {
                out.insert("oembed".to_string(), Value::Object(m));
            }
        }
    }
    if let Some(v) = section(ffi::meta_oxide_extract_rel_links, html, base_url) {
        let s = sparse(v);
        if is_nonempty_object(&s) {
            out.insert("rel_links".to_string(), s);
        }
    }
    Value::Object(out).to_string()
}

/// Single-section kernels; `{}`/`[]` on extractor failure, matching
/// the `meta_extractor` soft-fail contract.
pub fn meta_extract_meta_impl(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_meta, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Object(Map::new()))
        .to_string()
}

pub fn meta_extract_opengraph_impl(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_open_graph, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Object(Map::new()))
        .to_string()
}

pub fn meta_extract_twitter_impl(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_twitter, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Object(Map::new()))
        .to_string()
}

pub fn meta_extract_jsonld_impl(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_json_ld, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Array(Vec::new()))
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    const PAGE: &str = r#"<!DOCTYPE html><html><head>
<title>Test Page</title>
<meta name="description" content="A description">
<link rel="canonical" href="https://example.com/canonical">
<meta property="og:title" content="OG Title">
<meta property="og:type" content="article">
<meta name="twitter:card" content="summary">
<script type="application/ld+json">{"@type":"Article","headline":"Hi"}</script>
</head><body><div class="h-card"><span class="p-name">Jane</span></div></body></html>"#;

    #[test]
    fn all_sections_present() {
        let v: Value =
            serde_json::from_str(&meta_extract_all_impl(PAGE, Some("https://example.com")))
                .unwrap();
        assert_eq!(v["meta"]["title"], "Test Page");
        assert_eq!(v["meta"]["description"], "A description");
        assert_eq!(v["meta"]["canonical"], "https://example.com/canonical");
        assert_eq!(v["opengraph"]["title"], "OG Title");
        assert_eq!(v["twitter"]["card"], "summary");
        assert_eq!(v["jsonld"][0]["headline"], "Hi");
        assert!(v.get("microformats").is_some());
        assert!(v.get("rel_links").is_some());
        // No nulls leak through sparse().
        fn no_nulls(v: &Value) {
            match v {
                Value::Null => panic!("null leaked"),
                Value::Object(m) => m.values().for_each(no_nulls),
                Value::Array(a) => a.iter().for_each(no_nulls),
                _ => {}
            }
        }
        no_nulls(&v);
    }

    #[test]
    fn singles_and_empty() {
        let m: Value =
            serde_json::from_str(&meta_extract_meta_impl("<html></html>", None)).unwrap();
        assert_eq!(m, Value::Object(Map::new()));
        let j: Value =
            serde_json::from_str(&meta_extract_jsonld_impl("<html></html>", None)).unwrap();
        assert_eq!(j, Value::Array(Vec::new()));
        // NULs never reach the C boundary.
        let v: Value =
            serde_json::from_str(&meta_extract_all_impl("a\0b<title>T</title>", None))
                .unwrap();
        assert_eq!(v["meta"]["title"], "T");
    }
}

use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_all(html: &str, base_url: Option<String>) -> String {
    meta_extract_all_impl(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_meta(html: &str, base_url: Option<String>) -> String {
    meta_extract_meta_impl(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_opengraph(html: &str, base_url: Option<String>) -> String {
    meta_extract_opengraph_impl(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_twitter(html: &str, base_url: Option<String>) -> String {
    meta_extract_twitter_impl(html, base_url.as_deref())
}

#[pyfunction]
#[pyo3(signature = (html, base_url=None))]
pub fn meta_extract_jsonld(html: &str, base_url: Option<String>) -> String {
    meta_extract_jsonld_impl(html, base_url.as_deref())
}
