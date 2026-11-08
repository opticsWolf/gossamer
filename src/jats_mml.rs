//! MathML -> LaTeX (proposal P-A, v0.7.0-alpha).
//!
//! Port of `ikat/examples/auto-proof/mml.py` (M7.8, stdlib-only), the
//! reference implementation for structured-formula ingestion. Converts one
//! `<mml:math>` element (or a bare formula element) to bare LaTeX (no `$`
//! wrapping — the caller owns math mode).
//!
//! Structural repairs vs corpus hacks: the longest-known-prefix command
//! splitter, unclosed-fence fixup, `|`/`‖` scale rules and same-level
//! script separation are generic MathML/publisher failure modes and ARE
//! ported. The three publisher-misencoded glyph hacks (`U+01BB` -> `\zeta`,
//! `U+049F` -> `\beta`, `U+0277` -> `k`) are PLOS-corpus-specific and live
//! in the ikat exporter only — here those codepoints fall through to the
//! `?` passthrough branch (proposal 2.A.2).
//!
//! Command splitting uses the local `split_cmds` below (same table
//! derivation, pinned by `known_set_matches_sanitize`). Only the splitter
//! is ported — the OCR fence/script fixups stay behind (not needed on the
//! JATS path).
//! differential test requires the quirk. See `sanitize` module docs.

use roxmltree::Node;
use unicode_normalization::UnicodeNormalization;

static GREEK: &[(&str, &str)] = &[
    ("α", "\\alpha"),
    ("β", "\\beta"),
    ("γ", "\\gamma"),
    ("δ", "\\delta"),
    ("ε", "\\varepsilon"),
    ("κ", "\\kappa"),
    ("λ", "\\lambda"),
    ("μ", "\\mu"),
    ("π", "\\pi"),
    ("σ", "\\sigma"),
    ("τ", "\\tau"),
    ("χ", "\\chi"),
    ("η", "\\eta"),
    ("θ", "\\theta"),
    ("ι", "\\iota"),
    ("ν", "\\nu"),
    ("ξ", "\\xi"),
    ("ρ", "\\rho"),
    ("ς", "\\varsigma"),
    ("φ", "\\varphi"),
    ("ψ", "\\psi"),
    ("ω", "\\omega"),
    ("Γ", "\\Gamma"),
    ("Θ", "\\Theta"),
    ("Λ", "\\Lambda"),
    ("Ξ", "\\Xi"),
    ("Π", "\\Pi"),
    ("Σ", "\\Sigma"),
    ("Φ", "\\Phi"),
    ("Ψ", "\\Psi"),
    ("Ω", "\\Omega"),
    ("Δ", "\\Delta"),
    ("ϑ", "\\vartheta"),
    ("ϕ", "\\phi"),
    ("ϖ", "\\varpi"),
    ("ϵ", "\\epsilon"),
    ("υ", "\\upsilon"),
    ("ϐ", "\\beta"),
    ("ℓ", "\\ell"),
    ("ℵ", "\\aleph"),
    ("∝", "\\propto"),
    ("∥", "\\|"),
    ("⊥", "\\perp"),
    ("∠", "\\angle"),
    ("≃", "\\simeq"),
    ("≅", "\\cong"),
    ("℘", "\\wp"),
    ("ℛ", "\\Re"),
    ("ℱ", "\\mathcal{F}"),
    ("ℕ", "\\mathbb{N}"),
    ("ℝ", "\\mathbb{R}"),
    ("ℤ", "\\mathbb{Z}"),
    ("ℚ", "\\mathbb{Q}"),
    ("ℏ", "\\hbar"),
    ("∅", "\\emptyset"),
];
static OPS: &[(&str, &str)] = &[
    ("−", "-"),
    ("±", "\\pm"),
    ("°", "^{\\circ}"),
    ("×", "\\times"),
    ("÷", "\\div"),
    ("⋅", "\\cdot"),
    ("∙", "\\cdot"),
    ("∘", "\\circ"),
    ("∗", "*"),
    ("≤", "\\leq"),
    ("≥", "\\geq"),
    ("≠", "\\neq"),
    ("≈", "\\approx"),
    ("≡", "\\equiv"),
    ("∼", "\\sim"),
    ("←", "\\leftarrow"),
    ("→", "\\to"),
    ("⇒", "\\Rightarrow"),
    ("⟨", "\\langle"),
    ("⟩", "\\rangle"),
    ("〈", "\\langle"),
    ("〉", "\\rangle"),
    ("〈", "\\langle"),
    ("〉", "\\rangle"),
    ("′", "'"),
    ("″", "''"),
    ("∂", "\\partial"),
    ("∇", "\\nabla"),
    ("∞", "\\infty"),
    ("∫", "\\int"),
    ("∑", "\\sum"),
    ("∏", "\\prod"),
    ("⋀", "\\bigwedge"),
    ("⋁", "\\bigvee"),
    ("⋃", "\\bigcup"),
    ("⋂", "\\bigcap"),
    ("∈", "\\in"),
    ("∉", "\\notin"),
    ("∋", "\\ni"),
    ("⊂", "\\subset"),
    ("⊆", "\\subseteq"),
    ("⊃", "\\supset"),
    ("⊇", "\\supseteq"),
    ("∪", "\\cup"),
    ("∩", "\\cap"),
    ("⊕", "\\oplus"),
    ("⊗", "\\otimes"),
    ("∀", "\\forall"),
    ("∃", "\\exists"),
    ("¬", "\\neg"),
    ("∧", "\\wedge"),
    ("∨", "\\vee"),
    ("⋯", "\\cdots"),
    ("⋮", "\\vdots"),
    ("⋱", "\\ddots"),
    ("…", "\\cdots"),
    ("‖", "\\Vert"),
    ("‗", "_"),
    ("⁡", ""),
    ("⁢", ""),
    ("⁣", ""),
    ("⁤", "+"),
    (" ", "\\,"),
    (" ", "\\,"),
    (" ", "\\quad"),
    (" ", " "),
    ("¹", "^{1}"),
    ("²", "^{2}"),
    ("³", "^{3}"),
    ("*", "\\ast"),
    ("\u{200b}", ""),
    ("‍", ""),
    ("\u{2060}", ""),
    ("˜", "\\sim"),
];
static FUNC_NAMES: &[(&str, &str)] = &[
    ("log", "\\log"),
    ("ln", "\\ln"),
    ("sin", "\\sin"),
    ("cos", "\\cos"),
    ("tan", "\\tan"),
    ("exp", "\\exp"),
    ("max", "\\max"),
    ("min", "\\min"),
    ("lim", "\\lim"),
    ("sup", "\\sup"),
    ("inf", "\\inf"),
    ("det", "\\det"),
    ("dim", "\\dim"),
    ("ker", "\\ker"),
    ("arg", "\\arg"),
    ("deg", "\\deg"),
];
static LIMIT_OPS: &[&str] = &[
    "\\bigcap",
    "\\bigcup",
    "\\iint",
    "\\inf",
    "\\int",
    "\\lim",
    "\\liminf",
    "\\limsup",
    "\\max",
    "\\min",
    "\\prod",
    "\\sum",
    "\\sup",
];
static ACCENTS: &[(&str, &str)] = &[
    ("^", "hat"),
    ("¯", "bar"),
    ("ˉ", "bar"),
    ("¯\u{200b}", "bar"),
    ("-", "bar"),
    ("~", "tilde"),
    ("˜", "tilde"),
    ("→", "overrightarrow"),
    ("←", "overleftarrow"),
    (".", "dot"),
    ("..", "ddot"),
    ("¨", "ddot"),
];

