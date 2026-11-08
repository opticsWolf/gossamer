//! JATS input adapter (proposal P-B, v0.7.0).
//!
//! Publisher full-text JATS (PLOS/PMC/EuropePMC) carries formulas as
//! structured MathML, real `<table>` grids, and figure metadata — the
//! preferred formula source over PDF/OCR (proposal design principle: the
//! PDF stays the universal fallback, never the formula source of first
//! resort).
//!
//! Markdown shape mirrors the ikat exporter's proven contract
//! (`ikat-corpus/export_full.py`, which produced `ground-truth/*.md`):
//! inline math as fused `$…$` spans, display math as whole-paragraph
//! `$$tex \tag{N}$$` fences, real JATS tables as GFM pipe tables, figures
//! as `![alt](href)` links (remote URLs preserved — download is out of
//! scope), sections as `#`/`##` headings. ikat-specific extensions
//! (mermaid floats, `%% table` markers, `.bib` sidecars) are NOT emitted:
//! output is plain GFM.
//!
//! No ONNX engine is needed. Unknown JATS elements pass through
//! transparently (markup, not user error), but unbalanced `$` after fusion
//! is a hard error (house `ValueError`).

use std::collections::HashMap;

use roxmltree::Node;

use crate::jats_mml::{MmlStats, mathify, merge_math, mml_to_tex};

type Result<T> = std::result::Result<T, String>;

use pyo3::prelude::*;

/// Python binding: JATS XML string -> JSON
/// `{markdown, title, n_formulas, n_figures, n_tables}`.
///
/// Pure (no I/O): the caller owns file staging. Errors surface as
/// `ValueError` (unbalanced `$` after fusion, non-article root, XML
/// parse failure) — same contract as the other `_core` kernels.
#[pyfunction]
pub(crate) fn jats_convert_jats(py: Python<'_>, xml: String) -> PyResult<String> {
    py.detach(|| match convert_jats_str(&xml) {
        Ok(out) => Ok(serde_json::json!({
            "markdown": out.markdown,
            "title": out.title,
            "n_formulas": out.n_formulas,
            "n_figures": out.n_figures,
            "n_tables": out.n_tables,
        })
        .to_string()),
        Err(e) => Err(pyo3::exceptions::PyValueError::new_err(e)),
    })
}

/// Pure conversion output (no I/O): markdown plus counts.
#[derive(Debug)]
pub struct JatsOutput {
    pub markdown: String,
    pub title: String,
    pub n_formulas: usize,
    pub n_figures: usize,
    pub n_tables: usize,
}

struct JatsCtx {
    refkey: HashMap<String, String>,
    displays: Vec<(String, String)>,
    stats: MmlStats,
    n_formulas: usize,
    n_figures: usize,
    n_tables: usize,
}

impl JatsCtx {
    fn new() -> Self {
        Self {
            refkey: HashMap::new(),
            displays: Vec::new(),
            stats: MmlStats::default(),
            n_formulas: 0,
            n_figures: 0,
            n_tables: 0,
        }
    }
}

fn local<'a, 'input>(node: Node<'a, 'input>) -> &'input str {
    node.tag_name().name()
}

fn child_text<'a, 'input>(node: Node<'a, 'input>, name: &str) -> String {
    node.children()
        .find(|c| c.is_element() && local(*c) == name)
        .map(|c| collapse(&itertext(c)))
        .unwrap_or_default()
}

fn itertext<'a, 'input>(node: Node<'a, 'input>) -> String {
    node.descendants()
        .filter(|n| n.is_text())
        .filter_map(|n| n.text())
        .collect()
}

/// Collapse whitespace runs (pretty-printed JATS indentation) to single
/// spaces and trim. Keeps Unicode as-is (UTF-8 markdown, unlike the
/// TeX-targeted fold in the ikat exporter).
fn collapse(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut pending_space = false;
    for ch in s.chars() {
        if ch.is_whitespace() {
            pending_space = true;
        } else {
            if pending_space && !out.is_empty() {
                out.push(' ');
            }
            pending_space = false;
            out.push(ch);
        }
    }
    out
}

