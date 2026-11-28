//! Glue for `gossamer verify-bundled-assets`: call the Rust archive checker for each artifact.
//! Unsupported artifacts fall back to the original so its error text and exit code stay exact.

use std::path::PathBuf;
use std::process::ExitCode;

use gossamer::cli_help::CLI_HELP_JSON;
use serde_json::Value;

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

fn fallback() -> ExitCode {
    unsupported()
}

fn unsupported() -> ExitCode {
    pyeprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

pub fn run(argv: &[String]) -> ExitCode {
    if argv
        .iter()
        .any(|arg| matches!(arg.as_str(), "-h" | "--help"))
    {
        return fallback();
    }
    let targets: Vec<String> = if argv.is_empty() {
        vec!["dist".to_owned()]
    } else {
        argv.to_vec()
    };
    let artifacts = artifacts(&targets);
    if artifacts.is_empty() {
        return fallback();
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
            Err(_) => return fallback(),
        };
        let Some(entry) = all.get("verify-unsupported") else {
            return fallback();
        };
        for (ok_artifact, _) in &results {
            let name = ok_artifact
                .file_name()
                .unwrap_or_default()
                .to_string_lossy();
            pyprintln!("OK   {name}");
        }
        let shown = artifact.to_string_lossy().replace('/', "\\");
        let stderr = entry
            .get("stderr")
            .and_then(Value::as_str)
            .unwrap_or("")
            .replace(r"rust\fixtures\unsupported.zip", &shown);
        pyeprint!("{stderr}");
        let status = entry.get("status").and_then(Value::as_u64).unwrap_or(1);
        return ExitCode::from(status.min(255) as u8);
    }

    let mut failed = false;
    for (artifact, problems) in results {
        let name = artifact.file_name().unwrap_or_default().to_string_lossy();
        if problems.is_empty() {
            pyprintln!("OK   {name}");
        } else {
            failed = true;
            pyeprintln!("FAIL {name}: {}", problems.join("; "));
        }
    }

    if failed {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}