/// Per-conversion diagnostics. Same stat keys as the Python `stats` dict
/// (`mml_unclosed_fence`, `mml_short_frac`, `mml_matrix`,
/// `mml_passthrough`) plus the fold/loss counters, so the port can be
/// diffed against the reference.
#[derive(Debug, Default, Clone)]
pub struct MmlStats {
    /// `\left`/`\right` imbalances closed with `\right.`.
    pub mml_unclosed_fence: usize,
    /// `mfrac` elements with fewer than 2 children.
    pub mml_short_frac: usize,
    /// `mtable` elements converted.
    pub mml_matrix: usize,
    /// Unknown element tags passed through (capped at 12, sorted).
    pub mml_passthrough: Vec<String>,
    /// NFKD-folded chars wrapped in `\mathrm`.
    pub mathify_fold: usize,
    /// Chars mathify could not represent (capped at 12, sorted).
    pub mathify_lost: Vec<String>,
    /// Chars element converters could not represent (capped at 12, sorted).
    pub mml_lost: Vec<String>,
}

impl MmlStats {
    fn record(list: &mut Vec<String>, tag: &str) {
        if !list.iter().any(|t| t == tag) {
            list.push(tag.to_string());
            list.sort();
            list.truncate(12);
        }
    }
    /// Shorthand for the passthrough-table probe used by converters.
    pub fn record_passthrough(&mut self, tag: &str) {
        Self::record(&mut self.mml_passthrough, tag);
    }
}

