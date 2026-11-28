//! Resolve the same settings `gossamer.json` + environment contract as Python.

use std::path::PathBuf;
use std::sync::Once;

use gossamer::cli_args::Parsed;
use gossamer::config::{ConfigStore, Paths, ProcessEnv, find_config_file, load_json_file};
use gossamer::runtime::RuntimeConfig;
use serde_json::Value;

/// Effective config file: explicit flag > `GOSSAMER_CONFIG` > cwd > home.
pub fn config_path(parsed: &Parsed) -> Option<PathBuf> {
    if let Some(path) = parsed.config().filter(|value| !value.is_empty()) {
        return Some(PathBuf::from(path));
    }
    let paths = Paths::from_process().ok()?;
    find_config_file(&ProcessEnv, &paths, None)
}

static CONFIG_WARNING: Once = Once::new();

/// Parsed config object, or `None` when no file is discovered.
pub fn config(parsed: &Parsed) -> Option<Value> {
    let path = config_path(parsed)?;
    if is_missing_device(&path) {
        CONFIG_WARNING.call_once(|| {
            pyeprintln!(
                "ignoring gossamer.json: config file not found: {}",
                path.display()
            );
        });
        return None;
    }
    let mut store = ConfigStore::new();
    let display = path.display().to_string();
    match load_json_file(&mut store, &path, &display) {
        Ok(value) if value.is_object() => Some(value),
        Ok(_) => {
            CONFIG_WARNING.call_once(|| {
                pyeprintln!("ignoring gossamer.json: gossamer.json must hold an object: {display}");
            });
            None
        }
        Err(error) => {
            let message = error.message().to_string();
            CONFIG_WARNING.call_once(|| {
                pyeprintln!("ignoring gossamer.json: {message}");
            });
            None
        }
    }
}

fn is_missing_device(path: &std::path::Path) -> bool {
    if !cfg!(windows) {
        return false;
    }
    let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
        return false;
    };
    let stem = name.split('.').next().unwrap_or(name).to_ascii_lowercase();
    matches!(
        stem.as_str(),
        "nul"
            | "con"
            | "prn"
            | "aux"
            | "com1"
            | "com2"
            | "com3"
            | "com4"
            | "com5"
            | "com6"
            | "com7"
            | "com8"
            | "com9"
            | "lpt1"
            | "lpt2"
            | "lpt3"
            | "lpt4"
            | "lpt5"
            | "lpt6"
            | "lpt7"
            | "lpt8"
            | "lpt9"
    )
}

pub fn value(parsed: &Parsed, key: &str) -> Option<Value> {
    config(parsed)?.get(key).cloned()
}

/// Apply process-wide HTTP transport overrides from env/config.
pub fn configure_http(parsed: &Parsed) {
    let file = config(parsed).unwrap_or(Value::Null);
    let env_text = env_value;
    let file_text = |key: &str| {
        file.get(key)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    };
    let proxy = env_text("GOSSAMER_HTTP_PROXY").or_else(|| file_text("http_proxy"));
    let user_agent = env_text("GOSSAMER_USER_AGENT").or_else(|| file_text("user_agent"));
    let headers = env_dict("GOSSAMER_CUSTOM_HEADERS").unwrap_or_else(|| {
        file.get("custom_headers")
            .and_then(dict_pairs)
            .unwrap_or_default()
    });
    let cookies = env_dict("GOSSAMER_COOKIES")
        .unwrap_or_else(|| file.get("cookies").and_then(dict_pairs).unwrap_or_default());
    gossamer::fetch::configure_http(proxy, user_agent, headers, cookies);
}

fn env_dict(name: &str) -> Option<Vec<(String, String)>> {
    let raw = env_value(name)?;
    if raw.trim().is_empty() {
        return None;
    }
    let value: Value = serde_json::from_str(&raw).ok()?;
    dict_pairs(&value)
}

fn dict_pairs(value: &Value) -> Option<Vec<(String, String)>> {
    let object = value.as_object()?;
    Some(
        object
            .iter()
            .map(|(key, value)| {
                let text = value
                    .as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| value.to_string());
                (key.clone(), text)
            })
            .collect(),
    )
}

/// Validate resolved toolbox options exactly like Python's `ToolboxConfig`.
pub fn validate(parsed: &Parsed) -> Result<(), String> {
    let fetch_mode = parsed
        .fetch_mode()
        .map(str::to_string)
        .unwrap_or_else(|| text(parsed, "fetch_mode", "GOSSAMER_FETCH_MODE", "auto"));
    if !matches!(fetch_mode.as_str(), "auto" | "browser" | "static") {
        return Err(format!(
            "Invalid fetch_mode {}; expected 'auto', 'browser', or 'static'",
            gossamer::compat::py_repr(&fetch_mode)
        ));
    }
    if int(parsed, "candidate_cap", "GOSSAMER_CANDIDATE_CAP", 500) < 1 {
        return Err("candidate_cap must be >= 1".to_string());
    }
    if int(parsed, "max_concurrency", "GOSSAMER_MAX_CONCURRENCY", 8) < 1 {
        return Err("max_concurrency must be >= 1".to_string());
    }
    if int(
        parsed,
        "max_response_bytes",
        "GOSSAMER_MAX_RESPONSE_BYTES",
        5 * 1024 * 1024,
    ) < 1
    {
        return Err("max_response_bytes must be >= 1".to_string());
    }
    Ok(())
}

