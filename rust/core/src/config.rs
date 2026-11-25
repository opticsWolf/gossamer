//! Configuration discovery and resolution.
//!
//! Two optional JSON files drive gossamer:
//!
//! * `gossamer.json` — toolbox options and an inline `"keys"` section.
//!   Discovery: explicit path > `GOSSAMER_CONFIG` > `./gossamer.json` >
//!   `~/.gossamer/config.json`.
//! * keystore (default `~/.gossamer/keys.json`) — flat `{"NAME": "secret"}`.
//!   Discovery: explicit path > `GOSSAMER_KEYSTORE` > the `keystore` field of
//!   `gossamer.json` > `~/.gossamer/keys.json`.
//!
//! Resolution order everywhere is: explicit argument > `GOSSAMER_*` env >
//! legacy `STITCH_*` env > keystore file > `gossamer.json` `"keys"` section >
//! built-in default. Empty strings count as unset.
//!
//! The environment, the working directory and the home directory are inputs
//! rather than process globals, so behaviour stays testable and no interpreter
//! state hides in statics. Parsed-file caching lives in [`ConfigStore`], which
//! the caller owns.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde_json::{Map, Value};

/// Prefix for every current gossamer environment variable.
pub const PREFIX: &str = "GOSSAMER_";
/// Legacy prefix honored as a fallback by every reader.
pub const LEGACY_PREFIX: &str = "STITCH_";
/// Environment variable naming an explicit `gossamer.json`.
pub const CONFIG_ENV_VAR: &str = "GOSSAMER_CONFIG";
/// Environment variable naming an explicit keystore file.
pub const KEYSTORE_ENV_VAR: &str = "GOSSAMER_KEYSTORE";
/// Conventional `gossamer.json` filename.
pub const CONFIG_FILENAME: &str = "gossamer.json";
/// Conventional keystore filename.
pub const KEYSTORE_FILENAME: &str = "keys.json";

/// Secret slots recognized by `gossamer keystore --init`, in template order.
pub const KNOWN_KEYS: [&str; 27] = [
    "OPENALEX_KEY",
    "OPENALEX_EMAIL",
    "GOOGLE_API_KEY",
    "GOOGLE_CX",
    "BING_API_KEY",
    "EXA_API_KEY",
    "GITHUB_TOKEN",
    "NCBI_KEY",
    "NASA_KEY",
    "NVD_API_KEY",
    "ZENODO_TOKEN",
    "SEMANTICSCHOLAR_API_KEY",
    "HF_TOKEN",
    "CONGRESS_KEY",
    "CENSUS_KEY",
    "CHEMXIV_TOKEN",
    "ALPHA_VANTAGE_KEY",
    "COURTLISTENER_KEY",
    "GOVINFO_KEY",
    "UNPAYWALL_EMAIL",
    "EPO_KEY",
    "EPO_SECRET",
    "KIPRIS_KEY",
    "PATENTSVIEW_API_KEY",
    "LENS_API_KEY",
    "JPO_USERNAME",
    "JPO_PASSWORD",
];

/// Read-only view of environment variables.
///
/// Production code uses [`ProcessEnv`]; tests use a [`BTreeMap`] snapshot.
pub trait Env {
    /// Value of `name`, or `None` when unset.
    fn get(&self, name: &str) -> Option<String>;
}

/// Live process environment.
#[derive(Debug, Clone, Copy, Default)]
pub struct ProcessEnv;

impl Env for ProcessEnv {
    fn get(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }
}

impl Env for BTreeMap<String, String> {
    fn get(&self, name: &str) -> Option<String> {
        BTreeMap::get(self, name).cloned()
    }
}

/// Directories consulted during discovery.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Paths {
    /// Directory searched for `./gossamer.json`.
    pub cwd: PathBuf,
    /// Home directory holding the default `.gossamer` folder.
    pub home: PathBuf,
}

impl Paths {
    /// Paths from the live process environment (`current_dir` and `HOME`).
    ///
    /// Returns [`ConfigError`] when the working directory cannot be read.
    ///
    /// # Errors
    /// Fails when `std::env::current_dir` fails.
    pub fn from_process() -> Result<Self, ConfigError> {
        let cwd = std::env::current_dir().map_err(|e| {
            ConfigError::new(
                ConfigErrorKind::Io,
                format!("cannot read the current directory: {e}"),
            )
        })?;
        Ok(Self::new(cwd, home_dir()))
    }