/// Prose Owned-guard: escape `$` (math delimiter), rewrite `|` (would
/// re-scan as a GFM table), and guard the `[ [`ref pattern.
fn prose_guard(s: &str) -> String {
    s.replace('$', "\\$")
        .replace('|', "/")
        .replace("[ [`ref", "[[`ref")
}

/// Hard-error policy: unbalanced `$` after fusion is corrupt math.
fn check_dollars(seg: &str, what: &str) -> Result<()> {
    if !seg.matches('$').count().is_multiple_of(2) {
        return Err(format!("ValueError: unbalanced `$` in {what}: {seg:.120}"));
    }
    Ok(())
}

fn rich_text<'a, 'input>(node: Node<'a, 'input>, ctx: &mut JatsCtx) -> String {
    let mut parts = Vec::new();
    for child in node.children() {
        if child.is_text() {
            if let Some(t) = child.text() {
                // Keep boundary spaces: whitespace runs become one space
                // each; the final collapse trims + squeezes the join.
                let spaced: String = t
                    .chars()
                    .map(|c| if c.is_whitespace() { ' ' } else { c })
                    .collect();
                parts.push(prose_guard(&spaced));
            }
            continue;
        }
        if !child.is_element() {
            continue;
        }
        match local(child) {
            "xref" => {
                if child.attribute("ref-type") == Some("bibr") {
                    for rid in child.attribute("rid").unwrap_or("").split_whitespace() {
                        if let Some(key) = ctx.refkey.get(rid) {
                            parts.push(format!(" [`{key}`]"));
                        }
                    }
                } else {
                    parts.push(rich_text(child, ctx));
                }
            }
            "sup" => {
                let inner = collapse(&itertext(child));
                if inner.contains('$') {
                    parts.push(inner);
                } else {
                    parts.push(format!("$^{{{}}}$", mathify(&inner, &mut ctx.stats)));
                }
            }
            "sub" => {
                let inner = collapse(&itertext(child));
                if inner.contains('$') {
                    parts.push(inner);
                } else {
                    parts.push(format!("$_{{{}}}$", mathify(&inner, &mut ctx.stats)));
                }
            }
            "italic" | "i" => {
                parts.push(format!("*{}*", collapse(&itertext(child))));
            }
            "bold" | "b" => {
                parts.push(format!("**{}**", collapse(&itertext(child))));
            }
            "ext-link" | "uri" | "email" => {
                parts.push(collapse(&itertext(child)));
            }
            "inline-formula" => {
                let math = element_math(child);
                if let Some(m) = math {
                    let tex = mml_to_tex(m, &mut ctx.stats);
                    if !tex.is_empty() {
                        ctx.n_formulas += 1;
                        parts.push(format!("${tex}$"));
                    }
                }
            }
            "disp-formula" => {
                let math = element_math(child);
                let tex = math
                    .map(|m| mml_to_tex(m, &mut ctx.stats))
                    .unwrap_or_default();
                let lab = collapse(&child_text(child, "label"));
                if !tex.is_empty() {
                    ctx.n_formulas += 1;
                }
                ctx.displays.push((tex, lab));
                parts.push(format!("\u{e000}{}\u{e001}", ctx.displays.len() - 1));
            }
            _ => {
                // Unknown inline markup: transparent passthrough.
                parts.push(rich_text(child, ctx));
            }
        }
    }
    collapse(&parts.join(""))
}

/// First MathML `<math>` element in the subtree (skips `<alternatives>`
/// / `<tex-math>` wrappers publishers nest inside formula elements),
/// else first element child.
fn element_math<'a, 'input>(node: Node<'a, 'input>) -> Option<Node<'a, 'input>> {
    node.descendants()
        .find(|n| n.is_element() && local(*n) == "math")
        .or_else(|| node.children().find(|n| n.is_element()))
}

