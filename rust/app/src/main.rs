mod cli;
mod units;

use std::process::ExitCode;

fn main() -> ExitCode {
    let matches = cli::command().get_matches();
    let (command, sub) = matches.subcommand().unwrap_or((cli::ROOT, &matches));
    units::dispatch(command, sub).unwrap_or_else(|| {
        eprintln!("{}: no handler for `{command}`", cli::ROOT);
        ExitCode::from(2)
    })
}
