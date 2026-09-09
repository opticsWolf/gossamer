//! robots.txt parsing and matching (port of the pure parts of
//! `gossamer.robots`).
//!
//! Ported: path extraction (`_url_path`), rule compilation
//! (`_path_regex` — `*` wildcards, trailing-`$` anchors), group parsing
//! and selection (`_parse_robots`), and longest-match-wins evaluation.
//! Deliberately NOT ported: `RobotsChecker` (per-host cache, TTLs,
//! threading, injectable httpx client) — orchestration stays Python and
//! calls these kernels.
//!
//! Pinned by `tests/test_rust_parity_robots.py` (vendored original +
//! seeded fuzz over rule files, user agents, and paths).

use pyo3::prelude::*;
use regex::Regex;

/// Path (+ non-empty query string) that rules match against (mirrors
/// `_url_path`: empty path → `/`, fragment never included, empty query
/// adds no `?`).
pub fn url_path_impl(url: &str) -> String {
    let after = if let Some(i) = url.find("://") {
        // scheme://host… → drop the host part.
        let rest = &url[i + 3..];
        match rest.find(|c| c == '/' || c == '?' || c == '#') {
            Some(j) => &rest[j..],
            None => "/",
        }
    } else if let Some(i) = url.find(':') {
        if crate::urls::has_scheme_pub(&url[..i + 1]) {
            &url[i + 1..]
        } else {
            url
        }
    } else {
        url
    };
    let no_frag = after.split('#').next().unwrap_or("");
    let (path, query) = match no_frag.find('?') {
        Some(i) => (&no_frag[..i], Some(&no_frag[i + 1..])),
        None => (no_frag, None),
    };
    let path = if path.is_empty() { "/" } else { path };
    match query {
        Some(q) if !q.is_empty() => format!("{path}?{q}"),
        _ => path.to_string(),
    }
}

/// Compile one robots path rule (`*` → `.*`, trailing `$` anchors).
fn path_regex(pattern: &str) -> Result<Regex, String> {
    let (anchored, body) = match pattern.strip_suffix('$') {
        Some(b) => (true, b),
        None => (false, pattern),
    };
    let mut re = String::from("^");
    for ch in body.chars() {
        if ch == '*' {
            re.push_str(".*");
        } else {
            re.push_str(&regex::escape(&ch.to_string()));
        }
    }
    if anchored {
        re.push('$');
    }
    Regex::new(&re).map_err(|e| e.to_string())
}

struct Group {
    agents: Vec<String>,
    rules: Vec<(bool, String)>,
    crawl_delay: Option<f64>,
}

/// Parse robots.txt into `(rules, crawl_delay)` for one user agent.
/// Group selection: exact UA match (3) beats substring (2); strictly
/// greater wins (first wins ties); then the first `*` group; delay
/// falls back to the first `*` group carrying one.
/// Python `str.splitlines()` boundaries: \n, \r, \r\n, \x0b, \x0c,
/// \x1c-\x1e, \x85, \u2028, \u2029.
fn split_lines_py(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0usize;
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut i = 0usize;
    while i < chars.len() {
        let (bi, c) = chars[i];
        let boundary = match c {
            '\n' => 1,
            '\r' => {
                if i + 1 < chars.len() && chars[i + 1].1 == '\n' {
                    2
                } else {
                    1
                }
            }
            '\x0B' | '\x0C' | '\u{1C}' | '\u{1D}' | '\u{1E}' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => 1,
            _ => 0,
        };
        if boundary > 0 {
            lines.push(&text[start..bi]);
            i += boundary;
            start = if i < chars.len() {
                chars[i].0
            } else {
                text.len()
            };
        } else {
            i += 1;
        }
    }
    lines.push(&text[start..]);
    lines
}

