//! `gossamer crawl`: bounded focused link-graph traversal in Rust.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::document::prepare_url;
use gossamer::fetch::fetch_html_full_single;
use gossamer::metaextract::meta_extract_all;
use gossamer::service::crawl::{
    crawl_anchor_context, crawl_excerpt, crawl_expand_query, crawl_is_document, crawl_path_prior,
    crawl_score, crawl_term_hits, crawl_tokens, crawl_topic_words,
};
use gossamer::service::fetch::compact_metadata;
use serde_json::{Value, json};
use url::Url;

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
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
                "--seed-urls" | "--use-smart" | "--cache-dir" | "--config" | "--keystore"
                | "--log-level" | "--fetch-mode" => {}
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
    if search_prior {
        return unsupported();
    }

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
        query.unwrap_or_default()
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

    let result = json!({
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
        "skipped": [],
        "skipped_total": 0,
        "queue_dropped": queue_dropped,
        "count": pages.len(),
        "stop": stop,
    });
    println!(
        "{}",
        serde_json::to_string_pretty(&result).unwrap_or_else(|_| result.to_string())
    );
    ExitCode::SUCCESS
}
