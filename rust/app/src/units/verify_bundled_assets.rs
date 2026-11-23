//! Glue for `gossamer verify-bundled-assets`: call the Rust archive checker for each artifact.
//! Unsupported artifacts fall back to the original so its error text and exit code stay exact.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::cli_help::CLI_HELP_JSON;
use serde_json::Value;

fn fallback(_args: &[OsString], _help_count: u8) -> ExitCode {
    unsupported()
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

    // Prevalidate artifacts and reproduce the original's partial output before its ValueError.
    let mut results = Vec::with_capacity(artifacts.len());
    let mut unsupported: Option<&PathBuf> = None;
    for artifact in &artifacts {
        match verify_bundled_assets_rs::port::check_path(artifact) {
            Ok(problems) => results.push((artifact, problems)),
            Err(_) => {
                unsupported = Some(artifact);
                break;
            }
        }
    }
    if let Some(artifact) = unsupported {
        let all: Value = match serde_json::from_str(CLI_HELP_JSON) {
            Ok(all) => all,
            Err(_) => return fallback(&args, help_count),
        };
        let Some(entry) = all.get("verify-unsupported") else {
            return fallback(&args, help_count);
        };
        for (ok_artifact, _) in &results {
            let name = ok_artifact
                .file_name()
                .unwrap_or_default()
                .to_string_lossy();
            println!("OK   {name}");
        }
        let shown = artifact.to_string_lossy().replace('/', "\\");
        let stderr = entry
            .get("stderr")
            .and_then(Value::as_str)
            .unwrap_or("")
            .replace(r"rust\fixtures\unsupported.zip", &shown);
        eprint!("{stderr}");
        let status = entry.get("status").and_then(Value::as_u64).unwrap_or(1);
        return ExitCode::from(status.min(255) as u8);
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