/// `$$tex \tag{N}$$` display line; tag rides inside the fence.
fn emit_eq(tex: &str, lab: &str) -> String {
    let tex = tex.trim();
    if tex.is_empty() {
        return String::new();
    }
    let lab = lab.trim();
    let num = if lab.len() > 2 && lab.starts_with('(') && lab.ends_with(')') {
        &lab[1..lab.len() - 1]
    } else {
        lab
    };
    let tag = if num.is_empty() {
        String::new()
    } else {
        format!(" \\tag{{{num}}}")
    };
    format!("$${tex}{tag}$$")
}

/// Split display placeholders out of a paragraph, fuse + guard text runs.
fn flush_para(buf: &str, out: &mut Vec<String>, ctx: &JatsCtx) -> Result<()> {
    // Markers survive merge_math (no `$` inside), so fuse first.
    let fused = merge_math(buf);
    let mut pos = 0;
    let mut search_from = 0;
    while let Some(start) = fused[search_from..].find('\u{e000}') {
        let abs_start = search_from + start;
        let rest = &fused[abs_start + '\u{e000}'.len_utf8()..];
        let Some(end_rel) = rest.find('\u{e001}') else {
            break;
        };
        let num: usize = rest[..end_rel].parse().unwrap_or(usize::MAX);
        let text_seg = fused[pos..abs_start].trim();
        if !text_seg.is_empty() {
            check_dollars(text_seg, "paragraph")?;
            out.push(text_seg.to_string());
        }
        if num < ctx.displays.len() {
            let (tex, lab) = &ctx.displays[num];
            if !tex.trim().is_empty() {
                out.push(emit_eq(tex, lab));
            }
        }
        search_from = abs_start + '\u{e000}'.len_utf8() + end_rel + '\u{e001}'.len_utf8();
        pos = search_from;
    }
    let tail = fused[pos..].trim();
    if !tail.is_empty() {
        check_dollars(tail, "paragraph")?;
        out.push(tail.to_string());
    }
    Ok(())
}

/// Inline-rich child element: MathML-aware (titles/captions/labels can
/// carry math — raw itertext would flatten it to glyph soup). Stray
/// display placeholders degrade to inline spans.
fn rich_child<'a, 'input>(node: Node<'a, 'input>, name: &str, ctx: &mut JatsCtx) -> String {
    let el = node.children().find(|c| c.is_element() && local(*c) == name);
    match el {
        Some(el) => {
            let fused = merge_math(&rich_text(el, ctx));
            inline_displays(&fused, ctx)
        }
        None => String::new(),
    }
}

fn caption_text<'a, 'input>(fig_or_tw: Node<'a, 'input>, ctx: &mut JatsCtx) -> (String, String) {
    let label = rich_child(fig_or_tw, "label", ctx);
    let cap_el = fig_or_tw
        .children()
        .find(|c| c.is_element() && local(*c) == "caption");
    let mut cap = cap_el.map(|c| rich_text(c, ctx)).unwrap_or_default();
    // Strip "Fig. 1." / "Table 2" prefixes (the label carries the number).
    if let Ok(re) = regex::Regex::new(r"(?i)^(fig(ure)?|table)\.?\s*\w+\.?\s*") {
        cap = re.replace(&cap, "").into_owned();
    }
    (label, cap)
}

fn alt_text(label: &str, caption: &str) -> String {
    let mut alt = if label.is_empty() {
        caption.to_string()
    } else if caption.is_empty() {
        label.to_string()
    } else {
        format!("{label}: {caption}")
    };
    // Keep alt readable (~160 chars): word boundary, but never rewind
    // more than a few dozen chars for one long unbroken word.
    alt = alt.replace(['[', ']'], "");
    if alt.chars().count() > 160 {
        let cut: String = alt.chars().take(160).collect();
        alt = match cut.rfind(' ') {
            Some(i) if i >= 130 => cut[..i].to_string(),
            _ => cut,
        };
    }
    // Balance-aware: never end inside a $…$ span — a cut that orphans
    // a dollar would unbalance the whole document.
    let mut depth = 0u32;
    let mut prev = ' ';
    let mut last_open: Option<usize> = None;
    for (i, c) in alt.char_indices() {
        if c == '$' && prev != '\\' {
            if depth.is_multiple_of(2) {
                last_open = Some(i);
            }
            depth += 1;
        }
        prev = c;
    }
    if depth % 2 == 1 {
        if let Some(i) = last_open {
            alt.truncate(i);
            alt = alt.trim_end().to_string();
        }
    }
    alt
}

