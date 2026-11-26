//! Pure URL preparation shared by document-facing tools.
//!
//! The Python layer turns rejected URLs into recoverable JSON records. This module owns the
//! validation decision and error classification; the binding/front door owns presentation.

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;

use serde_json::{Map, Value, json};

use crate::ssrf::check_url;
use crate::urls::normalize_url;

/// Error class to report to callers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UrlPrepErrorKind {
    /// `normalize_url` rejected the raw input.
    Value,
    /// SSRF/public-host validation rejected the normalized URL.
    SsrfBlocked,
}

/// URL preparation failure with a stable Python-facing class name.
#[derive(Debug)]
pub struct UrlPrepError {
    kind: UrlPrepErrorKind,
    raw: String,
    message: String,
    backtrace: Backtrace,
}

impl UrlPrepError {
    /// Creates a preparation error.
    pub fn new(kind: UrlPrepErrorKind, raw: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind,
            raw: raw.into(),
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the Python-facing error class name.
    pub fn error_type(&self) -> &'static str {
        match self.kind {
            UrlPrepErrorKind::Value => "ValueError",
            UrlPrepErrorKind::SsrfBlocked => "SsrfBlockedError",
        }
    }

    /// Returns the raw caller-supplied URL.
    pub fn raw(&self) -> &str {
        &self.raw
    }

    /// Returns the plain rejection message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }

    /// Renders the same error record the Python tools return.
    pub fn to_record(&self) -> Value {
        let mut map = Map::new();
        map.insert("url".to_owned(), json!(self.raw));
        map.insert(
            "error".to_owned(),
            json!(format!("URL rejected: {}", self.message)),
        );
        map.insert("error_type".to_owned(), json!(self.error_type()));
        Value::Object(map)
    }
}

impl fmt::Display for UrlPrepError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for UrlPrepError {}

/// Normalizes and SSRF-validates a caller-supplied URL.
///
/// # Errors
///
/// Returns [`UrlPrepError`] when the URL cannot be interpreted or points at a non-public host.
pub fn prepare_url(raw: &str, runtime_minor: Option<u32>) -> Result<String, UrlPrepError> {
    let normalized = normalize_url(Some(raw), None, runtime_minor)
        .map_err(|e| UrlPrepError::new(UrlPrepErrorKind::Value, raw, e.message().to_owned()))?;
    check_url(&normalized, false, runtime_minor).map_err(|e| {
        UrlPrepError::new(UrlPrepErrorKind::SsrfBlocked, raw, e.message().to_owned())
    })?;
    Ok(normalized)
}

/// Collapse a name to one safe basename, matching `Path(...).stem` on Windows inputs.
pub fn sanitize_filename(name: &str, fallback: &str) -> String {
    let last = name
        .replace('\\', "/")
        .rsplit('/')
        .next()
        .unwrap_or("")
        .to_string();
    let stem = match last.rfind('.') {
        Some(index) if index > 0 && index + 1 < last.len() => &last[..index],
        _ => &last[..],
    };
    let replaced = stem
        .replace("..", "_")
        .trim()
        .chars()
        .map(|ch| {
            if ch.is_alphanumeric() || matches!(ch, '-' | '_' | ' ') {
                ch
            } else {
                '_'
            }
        })
        .collect::<String>();
    let cleaned = replaced.trim_matches(|ch| matches!(ch, '.' | '_' | '-'));
    if cleaned.is_empty() {
        fallback.to_string()
    } else {
        cleaned.to_string()
    }
}

/// Whether the JATS route may handle a source under the requested table format.
pub fn jats_tables_allows(source: &str, tables_as: &str) -> bool {
    if tables_as == "markdown" {
        return true;
    }
    let whole = source.rsplit('/').next().unwrap_or(source);
    let direct = whole
        .rfind('.')
        .map(|index| whole[index..].to_ascii_lowercase())
        .unwrap_or_default();
    if !direct.is_empty() {
        return !matches!(direct.as_str(), ".xlsx" | ".xls" | ".xlsb" | ".csv");
    }
    let without_fragment = source.split('#').next().unwrap_or(source);
    let without_query = without_fragment
        .split('?')
        .next()
        .unwrap_or(without_fragment);
    let last = without_query.rsplit('/').next().unwrap_or(without_query);
    let suffix = last
        .rfind('.')
        .map(|index| last[index..].to_ascii_lowercase())
        .unwrap_or_default();
    !matches!(suffix.as_str(), ".xlsx" | ".xls" | ".xlsb" | ".csv")
}

