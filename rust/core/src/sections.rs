//! Query-relevant section selection: heading splitting, tokenization, BM25, budgeted selection.
//!
//! Port of the deterministic kernels in legacy `src/sections.rs`. Float operations keep Python's
//! order exactly (`f64`); string slicing is char-based like Python. `Section::offset` is a Python
//! character index, not a UTF-8 byte index.

use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

use regex::{Captures, Regex};

use crate::compat::{byte_to_char_idx, char_count, char_head, py_strip};

const HEADING_PATTERN: &str = concat!(
    r"(?m)^\#{1,6}[ \t]+(?P<atx>.+?)(?:[ \t]+\#+)?[ \t]*$",
    r"|^(?P<setext>[^\n]+?)[ \t]*\n[ \t]*(?:=|-){2,}[ \t]*$",
);
const ASCII_TOKEN_PATTERN: &str = "[a-z0-9]+";
const CJK_RUN_PATTERN: &str = "[\u{3040}-\u{30ff}\u{4e00}-\u{9fff}\u{ac00}-\u{d7af}]+";

/// Compiles a documented constant pattern, including the pattern in any bug panic (`M-PANIC-MESSAGE`).
fn compile_pattern(pattern: &'static str) -> Regex {
    Regex::new(pattern)
        .unwrap_or_else(|e| panic!("invalid sections regex pattern {pattern:?}: {e}"))
}

// Immutable, process-lifetime regex caches (M-AVOID-STATICS): each OnceLock holds only a compiled
// constant pattern and is a performance optimization, never mutable application state.
static HEADING_RE: OnceLock<Regex> = OnceLock::new();
static ASCII_TOKEN_RE: OnceLock<Regex> = OnceLock::new();
static CJK_RUN_RE: OnceLock<Regex> = OnceLock::new();

fn heading_re() -> &'static Regex {
    HEADING_RE.get_or_init(|| compile_pattern(HEADING_PATTERN))
}

fn ascii_token_re() -> &'static Regex {
    ASCII_TOKEN_RE.get_or_init(|| compile_pattern(ASCII_TOKEN_PATTERN))
}

fn cjk_run_re() -> &'static Regex {
    CJK_RUN_RE.get_or_init(|| compile_pattern(CJK_RUN_PATTERN))
}

// Immutable constant word set, not per-request state (M-AVOID-STATICS).
fn stopwords() -> &'static HashSet<&'static str> {
    static WORDS: OnceLock<HashSet<&'static str>> = OnceLock::new();
    WORDS.get_or_init(|| {
        "a an and are as at be been but by can could did do does for from had \
         has have he her his i if in into is it its just me my no not of on \
         one only or our she so that the their then these they this to \
         too was we were what when which who will with you your"
            .split_whitespace()
            .collect()
    })
}

/// Heading-anchored markdown section.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Section {
    /// Heading title, or `(intro)` for untitled preamble text.
    pub anchor: String,
    /// Full source slice, starting at the heading.
    pub text: String,
    /// Python character offset of `text` in the source markdown.
    pub offset: usize,
}

/// Outcome of budget-aware section selection.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SelectionOutcome {
    /// Selected section text joined with blank lines and stripped.
    pub markdown: String,
    /// Number of sections found in the source.
    pub total_sections: usize,
    /// Number of sections selected.
    pub selected_count: usize,
    /// Anchors of the selected sections in source order.
    pub anchors: Vec<String>,
}

/// Applies the three Setext guards omitted from the regex (Rust regex has no look-around).
fn setext_title_ok(title: &str) -> bool {
    if title.chars().all(|c| c == ' ' || c == '\t') {
        return false;
    }
    let stripped = title.trim_matches(|c| c == ' ' || c == '\t');
    if !stripped.is_empty() && stripped.chars().all(|c| c == '-' || c == '=') {
        return false;
    }
    let mut chars = title.chars();
    !matches!(
        (chars.next(), chars.next()),
        (Some(a), Some(b)) if matches!(a, '-' | '*' | '+') && (b == ' ' || b == '\t')
    )
}

