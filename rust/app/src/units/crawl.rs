//! `gossamer crawl`: bounded focused link-graph traversal in Rust.

use std::collections::{HashMap, HashSet};
use std::process::ExitCode;

use gossamer::cli_args::Parsed;
use gossamer::discovery::fetch_text;
use gossamer::runtime::RuntimeState;
use gossamer::search::{duckduckgo_results, duckduckgo_search_url};
use gossamer::service::crawl::{
    CrawlCorpus, crawl_anchor_context, crawl_excerpt, crawl_expand_query, crawl_is_document,
    crawl_path_prior, crawl_score, crawl_term_hits, crawl_thesaurus_clusters, crawl_tokens,
    crawl_topic_words,
};

use gossamer::jsonfmt::py_dumps_pretty;
use gossamer::ssrf::check_url;
use gossamer::urls::normalize_url;
use serde_json::{Value, json};
use url::Url;

fn unsupported() -> ExitCode {
    pyeprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

const fn push_depth_zero() -> usize {
    0
}

fn skip_path(path: &str) -> bool {
    const PREFIXES: [&str; 17] = [
        "/login",
        "/signin",
        "/sign-in",
        "/signout",
        "/logout",
        "/signup",
        "/register",
        "/account",
        "/profile",
        "/cart",
        "/checkout",
        "/search",
        "/tag/",
        "/tags/",
        "/author/",
        "/feed",
        "/track",
    ];
    const EXTENSIONS: [&str; 26] = [
        ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".woff",
        ".woff2", ".ttf", ".eot", ".otf", ".mp3", ".mp4", ".webm", ".avi", ".mov", ".zip", ".gz",
        ".tar", ".rar", ".exe", ".dmg",
    ];
    let lower = path.to_ascii_lowercase();
    PREFIXES.iter().any(|prefix| lower.starts_with(prefix))
        || EXTENSIONS.iter().any(|ext| lower.ends_with(ext))
}

fn skip_reason(path: &str) -> Option<&'static str> {
    const PREFIXES: [&str; 17] = [
        "/login",
        "/signin",
        "/sign-in",
        "/signout",
        "/logout",
        "/signup",
        "/register",
        "/account",
        "/profile",
        "/cart",
        "/checkout",
        "/search",
        "/tag/",
        "/tags/",
        "/author/",
        "/feed",
        "/track",
    ];
    const EXTENSIONS: [&str; 26] = [
        ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".woff",
        ".woff2", ".ttf", ".eot", ".otf", ".mp3", ".mp4", ".webm", ".avi", ".mov", ".zip", ".gz",
        ".tar", ".rar", ".exe", ".dmg",
    ];
    let lower = path.to_ascii_lowercase();
    if PREFIXES.iter().any(|prefix| lower.starts_with(prefix)) {
        return Some("boilerplate path");
    }
    if EXTENSIONS.iter().any(|ext| lower.ends_with(ext)) {
        return Some("asset");
    }
    None
}

fn note_skipped(
    skipped: &mut Vec<Value>,
    skip_seen: &mut HashSet<(String, String)>,
    skipped_total: &mut usize,
    url: &str,
    reason: &str,
) {
    let mark = (url.to_string(), reason.to_string());
    if !skip_seen.insert(mark) {
        return;
    }
    *skipped_total += 1;
    if skipped.len() < 30 {
        skipped.push(json!({"url": url, "reason": reason}));
    }
}

fn trim_queue(queue: &mut Vec<(f64, usize, usize, String, String)>, queue_dropped: &mut usize) {
    const CAP: usize = 200;
    if queue.len() > CAP {
        queue.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
        *queue_dropped += queue.len() - CAP;
        queue.drain(0..queue.len() - CAP);
    }
}