    /// Paths from an explicit working directory and home directory.
    pub fn new(cwd: impl Into<PathBuf>, home: impl Into<PathBuf>) -> Self {
        Self {
            cwd: cwd.into(),
            home: home.into(),
        }
    }

    /// `~/.gossamer`.
    pub fn gossamer_dir(&self) -> PathBuf {
        self.home.join(".gossamer")
    }
}

fn home_dir() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

/// What went wrong while loading configuration.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfigErrorKind {
    /// A named file does not exist.
    NotFound,
    /// A file exists but does not hold valid JSON.
    InvalidJson,
    /// A file holds JSON that is not an object where one is required.
    NotObject,
    /// The file could not be read.
    Io,
}

/// Failure to load configuration, with the offending path and a backtrace.
#[derive(Debug)]
pub struct ConfigError {
    kind: ConfigErrorKind,
    message: String,
    backtrace: std::backtrace::Backtrace,
}

impl ConfigError {
    fn new(kind: ConfigErrorKind, message: String) -> Self {
        Self {
            kind,
            message,
            backtrace: std::backtrace::Backtrace::capture(),
        }
    }

    /// Classification used by bindings to pick an exception type.
    pub fn kind(&self) -> ConfigErrorKind {
        self.kind
    }

    /// Human-readable message, without the backtrace.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Backtrace captured where the error was created.
    pub fn backtrace(&self) -> &std::backtrace::Backtrace {
        &self.backtrace
    }
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ConfigError {}

/// Parsed-file cache and one-shot warning memory.
///
/// Mirrors the module-level cache of the reference implementation: files are
/// re-parsed only when their modification time changes. Hold one per process.
#[derive(Debug, Default)]
pub struct ConfigStore {
    file_cache: BTreeMap<String, (f64, Value)>,
    warned_perms: BTreeSet<String>,
}

impl ConfigStore {
    /// Empty store.
    pub fn new() -> Self {
        Self::default()
    }

    /// Drop every cached parse (tests that rewrite temporary files).
    pub fn clear_caches(&mut self) {
        self.file_cache.clear();
    }
}

/// A load result plus any warnings the caller should log.
#[derive(Debug)]
pub struct Keystore {
    /// Flat secret map with stringified values, in file order.
    pub values: Map<String, Value>,
    /// Warnings to log verbatim.
    pub warnings: Vec<String>,
}

/// Explicit legacy spellings for `new`, followed by the derived `STITCH_*` one.
///
/// `legacy` is the caller-supplied extra name or names; duplicates are dropped
/// while preserving first-seen order.
pub fn legacy_names(new: &str, legacy: Option<&[String]>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for name in legacy.unwrap_or(&[]).iter() {
        if !out.contains(name) {
            out.push(name.clone());
        }
    }
    if let Some(rest) = new.strip_prefix(PREFIX) {
        let derived = format!("{LEGACY_PREFIX}{rest}");
        if !out.contains(&derived) {
            out.push(derived);
        }
    }
    out
}

/// Candidate keystore/config keys: short name, full name, then legacies.
pub fn short_names(new: &str, legacy: Option<&[String]>) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    if let Some(rest) = new.strip_prefix(PREFIX) {
        names.push(rest.to_string());
    }
    names.push(new.to_string());
    for name in legacy_names(new, legacy) {
        names.push(name);
    }
    names
}

/// Locate `gossamer.json`.
///
/// Explicit path, then `GOSSAMER_CONFIG`, then `./gossamer.json`, then
/// `~/.gossamer/config.json`. Returns the first candidate that is a file.
pub fn find_config_file(env: &dyn Env, paths: &Paths, explicit: Option<&str>) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(explicit) = explicit.filter(|value| !value.is_empty()) {
        candidates.push(PathBuf::from(explicit));
    }
    if let Some(name) = env.get(CONFIG_ENV_VAR).filter(|value| !value.is_empty()) {
        candidates.push(PathBuf::from(name));
    }
    candidates.push(paths.cwd.join(CONFIG_FILENAME));
    candidates.push(paths.gossamer_dir().join("config.json"));
    candidates.into_iter().find(|path| path.is_file())
}

