use std::process::ExitCode;

mod units;

use gossamer::cli_args::Outcome;

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect();
    if let Some(first) = argv.first()
        && units::is_extra_command(first)
        && let Some(code) = units::dispatch_extra(first, &argv[1..])
    {
        return code;
    }
    match gossamer::cli_args::parse(&argv) {
        Outcome::Run(parsed) => units::dispatch(&parsed).unwrap_or_else(|| {
            eprintln!("gossamer: no handler for `{}`", parsed.command);
            ExitCode::from(2)
        }),
        Outcome::Help(key) => units::cli::emit(&key),
        Outcome::Error { stderr, status } => {
            eprint!("{stderr}");
            ExitCode::from(status)
        }
    }
}
