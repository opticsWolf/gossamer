//! Prompt-injection guard configuration and delivery framing.
//!
//! The detector itself is an optional Python extra in the reference. When it
//! is absent the guard still normalizes scoped text, attaches its deterministic
//! report, and wraps annotate-mode content in the untrusted marker.

use gossamer::cli_args::Parsed;
use gossamer::guard::{chunk_text, normalize_scopes, normalize_untrusted_text, wrap_untrusted};
use serde_json::{Value, json};

#[derive(Debug, Clone)]
pub struct GuardConfig {
    pub mode: String,
    pub scopes: Vec<String>,
    pub chunk_chars: usize,
    pub chunk_overlap: usize,
    pub max_chunks: usize,
}

impl GuardConfig {
    fn has(&self, scope: &str) -> bool {
        self.scopes.iter().any(|value| value == scope)
    }
}

/// Guard configuration, or `None` when disabled (the default).
pub fn config(parsed: &Parsed) -> Option<GuardConfig> {
    let file = crate::units::settings::value(parsed, "guard").unwrap_or(Value::Null);
    let file_bool = |key: &str| file.get(key).and_then(Value::as_bool);
    let enabled = match std::env::var("GOSSAMER_GUARD_ENABLED") {
        Ok(value) => !matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "" | "0" | "false" | "no" | "off"
        ),
        Err(_) => file_bool("enabled").unwrap_or(false),
    };
    if !enabled {
        return None;
    }
    let mode = match std::env::var("GOSSAMER_GUARD_MODE") {
        Ok(value) if !value.trim().is_empty() => value,
        _ => guard_value(parsed, "mode")
            .and_then(|value| value.as_str().map(str::to_string))
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| "annotate".to_string()),
    };
    let scopes = guard_scopes(parsed);
    let threshold = guard_float(parsed, "threshold", "GOSSAMER_GUARD_THRESHOLD", 0.7);
    let max_chunks =
        guard_int(parsed, "max_chunks", "GOSSAMER_GUARD_MAX_CHUNKS", 40).max(1) as usize;
    let scopes = normalize_scopes(Some(scopes)).unwrap_or_else(|_| {
        normalize_scopes(None).unwrap_or_else(|_| vec!["page_markdown".to_string()])
    });
    // Validate the same knobs Python validates; an invalid config disables the
    // guard here instead of aborting a request.
    let _ = gossamer::guard::validate_guard_config(
        &mode,
        threshold,
        900,
        120,
        max_chunks as i64,
        Some(scopes.clone()),
    )
    .ok()?;
    Some(GuardConfig {
        mode,
        scopes,
        chunk_chars: 900,
        chunk_overlap: 120,
        max_chunks,
    })
}

fn guard_value(parsed: &Parsed, key: &str) -> Option<Value> {
    crate::units::settings::value(parsed, "guard")?
        .get(key)
        .cloned()
}

fn guard_int(parsed: &Parsed, key: &str, env: &str, default: i64) -> i64 {
    if let Ok(value) = std::env::var(env)
        && let Ok(number) = value.trim().parse::<i64>()
    {
        return number;
    }
    guard_value(parsed, key)
        .and_then(|value| value.as_i64())
        .unwrap_or(default)
}

fn guard_float(parsed: &Parsed, key: &str, env: &str, default: f64) -> f64 {
    if let Ok(value) = std::env::var(env)
        && let Ok(number) = value.trim().parse::<f64>()
    {
        return number;
    }
    guard_value(parsed, key)
        .and_then(|value| value.as_f64())
        .unwrap_or(default)
}

fn guard_scopes(parsed: &Parsed) -> Vec<String> {
    if let Ok(value) = std::env::var("GOSSAMER_GUARD_SCOPES")
        && !value.trim().is_empty()
    {
        return value
            .split(',')
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(str::to_string)
            .collect();
    }
    match guard_value(parsed, "scopes") {
        Some(Value::String(value)) => value
            .split(',')
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(str::to_string)
            .collect(),
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect(),
        _ => normalize_scopes(None).unwrap_or_default(),
    }
}

/// Result of scanning the configured scopes.
pub struct Outcome {
    pub block: Value,
    pub main_text: Option<String>,
    pub withheld: bool,
}

/// Run the guard over `(scope, text)` pairs and merge one report.
pub fn evaluate(
    config: &GuardConfig,
    pairs: &[(&str, String)],
    main_scope: &str,
) -> Option<Outcome> {
    let mut scopes: Vec<String> = Vec::new();
    let mut main_text = None;
    let mut chunks_scanned = 0usize;
    for (scope, text) in pairs {
        if !config.has(scope) || text.is_empty() {
            continue;
        }
        let text = normalize_untrusted_text(text);
        if text.is_empty() {
            continue;
        }
        if *scope == main_scope {
            main_text = Some(text.clone());
        }
        chunks_scanned += chunk_text(
            &text,
            config.chunk_chars,
            config.chunk_overlap,
            config.max_chunks,
        )
        .len();
        scopes.push((*scope).to_string());
    }
    if scopes.is_empty() {
        return None;
    }
    static WARNED: std::sync::Once = std::sync::Once::new();
    WARNED.call_once(|| {
        eprintln!(
            "guard disabled at runtime: jailguard not importable (No module named 'jailguard'); install gossamer[guard]"
        );
    });
    let block = json!({
        "scanned": true,
        "scopes": scopes,
        "max_score": 0.0,
        "risk": "None",
        "flagged": [],
        "chunks_scanned": chunks_scanned,
        "elapsed_ms": 0.0,
        "action": config.mode,
    });
    Some(Outcome {
        block,
        main_text,
        withheld: false,
    })
}

/// Wrap annotate-mode content after the guard has normalized it.
pub fn annotate(config: &GuardConfig, outcome: &Outcome, text: &str, source: &str) -> String {
    if config.mode == "annotate"
        && outcome.block["scopes"].as_array().is_some_and(|scopes| {
            scopes.iter().any(|scope| {
                scope.as_str() == Some("page_markdown") || scope.as_str() == Some("document_text")
            })
        })
        && let Some(main) = &outcome.main_text
    {
        return wrap_untrusted(main, source);
    }
    if matches!(config.mode.as_str(), "annotate" | "redact")
        && let Some(main) = &outcome.main_text
    {
        return main.clone();
    }
    text.to_string()
}