/// Append the JATS route marker to an already-canonical cache base.
pub fn jats_cache_key(base: &str) -> String {
    format!("{base}#jats")
}

/// Render RSS/Atom/RDF feed entries as readable markdown.
///
/// Returns `None` for malformed XML or documents without `item`/`entry`
/// nodes so callers can fall back to the raw text, matching the Python
/// document extractor.
pub fn xml_feed_text(xml: &str) -> Option<String> {
    let root = crate::xmlatom::parse_document(xml).ok()?;
    fn first_text(node: &crate::xmlatom::Node, names: &[&str]) -> String {
        let mut stack: Vec<&crate::xmlatom::Node> = vec![node];
        while let Some(current) = stack.pop() {
            if names.contains(&current.local.as_str())
                && let Some(text) = current.text.trim().into()
                && !text.is_empty()
            {
                return text.to_string();
            }
            stack.extend(current.children.iter().rev());
        }
        String::new()
    }
    fn first_link(node: &crate::xmlatom::Node) -> String {
        let mut stack: Vec<&crate::xmlatom::Node> = vec![node];
        while let Some(current) = stack.pop() {
            if current.local == "link" {
                let href = current.attr("href");
                if !href.is_empty() {
                    return href.to_string();
                }
                let text = current.text.trim();
                if !text.is_empty() {
                    return text.to_string();
                }
            }
            stack.extend(current.children.iter().rev());
        }
        String::new()
    }
    fn collect<'a>(node: &'a crate::xmlatom::Node, out: &mut Vec<&'a crate::xmlatom::Node>) {
        if matches!(node.local.as_str(), "item" | "entry") {
            out.push(node);
        }
        for child in &node.children {
            collect(child, out);
        }
    }
    let mut entries = Vec::new();
    collect(&root, &mut entries);
    if entries.is_empty() {
        return None;
    }
    let feed_title = root
        .children
        .iter()
        .map(|child| first_text(child, &["title"]))
        .find(|text| !text.is_empty())
        .unwrap_or_default();
    let mut lines = Vec::new();
    if !feed_title.is_empty() {
        lines.push(format!("# {feed_title}"));
        lines.push(String::new());
    }
    lines.push(format!("Feed entries: {}", entries.len().min(50)));
    lines.push(String::new());
    for entry in entries.iter().take(50) {
        let title = first_text(entry, &["title"]);
        let link = first_link(entry);
        let desc = first_text(entry, &["description", "summary", "content"]);
        let date = first_text(entry, &["pubDate", "published", "updated", "date"]);
        lines.push(format!(
            "- **{}**",
            if title.is_empty() {
                "(untitled)"
            } else {
                &title
            }
        ));
        if !link.is_empty() {
            lines.push(format!("  Link: {link}"));
        }
        if !date.is_empty() {
            lines.push(format!("  Date: {date}"));
        }
        if !desc.is_empty() {
            lines.push(format!("  {desc}"));
        }
    }
    if entries.len() > 50 {
        lines.push(String::new());
        lines.push(format!(
            "… {} more entries not shown (capped at 50).",
            entries.len() - 50
        ));
    }
    Some(lines.join("\n"))
}

/// Error text for a page-range request on a format without page structure.
///
/// PDF and spreadsheet sources are page-capable; every other suffix gets the
/// canonical actionable message from the Python extractor.
pub fn page_selection_error(source: &str, is_url: bool) -> Option<String> {
    let suffix = if is_url {
        let after_scheme = source.split_once("://").map_or(source, |(_, rest)| rest);
        let path = after_scheme
            .split(['?', '#'])
            .next()
            .unwrap_or(after_scheme);
        let last = path.rsplit('/').next().unwrap_or(path);
        last.rfind('.')
            .map(|index| last[index..].to_ascii_lowercase())
            .unwrap_or_default()
    } else {
        std::path::Path::new(source)
            .extension()
            .and_then(|ext| ext.to_str())
            .map(|ext| format!(".{}", ext.to_ascii_lowercase()))
            .unwrap_or_default()
    };
    if matches!(suffix.as_str(), ".pdf" | ".xlsx" | ".xls" | ".xlsb") {
        return None;
    }
    Some(format!(
        "Page selection is supported for PDF (pages) and Excel workbooks (sheets); this source is {suffix}. Call extract_document without pages, or convert the file to PDF."
    ))
}

