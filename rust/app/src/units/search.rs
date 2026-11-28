//! Glue for `gossamer search`: provider search plus the Rust research fan-out.

use std::process::{Command, ExitCode};

use gossamer::cli_args::Parsed;
use gossamer::discovery::fetch_text;
use gossamer::jsonfmt::{py_dumps_pretty, py_dumps_pretty_utf8};
use gossamer::search::{duckduckgo_results, duckduckgo_search_url, unknown_provider_error};
use gossamer::service::research::research_plan;
use gossamer::service::search::{failover_results, fallback_note};
use gossamer::urls::normalize_url;
use serde_json::{Value, json};

const AVAILABLE: [&str; 1] = ["duckduckgo"];

fn pretty(value: &Value) -> String {
    py_dumps_pretty(value)
}

fn failover(results: Vec<Value>) -> (Vec<Value>, usize) {
    let payload = failover_results(&json!([["duckduckgo", results]]), results.len());
    let dropped = payload
        .get("dropped_dupes")
        .and_then(Value::as_u64)
        .unwrap_or(0) as usize;
    let results = payload
        .get("results")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    (results, dropped)
}

/// Run the DuckDuckGo provider and return `(results, fallback_note)` or an
/// error payload when no provider answered.
fn provider_search(
    query: &str,
    max_results: usize,
    provider: Option<&str>,
) -> Result<(Vec<Value>, Option<Value>, usize), Value> {
    if let Some(name) = provider
        && let Some(payload) = unknown_provider_error(name, &AVAILABLE)
    {
        return Err(payload);
    }
    if query.trim().is_empty() {
        pyeprintln!("DuckDuckGo search failed: query is mandatory.");
        pyeprintln!(
            "Provider DuckDuckGoProvider failed for '{query}': query is mandatory. — trying next"
        );
        return Err(json!({"error": "All search providers failed for: "}));
    }
    let url = duckduckgo_search_url(query);
    let html = match fetch_text(&url, 20.0) {
        Ok((html, _)) => html,
        Err(error) => {
            pyeprintln!("DuckDuckGo search failed: {error}");
            pyeprintln!("Provider DuckDuckGoProvider failed for '{query}': {error} — trying next");
            return Err(json!({"error": format!("All search providers failed for: {query}")}));
        }
    };
    let (results, dropped) = failover(duckduckgo_results(&html, max_results));
    let note = fallback_note(provider, &json!(AVAILABLE));
    Ok((results, note, dropped))
}

fn finish_search(results: Vec<Value>, note: Option<Value>, parsed: &Parsed) -> String {
    let Some(config) = crate::units::guard::config(parsed) else {
        return match note {
            Some(note) => {
                py_dumps_pretty_utf8(&json!({"results": results, "provider_fallback": note}))
            }
            None => py_dumps_pretty_utf8(&Value::Array(results)),
        };
    };
    let text = results
        .iter()
        .filter_map(Value::as_object)
        .map(|item| {
            format!(
                "{} {}",
                item.get("title").and_then(Value::as_str).unwrap_or(""),
                item.get("snippet").and_then(Value::as_str).unwrap_or("")
            )
        })
        .collect::<Vec<_>>()
        .join(" ");
    let Some(outcome) =
        crate::units::guard::evaluate(&config, &[("search_results", text)], "search_results")
    else {
        return match note {
            Some(note) => {
                py_dumps_pretty_utf8(&json!({"results": results, "provider_fallback": note}))
            }
            None => py_dumps_pretty_utf8(&Value::Array(results)),
        };
    };
    if outcome.withheld {
        return py_dumps_pretty_utf8(&json!({
            "error": "results withheld by prompt-injection guard",
            "guard": outcome.block,
        }));
    }
    let mut envelope = json!({"results": results, "guard": outcome.block});
    if let Some(note) = note
        && let Some(object) = envelope.as_object_mut()
    {
        object.insert("provider_fallback".to_string(), note);
    }
    py_dumps_pretty_utf8(&envelope)
}

