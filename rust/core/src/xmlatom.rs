//! Minimal ElementTree-shaped XML layer for feed parsers.
//!
//! Reproduces the exact slice used by the ported adapters: local-name matching that ignores
//! namespaces, direct-text-only `.text`, entity expansion, and XML 1.0 EOL normalization. Comments,
//! processing instructions, tails, and plugins are intentionally out of scope.

use std::backtrace::Backtrace;
use std::error::Error;
use std::fmt;

use quick_xml::XmlVersion;
use quick_xml::escape::unescape;
use quick_xml::events::Event;
use quick_xml::reader::Reader;

/// Canonical XML kernel error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct XmlError {
    message: String,
    backtrace: Backtrace,
}

impl XmlError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the legacy-compatible error message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace for diagnostics.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for XmlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for XmlError {}

/// One element: local name, direct attributes, direct text, and direct children.
///
/// Attribute keys follow ElementTree: unqualified attributes keep their bare name, prefixed
/// attributes resolve to `{namespace-uri}local`, and `xmlns` declarations never appear as
/// attributes.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Node {
    /// Local element name.
    pub local: String,
    /// Direct attributes as `(resolved_key, value)` pairs.
    pub attrs: Vec<(String, String)>,
    /// Direct text after EOL normalization and entity expansion; child tails excluded.
    pub text: String,
    /// Direct child elements in document order.
    pub children: Vec<Node>,
}

type XmlStackEntry = (Node, String, bool, Vec<(String, String)>);

impl Node {
    /// First direct child with this local name.
    pub fn child(&self, name: &str) -> Option<&Node> {
        self.children.iter().find(|c| c.local == name)
    }

    /// Direct children with this local name.
    pub fn children_named(&self, name: &str) -> Vec<&Node> {
        self.children.iter().filter(|c| c.local == name).collect()
    }

    /// Direct text of the first descendant with this local name, in document order.
    ///
    /// This mirrors `el.findtext(".//{*}name")`: absent returns `None`, while present-but-empty
    /// returns `Some("")`.
    pub fn descendant_text(&self, name: &str) -> Option<String> {
        let mut stack: Vec<&Node> = self.children.iter().rev().collect();
        while let Some(n) = stack.pop() {
            if n.local == name {
                return Some(n.text.clone());
            }
            stack.extend(n.children.iter().rev());
        }
        None
    }

    /// `el.get(key, "")`: first unqualified attribute match, else `""`.
    pub fn attr(&self, key: &str) -> &str {
        for (k, v) in self.attrs.iter() {
            if k == key {
                return v;
            }
        }
        ""
    }

    /// `el.get(key)`: raw optional variant.
    pub fn attr_opt(&self, key: &str) -> Option<&str> {
        for (k, v) in self.attrs.iter() {
            if k == key {
                return Some(v);
            }
        }
        None
    }
}

fn local_name(raw: &str) -> String {
    match raw.rsplit_once(':') {
        Some((_, local)) => local.to_string(),
        None => raw.to_string(),
    }
}

fn append_direct_text(stack: &mut [XmlStackEntry], text: &str) {
    if let Some(top) = stack.last_mut()
        && !top.2
    {
        top.1.push_str(text);
    }
}

fn resolve_prefix(key: &str, scope: &[(String, String)]) -> Result<String, XmlError> {
    match key.split_once(':') {
        None => Ok(key.to_string()),
        Some((prefix, local)) => match scope.iter().rev().find(|(p, _)| p == prefix) {
            Some((_, uri)) => Ok(format!("{{{uri}}}{local}")),
            None => Err(XmlError::new(format!(
                "ValueError: unbound prefix {prefix:?}"
            ))),
        },
    }
}