fn lookup(table: &'static [(&'static str, &'static str)], key: &str) -> Option<&'static str> {
    table.iter().find(|(k, _)| *k == key).map(|(_, v)| *v)
}

fn local_name<'a, 'input>(node: Node<'a, 'input>) -> &'input str {
    node.tag_name().name()
}

fn element_kids<'a, 'input>(node: Node<'a, 'input>) -> Vec<Node<'a, 'input>> {
    node.children().filter(|n| n.is_element()).collect()
}

fn node_text<'a: 'input, 'input>(node: Node<'a, 'input>) -> &'input str {
    node.text().unwrap_or("")
}

/// Longest-known-prefix command splitter (ported from bobine's
/// `sanitize::split_cmds`; the only piece of the OCR sanitize pipeline the
/// JATS/MathML path needs).
/// Split `\inL` -> `\in L`, leave `\infty}` alone: TeX reads the longest
/// alpha run after `\`, so `\infty}` is one command (never split) while
/// publisher output `\inL` is undefined - split it after the longest
/// KNOWN prefix. A regex alternation cannot do this: when the longest fails
/// it backtracks and shreds (`\infty` -> `\inf ty`).
pub fn split_cmds(tex: &str) -> String {
    let bytes = tex.as_bytes();
    let n = bytes.len();
    let mut out = String::with_capacity(tex.len() + 8);
    let mut i = 0;
    while i < n {
        let is_cmd_start = bytes[i] == b'\\'
            && i + 1 < n
            && (bytes[i + 1] as char).is_ascii_alphabetic();
        if !is_cmd_start {
            let ch = tex[i..].chars().next().unwrap();
            out.push(ch);
            i += ch.len_utf8();
            continue;
        }
        let mut j = i + 1;
        while j < n && (bytes[j] as char).is_ascii_alphabetic() {
            j += 1;
        }
        let run = &tex[i + 1..j];
        if is_known_cmd(run) {
            out.push_str(&tex[i..j]);
            i = j;
            continue;
        }
        let mut k = run.len();
        while k > 0 && !is_known_cmd(&run[..k]) {
            k -= 1;
        }
        if k > 0 {
            out.push_str(&tex[i..i + 1 + k]);
            out.push(' ');
            i += 1 + k;
        } else {
            out.push_str(&tex[i..j]);
            i = j;
        }
    }
    out
}

/// LaTeX command inventory for [`split_cmds`]: LaTeX base + amsmath +
/// amssymb. Sorted for binary search.
static KNOWN_CMDS: &[&str] = &[
    "Delta", "Gamma", "Lambda", "Omega", "Phi", "Pi", "Psi", "Re", "Rightarrow", "Sigma",
    "Theta", "Vert", "Xi", "aleph", "alpha", "angle", "approx", "arg", "ast", "beta",
    "bigcap", "bigcup", "bigvee", "bigwedge", "cap", "cdot", "cdots", "chi", "circ", "cong",
    "cos", "cup", "ddots", "deg", "delta", "det", "dim", "div", "ell", "emptyset", "epsilon",
    "equiv", "eta", "exists", "exp", "forall", "gamma", "geq", "hbar", "in", "inf", "infty",
    "int", "iota", "kappa", "ker", "lambda", "langle", "leftarrow", "leq", "lim", "ln",
    "log", "max", "min", "mu", "nabla", "neg", "neq", "ni", "notin", "nu", "omega", "oplus",
    "otimes", "partial", "perp", "phi", "pi", "pm", "prod", "propto", "psi", "quad",
    "rangle", "rho", "sigma", "sim", "simeq", "sin", "subset", "subseteq", "sum", "sup",
    "supset", "supseteq", "tan", "tau", "theta", "times", "to", "upsilon", "varepsilon",
    "varphi", "varpi", "varsigma", "vartheta", "vdots", "vee", "wedge", "wp", "xi",
];

fn is_known_cmd(name: &str) -> bool {
    KNOWN_CMDS.binary_search(&name).is_ok()
}

