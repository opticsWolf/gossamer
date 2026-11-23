//! Glue for `gossamer categories`: static taxonomy, generated from the Python source of truth.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::taxonomy::CATEGORIES_JSON;

fn fallback(args: &[OsString], help_count: u8) -> ExitCode {
    let mut full: Vec<OsString> = Vec::with_capacity(args.len() + help_count as usize + 1);
    full.push(OsString::from("categories"));
    full.extend(args.iter().cloned());
    for _ in 0..help_count {
        full.push(OsString::from("--help"));
    }
    crate::original::run(&full)
}

pub fn run(_command: &str, matches: &ArgMatches) -> ExitCode {
    let args: Vec<OsString> = matches
        .get_many::<OsString>("args")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    let help_count = matches.get_count("help");
    if help_count > 0 || !args.is_empty() {
        return fallback(&args, help_count);
    }
    println!("{CATEGORIES_JSON}");
    ExitCode::SUCCESS
}
