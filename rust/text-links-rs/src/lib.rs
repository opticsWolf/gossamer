//! Python bindings for `gossamer/text_links.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use port_error::{ErrorKind, PortError};
use pyo3::prelude::*;

pub mod port;

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
        ErrorKind::Lookup | ErrorKind::SkillNotFound => "KeyError",
        ErrorKind::Io => "OSError",
    };
    format!("{kind}: {}", error.message())
}

fn extract_links_inner(text: String, max_links: i64) -> Result<Vec<String>, String> {
    let out = port::extract_links(text, max_links).map_err(port_message)?;
    Ok(out)
}

/// `extract_links`.
#[pyfunction]
#[pyo3(name = "extract_links", signature = (text, max_links=50))]
fn extract_links(text: String, max_links: i64) -> PyResult<Vec<String>> {
    extract_links_inner(text, max_links).map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_links, m)?)?;
    Ok(())
}
