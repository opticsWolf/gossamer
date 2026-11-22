//! Pure Rust core for gossamer.
//!
//! This crate owns business logic and must not depend on PyO3, `Python`, `Bound`, or `PyResult`.
//! Python bindings live in a thin adapter crate and only marshal arguments and results.

pub mod compat;
pub mod ssrf;
pub mod textlinks;
mod urlparts;