fn inspect_page(url: &str, use_smart: &str, parsed: &Parsed) -> Result<Value, String> {
    let exe = std::env::current_exe().map_err(|error| error.to_string())?;
    let config_path = crate::units::settings::config_path(parsed).filter(|path| path.is_file());
    let mut command = Command::new(exe);
    for (flag, value) in [
        ("--cache-dir", parsed.cache_dir()),
        (
            "--config",
            config_path.as_deref().and_then(|path| path.to_str()),
        ),
        ("--keystore", parsed.keystore()),
        ("--log-level", parsed.log_level()),
        ("--fetch-mode", parsed.fetch_mode()),
    ] {
        if let Some(value) = value {
            command.arg(flag).arg(value);
        }
    }
    command
        .arg("inspect")
        .arg(url)
        .arg("--use-smart")
        .arg(use_smart);
    let output = command.output().map_err(|error| error.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !stderr.trim().is_empty() {
        pyeprint!("{stderr}");
    }
    match serde_json::from_str::<Value>(stdout.trim()) {
        Ok(value) => Ok(value),
        Err(_) => Ok(Value::String(stdout)),
    }
}

/// The `research` half of `web_search(search_only=False)`.
fn research(
    query: &str,
    depth: i64,
    max_results: i64,
    provider: Option<&str>,
    use_smart: &str,
    parsed: &Parsed,
) -> ExitCode {
    let plan = research_plan(query, depth, Some(max_results), 20);
    if plan.get("error").is_some() {
        pyprintln!("{}", pretty(&plan));
        return ExitCode::SUCCESS;
    }
    let topic = plan
        .get("topic")
        .and_then(Value::as_str)
        .unwrap_or(query)
        .to_string();
    let depth = plan.get("depth").and_then(Value::as_i64).unwrap_or(depth);
    let plan_results = plan
        .get("plan_results")
        .and_then(Value::as_i64)
        .unwrap_or(max_results) as usize;

    let (results, provider_fallback, dropped_dupes) =
        match provider_search(&topic, plan_results, provider) {
            Ok((results, note, dropped)) => (results, note, dropped),
            Err(_) => (Vec::new(), None, 0),
        };

    let mut sources: Vec<Value> = Vec::new();
    let mut fetched = 0usize;
    for item in results {
        if fetched >= depth as usize {
            break;
        }
        let Some(object) = item.as_object() else {
            continue;
        };
        let raw_url = object.get("url").and_then(Value::as_str).unwrap_or("");
        let Ok(url) = normalize_url(Some(raw_url), None, None) else {
            continue;
        };
        if gossamer::ssrf::check_url(&url, false, None).is_err() {
            continue;
        }
        fetched += 1;
        let title = object.get("title").and_then(Value::as_str).unwrap_or("");
        let snippet = object.get("snippet").and_then(Value::as_str).unwrap_or("");
        let mut record = json!({"url": url, "title": title, "snippet": snippet});
        match inspect_page(&url, use_smart, parsed) {
            Ok(page) => {
                let failed = page
                    .as_object()
                    .and_then(|map| map.get("error").or_else(|| map.get("warning")))
                    .map(|value| match value {
                        Value::String(text) => text.clone(),
                        other => other.to_string(),
                    });
                if let Some(message) = failed {
                    record["status"] = json!("error");
                    record["error"] = json!(message);
                } else {
                    record["status"] = json!("ok");
                    record["result"] = page;
                }
            }
            Err(error) => {
                record["status"] = json!("error");
                record["error"] = json!(error);
            }
        }
        sources.push(record);
    }

    let count = sources
        .iter()
        .filter(|source| source.get("status").and_then(Value::as_str) == Some("ok"))
        .count();
    let mut result = json!({
        "topic": topic,
        "depth": depth,
        "provider": provider,
        "sources": sources,
        "count": count,
        "dropped_dupes": dropped_dupes,
    });
    if let Some(note) = provider_fallback
        && let Some(object) = result.as_object_mut()
    {
        object.insert("provider_fallback".to_string(), note);
    }
    let payload = fit_research(&result, parsed);
    pyprintln!("{payload}");
    ExitCode::SUCCESS
}

fn config_int(parsed: &Parsed, key: &str, env: &str, default: i64) -> i64 {
    crate::units::settings::int(parsed, key, env, default)
}

fn config_str(parsed: &Parsed, key: &str, env: &str, default: &str) -> String {
    crate::units::settings::text(parsed, key, env, default)
}

fn fit_research(result: &Value, parsed: &Parsed) -> String {
    let char_limit = config_int(
        parsed,
        "max_markdown_chars",
        "GOSSAMER_MAX_MARKDOWN_CHARS",
        8000,
    );
    let token_limit = parsed
        .int("max_tokens")
        .filter(|value| *value > 0)
        .unwrap_or_else(|| config_int(parsed, "max_tokens", "GOSSAMER_MAX_TOKENS", 0));
    let build = |budget: Option<usize>| {
        let text = serde_json::to_string(result).unwrap_or_else(|_| "{}".to_string());
        gossamer::budget::shrink_research(&text, budget).unwrap_or_else(|_| "{}".to_string())
    };
    let out = build(None);
    let model = config_str(parsed, "model_name", "GOSSAMER_MODEL_NAME", "gpt-4o");
    if gossamer::budget::json_fits(&out, char_limit, token_limit, &model).unwrap_or(false) {
        return out;
    }
    let mut budget = if char_limit > 0 {
        char_limit as usize
    } else {
        out.len()
    } as i64;
    while budget > 240 {
        budget /= 2;
        let candidate = build(Some(budget as usize));
        if gossamer::budget::json_fits(&candidate, char_limit, token_limit, &model).unwrap_or(false)
        {
            return candidate;
        }
    }
    pyeprintln!(
        "Payload could not be shrunk into the output budget; returning an overflow envelope instead of invalid JSON"
    );
    pretty(&json!({
        "topic": result.get("topic").cloned().unwrap_or(Value::Null),
        "depth": result.get("depth").cloned().unwrap_or(Value::Null),
        "error": "research result too large for the output budget",
        "hint": "lower depth or raise max_tokens",
    }))
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let query = parsed.positional("query").unwrap_or_default().to_string();
    let provider = parsed.value("provider").map(str::to_string);
    let search_only = parsed.flag("search_only");
    let max_results = parsed.int("max_results").unwrap_or(5);
    let use_smart = parsed.value("use_smart").unwrap_or("auto");
    if !search_only {
        let depth = parsed.int("depth").unwrap_or(5);
        return research(
            &query,
            depth,
            max_results,
            provider.as_deref(),
            use_smart,
            parsed,
        );
    }
    let max_results = max_results.max(0) as usize;
    match provider_search(&query, max_results, provider.as_deref()) {
        Ok((results, note, _)) => pyprintln!("{}", finish_search(results, note, parsed)),
        Err(payload) => {
            pyprintln!("{}", pretty(&payload));
        }
    }
    ExitCode::SUCCESS
}
