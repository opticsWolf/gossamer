//! Pure two-tier cache: in-memory LRU plus file-based TTL storage.
//!
//! Time is supplied by the caller so replay can use deterministic fixtures. Disk writes are
//! atomic rename operations under the configured directory; clearing removes only cache-owned
//! files.

use std::backtrace::Backtrace;
use std::collections::VecDeque;
use std::error::Error;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

use crate::cacheutils::{cache_disk_key, cache_human_size};

/// Canonical cache error.
#[derive(Debug)]
pub struct CacheError {
    message: String,
    backtrace: Backtrace,
}

impl CacheError {
    /// Creates a cache error with a caller-facing message.
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the plain message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for CacheError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for CacheError {}

type Result<T> = std::result::Result<T, CacheError>;

/// Configuration for [`Cache`].
#[derive(Debug, Clone)]
pub struct CacheConfig {
    /// Directory holding `.cache`, `.meta`, and transient `.tmp` files.
    pub cache_dir: PathBuf,
    /// Time-to-live in seconds.
    pub ttl_seconds: i64,
    /// Maximum number of in-memory entries.
    pub max_memory_entries: usize,
    /// Optional disk byte cap; zero means unlimited.
    pub max_disk_bytes: u64,
}

#[derive(Debug, Clone)]
struct MemoryEntry {
    key: String,
    content: String,
    timestamp: f64,
}

struct CacheState {
    memory: VecDeque<MemoryEntry>,
    hits: u64,
    misses: u64,
    memory_hits: u64,
    disk_hits: u64,
    evictions: u64,
}

/// Two-tier cache with deterministic, caller-supplied time.
pub struct Cache {
    config: CacheConfig,
    state: Mutex<CacheState>,
}

impl Cache {
    /// Creates a cache and removes stale temporary files from a previous process.
    ///
    /// # Errors
    ///
    /// Returns [`CacheError`] when the cache directory cannot be created.
    pub fn new(config: CacheConfig) -> Result<Self> {
        fs::create_dir_all(&config.cache_dir).map_err(|e| {
            CacheError::new(format!(
                "cannot create cache directory {}: {e}",
                config.cache_dir.display()
            ))
        })?;
        let cache = Self {
            config,
            state: Mutex::new(CacheState {
                memory: VecDeque::new(),
                hits: 0,
                misses: 0,
                memory_hits: 0,
                disk_hits: 0,
                evictions: 0,
            }),
        };
        cache.cleanup_stale_tmp();
        Ok(cache)
    }

    /// Reads a value from either tier, promoting disk hits into memory.
    pub fn get(&self, key: &str, now: f64) -> Option<String> {
        if let Some(content) = self.memory_get(key, now) {
            return Some(content);
        }
        if let Some(content) = self.disk_get(key, now) {
            if let Ok(mut state) = self.state.lock() {
                state.hits += 1;
                state.disk_hits += 1;
            }
            self.memory_put(key, &content, now);
            return Some(content);
        }
        if let Ok(mut state) = self.state.lock() {
            state.misses += 1;
        }
        None
    }

    /// Reads a disk value ignoring TTL, without purging or touching statistics.
    pub fn get_stale(&self, key: &str) -> Option<String> {
        if let Ok(state) = self.state.lock()
            && let Some(entry) = state.memory.iter().find(|item| item.key == key)
        {
            return Some(entry.content.clone());
        }
        let path = self.cache_path(&format!("{}.cache", cache_disk_key(key)));
        fs::read_to_string(path).ok()
    }

    /// Writes a value through both tiers.
    pub fn put(&self, key: &str, content: &str, now: f64) {
        self.memory_put(key, content, now);
        self.disk_put(key, content, now);
        let _ = self.enforce_disk_cap();
    }

    /// Empties both tiers, removing only cache-owned files.
    ///
    /// # Errors
    ///
    /// Returns [`CacheError`] when the cache directory cannot be read.
    pub fn clear(&self) -> Result<u64> {
        if let Ok(mut state) = self.state.lock() {
            state.memory.clear();
        }
        self.clear_disk()
    }