fn fig_block<'a, 'input>(fig: Node<'a, 'input>, ctx: &mut JatsCtx) -> Vec<String> {
    ctx.n_figures += 1;
    let (label, cap) = caption_text(fig, ctx);
    let graphic = fig
        .descendants()
        .find(|n| n.is_element() && local(*n) == "graphic");
    let href = graphic
        .and_then(|g| {
            // NB: Node::attribute() misses these; scan manually.
            g.attributes().find_map(|a| {
                let n = a.name();
                if n == "href" || n.ends_with(":href") {
                    Some(a.value())
                } else {
                    None
                }
            })
        })
        .unwrap_or("")
        .to_string();
    if href.is_empty() {
        // Imageless float stays prose (honest, weave-safe).
        let head = if label.is_empty() { "Figure".to_string() } else { label };
        return vec![format!("**{head}.** {cap} (figure image not in JATS).")];
    }
    vec![format!("![{}]({href})", alt_text(&label, &cap))]
}

fn table_block<'a, 'input>(tw: Node<'a, 'input>, ctx: &mut JatsCtx) -> Vec<String> {
    let (label, cap) = caption_text(tw, ctx);
    let table = tw
        .descendants()
        .find(|n| n.is_element() && local(*n) == "table");
    let Some(table) = table else {
        return vec![];
    };
    let mut rows: Vec<Vec<String>> = Vec::new();
    for tr in table
        .descendants()
        .filter(|n| n.is_element() && local(*n) == "tr")
    {
        // Skip rows from nested tables (only direct table's rows).
        let mut cells = Vec::new();
        for c in tr.children().filter(|n| n.is_element()) {
            let t = local(c);
            if t != "td" && t != "th" {
                continue;
            }
            let mut cell = collapse(&rich_text(c, ctx))
                .replace('|', " / ");
            // Display placeholders cannot live in cells: inline fallback.
            cell = inline_displays(&cell, ctx);
            if cell.trim_start().starts_with('[') {
                cell = format!("{{}} {cell}");
            }
            cells.push(cell);
        }
        if !cells.is_empty() {
            rows.push(cells);
        }
    }
    if rows.is_empty() {
        return vec![];
    }
    ctx.n_tables += 1;
    let width = rows.iter().map(|r| r.len()).max().unwrap_or(0);
    let mut lines = Vec::new();
    if !label.is_empty() || !cap.is_empty() {
        let head = if label.is_empty() {
            "Table".to_string()
        } else {
            label
        };
        lines.push(format!("**{head}.** {cap}"));
        lines.push(String::new());
    }
    let mut norm: Vec<Vec<String>> = rows
        .into_iter()
        .map(|mut r| {
            r.resize(width, String::new());
            r
        })
        .collect();
    let head = norm.remove(0);
    lines.push(format!("| {} |", head.join(" | ")));
    lines.push(format!("|{}|", vec!["---"; width].join("|")));
    for r in norm {
        lines.push(format!("| {} |", r.join(" | ")));
    }
    lines
}

/// Replace display placeholders with inline `$…$` (for table cells etc.).
fn inline_displays(s: &str, ctx: &JatsCtx) -> String {
    let mut out = s.to_string();
    while let Some(start) = out.find('\u{e000}') {
        let rest = &out[start + '\u{e000}'.len_utf8()..];
        let Some(end_rel) = rest.find('\u{e001}') else {
            break;
        };
        let num: usize = rest[..end_rel].parse().unwrap_or(usize::MAX);
        let rep = if num < ctx.displays.len() && !ctx.displays[num].0.trim().is_empty() {
            format!("${}$", ctx.displays[num].0.trim())
        } else {
            String::new()
        };
        out.replace_range(
            start..start + '\u{e000}'.len_utf8() + end_rel + '\u{e001}'.len_utf8(),
            &rep,
        );
    }
    out
}

