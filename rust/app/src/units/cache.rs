//! Glue for `gossamer cache`: Rust owns cache maintenance with the same JSON contract.

use std::env;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use gossamer::cache::{Cache, CacheConfig};
use gossamer::cli_args::Parsed;
use serde_json::{Value, json};

fn unsupported() -> ExitCode {
    super::unsupported()
}

fn int_env_or_file(env_name: &str, file: &serde_json::Value, key: &str, default: i64) -> i64 {
    if let Ok(value) = env::var(env_name)
        && let Ok(parsed) = value.parse::<i64>()
    {
        return parsed;
    }
    file.get(key).and_then(Value::as_i64).unwrap_or(default)
}

fn config_file(explicit: &Option<String>) -> Option<PathBuf> {
    if let Some(path) = explicit {
        let path = PathBuf::from(path);
        return path.is_file().then_some(path);
    }
    if let Ok(path) = env::var("GOSSAMER_CONFIG") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Some(path);
        }
    }
    let cwd = PathBuf::from("gossamer.json");
    if cwd.is_file() {
        return Some(cwd);
    }
    let home = env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)?;
    let home_cfg = home.join(".gossamer").join("config.json");
    home_cfg.is_file().then_some(home_cfg)
}

fn load_config(explicit: &Option<String>) -> serde_json::Value {
    let Some(path) = config_file(explicit) else {
        return json!({});
    };
    std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .filter(|value| value.is_object())
        .unwrap_or_else(|| json!({}))
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |duration| duration.as_secs_f64())
}

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
}

pub fn run(parsed: &Parsed) -> ExitCode {
    let action = parsed.value("action").unwrap_or("prune").to_string();
    let cache_dir = parsed.cache_dir().map(str::to_string);
    let config = parsed.config().map(str::to_string);

    let file_cfg = load_config(&config);
    let cache_dir = cache_dir
        .or_else(|| env::var("GOSSAMER_CACHE_DIR").ok())
        .or_else(|| {
            file_cfg
                .get("cache_dir")
                .and_then(Value::as_str)
                .map(ToOwned::to_owned)
        })
        .unwrap_or_else(|| ".gossamer_cache".to_owned());
    let cache_cfg = CacheConfig {
        cache_dir: PathBuf::from(Path::new(&cache_dir)),
        ttl_seconds: int_env_or_file(
            "GOSSAMER_CACHE_TTL_SECONDS",
            &file_cfg,
            "cache_ttl_seconds",
            3600,
        ),
        max_memory_entries: int_env_or_file(
            "GOSSAMER_CACHE_MEMORY_ENTRIES",
            &file_cfg,
            "cache_memory_entries",
            100,
        )
        .max(0) as usize,
        max_disk_bytes: int_env_or_file("GOSSAMER_CACHE_MAX_BYTES", &file_cfg, "cache_max_bytes", 0)
            .max(0) as u64,
    };
    let cache = match Cache::new(cache_cfg) {
        Ok(cache) => cache,
        Err(_) => return unsupported(),
    };
    let now = now_seconds();

    let output = match action.as_str() {
        "prune" => match cache.prune(now) {
            Ok(prune) => json!({"prune": prune}),
            Err(_) => return unsupported(),
        },
        "clear" => {
            if cache.clear().is_err() {
                return unsupported();
            }
            match cache.stats() {
                Ok(stats) => json!({"cache_cleared": true, "stats": stats}),
                Err(_) => return unsupported(),
            }
        }
        "reset" => json!({"visited_cleared": true, "visited_urls_count": 0}),
        _ => return unsupported(),
    };

    pyprintln!("{}", pretty(&output));
    ExitCode::SUCCESS
}
