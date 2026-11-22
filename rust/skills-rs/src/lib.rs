//! Python bindings for `gossamer/skills/`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use port_error::{ErrorKind, PortError};
use pyo3::create_exception;
use pyo3::prelude::*;

create_exception!(skills_rs, SkillNotFound, pyo3::exceptions::PyKeyError);

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
        "SkillNotFound" => SkillNotFound::new_err(text),
        "KeyError" => e::PyKeyError::new_err(text),
        "IndexError" => e::PyIndexError::new_err(text),
        "OverflowError" => e::PyOverflowError::new_err(text),
        "ZeroDivisionError" => e::PyZeroDivisionError::new_err(text),
        "AttributeError" => e::PyAttributeError::new_err(text),
        "OSError" => e::PyOSError::new_err(text),
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
        ErrorKind::Type => "TypeError",
        ErrorKind::Attribute => "AttributeError",
        ErrorKind::Value => "ValueError",
        ErrorKind::Lookup => "SkillNotFound",
        ErrorKind::Io => "OSError",
        ErrorKind::Unsupported => "ValueError",
        ErrorKind::SkillNotFound => "SkillNotFound",
    };
    format!("{kind}: {}", error.message())
}

fn skill_names_inner() -> Result<serde_json::Value, String> {
    let out = port::skill_names().map_err(port_message)?;
    Ok(out)
}

/// `skill_names`.
#[pyfunction]
#[pyo3(name = "skill_names", signature = ())]
fn skill_names(py: Python<'_>) -> PyResult<Py<PyAny>> {
    skill_names_inner()
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn skill_text_inner(name: String) -> Result<String, String> {
    let out = port::skill_text(name).map_err(port_message)?;
    Ok(out)
}

/// `skill_text`.
#[pyfunction]
#[pyo3(name = "skill_text", signature = (name))]
fn skill_text(name: String) -> PyResult<String> {
    skill_text_inner(name).map_err(error)
}

fn skill_description_inner(name: String) -> Result<String, String> {
    let out = port::skill_description(name).map_err(port_message)?;
    Ok(out)
}

/// `skill_description`.
#[pyfunction]
#[pyo3(name = "skill_description", signature = (name))]
fn skill_description(name: String) -> PyResult<String> {
    skill_description_inner(name).map_err(error)
}

fn default_destinations_inner(project: bool) -> Result<serde_json::Value, String> {
    let out = port::default_destinations(project).map_err(port_message)?;
    Ok(out)
}

/// `default_destinations`.
#[pyfunction]
#[pyo3(name = "default_destinations", signature = (*, project=false))]
fn default_destinations(py: Python<'_>, project: bool) -> PyResult<Py<PyAny>> {
    default_destinations_inner(project)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn install_inner(
    destination: Bound<'_, PyAny>,
    names: Option<Bound<'_, PyAny>>,
    force: bool,
) -> Result<Vec<serde_json::Value>, String> {
    let destination: serde_json::Value = crate::pyconv::FromPy::from_py(&destination)?;
    let names: Option<Vec<String>> = match &names {
        Some(bound) => crate::pyconv::FromPy::from_py(bound)?,
        None => None,
    };
    let out = port::install(destination, names, force).map_err(port_message)?;
    Ok(out)
}

/// `install`.
#[pyfunction]
#[pyo3(name = "install", signature = (destination, *, names=None, force=false))]
fn install(
    py: Python<'_>,
    destination: Bound<'_, PyAny>,
    names: Option<Bound<'_, PyAny>>,
    force: bool,
) -> PyResult<Py<PyAny>> {
    install_inner(destination, names, force)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("SkillNotFound", m.py().get_type::<SkillNotFound>())?;
    m.add_function(wrap_pyfunction!(skill_names, m)?)?;
    m.add_function(wrap_pyfunction!(skill_text, m)?)?;
    m.add_function(wrap_pyfunction!(skill_description, m)?)?;
    m.add_function(wrap_pyfunction!(default_destinations, m)?)?;
    m.add_function(wrap_pyfunction!(install, m)?)?;
    Ok(())
}