/// Plain JATS text (JATS-level sub/sup, titles) -> bare math LaTeX.
/// Port of `mml_reference.mathify`.
pub fn mathify(text: &str, stats: &mut MmlStats) -> String {
    let mut out = String::with_capacity(text.len() + 8);
    for ch in text.chars() {
        let s = ch.to_string();
        if let Some(cmd) = lookup(GREEK, &s) {
            out.push_str(cmd);
        } else if let Some(op) = lookup(OPS, &s) {
            out.push_str(op);
        } else if (ch as u32) < 128 {
            out.push(ch);
        } else {
            let decomp: String = s.nfkd().collect();
            if decomp.chars().count() == 1 {
                if let Some(cmd) = lookup(GREEK, &decomp) {
                    out.push_str(cmd);
                    continue;
                }
            }
            let base: String = decomp.chars().filter(|c| (*c as u32) < 128).collect();
            if !base.is_empty()
                && base
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "+-=".contains(c))
            {
                if base.chars().count() == 1 {
                    out.push_str(&base);
                } else {
                    out.push_str(&format!("\\mathrm{{{base}}}"));
                }
                stats.mathify_fold += 1;
            } else {
                MmlStats::record(&mut stats.mathify_lost, &format!("U+{:04X}", ch as u32));
                out.push('?');
            }
        }
    }
    out
}

/// `<mi>` holding plain text -> what it means in math. Port of `_mi_text`.
fn mi_text(text: &str, variant: &str, stats: &mut MmlStats) -> String {
    let t = text.replace('\u{a0}', "").trim().to_string();
    if t.is_empty() {
        return String::new();
    }
    if t.chars().count() == 1 {
        let ch = t.chars().next().unwrap();
        let s = ch.to_string();
        let cmd: String = if let Some(cmd) = lookup(GREEK, &s) {
            cmd.to_string()
        } else if let Some(op) = lookup(OPS, &s) {
            return op.to_string();
        } else {
            let decomp: String = s.nfkd().collect();
            if decomp.chars().count() == 1 {
                if let Some(cmd) = lookup(GREEK, &decomp) {
                    cmd.to_string()
                } else if decomp.chars().next().map(|c| (c as u32) < 128 && c.is_alphanumeric()).unwrap_or(false) {
                    decomp
                } else if (ch as u32) < 128 && ch.is_alphanumeric() {
                    s
                } else {
                    MmlStats::record(&mut stats.mml_lost, &format!("U+{:04X}", ch as u32));
                    return "?".to_string();
                }
            } else if (ch as u32) < 128 && ch.is_alphanumeric() {
                s
            } else {
                MmlStats::record(&mut stats.mml_lost, &format!("U+{:04X}", ch as u32));
                return "?".to_string();
            }
        };
        match variant {
            // Upright requested; roman has no Greek, so Greek keeps its
            // command (shape distinction lost, content kept).
            "normal" => {
                if cmd.starts_with('\\') {
                    cmd
                } else {
                    format!("\\mathrm{{{cmd}}}")
                }
            }
            "bold" => {
                if cmd.starts_with('\\') {
                    format!("\\boldsymbol{{{cmd}}}")
                } else {
                    format!("\\mathbf{{{cmd}}}")
                }
            }
            "script" => {
                if cmd.chars().count() == 1 {
                    format!("\\mathcal{{{cmd}}}")
                } else {
                    cmd
                }
            }
            "double-struck" => {
                // Lowercase blackboard has no msbm glyph and prints plain
                // italic: drop it.
                if cmd.chars().count() == 1 {
                    let c = cmd.chars().next().unwrap();
                    if c.is_lowercase() {
                        cmd
                    } else {
                        format!("\\mathbb{{{cmd}}}")
                    }
                } else {
                    cmd
                }
            }
            "fraktur" => {
                if cmd.chars().count() == 1 {
                    format!("\\mathfrak{{{cmd}}}")
                } else {
                    cmd
                }
            }
            "sans-serif" | "monospace" => {
                if cmd.starts_with('\\') {
                    cmd
                } else {
                    format!("\\mathsf{{{cmd}}}")
                }
            }
            _ => cmd,
        }
    } else {
        let word: String = t
            .chars()
            .filter(|c| (*c as u32) < 128 || lookup(GREEK, &c.to_string()).is_some())
            .collect();
        if let Some(func) = lookup(FUNC_NAMES, &word) {
            return func.to_string();
        }
        if word.chars().all(|c| (c as u32) < 128)
            && !word.is_empty()
            && word.chars().all(|c| c.is_alphabetic())
        {
            if variant == "normal" {
                return format!("\\mathrm{{{word}}}");
            }
            return format!("\\mathit{{{word}}}");
        }
        mathify(&t, stats)
    }
}

fn is_single_command(s: &str) -> bool {
    s.starts_with('\\') && s[1..].chars().all(|c| c.is_ascii_alphabetic()) && s.len() > 1
}