/// Extract Markdown from Office bytes with the embedded `office_oxide` engine.
pub fn office_markdown(bytes: &[u8], extension: &str) -> Result<String, String> {
    use std::io::Cursor;

    use office_oxide::{Document, format::DocumentFormat};

    let format = DocumentFormat::from_extension(extension)
        .ok_or_else(|| format!("Unsupported document format: .{extension}"))?;
    let document = Document::from_reader(Cursor::new(bytes.to_vec()), format)
        .map_err(|error| error.to_string())?;
    Ok(document.to_markdown())
}

/// Build a structured `ParsedDocumentPayload` for Office bytes.
pub fn office_structured_payload(
    bytes: &[u8],
    extension: &str,
    file_name: &str,
    file_size_bytes: u64,
) -> Result<Value, String> {
    use std::io::Cursor;

    use office_oxide::{Document, format::DocumentFormat};

    let format = DocumentFormat::from_extension(extension)
        .ok_or_else(|| format!("Unsupported document format: .{extension}"))?;
    let document = Document::from_reader(Cursor::new(bytes.to_vec()), format)
        .map_err(|error| error.to_string())?;
    let ir_text = document
        .to_ir()
        .to_json_string()
        .map_err(|error| error.to_string())?;
    let ir: Value = serde_json::from_str(&ir_text).unwrap_or(Value::Null);
    let source_meta = ir.get("metadata").and_then(Value::as_object);
    let title = source_meta
        .and_then(|map| map.get("title"))
        .or_else(|| ir.get("title"))
        .filter(|value| !value.is_null())
        .cloned()
        .unwrap_or(Value::Null);
    let author = source_meta
        .and_then(|map| map.get("author"))
        .or_else(|| map_get(&ir, "creator"))
        .or_else(|| map_get(&ir, "author"))
        .filter(|value| !value.is_null())
        .cloned()
        .unwrap_or(Value::Null);
    let mut extra_meta = serde_json::Map::new();
    if let Some(map) = source_meta {
        for (key, value) in map {
            if key != "title" && key != "author" {
                extra_meta.insert(
                    key.clone(),
                    Value::String(crate::config::py_str_value(value)),
                );
            }
        }
    }
    let spreadsheet = matches!(extension, "xlsx" | "xls" | "pptx" | "ppt");
    let page_count = if spreadsheet {
        ir.get("sections")
            .and_then(Value::as_array)
            .map_or(1, |items| items.len().max(1))
    } else {
        1
    };
    let mut tables = Vec::new();
    if spreadsheet {
        for (index, pair) in crate::documents::office_ir_tables(&ir)
            .into_iter()
            .enumerate()
        {
            let pair = pair.as_array().cloned().unwrap_or_default();
            let title = pair.first().and_then(Value::as_str).unwrap_or("Section 1");
            let rows = pair
                .get(1)
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if rows.is_empty() {
                continue;
            }
            tables.push(serde_json::json!({
                "name": format!("{title}_table_{index}"),
                "headers": rows[0],
                "rows": rows.iter().skip(1).cloned().collect::<Vec<_>>(),
            }));
        }
    }
    let metadata = serde_json::json!({
        "jsonld": Value::Null,
        "microdata": Value::Null,
        "microformats": Value::Null,
        "dublin_core": Value::Null,
        "rdfa": Value::Null,
        "rel_links": Value::Null,
        "manifest": Value::Null,
        "extra_meta": extra_meta,
        "twitter_card": Value::Null,
        "twitter_title": Value::Null,
        "twitter_description": Value::Null,
        "twitter_image": Value::Null,
        "twitter_site": Value::Null,
        "og_title": Value::Null,
        "og_type": Value::Null,
        "og_image": Value::Null,
        "og_images": Value::Null,
        "og_description": Value::Null,
        "og_site_name": Value::Null,
        "og_url": Value::Null,
        "canonical": Value::Null,
        "language": Value::Null,
        "keywords": Value::Null,
        "robots": Value::Null,
        "file_name": file_name,
        "file_size_bytes": file_size_bytes,
        "format": extension,
        "title": title,
        "author": author,
        "description": Value::Null,
        "created_at": Value::Null,
        "modified_at": Value::Null,
        "page_count": page_count,
    });
    let plain = document.plain_text();
    let markdown = document.to_markdown();
    Ok(serde_json::json!({
        "metadata": metadata,
        "pages": [{
            "page_number": 1,
            "raw_text": plain,
            "markdown": markdown,
            "tables": tables,
        }],
        "tables": tables,
        "links": [],
        "guard": Value::Null,
    }))
}

