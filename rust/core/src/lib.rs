//! Pure Rust core for gossamer.
//!
//! This crate owns business logic and must not depend on PyO3, `Python`, `Bound`, or `PyResult`.
//! Python bindings live in a thin adapter crate and only marshal arguments and results.

pub mod budget;
pub mod cacheutils;
pub mod categories;
pub mod compat;
pub mod dedupe;
pub mod robots;
pub mod ssrf;
pub mod tokens;
pub mod textlinks;
pub mod urls;
mod urlparts;
