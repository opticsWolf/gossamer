//! Legacy adapter for the pure XML kernel.
//!
//! Adapter modules use `crate::xmlatom::{Node, parse_document}`; both now come from the pure
//! `gossamer` core (ADR-012). This file only preserves the legacy `Result<_, String>` signature.

pub use gossamer::xmlatom::Node;

pub fn parse_document(xml: &str) -> Result<Node, String> {
    gossamer::xmlatom::parse_document(xml).map_err(|e| e.message().to_owned())
}