/// Read `GOSSAMER_*`, then explicit/derived legacy `STITCH_*` spellings.
fn env_value(name: &str) -> Option<String> {
    let read = |key: &str| std::env::var(key).ok().filter(|value| !value.is_empty());
    if let Some(value) = read(name) {
        return Some(value);
    }
    if name == "GOSSAMER_MAX_RESPONSE_BYTES"
        && let Some(value) = read("STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES")
    {
        return Some(value);
    }
    name.strip_prefix("GOSSAMER_")
        .and_then(|suffix| read(&format!("STITCH_{suffix}")))
}

/// Cache directory: flag > env > `cache_dir` in the config file > default.
pub fn cache_dir(parsed: &Parsed) -> String {
    if let Some(value) = parsed.cache_dir().filter(|value| !value.is_empty()) {
        return value.to_string();
    }
    if let Some(value) = env_value("GOSSAMER_CACHE_DIR") {
        return value;
    }
    if let Some(value) = value(parsed, "cache_dir").and_then(|v| v.as_str().map(str::to_string))
        && !value.is_empty()
    {
        return value;
    }
    ".gossamer_cache".to_string()
}

/// Integer setting: env > config file > default.
pub fn int(parsed: &Parsed, key: &str, env: &str, default: i64) -> i64 {
    if let Some(value) = env_value(env)
        && let Ok(number) = value.trim().parse::<i64>()
    {
        return number;
    }
    if let Some(number) = value(parsed, key)
        .and_then(|value| value.as_i64().or_else(|| value.as_f64().map(|v| v as i64)))
    {
        return number;
    }
    default
}

/// Float setting: env > config file > default.
pub fn text(parsed: &Parsed, key: &str, env: &str, default: &str) -> String {
    if let Some(value) = env_value(env) {
        return value;
    }
    if let Some(value) = value(parsed, key).and_then(|v| v.as_str().map(str::to_string))
        && !value.is_empty()
    {
        return value;
    }
    default.to_string()
}

/// Boolean setting: env > config file > default.
pub fn runtime(parsed: &Parsed, cache_dir: &str) -> RuntimeConfig {
    let mut config = RuntimeConfig::from_cache_dir(PathBuf::from(cache_dir));
    if let Some(value) =
        env_value("GOSSAMER_CACHE_TTL_SECONDS").and_then(|value| value.trim().parse::<i64>().ok())
    {
        config.ttl_seconds = value;
    } else if let Some(value) = value(parsed, "cache_ttl_seconds").and_then(|v| v.as_i64()) {
        config.ttl_seconds = value;
    }
    if let Some(value) = env_value("GOSSAMER_CACHE_MEMORY_ENTRIES")
        .and_then(|value| value.trim().parse::<u64>().ok())
    {
        config.max_memory_entries = value as usize;
    } else if let Some(value) = value(parsed, "cache_memory_entries").and_then(|v| v.as_u64()) {
        config.max_memory_entries = value as usize;
    }
    if let Some(value) =
        env_value("GOSSAMER_CACHE_MAX_BYTES").and_then(|value| value.trim().parse::<u64>().ok())
    {
        config.max_disk_bytes = value;
    } else if let Some(value) = value(parsed, "cache_max_bytes").and_then(|v| v.as_u64()) {
        config.max_disk_bytes = value;
    }
    if let Some(value) =
        env_value("GOSSAMER_DOMAIN_DELAY").and_then(|value| value.trim().parse::<f64>().ok())
    {
        config.fetch_interval = value;
    } else if let Some(value) = value(parsed, "domain_delay").and_then(|v| v.as_f64()) {
        config.fetch_interval = value;
    }
    if let Some(value) =
        env_value("GOSSAMER_FETCH_JITTER").and_then(|value| value.trim().parse::<f64>().ok())
    {
        config.fetch_jitter = value;
    } else if let Some(value) = value(parsed, "fetch_jitter").and_then(|v| v.as_f64()) {
        config.fetch_jitter = value;
    }
    if let Some(value) = env_value("GOSSAMER_RESPECT_ROBOTS") {
        config.respect_robots = matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        );
    } else if let Some(value) = value(parsed, "respect_robots").and_then(|v| v.as_bool()) {
        config.respect_robots = value;
    }
    if let Some(value) =
        env_value("GOSSAMER_MAX_RESPONSE_BYTES").and_then(|value| value.trim().parse::<u64>().ok())
    {
        config.max_response_bytes = value;
    } else if let Some(value) = value(parsed, "max_response_bytes").and_then(|v| v.as_u64()) {
        config.max_response_bytes = value;
    }
    config
}
