//! Frozen CLI help output shared by every command.

use std::process::ExitCode;

use gossamer::cli_help::CLI_HELP_JSON;
use serde_json::Value;

fn fixture(key: &str) -> Option<Value> {
    let all: Value = serde_json::from_str(CLI_HELP_JSON).ok()?;
    all.get(key).cloned()
}

/// Print a frozen help or error fixture and return its exit status.
pub fn emit(key: &str) -> ExitCode {
    let Some(entry) = fixture(key) else {
        return super::unsupported();
    };
    let stdout = entry.get("stdout").and_then(Value::as_str).unwrap_or("");
    let stderr = entry.get("stderr").and_then(Value::as_str).unwrap_or("");
    print!("{stdout}");
    eprint!("{stderr}");
    let status = entry.get("status").and_then(Value::as_u64).unwrap_or(0);
    ExitCode::from(status.min(255) as u8)
}