/// Locate the keystore.
///
/// Explicit path, then `GOSSAMER_KEYSTORE`, then the `keystore` field of the
/// discovered `gossamer.json` (with `~` expanded), then
/// `~/.gossamer/keys.json`.
pub fn find_keystore_file(
    store: &mut ConfigStore,
    env: &dyn Env,
    paths: &Paths,
    explicit: Option<&str>,
) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(explicit) = explicit.filter(|value| !value.is_empty()) {
        candidates.push(PathBuf::from(explicit));
    }
    if let Some(name) = env.get(KEYSTORE_ENV_VAR).filter(|value| !value.is_empty()) {
        candidates.push(PathBuf::from(name));
    }
    if let Some(config_path) = find_config_file(env, paths, None)
        && let Ok(config) = load_config_file(store, env, paths, Some(&path_string(&config_path)))
        && let Some(Value::String(field)) = config.get("keystore")
    {
        let field = field.trim();
        if !field.is_empty() {
            candidates.push(expand_user(field, &paths.home));
        }
    }
    candidates.push(paths.gossamer_dir().join(KEYSTORE_FILENAME));
    candidates.into_iter().find(|path| path.is_file())
}

fn path_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn expand_user(raw: &str, home: &Path) -> PathBuf {
    if raw == "~" {
        return home.to_path_buf();
    }
    if let Some(rest) = raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")) {
        return home.join(rest);
    }
    PathBuf::from(raw)
}

fn modified_seconds(path: &Path) -> Option<f64> {
    let modified = fs::metadata(path).ok()?.modified().ok()?;
    // A clock before the Unix epoch yields the same "unknown" answer as a
    // missing mtime; callers then skip the cache rather than trust it.
    modified
        .duration_since(UNIX_EPOCH)
        .ok()
        .map(|d| d.as_secs_f64())
}

/// Parse `path` as JSON, reusing the cached value while its mtime is unchanged.
///
/// `display` is what error messages and the cache key use, so callers can keep
/// Python's `str(path)` spelling for non-path arguments.
///
/// # Errors
/// [`ConfigErrorKind::NotFound`] when `path` cannot be stat-ed,
/// [`ConfigErrorKind::InvalidJson`] when it does not parse, and
/// [`ConfigErrorKind::Io`] when it cannot be read.
pub fn load_json_file(
    store: &mut ConfigStore,
    path: &Path,
    display: &str,
) -> Result<Value, ConfigError> {
    let mtime = modified_seconds(path).ok_or_else(|| {
        ConfigError::new(
            ConfigErrorKind::NotFound,
            format!("config file not found: {display}"),
        )
    })?;
    if let Some((cached_mtime, cached)) = store.file_cache.get(display)
        && *cached_mtime == mtime
    {
        return Ok(cached.clone());
    }
    let text = fs::read_to_string(path).map_err(|e| {
        ConfigError::new(ConfigErrorKind::Io, format!("cannot read {display}: {e}"))
    })?;
    let value: Value = serde_json::from_str(&text).map_err(|e| {
        ConfigError::new(
            ConfigErrorKind::InvalidJson,
            format!(
                "invalid JSON in {display}: {}",
                python_json_detail(&text, &e)
            ),
        )
    })?;
    store
        .file_cache
        .insert(display.to_string(), (mtime, value.clone()));
    Ok(value)
}

/// Load `gossamer.json` as an object; `{}` when no file is discovered.
///
/// # Errors
/// [`ConfigErrorKind::NotFound`] when an explicit path or `GOSSAMER_CONFIG`
/// names a missing file, [`ConfigErrorKind::InvalidJson`] for invalid JSON,
/// and [`ConfigErrorKind::NotObject`] when the top level is not an object.
pub fn load_config_file(
    store: &mut ConfigStore,
    env: &dyn Env,
    paths: &Paths,
    explicit: Option<&str>,
) -> Result<Map<String, Value>, ConfigError> {
    let wanted = explicit
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| env.get(CONFIG_ENV_VAR).filter(|value| !value.is_empty()));
    if let Some(wanted) = wanted {
        return object_or_error(load_json_file(store, Path::new(&wanted), &wanted)?, &wanted);
    }
    let Some(found) = find_config_file(env, paths, None) else {
        return Ok(Map::new());
    };
    let display = path_string(&found);
    object_or_error(load_json_file(store, &found, &display)?, &display)
}

