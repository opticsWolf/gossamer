//! Python bindings for `scripts/audit_cargo.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use port_error::{ErrorKind, PortError};
use pyo3::prelude::*;

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
        _ => e::PyValueError::new_err(message),
    }
}

fn port_message(error: PortError) -> String {
    let kind = match error.kind() {
        ErrorKind::Value | ErrorKind::Unsupported => "ValueError",
        ErrorKind::Type => "TypeError",
        ErrorKind::Attribute => "AttributeError",
        ErrorKind::Lookup => "KeyError",
        ErrorKind::Io => "OSError",
        ErrorKind::SkillNotFound => "ValueError",
    };
    format!("{kind}: {}", error.message())
}

fn load_registry_packages_inner(lock_text: String) -> Result<Vec<serde_json::Value>, String> {
    let out = port::load_registry_packages(lock_text).map_err(port_message)?;
    Ok(out)
}

/// `load_registry_packages`.
#[pyfunction]
#[pyo3(name = "load_registry_packages", signature = (lock_text))]
fn load_registry_packages(py: Python<'_>, lock_text: String) -> PyResult<Py<PyAny>> {
    load_registry_packages_inner(lock_text)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(load_registry_packages, m)?)?;
    Ok(())
}
