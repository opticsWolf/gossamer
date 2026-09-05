//! Query-relevant section selection (port of `gossamer.sections`).
//!
//! Heading-anchored splitting (ATX + Setext), BM25 scoring with ASCII +
//! CJK-bigram tokens, and budget-aware selection. Float operations follow
//! the Python order exactly (f64); string slicing is char-based like
//! Python (never byte-based). Offsets are char indices, matching the
//! dataclass contract (`markdown[s.offset:s.offset+len(s.text)]`).
//!
//! Pinned by `tests/test_rust_parity_sections.py` (vendored original +
//! seeded markdown fuzz).

use pyo3::prelude::*;
use pyo3::types::PyTuple;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use crate::pycompat::{byte_to_char_idx, char_count, char_head, py_strip};
use std::sync::OnceLock;

static HEADING_RE: OnceLock<Regex> = OnceLock::new();

fn heading_re() -> &'static Regex {
    // The Setext branch omits the Python lookaheads (unsupported by the
    // `regex` crate): `setext_title_ok` applies them as post-filters.
    // Equivalence holds because no valid match can start strictly inside
    // a rejected span (its only interior line-start is the underline,
    // which always fails the [-=]+ rule itself).
    HEADING_RE.get_or_init(|| {
        Regex::new(
            r"(?m)^\#{1,6}[ \t]+(?P<atx>.+?)(?:[ \t]+\#+)?[ \t]*$|^(?P<setext>[^\n]+?)[ \t]*\n[ \t]*(?:=|-){2,}[ \t]*$",
        )
        .expect("sections heading regex must compile")
    })
}

/// The three Setext guards from the Python pattern, verbatim:
/// title not blank, not an all-`=`/`-` rule, not a list item.
fn setext_title_ok(title: &str) -> bool {
    if title.chars().all(|c| c == ' ' || c == '\t') {
        return false;
    }
    let stripped = title.trim_matches(|c| c == ' ' || c == '\t');
    if !stripped.is_empty() && stripped.chars().all(|c| c == '-' || c == '=') {
        return false;
    }
    let mut chars = title.chars();
    match (chars.next(), chars.next()) {
        (Some(a), Some(b))
            if matches!(a, '-' | '*' | '+') && (b == ' ' || b == '\t') =>
        {
            false
        }
        _ => true,
    }
}

static ASCII_TOKEN_RE: OnceLock<Regex> = OnceLock::new();

fn ascii_token_re() -> &'static Regex {
    ASCII_TOKEN_RE
        .get_or_init(|| Regex::new(r"[a-z0-9]+").expect("ascii token regex must compile"))
}

static CJK_RUN_RE: OnceLock<Regex> = OnceLock::new();

fn cjk_run_re() -> &'static Regex {
    CJK_RUN_RE.get_or_init(|| {
        Regex::new("[\u{3040}-\u{30ff}\u{4e00}-\u{9fff}\u{ac00}-\u{d7af}]+")
            .expect("cjk run regex must compile")
    })
}

fn stopwords() -> &'static HashSet<&'static str> {
    static WORDS: OnceLock<HashSet<&'static str>> = OnceLock::new();
    WORDS.get_or_init(|| {
        "a an and are as at be been but by can could did do does for from had \
         has have he her his i if in into is it its just me my no not of on \
         one only or our she so that the their them then these they this to \
         too was we were what when which who will with you your"
            .split_whitespace()
            .collect()
    })
}


#[pyclass]
pub struct Section {
    #[pyo3(get)]
    pub anchor: String,
    #[pyo3(get)]
    pub text: String,
    #[pyo3(get)]
    pub offset: usize,
}

#[pyclass]
pub struct SectionSelection {
    #[pyo3(get)]
    pub markdown: String,
    #[pyo3(get)]
    pub total_sections: usize,
    #[pyo3(get)]
    pub selected_count: usize,
    anchors: Vec<String>,
}

#[pymethods]
impl SectionSelection {
    /// Tuple (not list): the dataclass contract compares
    /// `sel.anchors == ("One", "Two")`.
    #[getter]
    fn anchors<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyTuple>> {
        PyTuple::new(py, &self.anchors)
    }
}