/// Script chars attached at top level at the very end of `base`.
/// Port of `mml_reference._trailing_scripts` (braced and bare forms).
fn trailing_scripts(base: &str) -> Vec<char> {
    let chars: Vec<char> = base.chars().collect();
    let mut found = Vec::new();
    let mut i = chars.len();
    while i > 0 {
        if chars[i - 1] == '}' {
            let mut depth = 0usize;
            let mut j = i - 1;
            loop {
                if chars[j] == '}' {
                    depth += 1;
                } else if chars[j] == '{' {
                    depth -= 1;
                    if depth == 0 {
                        break;
                    }
                }
                if j == 0 {
                    break;
                }
                j -= 1;
            }
            if j < 1 || (chars[j - 1] != '^' && chars[j - 1] != '_') {
                break;
            }
            if !found.contains(&chars[j - 1]) {
                found.push(chars[j - 1]);
            }
            i = j - 1;
        } else if i >= 2
            && (chars[i - 2] == '^' || chars[i - 2] == '_')
            && !matches!(chars[i - 1], '{' | '}' | '^' | '_')
        {
            if !found.contains(&chars[i - 2]) {
                found.push(chars[i - 2]);
            }
            i -= 2;
        } else {
            break;
        }
    }
    found
}

/// Sibling join with prescript awareness: an empty-base sup/sub landing
/// right after the same script level is a TeX double-script error, so an
/// empty atom separates them. Mixed levels pass through.
/// Port of `mml_reference._cat`.
fn cat(a: &str, b: &str) -> String {
    if a.is_empty() {
        return b.to_string();
    }
    if b.is_empty() {
        return a.to_string();
    }
    let lead = if b.starts_with('^') {
        Some('^')
    } else if b.starts_with('_') {
        Some('_')
    } else {
        None
    };
    if let Some(l) = lead {
        if trailing_scripts(a).contains(&l) {
            return format!("{a}{{}}{b}");
        }
    }
    format!("{a}{b}")
}

fn join<I: IntoIterator<Item = String>>(parts: I) -> String {
    parts.into_iter().fold(String::new(), |a, b| cat(&a, &b))
}

fn escape_text(s: &str) -> String {
    s.replace('\\', "\\textbackslash{}")
        .replace('{', "\\{")
        .replace('}', "\\}")
        .replace('_', "\\_")
        .replace('%', "\\%")
        .replace('&', "\\&")
        .replace('#', "\\#")
}

fn itertext<'a, 'input>(node: Node<'a, 'input>) -> String {
    node.descendants()
        .filter(|n| n.is_text())
        .filter_map(|n| n.text())
        .collect()
}

