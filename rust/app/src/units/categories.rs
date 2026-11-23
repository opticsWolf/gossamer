//! Glue for `gossamer categories`: static taxonomy, generated from the Python source of truth.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::taxonomy::CATEGORIES_JSON;

fn fallback(_args: &[OsString], _help_count: u8) -> ExitCode {
    unsupported()
}

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

pub fn run(command: &str, matches: &ArgMatches) -> ExitCode {
    if let Some(code) = super::cli::maybe_run(command, matches) {
        return code;
    }

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
