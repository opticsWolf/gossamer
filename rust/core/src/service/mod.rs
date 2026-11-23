//! Pure service kernels shared by the CLI, MCP tools and Python library.
//!
//! Network transport and browser processes stay outside the core; these
//! modules own the deterministic decisions that surround them.

pub mod fetch;
pub mod search;
