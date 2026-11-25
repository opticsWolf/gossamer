//! Pure Rust core for gossamer.
//!
//! This crate owns business logic and must not depend on PyO3, `Python`, `Bound`, or `PyResult`.
//! Python bindings live in a thin adapter crate and only marshal arguments and results.

pub mod adapters;
pub mod budget;
pub mod cache;
pub mod cacheutils;
pub mod categories;
pub mod cite;
pub mod cli_help;
pub mod cli_router;
pub mod compat;
pub mod config;
pub mod dedupe;
pub mod document;
pub mod discovery;
pub mod documents;
pub mod fetch;
pub mod guard;
pub mod jats;
pub mod jats_mml;
pub mod jsonfmt;
pub mod mcp;
pub mod openaccess;
pub mod mcp_dispatch;
pub mod metaextract;
pub mod miscutils;
pub mod port;
pub mod research_merge;
pub mod resourcestore;
pub mod robots;
pub mod search;
pub mod sections;
pub mod service;
pub mod stats;
pub mod providers;
pub mod sitemap;
pub mod ssrf;
pub mod tables;
pub mod taxonomy;
pub mod textlinks;
pub mod tokens;
mod urlparts;
pub mod urls;
pub mod xmlatom;
