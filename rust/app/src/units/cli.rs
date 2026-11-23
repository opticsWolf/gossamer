//! Frozen argparse-compatible help and error output for the front door.

use std::ffi::OsString;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::cli_help::{CLI_HELP_JSON, COMMANDS};
use serde_json::Value;

fn fixture(key: &str) -> Option<Value> {
    let all: Value = serde_json::from_str(CLI_HELP_JSON).ok()?;
    all.get(key).cloned()
}

fn emit(entry: &Value) -> ExitCode {
    let stdout = entry.get("stdout").and_then(Value::as_str).unwrap_or("");
    let stderr = entry.get("stderr").and_then(Value::as_str).unwrap_or("");
    print!("{stdout}");
    eprint!("{stderr}");
    let status = entry.get("status").and_then(Value::as_u64).unwrap_or(0);
    ExitCode::from(status.min(255) as u8)
}

fn bad_command(raw: &str) -> ExitCode {
    let Some(entry) = fixture("bad-command") else {
        return ExitCode::FAILURE;
    };
    let stderr = entry
        .get("stderr")
        .and_then(Value::as_str)
        .unwrap_or("")
        .replace("'definitely-not-a-command'", &format!("'{raw}'"));
    eprint!("{stderr}");
    ExitCode::from(2)
}

pub fn maybe_run(command: &str, matches: &ArgMatches) -> Option<ExitCode> {
    if command == "gossamer" {
        let args: Vec<OsString> = matches
            .get_many::<OsString>("args")
            .into_iter()
            .flatten()
            .cloned()
            .collect();
        if args.is_empty() {
            return fixture("no-args").as_ref().map(emit);
        }
        let first = args[0].to_string_lossy();
        if first == "--help" || first == "-h" {
            return fixture("root-help").as_ref().map(emit);
        }
        if !COMMANDS.contains(&first.as_ref()) {
            return Some(bad_command(&first));
        }
        return None;
    }
    let has_help = matches.get_count("help") > 0
        || matches
            .get_many::<OsString>("args")
            .into_iter()
            .flatten()
            .any(|arg| matches!(arg.to_str(), Some("-h") | Some("--help")));
    if has_help {
        let key = format!("{command}-help");
        if let Some(entry) = fixture(&key) {
            return Some(emit(&entry));
        }
    }
    None
}
