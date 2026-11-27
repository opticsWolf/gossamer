//! Stateful runtime shared by the Rust CLI tools.
//!
//! The pure core already owns the deterministic kernels. This module adds the
//! small amount of mutable state those kernels need in a long-lived process:
//! the two-tier cache, bounded visited/in-flight sets, per-domain timestamps,
//! and the per-host robots.txt cache. All time comes from the caller so replay
//! can pin decisions; network access stays inside the explicit `robots_*`
//! helpers and is fail-open exactly like the Python reference.

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};
use url::Url;

use crate::agent::crawl_host_key;
use crate::cache::{Cache, CacheConfig};
use crate::discovery::fetch_text_with_status_and_user_agent;
use crate::document::prepare_url;
use crate::robots::{match_rules, parse_robots, url_path};
use crate::service::fetch::throttle_gap;
use crate::urls::canonical_url;

/// Chrome UA used by the Python reference for the first rotation entry.
pub const ROBOTS_USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const DEFAULT_TTL_SECONDS: f64 = 86_400.0;
const FAILURE_TTL_SECONDS: f64 = 300.0;
const ROBOTS_TIMEOUT_SECONDS: f64 = 5.0;
const MAX_ROBOTS_BYTES: usize = 512 * 1024;
const VISITED_URL_CAP: usize = 20_000;
const DOMAIN_TS_CAP: usize = 4096;

fn env_f64(name: &str, default: f64) -> f64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<f64>().ok())
        .unwrap_or(default)
}

fn env_bool(name: &str, default: bool) -> bool {
    match std::env::var(name) {
        Ok(value) => !matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "off"
        ),
        Err(_) => default,
    }
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .unwrap_or_default()
}

/// Runtime knobs mirrored from `ToolboxConfig` and `GOSSAMER_*` variables.
#[derive(Debug, Clone)]
pub struct RuntimeConfig {
    /// Directory holding the two-tier page cache.
    pub cache_dir: PathBuf,
    /// Cache TTL in seconds.
    pub ttl_seconds: i64,
    /// Maximum in-memory cache entries.
    pub max_memory_entries: usize,
    /// Optional disk byte cap (zero means unlimited).
    pub max_disk_bytes: u64,
    /// Minimum gap between fetches to one domain.
    pub fetch_interval: f64,
    /// Uniform jitter upper bound added to the minimum gap.
    pub fetch_jitter: f64,
    /// Whether robots.txt rules are enforced.
    pub respect_robots: bool,
    /// User-Agent used for robots.txt probes and rule matching.
    pub user_agent: String,
    /// Bounded visited-set size.
    pub visited_cap: usize,
    /// Bounded domain-timestamp size.
    pub domain_cap: usize,
}

impl RuntimeConfig {
    /// Build a config from environment variables, with a caller-supplied cache directory.
    pub fn from_cache_dir(cache_dir: PathBuf) -> Self {
        let ttl_seconds = std::env::var("GOSSAMER_CACHE_TTL_SECONDS")
            .ok()
            .and_then(|value| value.trim().parse::<i64>().ok())
            .unwrap_or(3600);
        let max_memory_entries = std::env::var("GOSSAMER_CACHE_MEMORY_ENTRIES")
            .ok()
            .and_then(|value| value.trim().parse::<usize>().ok())
            .unwrap_or(100);
        let max_disk_bytes = std::env::var("GOSSAMER_CACHE_MAX_BYTES")
            .ok()
            .and_then(|value| value.trim().parse::<u64>().ok())
            .unwrap_or(0);
        Self {
            cache_dir,
            ttl_seconds,
            max_memory_entries,
            max_disk_bytes,
            fetch_interval: env_f64("GOSSAMER_DOMAIN_DELAY", 0.5),
            fetch_jitter: env_f64("GOSSAMER_FETCH_JITTER", 1.0),
            respect_robots: env_bool("GOSSAMER_RESPECT_ROBOTS", true),
            user_agent: ROBOTS_USER_AGENT.to_string(),
            visited_cap: VISITED_URL_CAP,
            domain_cap: DOMAIN_TS_CAP,
        }
    }
}