/// Single block-level element -> output lines.
fn block_node<'a, 'input>(node: Node<'a, 'input>, depth: usize, ctx: &mut JatsCtx, out: &mut Vec<String>) -> Result<()> {
    match local(node) {
        "title" => {}
        "sec" => {
            let stitle = rich_child(node, "title", ctx);
            let title = if stitle.is_empty() { "Section".to_string() } else { stitle };
            match depth {
                0 => out.push(format!("## {title}\n")),
                1 => out.push(format!("### {title}\n")),
                _ => out.push(format!("**{title}.**\n")),
            }
            blocks_in(node, depth + 1, ctx, out)?;
        }
        "p" => {
            let txt = rich_text(node, ctx);
            if !txt.trim().is_empty() {
                flush_para(&txt, out, ctx)?;
            }
        }
        "fig" => {
            for line in fig_block(node, ctx) {
                out.push(line);
            }
        }
        "table-wrap" => {
            for line in table_block(node, ctx) {
                out.push(line);
            }
        }
        "list" => {
            for item in node
                .descendants()
                .filter(|n| n.is_element() && local(*n) == "list-item")
            {
                let txt = rich_text(item, ctx);
                if !txt.trim().is_empty() {
                    flush_para(&format!("- {txt}"), out, ctx)?;
                }
            }
        }
        "disp-formula" => {
            // Section-level display math: own block (data-loss fix).
            let math = element_math(node);
            let tex = math
                .map(|m| mml_to_tex(m, &mut ctx.stats))
                .unwrap_or_default();
            let lab = collapse(&child_text(node, "label"));
            if !tex.trim().is_empty() {
                ctx.n_formulas += 1;
                out.push(emit_eq(&tex, &lab));
            }
        }
        "boxed-text" => {
            for p in node
                .descendants()
                .filter(|n| n.is_element() && local(*n) == "p")
            {
                let txt = rich_text(p, ctx);
                if !txt.trim().is_empty() {
                    flush_para(&txt, out, ctx)?;
                }
            }
        }
        "caption" | "label" | "object-id" => {}
        "supplementary-material" | "media" => {}
        _ => {
            // Transparent passthrough for unknown block markup.
            let txt = rich_text(node, ctx);
            if !txt.trim().is_empty() {
                flush_para(&txt, out, ctx)?;
            }
        }
    }
    Ok(())
}

fn blocks_in<'a, 'input>(sec: Node<'a, 'input>, depth: usize, ctx: &mut JatsCtx, out: &mut Vec<String>) -> Result<()> {
    for child in sec.children().filter(|n| n.is_element()) {
        block_node(child, depth, ctx, out)?;
    }
    Ok(())
}

/// roxmltree rejects documents with a DTD; JATS files always carry one
/// (`<!DOCTYPE article PUBLIC ...>`). Strip the declaration (quote- and
/// bracket-aware) before parsing. No internal subset is evaluated.
fn strip_doctype(xml: &str) -> String {
    let Some(start) = xml.find("<!DOCTYPE") else {
        return xml.to_string();
    };
    let bytes = xml.as_bytes();
    let mut i = start + "<!DOCTYPE".len();
    let mut quote = None;
    let mut depth = 0usize;
    while i < bytes.len() {
        let c = bytes[i] as char;
        if let Some(q) = quote {
            if c == q {
                quote = None;
            }
        } else if c == '"' || c == '\'' {
            quote = Some(c);
        } else if c == '[' {
            depth += 1;
        } else if c == ']' {
            depth = depth.saturating_sub(1);
        } else if c == '>' && depth == 0 {
            let mut out = String::with_capacity(xml.len());
            out.push_str(&xml[..start]);
            out.push_str(&xml[i + 1..]);
            return out;
        }
        i += 1;
    }
    xml.to_string()
}

