//! PyO3 adapter for the pure two-tier cache.

use std::path::PathBuf;

use gossamer::cache::{Cache, CacheConfig, CacheError};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::Value;

fn map_error(err: CacheError) -> PyErr {
    PyValueError::new_err(err.message().to_owned())
}

fn value_to_py(py: Python<'_>, value: Value) -> PyResult<Py<PyAny>> {
    let json = py.import("json")?;
    Ok(json.call_method1("loads", (value.to_string(),))?.unbind())
}

#[pyclass(name = "PyCache")]
pub struct PyCache {
    inner: Cache,
}

#[pymethods]
impl PyCache {
    #[new]
    #[pyo3(signature = (cache_dir, ttl_seconds=3600, max_memory_entries=100, max_disk_bytes=0))]
    fn new(
        cache_dir: &str,
        ttl_seconds: i64,
        max_memory_entries: usize,
        max_disk_bytes: u64,
    ) -> PyResult<Self> {
        let config = CacheConfig {
            cache_dir: PathBuf::from(cache_dir),
            ttl_seconds,
            max_memory_entries,
            max_disk_bytes,
        };
        Cache::new(config).map(|inner| Self { inner }).map_err(map_error)
    }

    fn get(&self, key: &str, now: f64) -> Option<String> {
        self.inner.get(key, now)
    }

    fn get_stale(&self, key: &str) -> Option<String> {
        self.inner.get_stale(key)
    }

    fn put(&self, key: &str, content: &str, now: f64) {
        self.inner.put(key, content, now);
    }

    fn clear(&self) -> PyResult<u64> {
        self.inner.clear().map_err(map_error)
    }

    fn prune(&self, py: Python<'_>, now: f64) -> PyResult<Py<PyAny>> {
        value_to_py(py, self.inner.prune(now).map_err(map_error)?)
    }

    fn stats(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        value_to_py(py, self.inner.stats().map_err(map_error)?)
    }

    fn memory_keys(&self) -> Vec<String> {
        self.inner.memory_keys()
    }

    fn memory_get(&self, key: &str) -> Option<String> {
        self.inner.peek_memory(key)
    }

    fn memory_remove(&self, key: &str) -> Option<String> {
        self.inner.take_memory(key)
    }

    fn memory_set(&self, key: &str, content: &str, timestamp: f64) {
        self.inner.set_memory_entry(key, content, timestamp);
    }

    fn memory_clear(&self) {
        self.inner.clear_memory();
    }
}