    /// Removes expired entries, enforces the byte cap, and cleans stale temporary files.
    ///
    /// # Errors
    ///
    /// Returns [`CacheError`] when the cache directory cannot be read.
    pub fn prune(&self, now: f64) -> Result<Value> {
        let removed_expired = self.prune_expired(now)?;
        let removed_evicted = self.enforce_disk_cap()?;
        self.cleanup_stale_tmp();
        let entries = self.disk_entries()?;
        Ok(json!({
            "removed_expired": removed_expired,
            "removed_evicted": removed_evicted,
            "total_entries": entries.len(),
            "total_bytes": entries.iter().map(|entry| entry.size).sum::<u64>(),
            "max_disk_bytes": self.config.max_disk_bytes,
        }))
    }

    /// Returns the same statistics shape exposed by the Python cache.
    ///
    /// # Errors
    ///
    /// Returns [`CacheError`] when the cache directory cannot be read.
    pub fn stats(&self) -> Result<Value> {
        let entries = self.disk_entries()?;
        let disk_size = entries.iter().map(|entry| entry.size).sum::<u64>();
        let state = self
            .state
            .lock()
            .map_err(|_| CacheError::new("cache state lock was poisoned".to_owned()))?;
        let hits = state.hits;
        let misses = state.misses;
        let mut out = Map::new();
        out.insert("memory_entries".to_owned(), json!(state.memory.len()));
        out.insert(
            "memory_max".to_owned(),
            json!(self.config.max_memory_entries),
        );
        out.insert("disk_entries".to_owned(), json!(entries.len()));
        out.insert("disk_size_bytes".to_owned(), json!(disk_size));
        out.insert(
            "disk_size_human".to_owned(),
            json!(cache_human_size(disk_size as f64)),
        );
        out.insert(
            "disk_max_bytes".to_owned(),
            json!(self.config.max_disk_bytes),
        );
        out.insert("disk_evictions".to_owned(), json!(state.evictions));
        let separator = std::path::MAIN_SEPARATOR.to_string();
        let cache_dir = self
            .config
            .cache_dir
            .to_string_lossy()
            .replace(['/', '\\'], separator.as_str());
        out.insert("cache_dir".to_owned(), json!(cache_dir));
        out.insert("ttl_seconds".to_owned(), json!(self.config.ttl_seconds));
        out.insert("total_hits".to_owned(), json!(hits));
        out.insert("total_misses".to_owned(), json!(misses));
        out.insert("memory_hits".to_owned(), json!(state.memory_hits));
        out.insert("disk_hits".to_owned(), json!(state.disk_hits));
        let hit_rate = if hits + misses == 0 {
            0.0
        } else {
            round4(hits as f64 / (hits + misses) as f64)
        };
        out.insert("hit_rate".to_owned(), json!(hit_rate));
        Ok(Value::Object(out))
    }

    fn memory_get(&self, key: &str, now: f64) -> Option<String> {
        let mut state = self.state.lock().ok()?;
        let index = state.memory.iter().position(|item| item.key == key)?;
        if now - state.memory[index].timestamp > self.config.ttl_seconds as f64 {
            state.memory.remove(index);
            return None;
        }
        let entry = state.memory.remove(index)?;
        state.memory.push_back(entry.clone());
        state.hits += 1;
        state.memory_hits += 1;
        Some(entry.content)
    }

    fn memory_put(&self, key: &str, content: &str, now: f64) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        if let Some(index) = state.memory.iter().position(|item| item.key == key) {
            let Some(mut entry) = state.memory.remove(index) else {
                return;
            };
            entry.content = content.to_owned();
            entry.timestamp = now;
            state.memory.push_back(entry);
            return;
        }
        while state.memory.len() >= self.config.max_memory_entries {
            if state.memory.pop_front().is_none() {
                break;
            }
        }
        state.memory.push_back(MemoryEntry {
            key: key.to_owned(),
            content: content.to_owned(),
            timestamp: now,
        });
    }

    fn cache_path(&self, name: &str) -> PathBuf {
        self.config.cache_dir.join(name)
    }

    fn disk_get(&self, key: &str, now: f64) -> Option<String> {
        let safe = cache_disk_key(key);
        let cache_file = self.cache_path(&format!("{safe}.cache"));
        let meta_file = self.cache_path(&format!("{safe}.meta"));
        if !cache_file.is_file() || !meta_file.is_file() {
            return None;
        }
        let meta: Value = serde_json::from_str(&fs::read_to_string(&meta_file).ok()?).ok()?;
        let timestamp = meta.get("timestamp")?.as_f64()?;
        if now - timestamp > self.config.ttl_seconds as f64 {
            self.remove_disk_files(&safe);
            return None;
        }
        let content = fs::read_to_string(&cache_file).ok()?;
        touch(&cache_file);
        Some(content)
    }