fn tex<'a, 'input>(node: Node<'a, 'input>, stats: &mut MmlStats) -> String {
    let t = local_name(node);
    match t {
        "mi" => {
            let variant = node.attribute("mathvariant").unwrap_or("").trim();
            mi_text(node_text(node), variant, stats)
        }
        "mn" => mathify(node_text(node).trim(), stats),
        "mo" => {
            let txt = node_text(node).trim().to_string();
            if txt.is_empty() {
                return String::new();
            }
            // Braces always scale (cases brace; JATS never marks stretchy,
            // and set braces size harmlessly); the root fixup closes strays.
            // A lone bar NEVER scales: kets have no right partner, and
            // cross-group \left..\right is a TeX error. Norms stay plain
            // \Vert (see mml_to_tex).
            if txt == "{" {
                return "\\left\\{".to_string();
            }
            if txt == "}" {
                return "\\right\\}".to_string();
            }
            if txt == "|" {
                return "|".to_string();
            }
            if (txt == "(" || txt == ")" || txt == "[" || txt == "]")
                && node.attribute("stretchy") == Some("true")
            {
                return match txt.as_str() {
                    "(" => "\\left(".to_string(),
                    ")" => "\\right)".to_string(),
                    "[" => "\\left[".to_string(),
                    _ => "\\right]".to_string(),
                };
            }
            if txt.chars().count() == 1 {
                if let Some(op) = lookup(OPS, &txt) {
                    return op.to_string();
                }
            }
            mathify(&txt, stats)
        }
        "mtext" => format!("\\text{{{}}}", escape_text(&itertext(node))),
        "mspace" => {
            let w = node.attribute("width").unwrap_or("").trim();
            let em = if w.ends_with("em") {
                w.trim_end_matches(['e', 'm']).parse::<f64>().unwrap_or(0.0)
            } else {
                0.0
            };
            if em >= 1.0 {
                "\\quad".to_string()
            } else if em >= 0.2 {
                "\\;".to_string()
            } else {
                "\\,".to_string()
            }
        }
        "mrow" | "mstyle" | "mpadded" | "mphantom" | "semantics" => {
            let mut kids = element_kids(node);
            if t == "semantics" {
                kids.retain(|c| local_name(*c) != "annotation");
                kids.truncate(1);
            }
            join(kids.into_iter().map(|c| tex(c, stats)))
        }
        "mfrac" => {
            let kids = element_kids(node);
            if kids.len() >= 2 {
                return format!("\\frac{{{}}}{{{}}}", tex(kids[0], stats), tex(kids[1], stats));
            }
            stats.mml_short_frac += 1;
            join(kids.into_iter().map(|c| tex(c, stats)))
        }
        "msqrt" => {
            let kids = element_kids(node);
            format!("\\sqrt{{{}}}", join(kids.into_iter().map(|c| tex(c, stats))))
        }
        "mroot" => {
            let kids = element_kids(node);
            if kids.len() >= 2 {
                return format!("\\sqrt[{}]{{{}}}", tex(kids[1], stats), tex(kids[0], stats));
            }
            format!("\\sqrt{{{}}}", join(kids.into_iter().map(|c| tex(c, stats))))
        }
        "msub" | "msup" | "msubsup" => {
            let kids = element_kids(node);
            let base = if !kids.is_empty() { tex(kids[0], stats) } else { String::new() };
            let sub = if kids.len() > 1 { tex(kids[1], stats) } else { String::new() };
            let mut sup = if t == "msubsup" && kids.len() > 2 {
                tex(kids[2], stats)
            } else {
                String::new()
            };
            if t == "msup" && kids.len() > 1 {
                sup = tex(kids[1], stats);
            }
            if base.is_empty() {
                // Empty-base prescript: the sup/sub floats left.
                if t == "msub" {
                    return format!("_{{{sub}}}");
                }
                return format!("^{{{sup}}}");
            }
            // A bare multi-letter base would misattach the script. Single
            // atoms (one char, one command, delimited) stay bare.
            let single = base.chars().count() == 1
                || is_single_command(&base);
            let mut base = base;
            if !single && !base.starts_with("\\left") && !base.starts_with('(') && !base.starts_with('[') {
                base = format!("{{{base}}}");
            }
            // Same-level nesting (prescript coded as msup-on-msup): TeX
            // forbids x^a^b, so an empty atom separates the scripts.
            let trailing = trailing_scripts(&base);
            if (t == "msub" || t == "msubsup") && !sub.is_empty() && trailing.contains(&'_') {
                base.push_str("{}");
            }
            if (t == "msup" || t == "msubsup") && !sup.is_empty() && trailing.contains(&'^') {
                base.push_str("{}");
            }
            let mut out = base;
            if t == "msub" || t == "msubsup" {
                out.push_str(&format!("_{{{sub}}}"));
            }
            if t == "msup" || t == "msubsup" {
                out.push_str(&format!("^{{{sup}}}"));
            }
            out
        }
        "munder" | "mover" | "munderover" => {
            let kids = element_kids(node);
            let base = if !kids.is_empty() { tex(kids[0], stats) } else { String::new() };
            let under = if kids.len() > 1 { tex(kids[1], stats) } else { String::new() };
            let mut over = if t == "munderover" && kids.len() > 2 {
                tex(kids[2], stats)
            } else {
                String::new()
            };
            if t == "mover" && kids.len() > 1 {
                over = tex(kids[1], stats);
            }
            if LIMIT_OPS.contains(&base.as_str()) {
                let mut out = base;
                if t == "munder" || t == "munderover" {
                    out.push_str(&format!("_{{{under}}}"));
                }
                if t == "mover" || t == "munderover" {
                    out.push_str(&format!("^{{{over}}}"));
                }
                return out;
            }
            if t == "mover" && over.chars().count() == 1 {
                if let Some(acc) = lookup(ACCENTS, &over) {
                    return format!("\\{acc}{{{base}}}");
                }
            }
            let mut out = base;
            if t == "munder" || t == "munderover" {
                out = format!("\\underset{{{under}}}{{{out}}}");
            }
            if t == "mover" || t == "munderover" {
                out = format!("\\overset{{{over}}}{{{out}}}");
            }
            out
        }
        "mtable" => {
            let mut rows = Vec::new();
            for tr in element_kids(node) {
                let tag = local_name(tr);
                if tag != "mtr" && tag != "mlabeledtr" {
                    continue;
                }
                let mut cells = Vec::new();
                for td in element_kids(tr) {
                    if local_name(td) == "mtd" {
                        cells.push(join(
                            element_kids(td).into_iter().map(|x| tex(x, stats)),
                        ));
                    }
                }
                rows.push(cells.join(" & "));
            }
            stats.mml_matrix += 1;
            format!("\\begin{{matrix}} {} \\end{{matrix}}", rows.join(" \\\\ "))
        }
        "mfenced" => {
            let o = node.attribute("open").unwrap_or("(");
            let c = node.attribute("close").unwrap_or(")");
            let sep = node.attribute("separators").unwrap_or(",");
            let parts: Vec<String> =
                element_kids(node).into_iter().map(|k| tex(k, stats)).collect();
            format!("{o}{}{c}", parts.join(&format!(" {sep} ")))
        }
        "annotation" => String::new(),
        _ => {
            stats.record_passthrough(t);
            join(element_kids(node).into_iter().map(|c| tex(c, stats)))
        }
    }
}

