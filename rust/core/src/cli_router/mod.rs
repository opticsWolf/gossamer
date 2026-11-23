//! CLI command routing helpers.

/// Return the canonical command name for a raw argv token.
pub fn route(token: &str) -> Option<&'static str> {
    const COMMANDS: [&str; 17] = [
        "search", "research", "categories", "inspect", "batch", "download",
        "locate-pdf", "extract", "check", "discover", "crawl", "cache", "cite",
        "skills", "benchmarks", "audit-cargo", "verify-bundled-assets",
    ];
    let lowered = token.trim().to_lowercase();
    COMMANDS.into_iter().find(|cmd| *cmd == lowered)
}
