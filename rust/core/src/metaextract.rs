//! HTML metadata extraction through the vendored `meta_oxide` C ABI.
//!
//! Pure port of legacy `src/metaextract.rs`. The FFI calls are wrapped in two private helpers with
//! explicit safety contracts; all public functions return deterministic JSON strings.

use std::borrow::Cow;
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

use meta_oxide::ffi;
use serde_json::{Map, Value};

// SAFETY: these are meta_oxide C ABI pointers. Every call site passes live NUL-terminated
// strings, accepts NULL for the optional base URL, and frees the returned string exactly once.
type SectionFn = unsafe extern "C" fn(*const c_char, *const c_char) -> *mut c_char;
// SAFETY: same C ABI contract as `SectionFn`, without the optional base URL argument.
type NoBaseSectionFn = unsafe extern "C" fn(*const c_char) -> *mut c_char;

/// NUL cannot cross a C string: the HTML tokenizer replaces NUL with U+FFFD anyway.
fn sanitize_html(html: &str) -> Cow<'_, str> {
    if html.contains('\0') {
        Cow::Owned(html.replace('\0', "\u{FFFD}"))
    } else {
        Cow::Borrowed(html)
    }
}

/// A NUL-bearing base URL is unusable; pass NULL instead of a truncated prefix.
fn sanitize_base(base: Option<&str>) -> Option<&str> {
    match base {
        Some(b) if b.contains('\0') => None,
        b => b,
    }
}

/// Calls one C-ABI section extractor; `None` on extractor/NUL/UTF-8/JSON failure.
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
    // SAFETY: `c_html` is a live NUL-terminated string for the duration of the call; `base_ptr`
    // is either a live NUL-terminated string or NULL, which the callee accepts. The returned
    // pointer is freed exactly once below, before any early return.
    let raw = unsafe { f(c_html.as_ptr(), base_ptr) };
    if raw.is_null() {
        return None;
    }
    // SAFETY: `raw` is non-null and points to a NUL-terminated C string owned by meta_oxide.
    let text_result = unsafe { CStr::from_ptr(raw) }.to_str().map(str::to_owned);
    // SAFETY: `raw` came from meta_oxide and has not been freed yet.
    unsafe { ffi::meta_oxide_string_free(raw) };
    serde_json::from_str(&text_result.ok()?).ok()
}

/// Dublin Core takes no base URL.
fn section_no_base(f: NoBaseSectionFn, html: &str) -> Option<Value> {
    let html = sanitize_html(html);
    let c_html = CString::new(html.as_ref()).ok()?;
    // SAFETY: `c_html` is a live NUL-terminated string for the duration of the call. The returned
    // pointer is freed exactly once below, before any early return.
    let raw = unsafe { f(c_html.as_ptr()) };
    if raw.is_null() {
        return None;
    }
    // SAFETY: `raw` is non-null and points to a NUL-terminated C string owned by meta_oxide.
    let text_result = unsafe { CStr::from_ptr(raw) }.to_str().map(str::to_owned);
    // SAFETY: `raw` came from meta_oxide and has not been freed yet.
    unsafe { ffi::meta_oxide_string_free(raw) };
    serde_json::from_str(&text_result.ok()?).ok()
}

/// Drops null values and empty arrays recursively, preserving empty objects.
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
        Value::Array(items) => Value::Array(items.into_iter().map(sparse).collect()),
        v => v,
    }
}

/// Untags one microformats `PropertyValue`.
fn mf_value(v: Value) -> Value {
    match v {
        Value::Object(mut m) if m.len() == 1 => match m.remove("Nested") {
            Some(inner) => mf_item(inner),
            None => Value::Object(m),
        },
        v => v,
    }
}

/// Normalizes one combined-parser microformat item to the Python-facing shape.
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
                    Value::Array(a) => Value::Array(a.into_iter().map(mf_value).collect()),
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
    for (k, v) in m {
        if v.is_null() {
            continue;
        }
        out.insert(k, v);
    }
    Value::Object(out)
}

/// Normalizes one microdata/RDFa item, unwrapping singular property arrays.
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
                let first = a.pop().expect("array length was checked");
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

/// Maps serde's `Json`/`Xml` oEmbed formats to `json`/`xml`.
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

fn is_nonempty_array(v: &Value) -> bool {
    matches!(v, Value::Array(a) if !a.is_empty())
}

fn is_nonempty_object(v: &Value) -> bool {
    matches!(v, Value::Object(m) if !m.is_empty())
}

