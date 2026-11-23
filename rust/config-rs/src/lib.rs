//! Python bindings for `gossamer/env.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use pyo3::prelude::*;
use serde_json::Value;

pub mod js;
pub mod port;
pub mod pyconv;
pub mod pynum;

#[allow(unused_imports)]
use pynum::Num;

fn error(message: String) -> PyErr {
    use pyo3::exceptions as e;
    let (kind, text) = message.split_once(": ").unwrap_or(("", message.as_str()));
    let text = text.to_string();
    match kind {
        "TypeError" => e::PyTypeError::new_err(text),
        "KeyError" => e::PyKeyError::new_err(text),
        "IndexError" => e::PyIndexError::new_err(text),
        "OverflowError" => e::PyOverflowError::new_err(text),
        "ZeroDivisionError" => e::PyZeroDivisionError::new_err(text),
        "AttributeError" => e::PyAttributeError::new_err(text),
        "RuntimeError" => e::PyRuntimeError::new_err(text),
        "NotImplementedError" => e::PyNotImplementedError::new_err(text),
        "LookupError" => e::PyLookupError::new_err(text),
        "ArithmeticError" => e::PyArithmeticError::new_err(text),
        "UnicodeError" => e::PyUnicodeError::new_err(text),
        "ValueError" => e::PyValueError::new_err(text),
        "FileNotFoundError" => e::PyFileNotFoundError::new_err(text),
        "OSError" => e::PyOSError::new_err(text),
        "PermissionError" => e::PyPermissionError::new_err(text),
        _ => e::PyValueError::new_err(message),
    }
}

fn getenv_inner(
    new: String,
    default: Option<Bound<'_, PyAny>>,
    legacy: Option<Bound<'_, PyAny>>,
) -> Result<Value, String> {
    let default: Option<Value> = match &default {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let legacy: Option<Value> = match &legacy {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::getenv(new, default, legacy)?;
    Ok(out)
}

/// `getenv`.
#[pyfunction]
#[pyo3(name = "getenv", signature = (new, default=None, legacy=None))]
fn getenv(
    py: Python<'_>,
    new: String,
    default: Option<Bound<'_, PyAny>>,
    legacy: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    getenv_inner(new, default, legacy)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn getenv_bool_inner(
    new: String,
    default: bool,
    legacy: Option<Bound<'_, PyAny>>,
) -> Result<bool, String> {
    let legacy: Option<Value> = match &legacy {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::getenv_bool(new, default, legacy)?;
    Ok(out)
}

/// `getenv_bool`.
#[pyfunction]
#[pyo3(name = "getenv_bool", signature = (new, default, legacy=None))]
fn getenv_bool(new: String, default: bool, legacy: Option<Bound<'_, PyAny>>) -> PyResult<bool> {
    getenv_bool_inner(new, default, legacy).map_err(error)
}

fn find_config_file_inner(explicit: Option<String>) -> Result<Value, String> {
    let out = port::find_config_file(explicit)?;
    Ok(out)
}

/// `find_config_file`.
#[pyfunction]
#[pyo3(name = "find_config_file", signature = (explicit=None))]
fn find_config_file(py: Python<'_>, explicit: Option<String>) -> PyResult<Py<PyAny>> {
    find_config_file_inner(explicit)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn find_keystore_file_inner(explicit: Option<String>) -> Result<Value, String> {
    let out = port::find_keystore_file(explicit)?;
    Ok(out)
}

/// `find_keystore_file`.
#[pyfunction]
#[pyo3(name = "find_keystore_file", signature = (explicit=None))]
fn find_keystore_file(py: Python<'_>, explicit: Option<String>) -> PyResult<Py<PyAny>> {
    find_keystore_file_inner(explicit)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn clear_caches_inner() -> Result<(), String> {
    port::clear_caches()?;
    Ok(())
}

/// `clear_caches`.
#[pyfunction]
#[pyo3(name = "clear_caches", signature = ())]
fn clear_caches() -> PyResult<()> {
    clear_caches_inner().map_err(error)
}

fn load_json_file_inner(path: Bound<'_, PyAny>) -> Result<Value, String> {
    let path: Value = crate::pyconv::FromPy::from_py(&path)?;
    let out = port::load_json_file(path)?;
    Ok(out)
}

/// `load_json_file`.
#[pyfunction]
#[pyo3(name = "load_json_file", signature = (path))]
fn load_json_file(py: Python<'_>, path: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    load_json_file_inner(path)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn load_config_file_inner(explicit: Option<Bound<'_, PyAny>>) -> Result<Value, String> {
    let explicit: Option<Value> = match &explicit {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::load_config_file(explicit)?;
    Ok(out)
}

/// `load_config_file`.
#[pyfunction]
#[pyo3(name = "load_config_file", signature = (explicit=None))]
fn load_config_file(py: Python<'_>, explicit: Option<Bound<'_, PyAny>>) -> PyResult<Py<PyAny>> {
    load_config_file_inner(explicit)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn load_keystore_inner(explicit: Option<Bound<'_, PyAny>>) -> Result<Value, String> {
    let explicit: Option<Value> = match &explicit {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    let out = port::load_keystore(explicit)?;
    Ok(out)
}

/// `load_keystore`.
#[pyfunction]
#[pyo3(name = "load_keystore", signature = (explicit=None))]
fn load_keystore(py: Python<'_>, explicit: Option<Bound<'_, PyAny>>) -> PyResult<Py<PyAny>> {
    load_keystore_inner(explicit)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn lookup_key_inner(names: Vec<String>, keystore: Option<String>) -> Result<Value, String> {
    let out = port::lookup_key(names, keystore)?;
    Ok(out)
}

/// `lookup_key`.
#[pyfunction]
#[pyo3(name = "lookup_key", signature = (names, keystore))]
fn lookup_key(py: Python<'_>, names: Vec<String>, keystore: Option<String>) -> PyResult<Py<PyAny>> {
    lookup_key_inner(names, keystore)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn keystore_template_inner() -> Result<Value, String> {
    let out = port::keystore_template()?;
    Ok(out)
}

/// `keystore_template`.
#[pyfunction]
#[pyo3(name = "keystore_template", signature = ())]
fn keystore_template(py: Python<'_>) -> PyResult<Py<PyAny>> {
    keystore_template_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn config_template_inner() -> Result<Value, String> {
    let out = port::config_template()?;
    Ok(out)
}

/// `config_template`.
#[pyfunction]
#[pyo3(name = "config_template", signature = ())]
fn config_template(py: Python<'_>) -> PyResult<Py<PyAny>> {
    config_template_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(getenv, m)?)?;
    m.add_function(wrap_pyfunction!(getenv_bool, m)?)?;
    m.add_function(wrap_pyfunction!(find_config_file, m)?)?;
    m.add_function(wrap_pyfunction!(find_keystore_file, m)?)?;
    m.add_function(wrap_pyfunction!(clear_caches, m)?)?;
    m.add_function(wrap_pyfunction!(load_json_file, m)?)?;
    m.add_function(wrap_pyfunction!(load_config_file, m)?)?;
    m.add_function(wrap_pyfunction!(load_keystore, m)?)?;
    m.add_function(wrap_pyfunction!(lookup_key, m)?)?;
    m.add_function(wrap_pyfunction!(keystore_template, m)?)?;
    m.add_function(wrap_pyfunction!(config_template, m)?)?;
    Ok(())
}