fn crawl_label(url: &str, anchor: &str, context: Option<Vec<String>>) -> Vec<String> {
    let mut label = crawl_tokens(Some(anchor));
    let path = Url::parse(url)
        .map(|value| value.path().to_string())
        .unwrap_or_default();
    label.extend(crawl_tokens(Some(&path)));
    if let Some(context) = context {
        label.extend(context);
    }
    label.sort();
    label.dedup();
    label
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

#[expect(
    clippy::too_many_arguments,
    reason = "mirrors the Python crawl external-candidate inputs exactly"
)]
fn add_external(
    url: &str,
    anchor: &str,
    push_depth: usize,
    rank_bonus: f64,
    exempt_floor: bool,
    same_host: bool,
    root_host: Option<&str>,
    min_score: f64,
    below_reason: &str,
    terms: &[String],
    root_terms: &[String],
    idf: &Value,
    base_terms: &[String],
    corpus_n: i64,
    seen: &mut Vec<String>,
    queue: &mut Vec<(f64, usize, usize, String, String)>,
    skipped: &mut Vec<Value>,
    skip_seen: &mut HashSet<(String, String)>,
    skipped_total: &mut usize,
    seq: &mut usize,
) -> bool {
    if seen.iter().any(|value| value == url) {
        return false;
    }
    if same_host
        && root_host
            != Url::parse(url)
                .ok()
                .and_then(|value| value.host_str().map(str::to_string))
                .as_deref()
    {
        note_skipped(skipped, skip_seen, skipped_total, url, "external host");
        return false;
    }
    let path = Url::parse(url)
        .map(|value| value.path().to_ascii_lowercase())
        .unwrap_or_default();
    if skip_path(&path) {
        let reason = if path.ends_with(".css")
            || path.ends_with(".js")
            || path.ends_with(".png")
            || path.ends_with(".jpg")
        {
            "asset"
        } else {
            "boilerplate path"
        };
        note_skipped(skipped, skip_seen, skipped_total, url, reason);
        return false;
    }
    let mut label = crawl_tokens(Some(anchor));
    let path_label = Url::parse(url)
        .map(|value| value.path().to_string())
        .unwrap_or_default();
    label.extend(crawl_tokens(Some(&path_label)));
    let mut score = crawl_score(
        &label,
        terms,
        root_terms,
        Some(idf),
        Some(base_terms),
        corpus_n,
        crawl_path_prior(url),
    ) + rank_bonus;
    if score < min_score && !exempt_floor {
        note_skipped(skipped, skip_seen, skipped_total, url, below_reason);
        return false;
    }
    score = score.max(min_score);
    seen.push(url.to_string());
    queue.push((
        score * 0.7_f64.powi(push_depth as i32),
        *seq,
        push_depth,
        url.to_string(),
        anchor.to_string(),
    ));
    *seq += 1;
    true
}

#[expect(
    clippy::too_many_arguments,
    reason = "mirrors the Python crawl page-record inputs exactly"
)]
fn page_record(
    url: &str,
    depth: usize,
    score: f64,
    markdown: &str,
    links_total: usize,
    fetch_method: &str,
    terms: &[String],
    excerpts: bool,
) -> Value {
    let mut record = json!({
        "url": url,
        "depth": depth,
        "status": "ok",
        "title": "",
        "score": score,
        "markdown": markdown.chars().take(300).collect::<String>(),
        "links_total": links_total,
        "fetch_method": fetch_method,
        "content_chars": markdown.chars().count(),
        "term_hits": crawl_term_hits(Some(markdown), terms),
    });
    if excerpts && let Some(excerpt) = crawl_excerpt(Some(markdown), terms, 300, 100) {
        record
            .as_object_mut()
            .expect("page record object")
            .insert("excerpt".to_string(), excerpt.into());
    }
    record
}

#[expect(
    clippy::too_many_arguments,
    reason = "mirrors the Python crawl child-record inputs exactly"
)]
fn child_record(
    url: &str,
    depth: usize,
    score: f64,
    markdown: &str,
    links_total: usize,
    fetch_method: &str,
    title: &str,
    terms: &[String],
    excerpts: bool,
) -> Value {
    let mut record = json!({
        "url": url,
        "depth": depth,
        "score": round3(score),
        "status": "ok",
        "fetch_method": fetch_method,
        "title": title,
        "markdown": markdown.chars().take(300).collect::<String>(),
        "links_total": links_total,
        "content_chars": markdown.chars().count(),
        "term_hits": crawl_term_hits(Some(markdown), terms),
    });
    if excerpts && let Some(excerpt) = crawl_excerpt(Some(markdown), terms, 300, 100) {
        record
            .as_object_mut()
            .expect("page record object")
            .insert("excerpt".to_string(), excerpt.into());
    }
    record
}

