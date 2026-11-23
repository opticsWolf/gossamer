//! Rust port of `gossamer/env.py` and `gossamer/settings.py`.
//!
//! Every function delegates to `gossamer::config`; this crate only supplies the
//! process environment, the working/home directories and one process-wide
//! [`ConfigStore`] (the parse cache the reference keeps in module globals).

#![forbid(unsafe_code)]

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use gossamer::config::{
    ConfigError, ConfigStore, Paths, ProcessEnv, config_template as core_config_template,
    exception_name, find_config_file as core_find_config, find_keystore_file as core_find_keystore,
    getenv as core_getenv, getenv_bool as core_getenv_bool,
    keystore_template as core_keystore_template, legacy_names_from_json,
    load_config_file as core_load_config, load_json_file as core_load_json,
    load_keystore as core_load_keystore, lookup_key as core_lookup_key, py_str_value,
};
use serde_json::Value;

/// Process-wide parse cache, mirroring the reference module globals.
fn store() -> &'static Mutex<ConfigStore> {
    static STORE: OnceLock<Mutex<ConfigStore>> = OnceLock::new();
    STORE.get_or_init(|| Mutex::new(ConfigStore::new()))
}

fn with<T>(body: impl FnOnce(&mut ConfigStore) -> T) -> Result<T, String> {
    let mut guard = store()
        .lock()
        .map_err(|_| "OSError: config store lock poisoned".to_string())?;
    Ok(body(&mut guard))
}

fn paths() -> Result<Paths, String> {
    Paths::from_process().map_err(|error| format!("OSError: {error}"))
}

fn failure(error: ConfigError) -> String {
    format!("{}: {error}", exception_name(error.kind()))
}

/// Resolve a repo-relative fixture path without changing its display string.
fn resolve(raw: &str) -> PathBuf {
    let direct = PathBuf::from(raw);
    if direct.is_file() {
        return direct;
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .join(raw)
}

/// Port of `getenv` (`gossamer/env.py:47`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn getenv(new: String, default: Option<Value>, legacy: Option<Value>) -> Result<Value, String> {
    let paths = paths()?;
    let legacy = legacy_names_from_json(legacy.as_ref());
    let string_default = match &default {
        Some(Value::String(text)) => Some(text.clone()),
        _ => None,
    };
    with(|store| {
        core_getenv(
            store,
            &ProcessEnv,
            &paths,
            &new,
            string_default,
            Some(&legacy),
        )
        .map(Value::String)
        .unwrap_or_else(|| default.unwrap_or(Value::Null))
    })
}

/// Port of `getenv_bool` (`gossamer/env.py:77`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn getenv_bool(new: String, default: bool, legacy: Option<Value>) -> Result<bool, String> {
    let paths = paths()?;
    let legacy = legacy_names_from_json(legacy.as_ref());
    with(|store| core_getenv_bool(store, &ProcessEnv, &paths, &new, default, Some(&legacy)))
}

/// Port of `find_config_file` (`gossamer/settings.py:42`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn find_config_file(explicit: Option<String>) -> Result<Value, String> {
    let paths = paths()?;
    Ok(optional_path(core_find_config(
        &ProcessEnv,
        &paths,
        explicit.as_deref(),
    )))
}

/// Port of `find_keystore_file` (`gossamer/settings.py:62`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn find_keystore_file(explicit: Option<String>) -> Result<Value, String> {
    let paths = paths()?;
    with(|store| {
        optional_path(core_find_keystore(
            store,
            &ProcessEnv,
            &paths,
            explicit.as_deref(),
        ))
    })
}

/// Log a warning through the reference module's logger.
fn warn(message: &str) {
    use pyo3::prelude::*;
    pyo3::Python::attach(|py| {
        let logged = py
            .import("logging")
            .and_then(|module| module.call_method1("getLogger", ("gossamer.settings",)))
            .and_then(|logger| logger.call_method1("warning", (message,)));
        let _ = logged;
    });
}

fn optional_path(path: Option<PathBuf>) -> Value {
    match path {
        Some(path) => Value::String(path.to_string_lossy().into_owned()),
        None => Value::Null,
    }
}

/// Port of `clear_caches` (`gossamer/settings.py:96`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn clear_caches() -> Result<(), String> {
    with(ConfigStore::clear_caches)
}

/// Port of `load_json_file` (`gossamer/settings.py:101`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn load_json_file(path: Value) -> Result<Value, String> {
    let display = py_str_value(&path);
    with(|store| core_load_json(store, &resolve(&display), &display).map_err(failure))?
}

/// Port of `load_config_file` (`gossamer/settings.py:118`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn load_config_file(explicit: Option<Value>) -> Result<Value, String> {
    let paths = paths()?;
    let explicit = explicit.map(|value| py_str_value(&value));
    with(|store| {
        core_load_config(store, &ProcessEnv, &paths, explicit.as_deref())
            .map(Value::Object)
            .map_err(failure)
    })?
}

/// Port of `load_keystore` (`gossamer/settings.py:134`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn load_keystore(explicit: Option<Value>) -> Result<Value, String> {
    let paths = paths()?;
    let explicit = explicit.map(|value| py_str_value(&value));
    with(|store| {
        core_load_keystore(store, &ProcessEnv, &paths, explicit.as_deref())
            .map(|loaded| {
                for warning in &loaded.warnings {
                    warn(warning);
                }
                Value::Object(loaded.values)
            })
            .map_err(failure)
    })?
}

/// Port of `lookup_key` (`gossamer/settings.py:165`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn lookup_key(names: Vec<String>, keystore: Option<String>) -> Result<Value, String> {
    let paths = paths()?;
    with(
        |store| match core_lookup_key(store, &ProcessEnv, &paths, &names, keystore.as_deref()) {
            Some(value) => Value::String(value),
            None => Value::Null,
        },
    )
}

/// Port of `keystore_template` (`gossamer/settings.py:201`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn keystore_template() -> Result<Value, String> {
    Ok(Value::Object(core_keystore_template()))
}

/// Port of `config_template` (`gossamer/settings.py:206`).
///
/// # Errors
/// Returns the exception text the reference raises, prefixed with its type.
pub fn config_template() -> Result<Value, String> {
    Ok(Value::Object(core_config_template()))
}