fn object_or_error(value: Value, display: &str) -> Result<Map<String, Value>, ConfigError> {
    match value {
        Value::Object(map) => Ok(map),
        _ => Err(ConfigError::new(
            ConfigErrorKind::NotObject,
            format!("gossamer.json must hold an object: {display}"),
        )),
    }
}

/// Load the keystore as a flat string map; empty when none is discovered.
///
/// Non-string values are stringified, `null` becomes `""`, and files readable
/// by group or others produce a one-shot permission warning.
///
/// # Errors
/// Same missing/invalid semantics as [`load_config_file`], plus
/// [`ConfigErrorKind::NotFound`] for an explicit missing keystore.
pub fn load_keystore(
    store: &mut ConfigStore,
    env: &dyn Env,
    paths: &Paths,
    explicit: Option<&str>,
) -> Result<Keystore, ConfigError> {
    let wanted = explicit
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| env.get(KEYSTORE_ENV_VAR).filter(|value| !value.is_empty()));
    let (path, display, value) = if let Some(wanted) = wanted {
        let path = PathBuf::from(&wanted);
        if !path.is_file() {
            return Err(ConfigError::new(
                ConfigErrorKind::NotFound,
                format!("keystore file not found: {wanted}"),
            ));
        }
        let value = load_json_file(store, &path, &wanted)?;
        (path, wanted, value)
    } else {
        let Some(found) = find_keystore_file(store, env, paths, None) else {
            return Ok(Keystore {
                values: Map::new(),
                warnings: Vec::new(),
            });
        };
        let display = path_string(&found);
        let value = load_json_file(store, &found, &display)?;
        (found, display, value)
    };

    let Value::Object(map) = value else {
        return Err(ConfigError::new(
            ConfigErrorKind::NotObject,
            format!("keystore must hold an object: {display}"),
        ));
    };

    let mut warnings = Vec::new();
    // 0o077 covers the group and other permission bits: any of them set means
    // the secret file is readable beyond its owner.
    let mode = file_mode(&path);
    if mode & 0o077 != 0 && store.warned_perms.insert(display.clone()) {
        warnings.push(format!(
            "keystore {display} is readable by group/other (mode {mode:o}); consider chmod 600"
        ));
    }

    let mut values = Map::new();
    for (key, value) in map {
        values.insert(key, Value::String(value_to_str(&value)));
    }
    Ok(Keystore { values, warnings })
}

#[cfg(unix)]
fn file_mode(path: &Path) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    fs::metadata(path)
        .map(|meta| meta.permissions().mode() & 0o777)
        .unwrap_or(0o600)
}

#[cfg(not(unix))]
fn file_mode(_path: &Path) -> u32 {
    // Windows has no group/other permission bits to warn about.
    0o600
}

/// First non-empty keystore value for `names`, tried in order.
pub fn lookup_key(
    store: &mut ConfigStore,
    env: &dyn Env,
    paths: &Paths,
    names: &[String],
    keystore: Option<&str>,
) -> Option<String> {
    let loaded = load_keystore(store, env, paths, keystore).ok()?;
    for name in names {
        if let Some(Value::String(value)) = loaded.values.get(name)
            && !value.is_empty()
        {
            return Some(value.clone());
        }
    }
    None
}

/// Read `new` with legacy fallback, then the keystore, then inline config keys.
///
/// Empty strings count as unset at every level.
///
/// # Examples
/// ```
/// use std::collections::BTreeMap;
/// use std::path::Path;
/// use gossamer::config::{ConfigStore, Paths, getenv};
///
/// let mut env = BTreeMap::new();
/// env.insert("GOSSAMER_MAX_TOKENS".to_string(), "11".to_string());
/// let paths = Paths::new(Path::new("."), Path::new("."));
/// let mut store = ConfigStore::new();
/// let value = getenv(&mut store, &env, &paths, "GOSSAMER_MAX_TOKENS", None, None);
/// assert_eq!(value.as_deref(), Some("11"));
/// ```
pub fn getenv(
    store: &mut ConfigStore,
    env: &dyn Env,
    paths: &Paths,
    new: &str,
    default: Option<String>,
    legacy: Option<&[String]>,
) -> Option<String> {
    if let Some(raw) = env.get(new).filter(|value| !value.is_empty()) {
        return Some(raw);
    }
    for old in legacy_names(new, legacy) {
        if let Some(raw) = env.get(&old).filter(|value| !value.is_empty()) {
            return Some(raw);
        }
    }
    let names = short_names(new, legacy);
    if let Some(found) = lookup_key(store, env, paths, &names, None).filter(|v| !v.is_empty()) {
        return Some(found);
    }
    let inline = load_config_file(store, env, paths, None)
        .ok()
        .and_then(|config| config.get("keys").cloned());
    if let Some(Value::Object(keys)) = inline {
        for name in &names {
            match keys.get(name) {
                Some(Value::String(value)) if !value.is_empty() => return Some(value.clone()),
                Some(Value::Null) | None => {}
                Some(value) => {
                    let rendered = value_to_str(value);
                    if !rendered.is_empty() {
                        return Some(rendered);
                    }
                }
            }
        }
    }
    default
}