fn config_int(parsed: &Parsed, key: &str, env: &str, default: i64) -> i64 {
    crate::units::settings::int(parsed, key, env, default)
}

fn config_str(parsed: &Parsed, key: &str, env: &str, default: &str) -> String {
    crate::units::settings::text(parsed, key, env, default)
}

fn shrink_payload(result: &Value, budget: Option<i64>) -> String {
    let mut out = result.clone();
    if let Some(budget) = budget {
        let keep = (budget / 500).max(1) as usize;
        let omitted = out
            .get("pages")
            .and_then(Value::as_array)
            .map(|pages| pages.len().saturating_sub(keep))
            .unwrap_or(0);
        if omitted > 0
            && let Some(pages) = out.get_mut("pages").and_then(Value::as_array_mut)
        {
            pages.truncate(keep);
        }
        if omitted > 0
            && let Some(object) = out.as_object_mut()
        {
            object.insert("pages_omitted".to_string(), Value::from(omitted));
        }
    }
    py_dumps_pretty(&out)
}

fn fit_crawl(result: &Value, parsed: &Parsed) -> String {
    let char_limit = config_int(
        parsed,
        "max_markdown_chars",
        "GOSSAMER_MAX_MARKDOWN_CHARS",
        8000,
    );
    let token_limit = config_int(parsed, "max_tokens", "GOSSAMER_MAX_TOKENS", 0);
    let model = config_str(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
    let fits = |text: &str| {
        gossamer::budget::json_fits(text, char_limit, token_limit, &model).unwrap_or(false)
    };
    let out = shrink_payload(result, None);
    if fits(&out) {
        return out;
    }
    let mut budget = if char_limit > 0 {
        char_limit
    } else {
        out.chars().count() as i64
    };
    while budget > 240 {
        budget /= 2;
        let candidate = shrink_payload(result, Some(budget));
        if fits(&candidate) {
            return candidate;
        }
    }
    pyeprintln!(
        "Payload could not be shrunk into the output budget; returning an overflow envelope instead of invalid JSON"
    );
    py_dumps_pretty(&json!({
        "root": result.get("root").cloned().unwrap_or(Value::Null),
        "error": "crawl result too large for the output budget",
        "hint": "lower max_pages or raise max_tokens",
    }))
}

fn inspect_child(
    parsed: &Parsed,
    url: &str,
    use_smart: &str,
    cache_dir: &str,
) -> Result<Value, String> {
    let exe = std::env::current_exe().map_err(|error| error.to_string())?;
    let config_path = crate::units::settings::config_path(parsed).filter(|path| path.is_file());
    let mut command = std::process::Command::new(exe);
    command.arg("--cache-dir").arg(cache_dir);
    if let Some(path) = config_path.as_deref() {
        command.arg("--config").arg(path);
    }
    if let Some(value) = parsed.keystore() {
        command.arg("--keystore").arg(value);
    }
    if let Some(value) = parsed.log_level() {
        command.arg("--log-level").arg(value);
    }
    if let Some(value) = parsed.fetch_mode() {
        command.arg("--fetch-mode").arg(value);
    }
    command
        .arg("inspect")
        .arg(url)
        .arg("--use-smart")
        .arg(use_smart);
    let output = command.output().map_err(|error| error.to_string())?;
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !stderr.is_empty() {
        pyeprint!("{stderr}");
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let page: Value = serde_json::from_str(stdout.trim()).map_err(|_| stdout.trim().to_string())?;
    if let Some(error) = page.get("error").and_then(Value::as_str) {
        return Err(error.to_string());
    }
    if let Some(warning) = page.get("warning").and_then(Value::as_str) {
        return Err(warning.to_string());
    }
    Ok(page)
}

fn links_from_page(page: &Value) -> Vec<(String, String)> {
    page.get("follow_up_links")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    Some((
                        item.get("url")?.as_str()?.to_string(),
                        item.get("title")?.as_str()?.to_string(),
                    ))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn page_markdown(page: &Value) -> String {
    page.get("markdown")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn page_title(page: &Value) -> String {
    page.pointer("/metadata/title")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn page_links_total(page: &Value) -> usize {
    page.get("total_links").and_then(Value::as_u64).unwrap_or(0) as usize
}

fn page_fetch_method(page: &Value) -> String {
    page.get("fetch_method")
        .and_then(Value::as_str)
        .unwrap_or("static")
        .to_string()
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let Some(root) = parsed.positional("root_url").map(str::to_string) else {
        return unsupported();
    };
    let query = parsed.value("query").map(str::to_string);
    let max_depth = parsed.int("max_depth").unwrap_or(3).clamp(0, 5) as usize;
    let max_pages = parsed.int("max_pages").unwrap_or(15).clamp(1, 50) as usize;
    let min_score = parsed.float("min_score").unwrap_or(0.05).max(0.0);
    let same_host = parsed.flag("same_host");
    let excerpts = parsed.flag("excerpts");
    let search_prior = parsed.flag("search_prior");
    let seeds: Vec<String> = parsed.values("seed_urls").to_vec();
    let cache_dir = crate::units::settings::cache_dir(parsed);
    let use_smart = parsed.value("use_smart").unwrap_or("auto");
    let runtime = match RuntimeState::new(crate::units::settings::runtime(parsed, &cache_dir)) {
        Ok(value) => value,
        Err(error) => {
            pyeprintln!("gossamer: error: {error}");
            return ExitCode::FAILURE;
        }
    };
    let root = match runtime.prepare_url(&root) {
        Ok(url) => url,
        Err(error) => {
            if error.error_type() == "SsrfBlockedError" {
                pyeprintln!(
                    "Blocked non-public URL {}: {}",
                    error.raw(),
                    error.message()
                );
            }
            let message = error.message();
            let message = message
                .strip_prefix("ValueError: ")
                .or_else(|| message.strip_prefix("SsrfBlockedError: "))
                .unwrap_or(message);
            pyprintln!(
                "{}",
                py_dumps_pretty(&json!({"error": format!("crawl: {message}")}))
            );
            return ExitCode::SUCCESS;
        }
    };
    let root_page = match inspect_child(parsed, &root, use_smart, &cache_dir) {
        Ok(page) => page,
        Err(detail) => {
            let payload =
                json!({"error": format!("crawl: root fetch failed: {detail}"), "root": root});
            pyprintln!("{}", py_dumps_pretty(&payload));
            return ExitCode::SUCCESS;
        }
    };
    runtime.mark_visited(&root);
    let full = page_markdown(&root_page);
    let links = links_from_page(&root_page);
    let root_total_links = page_links_total(&root_page);
    let root_fetch_method = page_fetch_method(&root_page);
    let root_title = page_title(&root_page);
    let base_terms = match query.as_deref() {
        Some(value) if !value.trim().is_empty() => crawl_tokens(Some(value)),
        _ => crawl_topic_words(Some(&format!("{full} {root_title}")), 40),
    };
    let query_echo = if query
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
    {
        query.clone().unwrap_or_default()
    } else {
        "derived from root page".to_string()
    };
    let (terms, expanded) = crawl_expand_query(&base_terms, crawl_thesaurus_clusters());
    let query_echo = if expanded == 0 {
        query_echo
    } else {
        format!("{query_echo} +{expanded}")
    };
    let mut pages = vec![page_record(
        &root,
        0,
        1.0,
        &full,
        root_total_links,
        &root_fetch_method,
        &terms,
        excerpts,
    )];
    if let Some(record) = pages[0].as_object_mut() {
        record.insert("title".to_string(), json!(root_title));
    }
    let root_host = Url::parse(&root)
        .ok()
        .and_then(|url| url.host_str().map(str::to_string));

    let mut queue: Vec<(f64, usize, usize, String, String)> = Vec::new();
    let mut seen: Vec<String> = vec![root.clone()];
    let mut doc_seen: HashSet<String> = HashSet::new();
    let mut documents: Vec<Value> = Vec::new();
    let mut documents_total = 0usize;
    let mut documents_below_score = 0usize;
    let mut queue_dropped = 0usize;
    let mut errors: Vec<Value> = Vec::new();
    let mut errors_total = 0usize;
    let mut seq = 0usize;
    let mut skipped: Vec<Value> = Vec::new();
    let mut skipped_total = 0usize;
    let mut skip_seen: HashSet<(String, String)> = HashSet::new();
    let mut search_results = 0usize;
    let page_terms = crawl_topic_words(Some(&format!("{full} {root_title}")), 40);
    let mut corpus = CrawlCorpus::new();
    corpus.add_page(&crawl_topic_words(
        Some(&format!("{full} {root_title}")),
        40,
    ));
    let idf = corpus.idf();
    let corpus_n = corpus.len();
    let mut context_cache: HashMap<String, Vec<String>> = HashMap::new();
    if max_depth >= 1 {
        for (link_url, raw_anchor) in &links {
            let Ok(candidate) = Url::parse(link_url) else {
                continue;
            };
            if !matches!(candidate.scheme(), "http" | "https") {
                continue;
            }
            if seen.iter().any(|value| value == link_url) {
                continue;
            }
            let anchor = raw_anchor.trim();
            if crawl_is_document(link_url) {
                let key = runtime.identity_key(link_url);
                if !doc_seen.insert(key) {
                    continue;
                }
                documents_total += 1;
                let label = crawl_label(link_url, anchor, None);
                let doc_score = crawl_score(
                    &label,
                    &terms,
                    &page_terms,
                    Some(&idf),
                    Some(&base_terms),
                    corpus_n,
                    crawl_path_prior(link_url),
                );
                if doc_score < min_score {
                    documents_below_score += 1;
                    note_skipped(
                        &mut skipped,
                        &mut skip_seen,
                        &mut skipped_total,
                        link_url,
                        "below min score",
                    );
                } else if documents.len() < 30 {
                    documents.push(json!({
                        "url": link_url,
                        "anchor": anchor,
                        "score": round3(doc_score),
                    }));
                }
                continue;
            }
            if same_host && root_host.as_deref() != candidate.host_str() {
                note_skipped(
                    &mut skipped,
                    &mut skip_seen,
                    &mut skipped_total,
                    link_url,
                    "external host",
                );
                continue;
            }
            let path = candidate.path().to_ascii_lowercase();
            if let Some(reason) = skip_reason(&path) {
                note_skipped(
                    &mut skipped,
                    &mut skip_seen,
                    &mut skipped_total,
                    link_url,
                    reason,
                );
                continue;
            }
            let context = if anchor.is_empty() || anchor == "(untitled)" {
                None
            } else if let Some(context) = context_cache.get(anchor) {
                Some(context.clone())
            } else {
                let context = crawl_anchor_context(Some(&full), Some(anchor), 50, 8);
                context_cache.insert(anchor.to_string(), context.clone());
                Some(context)
            };
            let label = crawl_label(link_url, anchor, context);
            let score = crawl_score(
                &label,
                &terms,
                &page_terms,
                Some(&idf),
                Some(&base_terms),
                corpus_n,
                crawl_path_prior(link_url),
            );
            if score < min_score {
                note_skipped(
                    &mut skipped,
                    &mut skip_seen,
                    &mut skipped_total,
                    link_url,
                    "below min score",
                );
                continue;
            }
            seen.push(link_url.clone());
            queue.push((
                score * 0.7_f64.powi(1),
                seq,
                1,
                link_url.clone(),
                anchor.to_string(),
            ));
            seq += 1;
        }
        trim_queue(&mut queue, &mut queue_dropped);
    }

    let root_terms = crawl_topic_words(Some(&format!("{full} {root_title}")), 40);
    for seed in &seeds {
        let candidate = match normalize_url(Some(seed), Some(&root), None) {
            Ok(url) => url,
            Err(_) => {
                note_skipped(
                    &mut skipped,
                    &mut skip_seen,
                    &mut skipped_total,
                    seed,
                    "invalid url",
                );
                continue;
            }
        };
        if let Err(error) = check_url(&candidate, gossamer::ssrf::env_allow_private(), None) {
            pyeprintln!("Blocked non-public URL {candidate}: {}", error.message());
            note_skipped(
                &mut skipped,
                &mut skip_seen,
                &mut skipped_total,
                seed,
                "ssrf blocked",
            );
            continue;
        }
        add_external(
            &candidate,
            "",
            push_depth_zero(),
            0.0,
            false,
            same_host,
            root_host.as_deref(),
            min_score,
            "seed below min score",
            &terms,
            &root_terms,
            &idf,
            &base_terms,
            corpus_n,
            &mut seen,
            &mut queue,
            &mut skipped,
            &mut skip_seen,
            &mut skipped_total,
            &mut seq,
        );
    }
    if search_prior && max_depth >= 1 {
        let focus = query
            .clone()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| {
                if root_title.is_empty() {
                    "site content".to_string()
                } else {
                    root_title.clone()
                }
            });
        let site = root_host.as_deref().unwrap_or("");
        let site_query = format!("site:{site} {focus}");
        let results = fetch_text(&duckduckgo_search_url(&site_query), 20.0)
            .ok()
            .map(|(body, _)| duckduckgo_results(&body, 5))
            .unwrap_or_default();
        for (index, result) in results.iter().take(5).enumerate() {
            let Some(candidate) = result.get("url").and_then(Value::as_str) else {
                continue;
            };
            let title = result.get("title").and_then(Value::as_str).unwrap_or("");
            let normalized = normalize_url(Some(candidate), Some(&root), None)
                .unwrap_or_else(|_| candidate.to_string());
            if add_external(
                &normalized,
                title,
                1,
                0.1 / ((index + 1) as f64),
                true,
                same_host,
                root_host.as_deref(),
                min_score,
                "below min score",
                &terms,
                &root_terms,
                &idf,
                &base_terms,
                corpus_n,
                &mut seen,
                &mut queue,
                &mut skipped,
                &mut skip_seen,
                &mut skipped_total,
                &mut seq,
            ) {
                search_results += 1;
            }
        }
    }

    let mut stop = "frontier exhausted";
    while pages.len() < max_pages && !queue.is_empty() {
        queue.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.1.cmp(&b.1)));
        let (score, _order, depth, url, _anchor) = queue.remove(0);
        if depth >= max_depth {
            continue;
        }
        if runtime.is_visited(&url) {
            continue;
        }
        let child_page = match inspect_child(parsed, &url, use_smart, &cache_dir) {
            Ok(page) => page,
            Err(error) => {
                errors_total += 1;
                if errors.len() < 30 {
                    errors.push(json!({
                        "url": url,
                        "depth": depth,
                        "error": error,
                    }));
                }
                continue;
            }
        };
        let child_md = page_markdown(&child_page);
        let child_links = links_from_page(&child_page);
        let child_total_links = page_links_total(&child_page);
        let child_fetch_method = page_fetch_method(&child_page);
        let child_title = page_title(&child_page);
        let record = child_record(
            &url,
            depth,
            score,
            &child_md,
            child_total_links,
            &child_fetch_method,
            &child_title,
            &terms,
            excerpts,
        );
        pages.push(record);
        corpus.add_page(&crawl_topic_words(
            Some(&format!("{child_md} {child_title}")),
            40,
        ));
        let child_idf = corpus.idf();
        let child_corpus_n = corpus.len();
        let child_page_terms = crawl_topic_words(Some(&format!("{child_md} {child_title}")), 40);
        let mut context_cache: HashMap<String, Vec<String>> = HashMap::new();
        for (link_url, raw_anchor) in child_links {
            let Ok(candidate) = Url::parse(&link_url) else {
                continue;
            };
            if !matches!(candidate.scheme(), "http" | "https") {
                continue;
            }
            let key = runtime.identity_key(&link_url);
            if seen.contains(&link_url) || doc_seen.contains(&key) {
                continue;
            }
            let anchor = raw_anchor.trim();
            if crawl_is_document(&link_url) {
                doc_seen.insert(key);
                documents_total += 1;
                let label = crawl_label(&link_url, anchor, None);
                let doc_score = crawl_score(
                    &label,
                    &terms,
                    &child_page_terms,
                    Some(&child_idf),
                    Some(&base_terms),
                    child_corpus_n,
                    crawl_path_prior(&link_url),
                );
                if doc_score < min_score {
                    documents_below_score += 1;
                    note_skipped(
                        &mut skipped,
                        &mut skip_seen,
                        &mut skipped_total,
                        &link_url,
                        "below min score",
                    );
                } else if documents.len() < 30 {
                    documents.push(json!({
                        "url": link_url,
                        "anchor": anchor,
                        "score": round3(doc_score),
                    }));
                }
                continue;
            }
            if same_host && root_host.as_deref() != candidate.host_str() {
                note_skipped(
                    &mut skipped,
                    &mut skip_seen,
                    &mut skipped_total,
                    &link_url,
                    "external host",
                );
                continue;
            }
            let path = candidate.path().to_ascii_lowercase();
            if let Some(reason) = skip_reason(&path) {
                note_skipped(
                    &mut skipped,
                    &mut skip_seen,
                    &mut skipped_total,
                    &link_url,
                    reason,
                );
                continue;
            }
            let context = if anchor.is_empty() || anchor == "(untitled)" {
                None
            } else if let Some(context) = context_cache.get(anchor) {
                Some(context.clone())
            } else {
                let context = crawl_anchor_context(Some(&child_md), Some(anchor), 50, 8);
                context_cache.insert(anchor.to_string(), context.clone());
                Some(context)
            };
            let label = crawl_label(&link_url, anchor, context);
            let link_score = crawl_score(
                &label,
                &terms,
                &child_page_terms,
                Some(&child_idf),
                Some(&base_terms),
                child_corpus_n,
                crawl_path_prior(&link_url),
            );
            if link_score < min_score {
                note_skipped(
                    &mut skipped,
                    &mut skip_seen,
                    &mut skipped_total,
                    &link_url,
                    "below min score",
                );
                continue;
            }
            seen.push(link_url.clone());
            queue.push((
                link_score * 0.7_f64.powi((depth + 1) as i32),
                seq,
                depth + 1,
                link_url,
                anchor.to_string(),
            ));
            seq += 1;
        }
        trim_queue(&mut queue, &mut queue_dropped);
    }
    if !queue.is_empty() && pages.len() >= max_pages {
        stop = "max_pages reached";
    }

    let skipped_total_value = skipped_total;
    let mut result = json!({
        "root": root,
        "query": query_echo,
        "max_depth": max_depth,
        "max_pages": max_pages,
        "same_host": same_host,
        "min_score": gossamer::jsonfmt::py_float_value(min_score),
        "excerpts": excerpts,
        "search_prior": search_prior,
        "pages": pages,
        "errors": errors,
        "errors_total": errors_total,
        "documents": documents,
        "documents_total": documents_total,
        "documents_below_score": documents_below_score,
        "skipped": skipped,
        "skipped_total": skipped_total_value,
        "queue_dropped": queue_dropped,
        "count": pages.len(),
        "stop": stop,
    });
    if search_prior && let Some(object) = result.as_object_mut() {
        object.insert("search_results".to_string(), json!(search_results));
    }
    pyprintln!("{}", fit_crawl(&result, parsed));
    ExitCode::SUCCESS
}
