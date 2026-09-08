//! Minimal ElementTree-shaped XML layer for the ported feed parsers.
//!
//! The originals use `xml.etree.ElementTree`: local-name matching that
//! ignores namespaces, direct-text-only `.text` (grandchild tails are
//! invisible to `_entry_field`-style reads), entity expansion, and XML
//! 1.0 EOL normalization. This module reproduces exactly that slice —
//! nothing more — on top of `quick-xml`, so the adapter kernels stay
//! mechanical field mappings.
//!
//! Deliberately NOT reproduced: comments/PIs (dropped, as ET drops
//! them from `.text`), tail text (invisible to the ported reads),
//! plugins like XInclude, and anything outside well-formed XML 1.0
//! (the Python wrappers run `ET.fromstring` first, so malformed input
//! raises `ParseError` there before any kernel runs).

use quick_xml::escape::unescape;
use quick_xml::events::Event;
use quick_xml::reader::Reader;

/// One element: local name, direct attributes (unqualified keys only —
/// `get("title")` in ElementTree never matches `xlink:title`), direct
/// text (entity-expanded, EOL-normalized, child tails excluded), and
/// direct child elements in document order.
#[derive(Debug, Clone, Default)]
pub struct Node {
    pub local: String,
    pub attrs: Vec<(String, String)>,
    pub text: String,
    pub children: Vec<Node>,
}

impl Node {
    /// First direct child with this local name.
    pub fn child(&self, name: &str) -> Option<&Node> {
        self.children.iter().find(|c| c.local == name)
    }

    /// Direct children with this local name.
    pub fn children_named(&self, name: &str) -> Vec<&Node> {
        self.children.iter().filter(|c| c.local == name).collect()
    }

    /// `el.get(key, "")`: first unqualified attribute match, else "".
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
    // ElementTree matches `{uri}local` by local part; quick-xml yields
    // `prefix:local` — both reduce to the text after the last separator.
    match raw.rsplit_once(':') {
        Some((_, local)) => local.to_string(),
        None => raw.to_string(),
    }
}

/// Parse one document into a root `Node`. Errors are `ValueError`
/// strings (unreachable in practice: the wrappers `ET.fromstring`
/// first, so malformed input raises `ParseError` in Python before any
/// kernel runs — this only guards direct kernel calls).
pub fn parse_document(xml: &str) -> Result<Node, String> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().expand_empty_elements = true;
    // Stack of open elements: the node under construction, its direct
    // text so far, and whether a child element has started (later
    // direct text is a grandchild tail — invisible to field reads).
    let mut stack: Vec<(Node, String, bool)> = Vec::new();
    let mut root: Option<Node> = None;
    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let local = local_name(e.local_name().as_ref());
                let mut attrs = Vec::new();
                for a in e.attributes() {
                    let a = a.map_err(|e| format!("ValueError: {e}"))?;
                    let key: &str = a.key.as_ref();
                    // Qualified attribute names never match plain gets.
                    if key.contains(':') {
                        continue;
                    }
                    let v = a
                        .unescape_value()
                        .map_err(|e| format!("ValueError: {e}"))?
                        .into_owned();
                    attrs.push((key.to_string(), v));
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
                ));
            }
            Ok(Event::Empty(_)) => {
                // Unreachable with expand_empty_elements, kept for API drift.
                continue;
            }
            Ok(Event::End(_e)) => {
                let (mut node, text, _) = stack.pop().unwrap_or_default();
                // Mismatched tags already error in the reader.
                node.text = text;
                match stack.last_mut() {
                    Some(parent) => parent.0.children.push(node),
                    None => root = Some(node),
                }
            }
            Ok(Event::Text(e)) => {
                // EOL-normalized (XML 1.0) direct text, then entity
                // expansion — matching expat/ElementTree `.text`.
                let owned = e.xml10_content().into_owned();
                let t = unescape(&owned)
                    .map_err(|e| format!("ValueError: {e}"))?;
                if let Some(top) = stack.last_mut() {
                    if !top.2 {
                        top.1.push_str(&t);
                    }
                }
            }
            Ok(Event::CData(e)) => {
                // CDATA skips entity expansion but keeps EOL normalization.
                let t: &str = e.as_ref();
                let t = t.replace("\r\n", "\n").replace('\r', "\n");
                if let Some(top) = stack.last_mut() {
                    if !top.2 {
                        top.1.push_str(&t);
                    }
                }
            }
            // Entity / character references arrive as their own events:
            // resolve them exactly as text (expat reports the expanded
            // characters to ElementTree the same way).
            Ok(Event::GeneralRef(e)) => {
                let raw = format!("&{0};", e.xml10_content());
                let t = unescape(&raw).map_err(|e| format!("ValueError: {e}"))?;
                if let Some(top) = stack.last_mut() {
                    if !top.2 {
                        top.1.push_str(&t);
                    }
                }
            }
            // Comments, PIs, declarations and doctypes are invisible.
            Ok(_) => {}
            Err(e) => return Err(format!("ValueError: {e}")),
        }
        buf.clear();
    }
    // Text outside the root element is dropped, as ET drops it.
    root.ok_or_else(|| "ValueError: no element found".to_string())
}