/// Parse robots.txt into `(rules, crawl_delay)` for one user agent.
/// Group selection: exact UA match (3) beats substring (2); strictly
/// greater wins (first wins ties); then the first `*` group; delay
/// falls back to the first `*` group carrying one.
pub fn parse_robots_impl(
    text: &str,
    user_agent: &str,
) -> (Vec<(bool, String)>, Option<f64>) {
    let mut groups: Vec<Group> = Vec::new();
    let mut current: Option<Group> = None;
    for raw_line in split_lines_py(text) {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some(colon) = line.find(':') else {
            continue;
        };
        let directive = line[..colon].trim().to_lowercase();
        let value = line[colon + 1..].trim().to_string();
        if directive == "user-agent" {
            if let Some(g) = current.take() {
                groups.push(g);
            }
            current = Some(Group {
                agents: vec![value],
                rules: Vec::new(),
                crawl_delay: None,
            });
            continue;
        }
        if current.is_none() {
            // Rules before any User-agent line: implicit * group.
            current = Some(Group {
                agents: vec!["*".to_string()],
                rules: Vec::new(),
                crawl_delay: None,
            });
        }
        let g = current.as_mut().expect("group open");
        if directive == "disallow" || directive == "allow" {
            // An empty "Disallow:" is a no-op.
            if !value.is_empty() {
                g.rules.push((directive == "allow", value));
            }
        } else if directive == "crawl-delay"
            || directive == "crawl delay"
            || directive == "crawler-delay"
        {
            if let Ok(v) = value.parse::<f64>() {
                g.crawl_delay = Some(v.max(0.0));
            }
        }
    }
    if let Some(g) = current.take() {
        groups.push(g);
    }
    if groups.is_empty() {
        return (Vec::new(), None);
    }
    let ua = user_agent.to_lowercase();
    let score = |group: &Group| -> u8 {
        let mut best = 0u8;
        for agent in &group.agents {
            let a = agent.trim().to_lowercase();
            if a == ua {
                return 3;
            }
            if ua.contains(&a) {
                best = 2;
            }
        }
        best
    };
    let mut best_score = 0u8;
    let mut chosen: Option<usize> = None;
    for (i, group) in groups.iter().enumerate() {
        let s = score(group);
        if s > best_score {
            best_score = s;
            chosen = Some(i);
        }
    }
    if chosen.is_none() {
        chosen = groups.iter().position(|g| {
            g.agents
                .iter()
                .any(|a| a.trim().to_lowercase() == "*")
        });
    }
    let Some(ci) = chosen else {
        return (Vec::new(), None);
    };
    let mut delay = groups[ci].crawl_delay;
    if delay.is_none() {
        for group in &groups {
            if group.crawl_delay.is_some()
                && group
                    .agents
                    .iter()
                    .any(|a| a.trim().to_lowercase() == "*")
            {
                delay = group.crawl_delay;
                break;
            }
        }
    }
    (groups[ci].rules.clone(), delay)
}

/// Longest matching path wins; ties go to Allow (mirrors `is_allowed`).
pub fn match_rules_impl(rules: &[(bool, String)], path: &str) -> bool {
    let mut best: Option<(usize, bool)> = None; // (path_len_chars, allow)
    for (allow, rule_path) in rules {
        let re = match path_regex(rule_path) {
            Ok(re) => re,
            Err(_) => continue,
        };
        if re.is_match(path) {
            let len = rule_path.chars().count();
            match best {
                None => best = Some((len, *allow)),
                Some((blen, ballow)) => {
                    if len > blen || (len == blen && *allow && !ballow) {
                        best = Some((len, *allow));
                    }
                }
            }
        }
    }
    best.map(|(_, allow)| allow).unwrap_or(true)
}

// ── PyO3 wrappers ────────────────────────────────────────────────

#[pyfunction]
pub fn robots_url_path(url: &str) -> String {
    url_path_impl(url)
}

#[pyfunction]
pub fn robots_parse(
    text: &str,
    user_agent: &str,
) -> (Vec<(bool, String)>, Option<f64>) {
    parse_robots_impl(text, user_agent)
}

#[pyfunction]
pub fn robots_match_url(rules: Vec<(bool, String)>, url_path: &str) -> bool {
    match_rules_impl(&rules, url_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn group_selection_prefers_exact_ua() {
        let text = "User-agent: bot\nDisallow: /a\nUser-agent: *\nDisallow: /b\n";
        let (rules, _) = parse_robots_impl(text, "mybot/2.0");
        assert_eq!(rules, vec![(false, "/a".to_string())]);
        let (rules, _) = parse_robots_impl(text, "other/1.0");
        assert_eq!(rules, vec![(false, "/b".to_string())]);
    }

    #[test]
    fn longest_match_wins_tie_goes_allow() {
        let rules = vec![
            (false, "/tmp".to_string()),
            (true, "/tmp/public".to_string()),
        ];
        assert!(!match_rules_impl(&rules, "/tmp/x"));
        assert!(match_rules_impl(&rules, "/tmp/public/x"));
        assert!(match_rules_impl(&rules, "/other"));
    }

    #[test]
    fn wildcards_anchors_and_delay_fallback() {
        let text = "User-agent: *\nDisallow: /*.pdf$\nCrawl-delay: 5\n";
        let (rules, delay) = parse_robots_impl(text, "anything");
        assert_eq!(delay, Some(5.0));
        assert!(!match_rules_impl(&rules, "/docs/f.pdf"));
        assert!(match_rules_impl(&rules, "/docs/f.pdf/x"));
        let text2 = "User-agent: a\nDisallow: /a\nUser-agent: *\nCrawl-delay: 7\n";
        let (_, delay2) = parse_robots_impl(text2, "a");
        assert_eq!(delay2, Some(7.0));
    }

    #[test]
    fn url_paths() {
        assert_eq!(url_path_impl("https://h/a?x=1#f"), "/a?x=1");
        assert_eq!(url_path_impl("https://h"), "/");
        assert_eq!(url_path_impl("notaurl?a=b"), "notaurl?a=b");
        assert_eq!(url_path_impl("https://h/p?"), "/p");
    }
}