fn map_get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.get(key).filter(|value| !value.is_null())
}

/// Extract Markdown from PDF bytes with the embedded `pdf_oxide` engine.
///
/// Errors are returned as strings so CLI/Python adapters can preserve their
/// own error envelopes instead of panicking on malformed input.
pub fn pdf_markdown(bytes: &[u8]) -> Result<String, String> {
    let document =
        pdf_oxide::PdfDocument::from_bytes(bytes.to_vec()).map_err(|error| error.to_string())?;
    let options = pdf_oxide::converters::ConversionOptions::default();
    document
        .to_markdown_all(&options)
        .map_err(|error| error.to_string())
}

/// Build the `ParsedDocumentPayload` envelope for an HTML fetch.
///
/// The CLI structured-inspect path uses this to emit the same flat metadata
/// shape as the Python Pydantic model while keeping parsing in pure core.
pub fn structured_html_payload(
    markdown: &str,
    links: &Value,
    html_metadata: &Value,
    url: &str,
    max_links: i64,
    tables: &[crate::tables::ExtractedTableGrid],
) -> Value {
    let parsed = url::Url::parse(url).ok();
    let path_slug = parsed
        .as_ref()
        .map(|value| value.path().trim_matches('/'))
        .unwrap_or("");
    let slug = if path_slug.is_empty() {
        parsed
            .as_ref()
            .and_then(url::Url::host_str)
            .unwrap_or(url)
            .to_string()
    } else {
        path_slug.to_string()
    };
    let base = serde_json::json!({
        "file_name": slug,
        "format": "html",
        "canonical": url,
    });
    let enriched = crate::metaextract::merge_into_document_metadata(html_metadata, &base);
    let get = |key: &str| enriched.get(key).cloned().unwrap_or(Value::Null);
    let get_or = |key: &str, default: Value| {
        let value = get(key);
        if value.is_null() { default } else { value }
    };
    let metadata = serde_json::json!({
        "jsonld": get("jsonld"),
        "microdata": get("microdata"),
        "microformats": get("microformats"),
        "dublin_core": get("dublin_core"),
        "rdfa": get("rdfa"),
        "rel_links": get("rel_links"),
        "manifest": get("manifest"),
        "extra_meta": get_or("extra_meta", json!({})),
        "twitter_card": get("twitter_card"),
        "twitter_title": get("twitter_title"),
        "twitter_description": get("twitter_description"),
        "twitter_image": get("twitter_image"),
        "twitter_site": get("twitter_site"),
        "og_title": get("og_title"),
        "og_type": get("og_type"),
        "og_image": get("og_image"),
        "og_images": get("og_images"),
        "og_description": get("og_description"),
        "og_site_name": get("og_site_name"),
        "og_url": get("og_url"),
        "canonical": get("canonical"),
        "language": get("language"),
        "keywords": get("keywords"),
        "robots": get("robots"),
        "file_name": get("file_name"),
        "file_size_bytes": get_or("file_size_bytes", json!(0)),
        "format": get("format"),
        "title": get("title"),
        "author": get("author"),
        "description": get("description"),
        "created_at": get("created_at"),
        "modified_at": get("modified_at"),
        "page_count": get_or("page_count", json!(1)),
    });
    let table_values: Vec<Value> = tables
        .iter()
        .map(|(name, headers, rows)| {
            serde_json::json!({"name": name, "headers": headers, "rows": rows})
        })
        .collect();
    let page = serde_json::json!({
        "page_number": 1,
        "raw_text": markdown,
        "markdown": markdown,
        "tables": table_values,
    });
    serde_json::json!({
        "metadata": metadata,
        "pages": [page],
        "tables": table_values,
        "links": crate::documents::follow_up_candidates(links, max_links),
        "guard": Value::Null,
    })
}

#[cfg(test)]
mod xml_feed_tests {
    use super::xml_feed_text;

    #[test]
    fn renders_rss_entries() {
        let xml = "<rss><channel><title>Feed</title><item><title>A</title><link>https://a.test</link><description>First</description></item></channel></rss>";
        assert_eq!(
            xml_feed_text(xml).as_deref(),
            Some("# Feed\n\nFeed entries: 1\n\n- **A**\n  Link: https://a.test\n  First")
        );
    }

