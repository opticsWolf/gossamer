//! Written by `rust-anything app lift`: every command is answered in Rust.

use std::process::ExitCode;

use clap::ArgMatches;

mod audit_cargo;
mod batch;
mod benchmarks;
mod cache;
mod categories;
mod check;
mod cite;
mod cli;
mod crawl;
mod discover;
mod download;
mod extract;
mod inspect;
mod locate_pdf;
mod research;
mod search;
mod skills;
mod verify_bundled_assets;

pub fn dispatch(command: &str, matches: &ArgMatches) -> Option<ExitCode> {
    match command {
        "gossamer" => Some(cli::run(command, matches)),
        "research" => Some(research::run(command, matches)),
        "download" => Some(download::run(command, matches)),
        "locate-pdf" => Some(locate_pdf::run(command, matches)),
        "extract" => Some(extract::run(command, matches)),
        "check" => Some(check::run(command, matches)),
        "discover" => Some(discover::run(command, matches)),
        "crawl" => Some(crawl::run(command, matches)),
        "cite" => Some(cite::run(command, matches)),
        "search" => Some(search::run(command, matches)),
        "categories" => Some(categories::run(command, matches)),
        "inspect" => Some(inspect::run(command, matches)),
        "batch" => Some(batch::run(command, matches)),
        "cache" => Some(cache::run(command, matches)),
        "benchmarks" => Some(benchmarks::run(command, matches)),
        "skills" => Some(skills::run(command, matches)),
        "audit-cargo" => Some(audit_cargo::run(command, matches)),
        "verify-bundled-assets" => Some(verify_bundled_assets::run(command, matches)),
        _ => None,
    }
}
