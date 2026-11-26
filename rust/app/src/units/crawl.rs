//! `gossamer crawl`: bounded focused link-graph traversal in Rust.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::discovery::fetch_text;
use gossamer::document::prepare_url;
use gossamer::fetch::fetch_html_full_single;
use gossamer::metaextract::meta_extract_all;
use gossamer::search::{duckduckgo_results, duckduckgo_search_url};
use gossamer::service::crawl::{
    crawl_anchor_context, crawl_excerpt, crawl_expand_query, crawl_is_document, crawl_path_prior,
    crawl_score, crawl_term_hits, crawl_tokens, crawl_topic_words,
};
use gossamer::service::fetch::compact_metadata;
use gossamer::ssrf::check_url;
use gossamer::urls::normalize_url;
use serde_json::{Value, json};
use url::Url;

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

const fn push_depth_zero() -> usize {
    0
}

fn skip_path(path: &str) -> bool {
    const PREFIXES: [&str; 16] = [
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
    terms: &[String],
    root_terms: &[String],
    seen: &mut Vec<String>,
    queue: &mut Vec<(f64, usize, usize, String, String)>,
    skipped: &mut Vec<Value>,
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
        skipped.push(json!({"url": url, "reason": "external host"}));
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
        skipped.push(json!({"url": url, "reason": reason}));
        return false;
    }
    let mut label = crawl_tokens(Some(anchor));
    label.extend(crawl_tokens(Some(url)));
    let mut score = crawl_score(
        &label,
        terms,
        root_terms,
        None,
        None,
        0,
        crawl_path_prior(url),
    ) + rank_bonus;
    if score < min_score && !exempt_floor {
        skipped.push(json!({"url": url, "reason": "below min score"}));
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

fn page_record(
    url: &str,
    depth: usize,
    score: f64,
    markdown: &str,
    links_total: usize,
    terms: &[String],
    excerpts: bool,
) -> Value {
    let mut record = json!({
        "url": url,
        "depth": depth,
        "status": "ok",
        "title": "",
        "score": score,
        "markdown": markdown.chars().take(8000).collect::<String>(),
        "links_total": links_total,
        "fetch_method": "static",
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

fn title_from_html(html: &str, url: &str) -> String {
    let raw: Value =
        serde_json::from_str(&meta_extract_all(html, Some(url))).unwrap_or(Value::Null);
    compact_metadata(&raw)
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

pub fn run(command: &str, matches: &ArgMatches) -> ExitCode {
    if let Some(code) = super::cli::maybe_run(command, matches) {
        return code;
    }

    let args: Vec<OsString> = matches
        .get_many::<OsString>("args")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    if matches.get_count("help") > 0 {
        return unsupported();
    }

    let mut root: Option<String> = None;
    let mut query: Option<String> = None;
    let mut max_depth = 3usize;
    let mut max_pages = 15usize;
    let mut min_score = 0.05_f64;
    let mut same_host = false;
    let mut excerpts = false;
    let mut search_prior = false;
    let mut seeds: Vec<String> = Vec::new();
    let mut index = 0usize;
    while index < args.len() {
        let value = args[index].to_string_lossy().into_owned();
        if value.starts_with('-') {
            if value == "--same-host" {
                same_host = true;
                index += 1;
                continue;
            }
            if value == "--excerpts" {
                excerpts = true;
                index += 1;
                continue;
            }
            if value == "--search-prior" {
                search_prior = true;
                index += 1;
                continue;
            }
            if index + 1 >= args.len() {
                return unsupported();
            }
            let next = args[index + 1].to_string_lossy().into_owned();
            match value.as_str() {
                "--query" => query = Some(next),
                "--max-depth" => {
                    if let Ok(parsed) = next.parse::<usize>() {
                        max_depth = parsed.min(10);
                    }
                }
                "--max-pages" => {
                    if let Ok(parsed) = next.parse::<usize>() {
                        max_pages = parsed.clamp(1, 100);
                    }
                }
                "--min-score" => {
                    if let Ok(parsed) = next.parse::<f64>() {
                        min_score = parsed.max(0.0);
                    }
                }
                "--seed-urls" => {
                    index += 1;
                    while index < args.len() {
                        let item = args[index].to_string_lossy().into_owned();
                        if item.starts_with('-') {
                            break;
                        }
                        seeds.push(item);
                        index += 1;
                    }
                    if seeds.is_empty() {
                        return unsupported();
                    }
                    continue;
                }
                "--use-smart" | "--cache-dir" | "--config" | "--keystore" | "--log-level"
                | "--fetch-mode" => {}
                _ => return unsupported(),
            }
            index += 2;
            continue;
        }
        if root.is_some() {
            return unsupported();
        }
        root = Some(value);
        index += 1;
    }
    let Some(root) = root else {
        return unsupported();
    };

    let root = match prepare_url(&root, None) {
        Ok(url) => url,
        Err(error) => {
            if error.error_type() == "SsrfBlockedError" {
                eprintln!(
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
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({"error": format!("crawl: {message}")}))
                    .unwrap_or_default()
            );
            return ExitCode::SUCCESS;
        }
    };
    let Ok((html, full, links, _removed, provenance)) =
        fetch_html_full_single(&root, 8000, 50_000_000)
    else {
        let payload = json!({"error": format!("crawl: root fetch failed: {root}"), "root": root});
        println!(
            "{}",
            serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string())
        );
        return ExitCode::SUCCESS;
    };
    let root_title = title_from_html(&html, &provenance.1);
    let base_terms = match query.as_deref() {
        Some(value) if !value.trim().is_empty() => crawl_tokens(Some(value)),
        _ => crawl_topic_words(Some(&format!("{full} {root_title}")), 12),
    };
    let query_echo = if query
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
    {
        query.clone().unwrap_or_default()
    } else {
        "derived from root page".to_string()
    };
    let empty_clusters = json!({});
    let (terms, expanded) = crawl_expand_query(&base_terms, &empty_clusters);
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
        links.len(),
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
    let mut queue_dropped = 0usize;
    let mut seq = 0usize;
    let mut skipped: Vec<Value> = Vec::new();
    let mut search_results = 0usize;
    let page_terms = crawl_topic_words(Some(&full), 12);
    for (link_url, anchor) in &links {
        let Ok(candidate) = Url::parse(link_url) else {
            continue;
        };
        if !matches!(candidate.scheme(), "http" | "https") || crawl_is_document(link_url) {
            queue_dropped += 1;
            continue;
        }
        if same_host && root_host.as_deref() != candidate.host_str() {
            queue_dropped += 1;
            continue;
        }
        if seen.contains(link_url) {
            queue_dropped += 1;
            continue;
        }
        seen.push(link_url.clone());
        let mut label = crawl_tokens(Some(anchor));
        label.extend(crawl_anchor_context(Some(&full), Some(anchor), 160, 8));
        label.extend(crawl_tokens(Some(link_url)));
        let score = crawl_score(
            &label,
            &terms,
            &page_terms,
            None,
            None,
            0,
            crawl_path_prior(link_url),
        );
        if score < min_score {
            queue_dropped += 1;
            continue;
        }
        queue.push((
            score * 0.7_f64.powi(1),
            seq,
            1,
            link_url.clone(),
            anchor.clone(),
        ));
        seq += 1;
    }

    let root_terms = crawl_topic_words(Some(&format!("{full} {root_title}")), 12);
    for seed in &seeds {
        let candidate = match normalize_url(Some(seed), Some(&root), None) {
            Ok(url) => url,
            Err(_) => {
                skipped.push(json!({"url": seed, "reason": "invalid url"}));
                continue;
            }
        };
        if let Err(error) = check_url(&candidate, false, None) {
            let _ = error;
            skipped.push(json!({"url": seed, "reason": "ssrf blocked"}));
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
            &terms,
            &root_terms,
            &mut seen,
            &mut queue,
            &mut skipped,
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
                &terms,
                &root_terms,
                &mut seen,
                &mut queue,
                &mut skipped,
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
        let Ok((child_html, child_md, child_links, _child_removed, _child_prov)) =
            fetch_html_full_single(&url, 8000, 50_000_000)
        else {
            continue;
        };
        let mut record = page_record(
            &url,
            depth,
            score,
            &child_md,
            child_links.len(),
            &terms,
            excerpts,
        );
        if let Some(map) = record.as_object_mut() {
            map.insert(
                "title".to_string(),
                json!(title_from_html(&child_html, &url)),
            );
        }
        pages.push(record);
    }
    if !queue.is_empty() && pages.len() >= max_pages {
        stop = "max_pages reached";
    }

    let skipped_total_value = skipped.len();
    let mut result = json!({
        "root": root,
        "query": query_echo,
        "max_depth": max_depth,
        "max_pages": max_pages,
        "same_host": same_host,
        "min_score": min_score,
        "excerpts": excerpts,
        "search_prior": search_prior,
        "pages": pages,
        "errors": [],
        "errors_total": 0,
        "documents": [],
        "documents_total": 0,
        "documents_below_score": 0,
        "skipped": skipped,
        "skipped_total": skipped_total_value,
        "queue_dropped": queue_dropped,
        "count": pages.len(),
        "stop": stop,
    });
    if search_prior && let Some(object) = result.as_object_mut() {
        object.insert("search_results".to_string(), json!(search_results));
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&result).unwrap_or_else(|_| result.to_string())
    );
    ExitCode::SUCCESS
}