/// Splits markdown into heading-anchored sections.
///
/// ATX and Setext headings are recognized. Blank input returns no sections; input with no heading
/// returns one `(intro)` section.
///
/// # Examples
///
/// ```
/// use gossamer::sections::split_sections;
///
/// let sections = split_sections("intro\n\n# Alpha\n\nbody\n");
/// assert_eq!(sections[0].anchor, "(intro)");
/// assert_eq!(sections[1].anchor, "Alpha");
/// ```
pub fn split_sections(markdown: &str) -> Vec<Section> {
    if py_strip(markdown).is_empty() {
        return Vec::new();
    }
    let caps_list: Vec<Captures> = heading_re()
        .captures_iter(markdown)
        .filter(|caps| match caps.name("setext") {
            Some(g) => setext_title_ok(g.as_str()),
            None => true,
        })
        .collect();
    if caps_list.is_empty() {
        return vec![Section {
            anchor: "(intro)".to_string(),
            text: markdown.to_string(),
            offset: 0,
        }];
    }
    let starts: Vec<usize> = caps_list
        .iter()
        .map(|c| c.get(0).expect("whole regex match must exist").start())
        .collect();
    let mut sections = Vec::new();
    if !py_strip(&markdown[..starts[0]]).is_empty() {
        sections.push(Section {
            anchor: "(intro)".to_string(),
            text: markdown[..starts[0]].to_string(),
            offset: 0,
        });
    }
    for (i, caps) in caps_list.iter().enumerate() {
        let start = starts[i];
        let end = if i + 1 < starts.len() {
            starts[i + 1]
        } else {
            markdown.len()
        };
        let title = caps
            .name("atx")
            .or_else(|| caps.name("setext"))
            .map(|g| g.as_str())
            .unwrap_or("");
        sections.push(Section {
            anchor: py_strip(title).to_string(),
            text: markdown[start..end].to_string(),
            offset: byte_to_char_idx(markdown, start),
        });
    }
    sections
}

/// Tokenizes text with ASCII words and CJK bigrams, dropping stopwords and one-char words.
///
/// # Examples
///
/// ```
/// use gossamer::sections::tokenize_text;
///
/// assert_eq!(tokenize_text("The Quick Brown Fox"), vec!["quick", "brown", "fox"]);
/// assert_eq!(tokenize_text("日本語"), vec!["日本", "本語"]);
/// ```
pub fn tokenize_text(text: &str) -> Vec<String> {
    let lowered = text.to_lowercase();
    let stops = stopwords();
    let mut tokens: Vec<String> = ascii_token_re()
        .find_iter(&lowered)
        .map(|m| m.as_str().to_string())
        .filter(|t| t.chars().count() > 1 && !stops.contains(t.as_str()))
        .collect();
    for m in cjk_run_re().find_iter(&lowered) {
        let run = m.as_str();
        let chars: Vec<char> = run.chars().collect();
        if chars.len() == 1 {
            tokens.push(run.to_string());
        } else {
            for w in chars.windows(2) {
                tokens.push(w.iter().collect());
            }
        }
    }
    tokens
}

/// Computes BM25 scores for one query against each document.
///
/// The operation order matches the Python oracle exactly, so results are bit-for-bit equal for the
/// recorded cases.
///
/// # Examples
///
/// ```
/// use gossamer::sections::bm25_scores;
///
/// let scores = bm25_scores(&["quantum".to_string()], &["quantum lattice".to_string()], 1.5, 0.75);
/// assert!(scores[0] > 0.0);
/// ```
pub fn bm25_scores(query_tokens: &[String], docs: &[String], k1: f64, b: f64) -> Vec<f64> {
    let n = docs.len();
    if n == 0 || query_tokens.is_empty() {
        return vec![0.0; n];
    }
    let doc_tokens: Vec<Vec<String>> = docs.iter().map(|d| tokenize_text(d)).collect();
    let total_len: usize = doc_tokens.iter().map(|d| d.len()).sum();
    let mut avgdl = total_len as f64 / n as f64;
    if avgdl == 0.0 {
        avgdl = 1.0;
    }
    let mut df: HashMap<&str, usize> = HashMap::new();
    for d in &doc_tokens {
        let mut seen = HashSet::new();
        for term in d {
            if seen.insert(term.as_str()) {
                *df.entry(term.as_str()).or_insert(0) += 1;
            }
        }
    }
    let mut query_terms: Vec<&str> = Vec::new();
    let mut qseen = HashSet::new();
    for q in query_tokens {
        if qseen.insert(q.as_str()) && df.contains_key(q.as_str()) {
            query_terms.push(q.as_str());
        }
    }
    if query_terms.is_empty() {
        return vec![0.0; n];
    }
    let nf = n as f64;
    doc_tokens
        .iter()
        .map(|d| {
            let mut tf: HashMap<&str, usize> = HashMap::new();
            for term in d {
                *tf.entry(term.as_str()).or_insert(0) += 1;
            }
            let mut score = 0.0;
            for q in &query_terms {
                let f = *tf.get(q).unwrap_or(&0) as f64;
                if f == 0.0 {
                    continue;
                }
                let n_q = *df.get(q).unwrap_or(&0) as f64;
                let idf = (1.0 + (nf - n_q + 0.5) / (n_q + 0.5)).ln();
                score += idf * (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * d.len() as f64 / avgdl));
            }
            score.max(0.0)
        })
        .collect()
}

