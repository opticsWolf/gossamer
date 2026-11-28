use std::process::ExitCode;

/// Match Python's Windows text-stream newline translation for CLI output.
#[cfg(windows)]
fn py_newlines(text: &str) -> std::borrow::Cow<'_, str> {
    if text.contains('\n') {
        std::borrow::Cow::Owned(text.replace("\r\n", "\n").replace('\n', "\r\n"))
    } else {
        std::borrow::Cow::Borrowed(text)
    }
}

#[cfg(not(windows))]
fn py_newlines(text: &str) -> std::borrow::Cow<'_, str> {
    std::borrow::Cow::Borrowed(text)
}

macro_rules! pyprint {
    ($($arg:tt)*) => {{
        let text = format!($($arg)*);
        ::std::print!("{}", $crate::py_newlines(&text));
    }};
}

macro_rules! pyeprint {
    ($($arg:tt)*) => {{
        let text = format!($($arg)*);
        ::std::eprint!("{}", $crate::py_newlines(&text));
    }};
}

macro_rules! pyprintln {
    () => {
        pyprint!("\n")
    };
    ($($arg:tt)*) => {
        pyprint!("{}\n", format_args!($($arg)*))
    };
}

macro_rules! pyeprintln {
    () => {
        pyeprint!("\n")
    };
    ($($arg:tt)*) => {
        pyeprint!("{}\n", format_args!($($arg)*))
    };
}

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
        Outcome::Run(parsed) => {
            if parsed.command != "skills" {
                units::settings::configure_http(&parsed);
                if let Err(error) = units::settings::validate(&parsed) {
                    pyeprintln!("gossamer: error: {error}");
                    return ExitCode::FAILURE;
                }
            }
            units::dispatch(&parsed).unwrap_or_else(|| {
                pyeprintln!("gossamer: no handler for `{}`", parsed.command);
                ExitCode::from(2)
            })
        }
        Outcome::Help(key) => units::cli::emit(&key),
        Outcome::Error { stderr, status } => {
            pyeprint!("{stderr}");
            ExitCode::from(status)
        }
    }
}
