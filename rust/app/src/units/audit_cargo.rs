//! Glue for `gossamer audit-cargo`. The Rust port covers Cargo.lock parsing; the OSV query still
//! runs in the original script, so any non-empty lock falls back.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;

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
    if help_count > 0 {
        return fallback(&args, help_count);
    }

    let strings: Vec<String> = args
        .iter()
        .map(|value| value.to_string_lossy().into_owned())
        .collect();
    if strings.len() > 1 {
        return fallback(&args, help_count);
    }
    let lock_path = strings.first().map(String::as_str).unwrap_or("Cargo.lock");

    let Ok(lock_text) = std::fs::read_to_string(lock_path) else {
        return fallback(&args, help_count);
    };
    let Ok(packages) = audit_cargo_rs::port::load_registry_packages(lock_text) else {
        return fallback(&args, help_count);
    };

    if packages.is_empty() {
        println!("no registry packages found in {lock_path}");
        return ExitCode::SUCCESS;
    }

    // OSV lookup is not ported yet; let the original script do the network part.
    fallback(&args, help_count)
}