/// Boolean variant of [`getenv`]; true for `1`, `true`, `yes` or `on`.
pub fn getenv_bool(
    store: &mut ConfigStore,
    env: &dyn Env,
    paths: &Paths,
    new: &str,
    default: bool,
    legacy: Option<&[String]>,
) -> bool {
    match getenv(store, env, paths, new, None, legacy) {
        None => default,
        Some(raw) => matches!(
            raw.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
    }
}

/// `{name: ""}` template covering every known secret slot, in template order.
pub fn keystore_template() -> Map<String, Value> {
    let mut map = Map::new();
    for name in KNOWN_KEYS {
        map.insert(name.to_string(), Value::String(String::new()));
    }
    map
}

/// Documented `gossamer.json` template (safe defaults, no secrets).
pub fn config_template() -> Map<String, Value> {
    let mut map = Map::new();
    map.insert(
        "$comment".to_string(),
        Value::String(
            "gossamer configuration (docs: README, gossamer/mcp_server.py). \
             Environment variables override every value here."
                .to_string(),
        ),
    );
    for (key, value) in [
        ("cache_dir", Value::String(".gossamer_cache".into())),
        ("cache_ttl_seconds", Value::from(3600)),
        ("cache_max_bytes", Value::from(0)),
        ("cache_memory_entries", Value::from(100)),
        ("ddgs_delay", Value::from(1.0)),
        ("ddgs_jitter", Value::from(1.0)),
        ("domain_delay", Value::from(0.5)),
        ("fetch_jitter", Value::from(1.0)),
        ("fetch_mode", Value::String("auto".into())),
        ("max_markdown_chars", Value::from(8000)),
        ("max_tokens", Value::from(0)),
        ("model_name", Value::String("gpt-4o".into())),
        ("max_links", Value::from(20)),
        ("candidate_cap", Value::from(500)),
        ("max_concurrency", Value::from(8)),
        ("max_response_bytes", Value::from(5 * 1024 * 1024)),
        ("liveness_timeout", Value::from(10.0)),
        ("link_budget_ratio", Value::from(0.25)),
        ("respect_robots", Value::Bool(true)),
        ("conditional_revalidation", Value::Bool(true)),
        ("search_merge", Value::Bool(false)),
        ("keystore", Value::String("~/.gossamer/keys.json".into())),
        ("keys", Value::Object(Map::new())),
    ] {
        map.insert(key.to_string(), value);
    }
    let mut guard = Map::new();
    guard.insert("enabled".into(), Value::Bool(false));
    guard.insert("mode".into(), Value::String("annotate".into()));
    guard.insert(
        "scopes".into(),
        Value::String("page_markdown,document_text".into()),
    );
    guard.insert("threshold".into(), Value::from(0.7));
    guard.insert("max_chunks".into(), Value::from(40));
    map.insert("guard".into(), Value::Object(guard));
    map
}

/// Coerce the reference's `legacy` argument (string, list, or nothing).
///
/// Non-string entries are stringified the way Python's `str()` would.
pub fn legacy_names_from_json(legacy: Option<&Value>) -> Vec<String> {
    match legacy {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::String(name)) => vec![name.clone()],
        Some(Value::Array(items)) => items.iter().map(py_str_value).collect(),
        Some(other) => vec![py_str_value(other)],
    }
}

/// Prefix a core error with the exception type the reference raises.
pub fn exception_name(kind: ConfigErrorKind) -> &'static str {
    match kind {
        ConfigErrorKind::NotFound => "FileNotFoundError",
        ConfigErrorKind::InvalidJson | ConfigErrorKind::NotObject => "ValueError",
        ConfigErrorKind::Io => "OSError",
    }
}