/// Extracts all metadata sections present in an HTML document.
///
/// Sections follow the legacy presence rules: `meta`, `opengraph`, `twitter`, and `dublin_core`
/// appear when their extractor succeeds; others only when non-empty.
///
/// # Examples
///
/// ```
/// use gossamer::metaextract::meta_extract_all;
///
/// let json = meta_extract_all("<title>Demo</title>", Some("https://example.com"));
/// assert!(json.contains("\"title\":\"Demo\""));
/// ```
pub fn meta_extract_all(html: &str, base_url: Option<&str>) -> String {
    let mut out = Map::new();
    if let Some(v) = section(ffi::meta_oxide_extract_meta, html, base_url) {
        out.insert("meta".to_string(), sparse(v));
    }
    if let Some(v) = section(ffi::meta_oxide_extract_open_graph, html, base_url) {
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
    if let Some(Value::Array(items)) = section(ffi::meta_oxide_extract_microdata, html, base_url) {
        let mapped: Vec<Value> = items
            .into_iter()
            .map(|e| flat_item(e, &["type", "id"]))
            .collect();
        if !mapped.is_empty() {
            out.insert("microdata".to_string(), Value::Array(mapped));
        }
    }
    let mf = section(ffi::meta_oxide_extract_microformats, html, base_url).or_else(|| {
        if base_url.is_some() {
            section(ffi::meta_oxide_extract_microformats, html, None)
        } else {
            None
        }
    });
    if let Some(v) = mf
        && let Value::Object(formats) = sparse(v)
    {
        let mapped: Map<String, Value> = formats
            .into_iter()
            .map(|(k, items)| {
                let mapped = match items {
                    Value::Array(a) => Value::Array(a.into_iter().map(mf_item).collect()),
                    other => other,
                };
                (k, mapped)
            })
            .collect();
        if !mapped.is_empty() {
            out.insert("microformats".to_string(), Value::Object(mapped));
        }
    }
    if let Some(Value::Array(items)) = section(ffi::meta_oxide_extract_rdfa, html, base_url) {
        let mapped: Vec<Value> = items
            .into_iter()
            .map(|e| flat_item(e, &["type", "about", "vocab"]))
            .collect();
        if !mapped.is_empty() {
            out.insert("rdfa".to_string(), Value::Array(mapped));
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
    if let Some(v) = section(ffi::meta_oxide_extract_oembed, html, base_url)
        && let Value::Object(mut m) = sparse(v)
    {
        for key in ["json_endpoints", "xml_endpoints"] {
            if let Some(Value::Array(eps)) = m.remove(key) {
                let fixed: Vec<Value> = eps
                    .into_iter()
                    .map(|e| match e {
                        Value::Object(o) => Value::Object(fix_oembed_endpoint(o)),
                        other => other,
                    })
                    .collect();
                m.insert(key.to_string(), Value::Array(fixed));
            }
        }
        let has = m.get("json_endpoints").is_some_and(is_nonempty_array)
            || m.get("xml_endpoints").is_some_and(is_nonempty_array);
        if has {
            out.insert("oembed".to_string(), Value::Object(m));
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

/// Extracts the `meta` section only, returning `{}` on extractor failure.
///
/// # Examples
///
/// ```
/// use gossamer::metaextract::meta_extract_meta;
///
/// let json = meta_extract_meta("<meta name=\"description\" content=\"Demo\">", None);
/// assert!(json.contains("\"description\":\"Demo\""));
/// ```
pub fn meta_extract_meta(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_meta, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Object(Map::new()))
        .to_string()
}

/// Extracts Open Graph metadata only, returning `{}` on extractor failure.
///
/// # Examples
///
/// ```
/// use gossamer::metaextract::meta_extract_opengraph;
///
/// let json = meta_extract_opengraph("<meta property=\"og:title\" content=\"Demo\">", None);
/// assert!(json.contains("\"title\":\"Demo\""));
/// ```
pub fn meta_extract_opengraph(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_open_graph, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Object(Map::new()))
        .to_string()
}

/// Extracts Twitter card metadata only, returning `{}` on extractor failure.
///
/// # Examples
///
/// ```
/// use gossamer::metaextract::meta_extract_twitter;
///
/// let json = meta_extract_twitter("<meta name=\"twitter:card\" content=\"summary\">", None);
/// assert!(json.contains("\"card\":\"summary\""));
/// ```
pub fn meta_extract_twitter(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_twitter, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Object(Map::new()))
        .to_string()
}

/// Extracts JSON-LD blocks only, returning `[]` on extractor failure.
///
/// # Examples
///
/// ```
/// use gossamer::metaextract::meta_extract_jsonld;
///
/// let json = meta_extract_jsonld("<script type=\"application/ld+json\">{\"a\":1}</script>", None);
/// assert_eq!(json, "[{\"a\":1}]");
/// ```
pub fn meta_extract_jsonld(html: &str, base_url: Option<&str>) -> String {
    section(ffi::meta_oxide_extract_json_ld, html, base_url)
        .map(sparse)
        .unwrap_or(Value::Array(Vec::new()))
        .to_string()
}


/// Merge meta-oxide HTML metadata into a base document metadata payload.
pub fn merge_into_document_metadata(html_meta: &Value, base_metadata: &Value) -> Value {
    let mut merged = base_metadata.as_object().cloned().unwrap_or_default();
    const FIELD_COPIES: [(&str, &str, &str); 16] = [
        ("meta", "description", "description"),
        ("meta", "canonical", "canonical"),
        ("meta", "keywords", "keywords"),
        ("meta", "language", "language"),
        ("meta", "robots", "robots"),
        ("opengraph", "title", "og_title"),
        ("opengraph", "type", "og_type"),
        ("opengraph", "image", "og_image"),
        ("opengraph", "description", "og_description"),
        ("opengraph", "site_name", "og_site_name"),
        ("opengraph", "url", "og_url"),
        ("twitter", "card", "twitter_card"),
        ("twitter", "title", "twitter_title"),
        ("twitter", "description", "twitter_description"),
        ("twitter", "image", "twitter_image"),
        ("twitter", "site", "twitter_site"),
    ];
    let section = |name: &str| html_meta.get(name).and_then(Value::as_object);
    for (from, key, target) in FIELD_COPIES {
        let value = section(from).and_then(|map| map.get(key));
        if value.is_some_and(|v| !v.is_null() && v.as_str() != Some("") && !matches!(v, Value::Bool(false)) && !matches!(v, Value::Number(n) if n.as_f64() == Some(0.0))) {
            merged.insert(target.to_string(), value.cloned().unwrap_or(Value::Null));
        }
    }
    let std_meta = section("meta");
    let base_title = merged.get("title").cloned().unwrap_or(Value::Null);
    let title_unset = base_title.is_null() || base_title.as_str() == Some("");
    if title_unset && std_meta.and_then(|m| m.get("title")).is_some_and(|v| !v.is_null() && v.as_str() != Some("")) {
        merged.insert("title".to_string(), std_meta.and_then(|m| m.get("title")).cloned().unwrap_or(Value::Null));
    }
    let images = section("opengraph").and_then(|m| m.get("images"));
    if images.is_some_and(Value::is_array) && images.is_some_and(|v| v.as_array().is_some_and(|a| !a.is_empty())) {
        merged.insert("og_images".to_string(), images.cloned().unwrap_or(Value::Null));
    }
    for name in ["jsonld", "rel_links", "dublin_core", "microformats", "microdata", "rdfa", "manifest"] {
        let value = html_meta.get(name);
        if value.is_some_and(|v| !v.is_null() && v.as_str() != Some("") && !matches!(v, Value::Bool(false)) && !matches!(v, Value::Number(n) if n.as_f64() == Some(0.0)) && !matches!(v, Value::Array(a) if a.is_empty()) && !matches!(v, Value::Object(m) if m.is_empty())) {
            merged.insert(name.to_string(), value.cloned().unwrap_or(Value::Null));
        }
    }
    Value::Object(merged)
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
            serde_json::from_str(&meta_extract_all(PAGE, Some("https://example.com"))).unwrap();
        assert_eq!(v["meta"]["title"], "Test Page");
        assert_eq!(v["meta"]["description"], "A description");
        assert_eq!(v["meta"]["canonical"], "https://example.com/canonical");
        assert_eq!(v["opengraph"]["title"], "OG Title");
        assert_eq!(v["twitter"]["card"], "summary");
        assert_eq!(v["jsonld"][0]["headline"], "Hi");
        assert!(v.get("microformats").is_some());
        assert!(v.get("rel_links").is_some());
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
        let m: Value = serde_json::from_str(&meta_extract_meta("<html></html>", None)).unwrap();
        assert_eq!(m, Value::Object(Map::new()));
        let j: Value = serde_json::from_str(&meta_extract_jsonld("<html></html>", None)).unwrap();
        assert_eq!(j, Value::Array(Vec::new()));
        let v: Value =
            serde_json::from_str(&meta_extract_all("a\0b<title>T</title>", None)).unwrap();
        assert_eq!(v["meta"]["title"], "T");
    }
}
