//! Glue for `gossamer categories`: static taxonomy, generated from the Python source of truth.

use std::process::ExitCode;

use gossamer::cli_args::Parsed;
use gossamer::taxonomy::CATEGORIES_JSON;

pub fn run(_parsed: &Parsed) -> ExitCode {
    println!("{CATEGORIES_JSON}");
    ExitCode::SUCCESS
}