/// Python `str(value)` spelling for a decoded JSON value.
pub fn py_str_value(value: &Value) -> String {
    value_to_str(value)
}

fn value_to_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        other => py_container_repr(other),
    }
}

fn py_container_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => crate::compat::py_repr(text),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(py_container_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(key, value)| {
                    format!(
                        "{}: {}",
                        crate::compat::py_repr(key),
                        py_container_repr(value)
                    )
                })
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// CPython-shaped `json` error detail: `Expecting value: line 1 column 1 (char 0)`.
///
/// Only the message categories the parser can produce are reconstructed; exotic
/// malformed input may word the detail differently from CPython while keeping
/// the same `ValueError` type and path prefix.
fn python_json_detail(text: &str, error: &serde_json::Error) -> String {
    let message = error.to_string();
    let category = if message.contains("key must be a string") {
        "Expecting property name enclosed in double quotes"
    } else if message.contains("trailing characters") {
        "Extra data"
    } else if message.contains("expected `,` or `}`") || message.contains("expected `,` or `]`") {
        "Expecting ',' delimiter"
    } else if message.contains("expected `:`") {
        "Expecting ':' delimiter"
    } else if message.contains("EOF while parsing a string")
        || message.contains("unterminated string")
    {
        "Unterminated string starting at"
    } else {
        "Expecting value"
    };
    let line = error.line();
    let (char_offset, char_column) = char_position(text, line, error.column());
    format!("{category}: line {line} column {char_column} (char {char_offset})")
}

/// Character offset and 1-based column for serde's byte-based line/column pair.
///
/// `serde_json` reports a byte column; CPython reports characters, so any
/// multi-byte character earlier on the line shifts the two apart.
fn char_position(text: &str, line: usize, byte_column: usize) -> (usize, usize) {
    let mut char_offset = 0usize;
    for (index, current) in text.split('\n').enumerate() {
        if index + 1 == line {
            let byte_index = byte_column.saturating_sub(1);
            let chars_on_line = current
                .bytes()
                .take(byte_index)
                .filter(|byte| byte & 0xC0 != 0x80)
                .count();
            return (char_offset + chars_on_line, chars_on_line + 1);
        }
        char_offset += current.chars().count() + 1;
    }
    let total = text.chars().count();
    (total, total + 1)
}

