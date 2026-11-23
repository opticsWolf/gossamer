//! Frozen CLI help and argument-error output.
//!
//! The JSON fixture is generated from `python -m gossamer.cli` with UTF-8 output. Help text and
//! argparse error wording are external contracts; this module only stores them.

/// Map of case name to `{stdout, stderr, status}`.
pub const CLI_HELP_JSON: &str = include_str!("cli_help.json");

/// Command names in argparse order.
pub const COMMANDS: [&str; 17] = [
    "search",
    "research",
    "categories",
    "inspect",
    "batch",
    "download",
    "locate-pdf",
    "extract",
    "check",
    "discover",
    "crawl",
    "cache",
    "cite",
    "skills",
    "benchmarks",
    "audit-cargo",
    "verify-bundled-assets",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixture_contains_contract_cases() {
        let parsed: serde_json::Value = serde_json::from_str(CLI_HELP_JSON)
            .unwrap_or_else(|e| panic!("CLI help fixture must be JSON: {e}"));
        for key in [
            "no-args",
            "root-help",
            "bad-command",
            "benchmarks-help",
            "benchmarks-unknown",
            "skills-install-missing-dest",
            "skills-install-skill-missing-value",
            "skills-install-unknown-flag",
            "verify-unsupported",
        ] {
            assert!(parsed.get(key).is_some(), "missing CLI fixture {key}");
        }
    }
}