    #[test]
    fn falls_back_for_sitemaps() {
        assert_eq!(
            xml_feed_text("<urlset><url>https://a.test</url></urlset>"),
            None
        );
        assert_eq!(xml_feed_text("<broken"), None);
    }
}

/// Canonical structured-extraction error for formats the structured parser
/// does not accept. `None` means the parser owns the source.
pub fn structured_format_error(source: &str, is_url: bool) -> Option<String> {
    let suffix = if is_url {
        let after_scheme = source.split_once("://").map_or(source, |(_, rest)| rest);
        let path = after_scheme
            .split(['?', '#'])
            .next()
            .unwrap_or(after_scheme);
        let last = path.rsplit('/').next().unwrap_or(path);
        last.rfind('.')
            .map(|index| last[index..].to_ascii_lowercase())
            .unwrap_or_default()
    } else {
        std::path::Path::new(source)
            .extension()
            .and_then(|ext| ext.to_str())
            .map(|ext| format!(".{}", ext.to_ascii_lowercase()))
            .unwrap_or_default()
    };
    if matches!(
        suffix.as_str(),
        ".pdf" | ".docx" | ".xlsx" | ".pptx" | ".xls" | ".ppt" | ".xlsb" | ".ods" | ".doc"
    ) {
        return None;
    }
    Some(format!(
        "Unsupported file format '{suffix}'. Supported: pdf, docx, xlsx, pptx, xls, ppt, xlsb, ods, doc."
    ))
}

#[cfg(test)]
mod page_selection_tests {
    use super::page_selection_error;

    #[test]
    fn rejects_flat_formats() {
        assert_eq!(
            page_selection_error("/tmp/a.txt", false).as_deref(),
            Some(
                "Page selection is supported for PDF (pages) and Excel workbooks (sheets); this source is .txt. Call extract_document without pages, or convert the file to PDF."
            )
        );
    }

    #[test]
    fn accepts_paged_formats() {
        assert_eq!(
            page_selection_error("https://x.test/a.pdf?download=1", true),
            None
        );
        assert_eq!(page_selection_error("/tmp/a.xlsx", false), None);
    }
}

#[cfg(test)]
mod structured_format_tests {
    use super::structured_format_error;

    #[test]
    fn rejects_text() {
        assert_eq!(
            structured_format_error("/tmp/a.txt", false).as_deref(),
            Some(
                "Unsupported file format '.txt'. Supported: pdf, docx, xlsx, pptx, xls, ppt, xlsb, ods, doc."
            )
        );
    }

    #[test]
    fn accepts_structured_formats() {
        assert_eq!(
            structured_format_error("https://x.test/a.pdf?download=1", true),
            None
        );
        assert_eq!(structured_format_error("/tmp/a.docx", false), None);
    }
}

#[cfg(test)]
mod structured_payload_tests {
    use super::*;

    #[test]
    fn builds_pydantic_compatible_shape() {
        let metadata = json!({
            "meta": {"title": "T", "description": "D"},
            "opengraph": {"title": "OG"},
            "rel_links": {"icon": ["data:,"]},
        });
        let links = json!([["https://a.test/x", "A"]]);
        let out = structured_html_payload(
            "# T",
            &links,
            &metadata,
            "https://example.com/a",
            5,
            &[(
                "Table 1".to_string(),
                vec!["A".to_string()],
                vec![vec!["1".to_string()]],
            )],
        );
        assert_eq!(out["metadata"]["file_name"], json!("a"));
        assert_eq!(out["metadata"]["title"], json!("T"));
        assert_eq!(out["metadata"]["og_title"], json!("OG"));
        assert_eq!(out["metadata"]["description"], json!("D"));
        assert_eq!(out["pages"][0]["markdown"], json!("# T"));
        assert_eq!(out["tables"][0]["name"], json!("Table 1"));
        assert_eq!(out["links"][0]["url"], json!("https://a.test/x"));
        assert_eq!(
            out.as_object()
                .map(|m| m.keys().cloned().collect::<Vec<_>>()),
            Some(vec![
                "metadata".to_string(),
                "pages".to_string(),
                "tables".to_string(),
                "links".to_string(),
                "guard".to_string(),
            ])
        );
    }
}