/// Python-compatible coercion for `list[str]` tool arguments.
pub fn ensure_str_list(value: Option<&Value>, name: &str) -> Result<Vec<String>, String> {
    fn py_type(value: &Value) -> &'static str {
        match value {
            Value::Null => "NoneType",
            Value::Bool(_) => "bool",
            Value::Number(number) => {
                if number.is_i64() || number.is_u64() {
                    "int"
                } else {
                    "float"
                }
            }
            Value::String(_) => "str",
            Value::Array(_) => "list",
            Value::Object(_) => "dict",
        }
    }
    match value {
        None => Ok(Vec::new()),
        Some(Value::String(text)) => {
            if text.trim().is_empty() {
                Ok(Vec::new())
            } else {
                Ok(vec![text.clone()])
            }
        }
        Some(Value::Array(items)) => {
            let mut out = Vec::with_capacity(items.len());
            for item in items {
                match item {
                    Value::String(text) => out.push(text.clone()),
                    other => {
                        return Err(format!(
                            "TypeError: {} must be a list of strings, got an item of type {}",
                            name,
                            py_type(other)
                        ));
                    }
                }
            }
            Ok(out)
        }
        Some(Value::Object(object)) => {
            if let Some(Value::Array(items)) = object.get("$tuple") {
                let mut out = Vec::with_capacity(items.len());
                for item in items {
                    match item {
                        Value::String(text) => out.push(text.clone()),
                        other => {
                            return Err(format!(
                                "TypeError: {} must be a list of strings, got an item of type {}",
                                name,
                                py_type(other)
                            ));
                        }
                    }
                }
                Ok(out)
            } else {
                Err(format!(
                    "TypeError: {} must be a list of strings (a bare string is also accepted and wrapped), got dict",
                    name
                ))
            }
        }
        Some(other) => Err(format!(
            "TypeError: {} must be a list of strings (a bare string is also accepted and wrapped), got {}",
            name,
            py_type(other)
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect()
    }

    #[test]
    fn legacy_names_keep_explicit_then_derived_order() {
        let legacy = vec!["STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES".to_string()];
        let names = legacy_names("GOSSAMER_MAX_RESPONSE_BYTES", Some(&legacy));
        assert_eq!(
            names,
            vec![
                "STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES".to_string(),
                "STITCH_MAX_RESPONSE_BYTES".to_string()
            ]
        );
    }

    #[test]
    fn legacy_names_deduplicate() {
        let legacy = vec!["STITCH_MAX_LINKS".to_string()];
        let names = legacy_names("GOSSAMER_MAX_LINKS", Some(&legacy));
        assert_eq!(names, vec!["STITCH_MAX_LINKS".to_string()]);
    }

    #[test]
    fn short_names_put_short_form_first() {
        let names = short_names("GOSSAMER_OPENALEX_KEY", None);
        assert_eq!(
            names,
            vec![
                "OPENALEX_KEY".to_string(),
                "GOSSAMER_OPENALEX_KEY".to_string(),
                "STITCH_OPENALEX_KEY".to_string()
            ]
        );
    }

    #[test]
    fn empty_env_values_count_as_unset() {
        let dir = std::env::temp_dir().join("gossamer-config-empty-env");
        let paths = Paths::new(&dir, &dir);
        let mut store = ConfigStore::new();
        let environment = env(&[("GOSSAMER_MODEL_NAME", "")]);
        assert_eq!(
            getenv(
                &mut store,
                &environment,
                &paths,
                "GOSSAMER_MODEL_NAME",
                Some("fallback".into()),
                None
            ),
            Some("fallback".to_string())
        );
    }

    #[test]
    fn env_beats_legacy_spelling() {
        let dir = std::env::temp_dir().join("gossamer-config-env-order");
        let paths = Paths::new(&dir, &dir);
        let mut store = ConfigStore::new();
        let environment = env(&[("GOSSAMER_MAX_TOKENS", "11"), ("STITCH_MAX_TOKENS", "22")]);
        assert_eq!(
            getenv(
                &mut store,
                &environment,
                &paths,
                "GOSSAMER_MAX_TOKENS",
                None,
                None
            ),
            Some("11".to_string())
        );
    }

    #[test]
    fn boolean_spellings_match_the_reference() {
        for raw in ["1", "true", "TRUE", " yes ", "on"] {
            let dir = std::env::temp_dir().join("gossamer-config-bool");
            let paths = Paths::new(&dir, &dir);
            let mut store = ConfigStore::new();
            let environment = env(&[("GOSSAMER_RESPECT_ROBOTS", raw)]);
            assert!(
                getenv_bool(
                    &mut store,
                    &environment,
                    &paths,
                    "GOSSAMER_RESPECT_ROBOTS",
                    false,
                    None
                ),
                "{raw} should read as true"
            );
        }
        let dir = std::env::temp_dir().join("gossamer-config-bool");
        let paths = Paths::new(&dir, &dir);
        let mut store = ConfigStore::new();
        let environment = env(&[("GOSSAMER_RESPECT_ROBOTS", "0")]);
        assert!(!getenv_bool(
            &mut store,
            &environment,
            &paths,
            "GOSSAMER_RESPECT_ROBOTS",
            true,
            None
        ));
    }

    #[test]
    fn templates_hold_every_known_slot() {
        let template = keystore_template();
        assert_eq!(template.len(), KNOWN_KEYS.len());
        assert_eq!(
            template.get("OPENALEX_KEY"),
            Some(&Value::String(String::new()))
        );
        assert_eq!(
            config_template().get("max_response_bytes"),
            Some(&Value::from(5_242_880))
        );
    }

    #[test]
    fn json_detail_matches_cpython_wording() {
        let error = serde_json::from_str::<Value>("{not json").unwrap_err();
        assert_eq!(
            python_json_detail("{not json", &error),
            "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
        );
    }

    #[test]
    fn json_detail_counts_characters_not_bytes() {
        let error = serde_json::from_str::<Value>("{\"ä\": }").unwrap_err();
        let detail = python_json_detail("{\"ä\": }", &error);
        assert_eq!(
            detail, "Expecting value: line 1 column 7 (char 6)",
            "CPython counts characters, serde counts bytes"
        );
    }
}