#[derive(Debug, Clone)]
struct HostState {
    rules: Vec<(bool, String)>,
    crawl_delay: Option<f64>,
    disallow_all: bool,
    fetched_at: f64,
    ttl: f64,
}

impl Default for HostState {
    fn default() -> Self {
        Self {
            rules: Vec::new(),
            crawl_delay: None,
            disallow_all: false,
            fetched_at: 0.0,
            ttl: FAILURE_TTL_SECONDS,
        }
    }
}

#[derive(Debug, Default)]
struct RuntimeInner {
    visited: VecDeque<String>,
    in_flight: HashSet<String>,
    domains: VecDeque<(String, f64)>,
    robots: HashMap<String, HostState>,
    robots_in_flight: HashSet<String>,
}

/// Stateful runtime used by the Rust front door.
pub struct RuntimeState {
    config: RuntimeConfig,
    cache: Cache,
    inner: Mutex<RuntimeInner>,
}

impl RuntimeState {
    /// Create the runtime, including the two-tier cache directory.
    ///
    /// # Errors
    ///
    /// Returns the cache creation message when the cache directory cannot be created.
    pub fn new(config: RuntimeConfig) -> Result<Self, String> {
        let cache = Cache::new(CacheConfig {
            cache_dir: config.cache_dir.clone(),
            ttl_seconds: config.ttl_seconds,
            max_memory_entries: config.max_memory_entries,
            max_disk_bytes: config.max_disk_bytes,
        })
        .map_err(|error| error.message().to_string())?;
        Ok(Self {
            config,
            cache,
            inner: Mutex::new(RuntimeInner::default()),
        })
    }

