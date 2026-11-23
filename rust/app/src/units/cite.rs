//! Help-only glue for `cite`; other invocations are outside the recorded contract.

use clap::ArgMatches;
use std::process::ExitCode;

pub fn run(command: &str, matches: &ArgMatches) -> ExitCode {
    if let Some(code) = super::cli::maybe_run(command, matches) {
        return code;
    }
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}