pub fn split_sections_impl(markdown: &str) -> Vec<Section> {
    // Emptiness gate uses the Python strip set (differs from `.trim()` on
    // exotic controls like 0x1C-0x1F and NEL).
    if py_strip(markdown).is_empty() {
        return Vec::new();
    }
    let caps_list: Vec<regex::Captures> = heading_re()
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
        .map(|c| c.get(0).expect("whole match").start())
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

pub fn tokenize_text_impl(text: &str) -> Vec<String> {
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

pub fn bm25_scores_impl(
    query_tokens: &[String],
    docs: &[String],
    k1: f64,
    b: f64,
) -> Vec<f64> {
    let n = docs.len();
    if n == 0 || query_tokens.is_empty() {
        return vec![0.0; n];
    }
    let doc_tokens: Vec<Vec<String>> =
        docs.iter().map(|d| tokenize_text_impl(d)).collect();
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
    // Dedupe query terms, keep order; drop terms absent from every doc.
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

pub struct SelectionOutcome {
    pub markdown: String,
    pub total_sections: usize,
    pub selected_count: usize,
    pub anchors: Vec<String>,
}

pub fn select_relevant_sections_impl(
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
    let query_tokens = tokenize_text_impl(query);
    if query_tokens.is_empty() {
        return None;
    }
    let sections = split_sections_impl(markdown);
    if sections.len() <= 1 {
        return None;
    }
    let texts: Vec<String> = sections.iter().map(|s| s.text.clone()).collect();
    let scores = bm25_scores_impl(&query_tokens, &texts, 1.5, 0.75);
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
            remaining = 0;
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

// ── PyO3 wrappers ────────────────────────────────────────────────

#[pyfunction]
pub fn split_sections(markdown: &str) -> Vec<Section> {
    split_sections_impl(markdown)
}

#[pyfunction]
pub fn tokenize_text(text: &str) -> Vec<String> {
    tokenize_text_impl(text)
}

#[pyfunction]
#[pyo3(signature = (query_tokens, docs, k1 = 1.5, b = 0.75))]
pub fn bm25_scores(
    query_tokens: Vec<String>,
    docs: Vec<String>,
    k1: f64,
    b: f64,
) -> Vec<f64> {
    bm25_scores_impl(&query_tokens, &docs, k1, b)
}

#[pyfunction]
pub fn select_relevant_sections(
    markdown: &str,
    query: &str,
    max_chars: i64,
) -> Option<SectionSelection> {
    select_relevant_sections_impl(markdown, query, max_chars).map(|o| SectionSelection {
        markdown: o.markdown,
        total_sections: o.total_sections,
        selected_count: o.selected_count,
        anchors: o.anchors,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atx_and_setext_split() {
        let md = "intro\n\n# Alpha\n\nbody A\n\nTitle\n=====\n\nbody B\n";
        let secs = split_sections_impl(md);
        assert_eq!(secs.len(), 3);
        assert_eq!(secs[0].anchor, "(intro)");
        assert_eq!(secs[1].anchor, "Alpha");
        assert_eq!(secs[2].anchor, "Title");
        assert_eq!(secs[1].offset, md[..md.find("# Alpha").unwrap()].chars().count());
    }

    #[test]
    fn tokenize_drops_stops_and_singles() {
        assert_eq!(
            tokenize_text_impl("The Quick Brown Fox"),
            vec!["quick", "brown", "fox"]
        );
        assert_eq!(tokenize_text_impl("a b c xray"), vec!["xray"]);
        assert_eq!(tokenize_text_impl("日本語"), vec!["日本", "本語"]);
    }

    #[test]
    fn selection_picks_relevant_under_budget() {
        let md = "# One\n\napple apple\n\n# Two\n\nquantum lattice\n";
        let sel = select_relevant_sections_impl(md, "quantum lattice", 30).unwrap();
        assert_eq!(sel.selected_count, 1);
        assert_eq!(sel.anchors, vec!["Two".to_string()]);
        assert!(sel.markdown.contains("quantum lattice"));
    }

    #[test]
    fn degenerate_inputs_yield_none() {
        assert!(select_relevant_sections_impl("short", "q", 100).is_none());
        assert!(select_relevant_sections_impl("# A\n\ntext\n", "", 5).is_none());
        assert!(select_relevant_sections_impl("# A\n\ntext\n", "zzz", 5).is_none());
    }
}