    fn disk_put(&self, key: &str, content: &str, now: f64) {
        let safe = cache_disk_key(key);
        let cache_file = self.cache_path(&format!("{safe}.cache"));
        let meta_file = self.cache_path(&format!("{safe}.meta"));
        let _ = atomic_write(&cache_file, content);
        let meta = json!({"timestamp": now}).to_string();
        let _ = atomic_write(&meta_file, &meta);
    }

    fn disk_entries(&self) -> Result<Vec<DiskEntry>> {
        let mut entries = Vec::new();
        let read = fs::read_dir(&self.config.cache_dir).map_err(|e| {
            CacheError::new(format!(
                "cannot read cache directory {}: {e}",
                self.config.cache_dir.display()
            ))
        })?;
        for item in read.flatten() {
            let path = item.path();
            if !path.is_file() || path.extension().and_then(|v| v.to_str()) != Some("cache") {
                continue;
            }
            let Some(prefix) = path.file_stem().and_then(|v| v.to_str()) else {
                continue;
            };
            let Ok(meta) = path.metadata() else {
                continue;
            };
            let mut size = meta.len();
            let meta_path = self.cache_path(&format!("{prefix}.meta"));
            if let Ok(meta_stat) = meta_path.metadata() {
                size += meta_stat.len();
            }
            let mtime = meta
                .modified()
                .ok()
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map_or(0.0, |duration| duration.as_secs_f64());
            entries.push(DiskEntry {
                mtime,
                size,
                prefix: prefix.to_owned(),
            });
        }
        entries.sort_by(|a, b| {
            a.mtime
                .total_cmp(&b.mtime)
                .then(a.size.cmp(&b.size))
                .then(a.prefix.cmp(&b.prefix))
        });
        Ok(entries)
    }

    fn enforce_disk_cap(&self) -> Result<u64> {
        if self.config.max_disk_bytes == 0 {
            return Ok(0);
        }
        let mut entries = self.disk_entries()?;
        let mut total = entries.iter().map(|entry| entry.size).sum::<u64>();
        if total <= self.config.max_disk_bytes {
            return Ok(0);
        }
        let mut evicted = 0u64;
        for entry in &entries {
            if total <= self.config.max_disk_bytes {
                break;
            }
            self.remove_disk_files(&entry.prefix);
            total -= entry.size.min(total);
            evicted += 1;
        }
        entries.clear();
        if let Ok(mut state) = self.state.lock() {
            state.evictions += evicted;
        }
        Ok(evicted)
    }

    fn prune_expired(&self, now: f64) -> Result<u64> {
        let mut removed = 0u64;
        for entry in self.disk_entries()? {
            let meta_file = self.cache_path(&format!("{}.meta", entry.prefix));
            let Ok(text) = fs::read_to_string(meta_file) else {
                continue;
            };
            let Ok(meta) = serde_json::from_str::<Value>(&text) else {
                continue;
            };
            if let Some(timestamp) = meta.get("timestamp").and_then(Value::as_f64)
                && now - timestamp > self.config.ttl_seconds as f64
            {
                self.remove_disk_files(&entry.prefix);
                removed += 1;
            }
        }
        Ok(removed)
    }

    fn cleanup_stale_tmp(&self) {
        if let Ok(read) = fs::read_dir(&self.config.cache_dir) {
            for item in read.flatten() {
                let path = item.path();
                if path.extension().and_then(|v| v.to_str()) == Some("tmp") {
                    let _ = fs::remove_file(path);
                }
            }
        }
    }

    fn remove_disk_files(&self, prefix: &str) {
        for name in [format!("{prefix}.cache"), format!("{prefix}.meta")] {
            let _ = fs::remove_file(self.cache_path(&name));
        }
    }