/// One `<mml:math>` element (or a bare formula element) -> bare LaTeX
/// (no `$` wrapping; the caller owns math mode). Closes stray stretchy
/// fences: a lone stretchy `{` gets its `\right.` so the span compiles.
pub fn mml_to_tex<'a, 'input>(node: Node<'a, 'input>, stats: &mut MmlStats) -> String {
    let tex = if local_name(node) == "math" {
        join(element_kids(node).into_iter().map(|c| tex(c, stats)))
    } else {
        tex(node, stats)
    };
    // Norms stay plain \Vert: positional pairing cannot respect TeX
    // grouping, and multline splits would strand halves. Big fences from
    // real stretchy JATS still size; single bars never scale (kets).
    // NOTE (fidelity quirk, see module docs): the imbalance count is a
    // substring count, so `\leftarrow` alone counts as an open and gains
    // a `\right.`. The reference does this; byte-parity requires it.
    let tex = split_cmds(&tex);
    let lefts = tex.matches("\\left").count() as i64 - tex.matches("\\right").count() as i64;
    let mut tex = tex;
    if lefts > 0 {
        for _ in 0..lefts {
            tex.push_str(" \\right.");
        }
        stats.mml_unclosed_fence += lefts as usize;
    }
    tex.trim().to_string()
}

/// Fuses JATS-split math runs (publisher markup splits one formula across
/// `<inline-formula>` runs / styled text runs). Port of `merge_math`.
pub fn merge_math(par: &str) -> String {
    use std::sync::OnceLock;
    static RES: OnceLock<(regex::Regex, regex::Regex, regex::Regex)> = OnceLock::new();
    let (re_bold_fuse, re_subsup_fuse, re_pair_fuse) = RES.get_or_init(|| {
        (
            regex::Regex::new(r"\*\$([^\$]*\\[^\$]*)\$\*").unwrap(),
            regex::Regex::new(r"\$([^\$]*)\$\$([_\^]\{[^\$]*\})\$").unwrap(),
            regex::Regex::new(r"\$([^\$]+)\$\$([^\$]+)\$").unwrap(),
        )
    });
    let mut par = par.to_string();
    loop {
        let prev = par.clone();
        // Closure replacers: `$` needs no escaping (unlike `$`-strings).
        par = re_bold_fuse
            .replace_all(&par, |c: &regex::Captures| format!("${}$", &c[1]))
            .into_owned();
        par = re_subsup_fuse
            .replace_all(&par, |c: &regex::Captures| format!("${}{}$", &c[1], &c[2]))
            .into_owned();
        par = re_pair_fuse
            .replace_all(&par, |c: &regex::Captures| format!("${}{}$", &c[1], &c[2]))
            .into_owned();
        if par == prev {
            break;
        }
    }
    par
}

#[cfg(test)]
mod tests {
    use super::*;

    fn convert(xml: &str) -> String {
        let doc = roxmltree::Document::parse(xml).unwrap();
        mml_to_tex(doc.root_element(), &mut MmlStats::default())
    }

    #[test]
    fn known_set_matches_sanitize() {
        // The splitter table must equal the GREEK/OPS/FUNC_NAMES derivation
        // or mml.rs and the table diverge.
        for v in GREEK.iter().map(|(_, v)| *v).chain(OPS.iter().map(|(_, v)| *v)).chain(
            FUNC_NAMES.iter().map(|(_, v)| *v),
        ) {
            if v.starts_with('\\') && v[1..].chars().all(|c| c.is_ascii_alphabetic()) {
                assert!(
                    split_cmds(&format!("{v}L")) != format!("{v}L"),
                    "{v} should be a known prefix"
                );
            }
        }
    }