/// Parses one XML document into a root [`Node`].
///
/// Input is expected to be well-formed XML 1.0; callers that need ElementTree-style `ParseError`
/// semantics should validate first.
///
/// # Errors
///
/// Returns [`XmlError`] for reader failures, invalid attribute values, unbound prefixes, invalid
/// entity references, or documents with no root element.
///
/// # Examples
///
/// ```
/// use gossamer::xmlatom::parse_document;
///
/// let root = parse_document("<f xmlns:a='u'><a:x>y<b/>tail</a:x></f>").unwrap();
/// assert_eq!(root.child("x").unwrap().text, "y");
/// ```
pub fn parse_document(xml: &str) -> Result<Node, XmlError> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().expand_empty_elements = true;
    let mut stack: Vec<XmlStackEntry> = Vec::new();
    let mut root: Option<Node> = None;
    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let local = local_name(e.local_name().as_ref());
                let mut scope: Vec<(String, String)> = match stack.last() {
                    Some(top) => top.3.clone(),
                    None => vec![(
                        "xml".to_string(),
                        "http://www.w3.org/XML/1998/namespace".to_string(),
                    )],
                };
                let mut raw_attrs: Vec<(String, String)> = Vec::new();
                for a in e.attributes() {
                    let a = a.map_err(|e| XmlError::new(format!("ValueError: {e}")))?;
                    let key: &str = a.key.as_ref();
                    let v = a
                        .normalized_value(XmlVersion::Implicit1_0)
                        .map_err(|e| XmlError::new(format!("ValueError: {e}")))?
                        .into_owned();
                    if key == "xmlns" {
                        scope.push((String::new(), v));
                    } else if let Some(prefix) = key.strip_prefix("xmlns:") {
                        scope.push((prefix.to_string(), v));
                    } else {
                        raw_attrs.push((key.to_string(), v));
                    }
                }
                let mut attrs = Vec::with_capacity(raw_attrs.len());
                for (k, v) in raw_attrs.into_iter() {
                    attrs.push((resolve_prefix(&k, &scope)?, v));
                }
                if let Some(top) = stack.last_mut() {
                    top.2 = true;
                }
                stack.push((
                    Node {
                        local,
                        attrs,
                        ..Node::default()
                    },
                    String::new(),
                    false,
                    scope,
                ));
            }
            Ok(Event::Empty(_)) => {
                // Unreachable with expand_empty_elements, kept for API drift.
                continue;
            }
            Ok(Event::End(_e)) => {
                let (mut node, text, _, _) = stack.pop().unwrap_or_default();
                node.text = text;
                match stack.last_mut() {
                    Some(parent) => parent.0.children.push(node),
                    None => root = Some(node),
                }
            }
            Ok(Event::Text(e)) => {
                let owned = e.xml10_content().into_owned();
                let t = unescape(&owned).map_err(|e| XmlError::new(format!("ValueError: {e}")))?;
                append_direct_text(&mut stack, &t);
            }
            Ok(Event::CData(e)) => {
                let t: &str = e.as_ref();
                let t = t.replace("\r\n", "\n").replace('\r', "\n");
                append_direct_text(&mut stack, &t);
            }
            Ok(Event::GeneralRef(e)) => {
                let raw = format!("&{0};", e.xml10_content());
                let t = unescape(&raw).map_err(|e| XmlError::new(format!("ValueError: {e}")))?;
                append_direct_text(&mut stack, &t);
            }
            Ok(_) => {}
            Err(e) => return Err(XmlError::new(format!("ValueError: {e}"))),
        }
        buf.clear();
    }
    root.ok_or_else(|| XmlError::new("ValueError: no element found"))
}

/// Strip an XML namespace: `{ns}Obs` becomes `Obs`.
pub fn xml_local_name(tag: &str) -> &str {
    tag.rsplit_once('}').map(|(_, tail)| tail).unwrap_or(tag)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_names_and_tails() {
        let root = parse_document("<f xmlns:a='u'><a:x>y<b/>tail</a:x></f>").unwrap();
        let x = root.child("x").unwrap();
        assert_eq!(x.text, "y");
        assert_eq!(x.attr("missing"), "");
    }

    #[test]
    fn entity_and_cdata_normalization() {
        let root = parse_document("<r a='x&#10;y'>a&amp;b<![CDATA[c\rd]]></r>").unwrap();
        assert_eq!(root.text, "a&bc\nd");
        assert_eq!(root.attr("a"), "x\ny");
    }

    #[test]
    fn descendant_and_attrs() {
        let root =
            parse_document("<r xmlns:p='urn:p'><p:x p:k='v'><y/></p:x><y>last</y></r>").unwrap();
        assert_eq!(root.descendant_text("y"), Some(String::new()));
        assert_eq!(root.child("x").unwrap().attr("{urn:p}k"), "v");
        assert_eq!(root.children_named("y").len(), 1);
    }

    #[test]
    fn no_root_is_error() {
        assert!(parse_document("<!-- only a comment -->").is_err());
    }
}
