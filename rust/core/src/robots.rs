//! robots.txt parsing and matching kernels.
//!
//! Pure port of the legacy PyO3 `src/robots.rs`. Network fetching, TTL caching, and threading stay
//! in the Python `RobotsChecker`; this module only handles deterministic string kernels.

use regex::Regex;

use crate::urls::has_scheme;

/// Path (+ non-empty query string) that robots rules match against.
///
/// `runtime_minor` selects CPython scheme-detection behavior; `None` uses the newest spelling.
///
/// # Examples
///
/// ```
/// use gossamer::robots::url_path;
///
/// assert_eq!(url_path("https://h/a?x=1#f", None), "/a?x=1");
/// assert_eq!(url_path("https://h", None), "/");
/// ```
pub fn url_path(url: &str, runtime_minor: Option<u32>) -> String {
    let after = if let Some(i) = url.find("://") {
        let rest = &url[i + 3..];
        match rest.find(['/', '?', '#']) {
            Some(j) => &rest[j..],
            None => "/",
        }
    } else if let Some(i) = url.find(':') {
        if has_scheme(&url[..i + 1], runtime_minor) {
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

/// Compiles one robots path rule (`*` → `.*`, trailing `$` anchors).
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

/// Python `str.splitlines()` boundaries, including the final (possibly empty) segment.
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

/// Parses robots.txt and returns `(rules, crawl_delay)` for one user agent.
///
/// Group selection: exact UA match beats substring; strictly greater wins (first wins ties), then
/// the first `*` group. Delay falls back to the first `*` group carrying one.
pub fn parse_robots(text: &str, user_agent: &str) -> (Vec<(bool, String)>, Option<f64>) {
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
            current = Some(Group {
                agents: vec!["*".to_string()],
                rules: Vec::new(),
                crawl_delay: None,
            });
        }
        let g = current
            .as_mut()
            .expect("current group must be open after implicit-star setup");
        if directive == "disallow" || directive == "allow" {
            if !value.is_empty() {
                g.rules.push((directive == "allow", value));
            }
        } else if (directive == "crawl-delay"
            || directive == "crawl delay"
            || directive == "crawler-delay")
            && let Ok(v) = value.parse::<f64>()
        {
            g.crawl_delay = Some(v.max(0.0));
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

/// Evaluates robots rules; longest matching path wins, ties go to Allow.
pub fn match_rules(rules: &[(bool, String)], path: &str) -> bool {
    let mut best: Option<(usize, bool)> = None;
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn group_selection_prefers_exact_ua() {
        let text = "User-agent: bot\nDisallow: /a\nUser-agent: *\nDisallow: /b\n";
        let (rules, _) = parse_robots(text, "mybot/2.0");
        assert_eq!(rules, vec![(false, "/a".to_string())]);
        let (rules, _) = parse_robots(text, "other/1.0");
        assert_eq!(rules, vec![(false, "/b".to_string())]);
    }

    #[test]
    fn longest_match_wins_tie_goes_allow() {
        let rules = vec![
            (false, "/tmp".to_string()),
            (true, "/tmp/public".to_string()),
        ];
        assert!(!match_rules(&rules, "/tmp/x"));
        assert!(match_rules(&rules, "/tmp/public/x"));
        assert!(match_rules(&rules, "/other"));
    }

    #[test]
    fn wildcards_anchors_and_delay_fallback() {
        let text = "User-agent: *\nDisallow: /*.pdf$\nCrawl-delay: 5\n";
        let (rules, delay) = parse_robots(text, "anything");
        assert_eq!(delay, Some(5.0));
        assert!(!match_rules(&rules, "/docs/f.pdf"));
        assert!(match_rules(&rules, "/docs/f.pdf/x"));
        let text2 = "User-agent: a\nDisallow: /a\nUser-agent: *\nCrawl-delay: 7\n";
        let (_, delay2) = parse_robots(text2, "a");
        assert_eq!(delay2, Some(7.0));
    }

    #[test]
    fn url_paths() {
        assert_eq!(url_path("https://h/a?x=1#f", None), "/a?x=1");
        assert_eq!(url_path("https://h", None), "/");
        assert_eq!(url_path("notaurl?a=b", None), "notaurl?a=b");
        assert_eq!(url_path("https://h/p?", None), "/p");
    }
}