/// Selects query-relevant sections under a character budget.
///
/// Returns `None` for empty/short input, empty queries, no sectionable headings, no positive BM25
/// score, or no non-empty selection.
///
/// # Examples
///
/// ```
/// use gossamer::sections::select_relevant_sections;
///
/// let md = "# One\n\napple apple\n\n# Two\n\nquantum lattice\n";
/// let selected = select_relevant_sections(md, "quantum lattice", 30).unwrap();
/// assert_eq!(selected.anchors, vec!["Two".to_string()]);
/// ```
pub fn select_relevant_sections(
    markdown: &str,
    query: &str,
    max_chars: i64,
) -> Option<SelectionOutcome> {
    if max_chars <= 0 || markdown.is_empty() {
        return None;
    }
    if char_count(markdown) <= max_chars as usize {
        return None;
    }
    let query_tokens = tokenize_text(query);
    if query_tokens.is_empty() {
        return None;
    }
    let sections = split_sections(markdown);
    if sections.len() <= 1 {
        return None;
    }
    let texts: Vec<String> = sections.iter().map(|s| s.text.clone()).collect();
    let scores = bm25_scores(&query_tokens, &texts, 1.5, 0.75);
    if scores.iter().all(|&s| s <= 0.0) {
        return None;
    }
    let mut order: Vec<usize> = (0..sections.len()).collect();
    order.sort_by(|&a, &b| {
        scores[b]
            .partial_cmp(&scores[a])
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.cmp(&b))
    });
    let mut picked: Vec<(usize, String)> = Vec::new();
    let mut remaining = max_chars as usize;
    for i in order {
        if scores[i] <= 0.0 {
            break;
        }
        let text = &sections[i].text;
        if char_count(text) <= remaining {
            remaining -= char_count(text);
            picked.push((i, text.clone()));
        } else if remaining > 0 {
            picked.push((i, char_head(text, remaining).to_string()));
            break;
        }
    }
    if picked.is_empty() {
        return None;
    }
    picked.sort_by_key(|t| t.0);
    let selected = py_strip(
        &picked
            .iter()
            .map(|(_, t)| py_strip(t).to_string())
            .collect::<Vec<_>>()
            .join("\n\n"),
    )
    .to_string();
    if selected.is_empty() {
        return None;
    }
    Some(SelectionOutcome {
        markdown: selected,
        total_sections: sections.len(),
        selected_count: picked.len(),
        anchors: picked
            .iter()
            .map(|(i, _)| sections[*i].anchor.clone())
            .collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atx_and_setext_split() {
        let md = "intro\n\n# Alpha\n\nbody A\n\nTitle\n=====\n\nbody B\n";
        let secs = split_sections(md);
        assert_eq!(secs.len(), 3);
        assert_eq!(secs[0].anchor, "(intro)");
        assert_eq!(secs[1].anchor, "Alpha");
        assert_eq!(secs[2].anchor, "Title");
        assert_eq!(
            secs[1].offset,
            md[..md.find("# Alpha").unwrap()].chars().count()
        );
    }

    #[test]
    fn tokenize_drops_stops_and_singles() {
        assert_eq!(
            tokenize_text("The Quick Brown Fox"),
            vec!["quick", "brown", "fox"]
        );
        assert_eq!(tokenize_text("a b c xray"), vec!["xray"]);
        assert_eq!(tokenize_text("日本語"), vec!["日本", "本語"]);
    }

    #[test]
    fn selection_picks_relevant_under_budget() {
        let md = "# One\n\napple apple\n\n# Two\n\nquantum lattice\n";
        let sel = select_relevant_sections(md, "quantum lattice", 30).unwrap();
        assert_eq!(sel.selected_count, 1);
        assert_eq!(sel.anchors, vec!["Two".to_string()]);
        assert!(sel.markdown.contains("quantum lattice"));
    }

    #[test]
    fn degenerate_inputs_yield_none() {
        assert!(select_relevant_sections("short", "q", 100).is_none());
        assert!(select_relevant_sections("# A\n\ntext\n", "", 5).is_none());
        assert!(select_relevant_sections("# A\n\ntext\n", "zzz", 5).is_none());
    }
}