    fn clear_disk(&self) -> Result<u64> {
        let read = fs::read_dir(&self.config.cache_dir).map_err(|e| {
            CacheError::new(format!(
                "cannot read cache directory {}: {e}",
                self.config.cache_dir.display()
            ))
        })?;
        let mut removed = 0u64;
        for item in read.flatten() {
            let path = item.path();
            let cache_file = path.extension().and_then(|v| v.to_str()) == Some("cache")
                || path.extension().and_then(|v| v.to_str()) == Some("meta")
                || path.extension().and_then(|v| v.to_str()) == Some("tmp");
            if path.is_file() && cache_file && fs::remove_file(path).is_ok() {
                removed += 1;
            }
        }
        Ok(removed)
    }
    /// Keys currently held in the in-memory LRU, least-recently-used first.
    pub fn memory_keys(&self) -> Vec<String> {
        self.state.lock().map(|state| state.memory.iter().map(|entry| entry.key.clone()).collect()).unwrap_or_default()
    }

    /// Read one in-memory entry without touching statistics or recency.
    pub fn peek_memory(&self, key: &str) -> Option<String> {
        self.state.lock().ok()?.memory.iter().find(|entry| entry.key == key).map(|entry| entry.content.clone())
    }

    /// Remove one in-memory entry, if present.
    pub fn take_memory(&self, key: &str) -> Option<String> {
        let mut state = self.state.lock().ok()?;
        let index = state.memory.iter().position(|entry| entry.key == key)?;
        Some(state.memory.remove(index)?.content)
    }

    /// Replace or insert an in-memory entry with an explicit timestamp.
    pub fn set_memory_entry(&self, key: &str, content: &str, timestamp: f64) {
        if let Ok(mut state) = self.state.lock() {
            if let Some(entry) = state.memory.iter_mut().find(|entry| entry.key == key) {
                entry.content = content.to_string();
                entry.timestamp = timestamp;
            } else {
                state.memory.push_back(MemoryEntry {
                    key: key.to_string(),
                    content: content.to_string(),
                    timestamp,
                });
            }
        }
    }

    /// Empty the in-memory tier.
    pub fn clear_memory(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.memory.clear();
        }
    }
}

struct DiskEntry {
    mtime: f64,
    size: u64,
    prefix: String,
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

fn touch(path: &Path) {
    let Ok(file) = OpenOptions::new().write(true).open(path) else {
        return;
    };
    let _ = file.set_modified(SystemTime::now());
}

fn atomic_write(path: &Path, text: &str) -> io::Result<()> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| io::Error::other(e.to_string()))?
        .as_nanos();
    let name = path.file_name().and_then(|v| v.to_str()).unwrap_or("cache");
    let tmp = path.with_file_name(format!("{name}.{}.{}.tmp", std::process::id(), nonce));
    let mut file = File::create(&tmp)?;
    file.write_all(text.as_bytes())?;
    file.flush()?;
    drop(file);
    match fs::rename(&tmp, path) {
        Ok(()) => Ok(()),
        Err(err) => {
            let _ = fs::remove_file(&tmp);
            Err(err)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gossamer-cache-test-{name}"));
        let _ = fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn disk_roundtrip_and_ttl() {
        let dir = temp_dir("roundtrip");
        let cache = Cache::new(CacheConfig {
            cache_dir: dir.clone(),
            ttl_seconds: 10,
            max_memory_entries: 4,
            max_disk_bytes: 0,
        })
        .expect("cache");
        cache.put("url", "body", 100.0);
        assert_eq!(cache.get("other", 100.0), None);
        assert_eq!(cache.get("url", 105.0).as_deref(), Some("body"));
        assert_eq!(cache.get_stale("url"), Some("body".to_owned()));
        // Force the disk tier by clearing memory through a fresh cache instance.
        let disk = Cache::new(CacheConfig {
            cache_dir: dir.clone(),
            ttl_seconds: 10,
            max_memory_entries: 4,
            max_disk_bytes: 0,
        })
        .expect("cache");
        assert_eq!(disk.get("url", 109.0).as_deref(), Some("body"));
        assert_eq!(disk.get("url", 200.0), None);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn stats_and_clear_are_stable() {
        let dir = temp_dir("stats");
        let cache = Cache::new(CacheConfig {
            cache_dir: dir.clone(),
            ttl_seconds: 10,
            max_memory_entries: 4,
            max_disk_bytes: 0,
        })
        .expect("cache");
        cache.put("a", "1", 100.0);
        cache.put("b", "22", 101.0);
        let stats = cache.stats().expect("stats");
        assert_eq!(stats["disk_entries"], json!(2));
        assert_eq!(cache.clear().expect("clear"), 4);
        let _ = fs::remove_dir_all(dir);
    }
}
