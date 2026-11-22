//! Glue for `gossamer verify-bundled-assets`: call the Rust archive checker for each artifact.
//! Unsupported artifacts fall back to the original so its error text and exit code stay exact.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::ArgMatches;

fn fallback(args: &[OsString], help_count: u8) -> ExitCode {
    let mut full: Vec<OsString> = Vec::with_capacity(args.len() + help_count as usize + 1);
    full.push(OsString::from("verify-bundled-assets"));
    full.extend(args.iter().cloned());
    for _ in 0..help_count {
        full.push(OsString::from("--help"));
    }
    crate::original::run(&full)
}

fn artifacts(targets: &[String]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for target in targets {
        let path = PathBuf::from(target);
        if path.is_dir() {
            let mut names: Vec<PathBuf> = std::fs::read_dir(&path)
                .into_iter()
                .flatten()
                .flatten()
                .map(|entry| entry.path())
                .filter(|candidate| {
                    candidate.extension().and_then(|ext| ext.to_str()) == Some("whl")
                        || candidate
                            .file_name()
                            .and_then(|name| name.to_str())
                            .is_some_and(|name| name.ends_with(".tar.gz"))
                })
                .collect();
            names.sort();
            out.extend(names);
        } else {
            out.push(path);
        }
    }
    out
}

pub fn run(_command: &str, matches: &ArgMatches) -> ExitCode {
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

    let targets: Vec<String> = if args.is_empty() {
        vec!["dist".to_owned()]
    } else {
        args.iter()
            .map(|value| value.to_string_lossy().into_owned())
            .collect()
    };
    let artifacts = artifacts(&targets);
    if artifacts.is_empty() {
        return fallback(&args, help_count);
    }

    // Prevalidate every artifact before writing anything. A later unsupported path must not
    // leave Rust output behind before the fallback runs the original over the whole command.
    let mut results = Vec::with_capacity(artifacts.len());
    for artifact in &artifacts {
        match verify_bundled_assets_rs::port::check_path(artifact) {
            Ok(problems) => results.push((artifact, problems)),
            Err(_) => return fallback(&args, help_count),
        }
    }

    let mut failed = false;
    for (artifact, problems) in results {
        let name = artifact.file_name().unwrap_or_default().to_string_lossy();
        if problems.is_empty() {
            println!("OK   {name}");
        } else {
            failed = true;
            eprintln!("FAIL {name}: {}", problems.join("; "));
        }
    }

    if failed {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}