    /// Return the underlying two-tier cache.
    pub fn cache(&self) -> &Cache {
        &self.cache
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, RuntimeInner> {
        self.inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Normalize and validate a caller-supplied URL.
    pub fn prepare_url(&self, raw: &str) -> Result<String, crate::document::UrlPrepError> {
        prepare_url(raw, None)
    }

    /// Canonical identity used by visited and in-flight bookkeeping.
    pub fn identity_key(&self, url: &str) -> String {
        canonical_url(url, "drop-tracking", None)
            .map(|value| value.to_string())
            .unwrap_or_else(|_| url.to_string())
    }

    /// Namespaced page-cache key for one canonical URL.
    pub fn page_cache_key(&self, url: &str) -> String {
        format!("page:{}", self.identity_key(url))
    }

    /// Read a fresh page-cache entry, or `None` on miss/corruption.
    pub fn page_cache_get(&self, url: &str, now: f64) -> Option<Value> {
        let raw = self.cache.get(&self.page_cache_key(url), now)?;
        serde_json::from_str(&raw).ok()
    }

    /// Write an untruncated page-cache entry.
    pub fn page_cache_put(&self, url: &str, entry: &Value, now: f64) {
        if let Ok(raw) = serde_json::to_string(entry) {
            self.cache.put(&self.page_cache_key(url), &raw, now);
        }
    }

    /// Claim a URL for fetching; returns false when already visited or in flight.
    pub fn claim(&self, url: &str) -> bool {
        let key = self.identity_key(url);
        let mut inner = self.lock();
        if inner.visited.contains(&key) || inner.in_flight.contains(&key) {
            return false;
        }
        inner.in_flight.insert(key);
        true
    }

    /// Release an idempotent in-flight claim.
    pub fn release(&self, url: &str) {
        let key = self.identity_key(url);
        self.lock().in_flight.remove(&key);
    }

    /// Forget a visited URL so an explicit render strategy may refetch it.
    pub fn forget_visited(&self, url: &str) {
        let key = self.identity_key(url);
        self.lock().visited.retain(|item| item != &key);
    }

    /// True when the canonical identity is in the bounded visited set.
    pub fn is_visited(&self, url: &str) -> bool {
        let key = self.identity_key(url);
        self.lock().visited.contains(&key)
    }

    /// Mark a successful fetch, evicting oldest entries down to half the cap.
    pub fn mark_visited(&self, url: &str) {
        let key = self.identity_key(url);
        let cap = self.config.visited_cap.max(1);
        let mut inner = self.lock();
        if let Some(index) = inner.visited.iter().position(|item| item == &key) {
            inner.visited.remove(index);
        }
        inner.visited.push_back(key);
        while inner.visited.len() > cap {
            inner.visited.pop_front();
            if inner.visited.len() <= cap / 2 {
                break;
            }
        }
    }

    /// Clear visited and in-flight state, matching `manage_cache reset`.
    pub fn reset_visited(&self) -> usize {
        let mut inner = self.lock();
        let count = inner.visited.len();
        inner.visited.clear();
        inner.in_flight.clear();
        count
    }

    /// Per-domain politeness gap; the caller owns the sleep.
    ///
    /// `politeness_root` restricts throttling to one crawl host, matching the
    /// Python crawl path. Jitter is deterministic here (derived from the URL
    /// and time) so replay never depends on a random source.
    pub fn throttle_gap(&self, url: &str, politeness_root: Option<&str>, now: f64) -> f64 {
        let parsed = Url::parse(url).ok();
        let domain = parsed
            .as_ref()
            .and_then(|value| value.host_str())
            .unwrap_or("")
            .to_string();
        if let Some(root) = politeness_root
            && crawl_host_key(url) != root
        {
            return 0.0;
        }
        let delay = if self.config.respect_robots {
            self.robots_delay(url, now)
        } else {
            None
        };
        let mut inner = self.lock();
        let last = inner
            .domains
            .iter()
            .rev()
            .find(|(name, _)| name == &domain)
            .map(|(_, value)| *value)
            .unwrap_or(-1.0);
        let elapsed = if last >= 0.0 { now - last } else { f64::MAX };
        let jitter_seed = (now * 1000.0 + domain.len() as f64).fract();
        let jitter = if self.config.fetch_interval > 0.0 {
            jitter_seed * self.config.fetch_jitter.max(0.0)
        } else {
            0.0
        };
        let (_gap, sleep) = throttle_gap(elapsed, self.config.fetch_interval, jitter, delay);
        if let Some(index) = inner.domains.iter().position(|(name, _)| name == &domain) {
            inner.domains.remove(index);
        }
        inner.domains.push_back((domain, now + sleep));
        while inner.domains.len() > self.config.domain_cap {
            inner.domains.pop_front();
        }
        sleep
    }

    fn host_key(url: &str) -> String {
        Url::parse(url)
            .ok()
            .map(|parsed| {
                let host = parsed.host_str().unwrap_or("").to_ascii_lowercase();
                match parsed.port() {
                    Some(port) => format!("{}://{}:{}", parsed.scheme(), host, port),
                    None => format!("{}://{}", parsed.scheme(), host),
                }
            })
            .unwrap_or_default()
    }

    fn robots_state(&self, url: &str, now: f64) -> HostState {
        let host = Self::host_key(url);
        if host.is_empty() {
            return HostState::default();
        }
        {
            let mut inner = self.lock();
            if let Some(state) = inner.robots.get(&host)
                && (now - state.fetched_at) < state.ttl
            {
                return state.clone();
            }
            if inner.robots_in_flight.contains(&host) {
                return inner.robots.get(&host).cloned().unwrap_or_default();
            }
            inner.robots_in_flight.insert(host.clone());
        }
        let state = self.fetch_robots_state(url, now);
        let mut inner = self.lock();
        inner.robots_in_flight.remove(&host);
        inner.robots.insert(host, state.clone());
        state
    }

    fn fetch_robots_state(&self, url: &str, now: f64) -> HostState {
        let mut state = HostState {
            fetched_at: now,
            ttl: FAILURE_TTL_SECONDS,
            ..HostState::default()
        };
        let Ok(parsed) = Url::parse(url) else {
            return state;
        };
        let Some(host) = parsed.host_str() else {
            return state;
        };
        let authority = match parsed.port() {
            Some(port) => format!("{host}:{port}"),
            None => host.to_string(),
        };
        let robots_url = format!("{}://{authority}/robots.txt", parsed.scheme());
        let Ok((body, _final_url, status)) = fetch_text_with_status_and_user_agent(
            &robots_url,
            ROBOTS_TIMEOUT_SECONDS,
            &self.config.user_agent,
        ) else {
            return state;
        };
        if status == 401 || status == 403 {
            state.disallow_all = true;
            state.ttl = DEFAULT_TTL_SECONDS;
            return state;
        }
        if status >= 400 {
            if status == 404 {
                state.ttl = DEFAULT_TTL_SECONDS;
            }
            return state;
        }
        let body: String = body.chars().take(MAX_ROBOTS_BYTES).collect();
        let (rules, delay) = parse_robots(&body, &self.config.user_agent);
        state.rules = rules;
        state.crawl_delay = delay;
        state.ttl = DEFAULT_TTL_SECONDS;
        state
    }

    /// True when robots.txt permits fetching this URL.
    pub fn robots_allows(&self, url: &str, now: f64) -> bool {
        if !self.config.respect_robots {
            return true;
        }
        let state = self.robots_state(url, now);
        if state.disallow_all {
            return false;
        }
        match_rules(&state.rules, &url_path(url, None))
    }

    /// Cached `Crawl-delay` for one URL, if the host requested one.
    pub fn robots_delay(&self, url: &str, now: f64) -> Option<f64> {
        if !self.config.respect_robots {
            return None;
        }
        self.robots_state(url, now).crawl_delay
    }

    /// Current wall clock as seconds since the Unix epoch.
    pub fn now(&self) -> f64 {
        unix_now()
    }

    /// Snapshot counters for diagnostics and tests.
    pub fn stats(&self) -> Value {
        let inner = self.lock();
        json!({
            "visited_urls_count": inner.visited.len(),
            "in_flight_count": inner.in_flight.len(),
            "domain_timestamps": inner.domains.len(),
            "robots_hosts": inner.robots.len(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn runtime(cache_dir: &str) -> RuntimeState {
        let mut config = RuntimeConfig::from_cache_dir(PathBuf::from(cache_dir));
        config.fetch_interval = 0.5;
        config.fetch_jitter = 0.25;
        config.respect_robots = false;
        RuntimeState::new(config).expect("runtime must build")
    }

    #[test]
    fn visited_uses_bounded_fifo_identity() {
        let state = runtime("target/runtime-test-cache");
        assert!(state.claim("https://Example.com/a?utm_source=x"));
        assert!(!state.claim("https://example.com/a"));
        state.release("https://Example.com/a?utm_source=x");
        state.mark_visited("https://example.com/a");
        assert!(state.is_visited("https://example.com/a?utm_source=y"));
        assert_eq!(state.reset_visited(), 1);
    }

    #[test]
    fn page_cache_keys_share_canonical_identity() {
        let state = runtime("target/runtime-test-cache");
        assert_eq!(
            state.page_cache_key("https://Example.com/a?utm_source=x"),
            state.page_cache_key("https://example.com/a")
        );
    }

    #[test]
    fn throttle_updates_only_matching_crawl_host() {
        let state = runtime("target/runtime-test-cache");
        let first = state.throttle_gap("https://example.com/a", Some("example.com"), 0.0);
        assert_eq!(first, 0.0);
        let second = state.throttle_gap("https://example.com/b", Some("example.com"), 0.1);
        assert!(second > 0.0);
        let external = state.throttle_gap("https://other.test/a", Some("example.com"), 0.0);
        assert_eq!(external, 0.0);
    }
}
