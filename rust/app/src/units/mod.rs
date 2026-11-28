//! CLI command dispatch and the shared unsupported-shape exit.

use std::process::ExitCode;

use gossamer::cli_args::Parsed;

mod audit_cargo;
mod batch;
mod benchmarks;
mod cache;
mod categories;
mod check;
mod cite;
pub mod cli;
mod crawl;
mod discover;
mod download;
mod extract;
mod guard;
mod inspect;
mod locate_pdf;
mod research;
mod search;
mod settings;
mod skills;
mod verify_bundled_assets;

/// Error for a command shape the frozen contract does not implement.
pub fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

/// Dispatch one parsed invocation.
pub fn dispatch(parsed: &Parsed) -> Option<ExitCode> {
    match parsed.command.as_str() {
        "gossamer" => Some(cli::emit("root-help")),
        "research" => Some(research::run(parsed)),
        "download" => Some(download::run(parsed)),
        "locate-pdf" => Some(locate_pdf::run(parsed)),
        "extract" => Some(extract::run(parsed)),
        "check" => Some(check::run(parsed)),
        "discover" => Some(discover::run(parsed)),
        "crawl" => Some(crawl::run(parsed)),
        "cite" => Some(cite::run(parsed)),
        "search" => Some(search::run(parsed)),
        "categories" => Some(categories::run(parsed)),
        "inspect" => Some(inspect::run(parsed)),
        "batch" => Some(batch::run(parsed)),
        "cache" => Some(cache::run(parsed)),
        "skills" => Some(skills::run(parsed)),
        _ => None,
    }
}

/// Dispatch one of the Rust-only harness commands with raw argv.
pub fn dispatch_extra(command: &str, argv: &[String]) -> Option<ExitCode> {
    match command {
        "benchmarks" => Some(benchmarks::run(argv)),
        "audit-cargo" => Some(audit_cargo::run(argv)),
        "verify-bundled-assets" => Some(verify_bundled_assets::run(argv)),
        _ => None,
    }
}

/// True when argv names a Rust-only harness command.
pub fn is_extra_command(name: &str) -> bool {
    matches!(name, "benchmarks" | "audit-cargo" | "verify-bundled-assets")
}