    #[test]
    fn mi_variants() {
        assert_eq!(convert("<mi>x</mi>"), "x");
        assert_eq!(convert("<mi>\u{03b1}</mi>"), "\\alpha");
        assert_eq!(convert("<mi mathvariant=\"normal\">\u{03b1}</mi>"), "\\alpha");
        assert_eq!(convert("<mi mathvariant=\"normal\">E</mi>"), "\\mathrm{E}");
        assert_eq!(convert("<mi mathvariant=\"bold\">H</mi>"), "\\mathbf{H}");
        assert_eq!(convert("<mi mathvariant=\"bold\">\u{03b1}</mi>"), "\\boldsymbol{\\alpha}");
        assert_eq!(convert("<mi>sin</mi>"), "\\sin");
        assert_eq!(convert("<mi>xyz</mi>"), "\\mathit{xyz}");
    }

    #[test]
    fn mn_mo_mtext_mspace() {
        assert_eq!(convert("<mn>2</mn>"), "2");
        assert_eq!(convert("<mo>{</mo>"), "\\left\\{ \\right."); // reference quirk
        assert_eq!(convert("<mo>}</mo>"), "\\right\\}");
        assert_eq!(convert("<mo>|</mo>"), "|");
        assert_eq!(convert("<mo stretchy=\"true\">(</mo>"), "\\left( \\right."); // lone stretchy opens close
        assert_eq!(convert("<mo>(</mo>"), "(");
        assert_eq!(convert("<mo>\u{2208}</mo>"), "\\in");
        assert_eq!(convert("<mtext>hello {world}</mtext>"), "\\text{hello \\{world\\}}");
        assert_eq!(convert("<mspace width=\"1em\"/>"), "\\quad");
        assert_eq!(convert("<mspace width=\"0.3em\"/>"), "\\;");
        assert_eq!(convert("<mspace width=\"2px\"/>"), "\\,");
    }

    #[test]
    fn structural_transparency() {
        assert_eq!(convert("<mrow><mi>x</mi><mo>+</mo><mi>y</mi></mrow>"), "x+y");
        assert_eq!(
            convert("<semantics><mi>x</mi><annotation>note</annotation></semantics>"),
            "x"
        );
        assert_eq!(convert("<mfrac><mn>1</mn><mn>2</mn></mfrac>"), "\\frac{1}{2}");
        assert_eq!(convert("<msqrt><mi>x</mi></msqrt>"), "\\sqrt{x}");
        assert_eq!(convert("<mroot><mi>x</mi><mn>3</mn></mroot>"), "\\sqrt[3]{x}");
    }

    #[test]
    fn scripts_prescript_and_separation() {
        assert_eq!(convert("<msup><mi>x</mi><mi>a</mi></msup>"), "x^{a}");
        // Multi-letter bare base gets braced (after \mathit shaping).
        assert_eq!(convert("<msup><mi>xy</mi><mi>a</mi></msup>"), "{\\mathit{xy}}^{a}");
        // Same-level nesting: braced base resets the script context
        // (trailing_scripts sees the outer group, not the inner ^).
        assert_eq!(
            convert("<msup><msup><mi>x</mi><mi>a</mi></msup><mi>b</mi></msup>"),
            "{x^{a}}^{b}"
        );
    }

    #[test]
    fn fences_and_accents() {
        // Fidelity quirk: \leftarrow counts as an open (matches reference).
        assert_eq!(convert("<mo>\u{2190}</mo>"), "\\leftarrow \\right.");
        assert_eq!(convert("<mover><mi>x</mi><mo>^</mo></mover>"), "\\hat{x}");
        assert_eq!(
            convert("<munderover><mo>\u{2211}</mo><mi>i</mi><mi>n</mi></munderover>"),
            "\\sum_{i}^{n}"
        );
        assert_eq!(
            convert("<munder><mi>x</mi><mo>\u{23df}</mo></munder>"),
            "\\underset{?}{x}"
        );
    }

    #[test]
    fn tables_fences_unknown() {
        assert_eq!(
            convert(
                "<mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr></mtable>"
            ),
            "\\begin{matrix} a & b \\end{matrix}"
        );
        assert_eq!(
            convert("<mfenced><mi>x</mi><mi>y</mi></mfenced>"),
            "(x , y)"
        );
        assert_eq!(convert("<mystery><mi>q</mi></mystery>"), "q");
        assert_eq!(convert("<annotation>x</annotation>"), "");
    }

    #[test]
    fn merge_math_fuses_runs() {
        assert_eq!(merge_math("$a$$b$"), "$ab$");
        assert_eq!(merge_math("x $a$ y"), "x $a$ y");
    }
}
