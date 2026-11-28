//! Glue for `gossamer audit-cargo`. The Rust port covers Cargo.lock parsing; the OSV query still
//! runs in the original script, so any non-empty lock falls back.

use std::process::ExitCode;

pub fn run(argv: &[String]) -> ExitCode {
    if argv
        .iter()
        .any(|arg| matches!(arg.as_str(), "-h" | "--help"))
    {
        return super::unsupported();
    }
    if argv.len() > 1 || argv.iter().any(|arg| arg.starts_with('-')) {
        return super::unsupported();
    }
    let lock_path = argv.first().map(String::as_str).unwrap_or("Cargo.lock");

    let Ok(lock_text) = std::fs::read_to_string(lock_path) else {
        return super::unsupported();
    };
    let Ok(packages) = audit_cargo_rs::port::load_registry_packages(lock_text) else {
        return super::unsupported();
    };

    if packages.is_empty() {
        println!("no registry packages found in {lock_path}");
        return ExitCode::SUCCESS;
    }

    // OSV lookup is not ported yet; let the original script do the network part.
    super::unsupported()
}