/// Pure JATS XML -> markdown conversion (no I/O; unit-testable).
pub fn convert_jats_str(xml: &str) -> Result<JatsOutput> {
    let xml = strip_doctype(xml);
    let doc = roxmltree::Document::parse(&xml)
        .map_err(|e| format!("ValueError: XML parse: {e}"))?;
    let root = doc.root_element();
    if local(root) != "article" {
        return Err("ValueError: not a JATS article".to_string());
    }
    let mut ctx = JatsCtx::new();

    // Citation keys from back/ref-list (stable refNN numbering).
    if let Some(back) = root
        .descendants()
        .find(|n| n.is_element() && local(*n) == "back")
    {
        let mut n = 0;
        for r in back
            .descendants()
            .filter(|n| n.is_element() && local(*n) == "ref")
        {
            n += 1;
            if let Some(id) = r.attribute("id") {
                ctx.refkey.insert(id.to_string(), format!("ref{n:02}"));
            }
        }
    }

    let front = root
        .descendants()
        .find(|n| n.is_element() && local(*n) == "front");
    let article_meta = front.and_then(|f| {
        f.children()
            .find(|c| c.is_element() && local(*c) == "article-meta")
    });

    let title = article_meta
        .map(|am| {
            am.children()
                .find(|c| c.is_element() && local(*c) == "title-group")
                .map(|tg| {
                    let t = tg
                        .children()
                        .find(|c| c.is_element() && local(*c) == "article-title");
                    t.map(|el| rich_text(el, &mut ctx)).unwrap_or_default()
                })
                .unwrap_or_default()
        })
        .unwrap_or_default();
    let title = merge_math(title.trim()).trim().to_string();
    check_dollars(&title, "title")?;

    let mut out: Vec<String> = Vec::new();
    out.push(format!("# {title}\n"));

    if let Some(am) = article_meta {
        if let Some(abs) = am
            .children()
            .find(|c| c.is_element() && local(*c) == "abstract")
        {
            out.push("## Abstract\n".to_string());
            for p in abs.children().filter(|n| n.is_element() && local(*n) == "p") {
                let txt = rich_text(p, &mut ctx);
                if !txt.trim().is_empty() {
                    flush_para(&txt, &mut out, &ctx)?;
                }
            }
        }
    }

    if let Some(body) = root
        .descendants()
        .find(|n| n.is_element() && local(*n) == "body")
    {
        for child in body.children().filter(|n| n.is_element()) {
            if local(child) == "sec" {
                let stitle = rich_child(child, "title", &mut ctx);
                out.push(format!("## {}\n", if stitle.is_empty() { "Section".to_string() } else { stitle }));
                blocks_in(child, 1, &mut ctx, &mut out)?;
            } else {
                block_node(child, 1, &mut ctx, &mut out)?;
            }
        }
    }

    if let Some(back) = root
        .descendants()
        .find(|n| n.is_element() && local(*n) == "back")
    {
        if let Some(ack) = back
            .children()
            .find(|c| c.is_element() && local(*c) == "ack")
        {
            let paras: Vec<String> = ack
                .descendants()
                .filter(|n| n.is_element() && local(*n) == "p")
                .map(|p| rich_text(p, &mut ctx))
                .filter(|t| !t.trim().is_empty())
                .collect();
            if !paras.is_empty() {
                out.push("## Acknowledgements\n".to_string());
                for p in paras {
                    flush_para(&p, &mut out, &ctx)?;
                }
            }
        }
        let refs: Vec<(String, String)> = back
            .descendants()
            .filter(|n| n.is_element() && local(*n) == "ref")
            .filter_map(|r| {
                r.attribute("id").map(|id| {
                    (
                        ctx.refkey.get(id).cloned().unwrap_or_default(),
                        collapse(&itertext(r)),
                    )
                })
            })
            .collect();
        if !refs.is_empty() {
            out.push("## References\n".to_string());
            for (key, text) in refs {
                if key.is_empty() {
                    out.push(format!("- {text}"));
                } else {
                    out.push(format!("- [`{key}`] {text}"));
                }
            }
        }
    }

    // Join blocks with blank lines (display fences get breathing room).
    let mut md = String::new();
    for (i, b) in out.iter().enumerate() {
        if i > 0 {
            md.push('\n');
        }
        md.push_str(b);
        if !b.ends_with('\n') {
            md.push('\n');
        }
    }
    Ok(JatsOutput {
        markdown: md,
        title,
        n_formulas: ctx.n_formulas,
        n_figures: ctx.n_figures,
        n_tables: ctx.n_tables,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root_of(xml: &str) -> roxmltree::Document<'_> {
        roxmltree::Document::parse(xml).unwrap()
    }

    #[test]
    fn inline_math_and_citations() {
        let doc = root_of(
            "<article><back><ref-list><ref id=\"r1\"><mixed-citation>Smith 2020</mixed-citation></ref></ref-list></back>\
             <body><p>As shown <xref ref-type=\"bibr\" rid=\"r1\">[1]</xref> we have \
             <inline-formula><mml:math xmlns:mml=\"http://www.w3.org/1998/Math/MathML\"><mml:mi>x</mml:mi></mml:math></inline-formula> done.</p></body></article>",
        );
        let mut ctx = JatsCtx::new();
        ctx.refkey.insert("r1".into(), "ref01".into());
        let body = doc
            .root_element()
            .descendants()
            .find(|n| n.is_element() && local(*n) == "body")
            .unwrap();
        let p = body
            .children()
            .find(|n| n.is_element() && local(*n) == "p")
            .unwrap();
        let txt = rich_text(p, &mut ctx);
        assert!(txt.contains("`ref01`"), "citation: {txt}");
        assert!(txt.contains("$x$"), "inline math: {txt}");
        assert_eq!(ctx.n_formulas, 1);
    }

    #[test]
    fn display_emit_eq_tags() {
        assert_eq!(emit_eq("x^2", "(1)"), "$$x^2 \\tag{1}$$");
        assert_eq!(emit_eq("x^2", ""), "$$x^2$$");
        assert_eq!(emit_eq("  ", "(2)"), "");
    }

    #[test]
    fn minimal_article_converts() {
        let xml = "<article><front><article-meta><title-group><article-title>Test <inline-formula><mml:math xmlns:mml=\"http://www.w3.org/1998/Math/MathML\"><mml:mi>\u{3b1}</mml:mi></mml:math></inline-formula></article-title></title-group>\
            <abstract><p>Abs <inline-formula><mml:math xmlns:mml=\"http://www.w3.org/1998/Math/MathML\"><mml:mn>2</mml:mn></mml:math></inline-formula>.</p></abstract></article-meta></front>\
            <body><sec><title>Intro</title><p>Hi <disp-formula><mml:math xmlns:mml=\"http://www.w3.org/1998/Math/MathML\"><mml:msup><mml:mi>x</mml:mi><mml:mn>2</mml:mn></mml:msup></mml:math><label>(1)</label></disp-formula> end.</p></sec></body></article>";
        let out = convert_jats_str(xml).unwrap();
        assert!(out.markdown.starts_with("# Test $\\alpha$\n"), "title: {}", &out.markdown[..60]);
        assert!(out.markdown.contains("## Abstract\n"));
        assert!(out.markdown.contains("## Intro\n"));
        assert!(out.markdown.contains("$$x^{2} \\tag{1}$$"));
        assert_eq!(out.n_formulas, 3);
    }

    #[test]
    fn unbalanced_dollars_hard_error() {
        // A literal $ in JATS text is escaped by prose_guard; the guard
        // itself is the contract.
        assert!(check_dollars("a $b c", "t").is_err());
        assert!(check_dollars("a $b$ c", "t").is_ok());
        // Empty front matter stays permissive (empty title, no error).
        let out = convert_jats_str("<article><front></front></article>").unwrap();
        assert!(out.markdown.starts_with("# \n") || out.markdown.starts_with("#\n"));
    }
}

