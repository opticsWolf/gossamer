//! Glue from the front door to `skills-rs`: parse `gossamer skills …`, call `port.rs`, and print
//! what the Python CLI prints. Unhandled forms (notably `--help`) fall back to the original.

use std::env;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::ArgMatches;
use gossamer::cli_help::CLI_HELP_JSON;
use serde_json::{Value, json};

fn fallback(_args: &[OsString]) -> ExitCode {
    unsupported()
}

fn error(message: &str) -> ExitCode {
    eprintln!("gossamer: error: {message}");
    ExitCode::from(1)
}

fn home() -> PathBuf {
    PathBuf::from(
        env::var_os("USERPROFILE")
            .or_else(|| env::var_os("HOME"))
            .unwrap_or_default(),
    )
}

fn destination_paths(project: bool) -> Vec<PathBuf> {
    if project {
        let root = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        vec![
            root.join(".claude").join("skills"),
            root.join(".opencode").join("skills"),
        ]
    } else {
        let home = home();
        vec![
            home.join(".claude").join("skills"),
            home.join(".config").join("opencode").join("skills"),
        ]
    }
}

fn unsupported() -> ExitCode {
    eprintln!("gossamer: error: unsupported command shape");
    ExitCode::from(1)
}

pub fn run(command: &str, matches: &ArgMatches) -> ExitCode {
    if let Some(code) = super::cli::maybe_run(command, matches) {
        return code;
    }

    let raw: Vec<OsString> = matches
        .get_many::<OsString>("args")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    let help_count = matches.get_count("help");
    if help_count > 0 {
        let mut full: Vec<OsString> = Vec::with_capacity(raw.len() + help_count as usize + 1);
        full.push(OsString::from("skills"));
        full.extend(raw.iter().cloned());
        for _ in 0..help_count {
            full.push(OsString::from("--help"));
        }
        return unsupported();
    }

    let args: Vec<String> = raw
        .iter()
        .map(|value| value.to_string_lossy().into_owned())
        .collect();
    let Some(subcommand) = args.first().map(String::as_str) else {
        return fallback(&raw);
    };

    match subcommand {
        "list" if args.len() == 1 => list(),
        "show" if args.len() == 2 => show(&args[1]),
        "install" => install(&args[1..], &raw),
        _ => fallback(&raw),
    }
}

fn list() -> ExitCode {
    let Ok(names) = skills_rs::port::skill_names().map_err(|err| err.message().to_owned()) else {
        return error("cannot list bundled skills");
    };
    let Some(names) = names.get("$tuple").and_then(Value::as_array) else {
        return error("cannot list bundled skills");
    };
    for name in names.iter().filter_map(Value::as_str) {
        match skills_rs::port::skill_description(name.to_owned()) {
            Ok(description) => println!("{name}\n    {description}"),
            Err(err) => return error(err.message()),
        }
    }
    println!("Install with `gossamer skills install`.");
    ExitCode::SUCCESS
}

fn show(name: &str) -> ExitCode {
    match skills_rs::port::skill_text(name.to_owned()) {
        Ok(text) => {
            print!("{text}");
            ExitCode::SUCCESS
        }
        Err(err) => error(err.message()),
    }
}

struct InstallArgs {
    destinations: Vec<String>,
    names: Option<Vec<String>>,
    force: bool,
    project: bool,
}

/// Parse only the argument shapes this glue can answer exactly. Anything else goes back to
/// argparse, whose usage text and exit code are part of the CLI contract.
fn parse_install_args(args: &[String]) -> Option<InstallArgs> {
    let mut destinations = Vec::new();
    let mut names: Option<Vec<String>> = None;
    let mut force = false;
    let mut project = false;
    let mut index = 0;

    while index < args.len() {
        let argument = args[index].as_str();
        if argument == "--force" {
            force = true;
            index += 1;
            continue;
        }
        if argument == "--project" {
            project = true;
            index += 1;
            continue;
        }
        let (flag, inline) = argument
            .split_once('=')
            .map(|(flag, value)| (flag, Some(value.to_owned())))
            .unwrap_or((argument, None));
        if flag != "--dest" && flag != "--skill" {
            return None;
        }
        let value = match inline {
            Some(value) => value,
            None => {
                index += 1;
                args.get(index)?.clone()
            }
        };
        if flag == "--dest" {
            destinations.push(value);
        } else {
            let values = names.get_or_insert_with(Vec::new);
            if !values.contains(&value) {
                values.push(value);
            }
        }
        index += 1;
    }

    Some(InstallArgs {
        destinations,
        names,
        force,
        project,
    })
}

fn option_missing_value(args: &[String], flag: &str) -> bool {
    args.iter().enumerate().any(|(index, argument)| {
        argument == flag && (index + 1 >= args.len() || args[index + 1].starts_with('-'))
    })
}

fn argparse_error(args: &[String]) -> Option<ExitCode> {
    let key = if option_missing_value(args, "--dest") {
        "skills-install-missing-dest"
    } else if option_missing_value(args, "--skill") {
        "skills-install-skill-missing-value"
    } else if args.iter().any(|arg| {
        arg.starts_with('-')
            && !matches!(
                arg.split_once('=').map_or(arg.as_str(), |(name, _)| name),
                "--dest" | "--skill" | "--force" | "--project"
            )
    }) {
        "skills-install-unknown-flag"
    } else {
        return None;
    };
    let all: Value = serde_json::from_str(CLI_HELP_JSON).ok()?;
    let entry = all.get(key)?;
    let stderr = entry.get("stderr").and_then(Value::as_str).unwrap_or("");
    eprint!("{stderr}");
    let status = entry.get("status").and_then(Value::as_u64).unwrap_or(2);
    Some(ExitCode::from(status.min(255) as u8))
}

fn install(args: &[String], raw: &[OsString]) -> ExitCode {
    let Some(parsed) = parse_install_args(args) else {
        return argparse_error(args).unwrap_or_else(|| fallback(raw));
    };
    if !parsed.destinations.is_empty() && parsed.project {
        return error("--dest and --project are mutually exclusive");
    }

    let targets: Vec<PathBuf> = if parsed.destinations.is_empty() {
        destination_paths(parsed.project)
    } else {
        parsed.destinations.iter().map(PathBuf::from).collect()
    };
    let selected: Vec<String> = parsed
        .names
        .clone()
        .unwrap_or_else(|| vec!["gossamer".to_owned()]);

    let mut statuses: Vec<String> = Vec::new();
    for destination in &targets {
        let value = json!(destination.to_string_lossy());
        match skills_rs::port::install(value, parsed.names.clone(), parsed.force)
            .map_err(|err| err.message().to_owned())
        {
            Ok(results) => {
                for result in results {
                    let status = result
                        .get("$object")
                        .and_then(|_| result.get("attrs"))
                        .and_then(|attrs| attrs.get("status"))
                        .and_then(Value::as_str)
                        .unwrap_or("installed")
                        .to_owned();
                    statuses.push(status);
                }
            }
            Err(message) => return error(&message),
        }
    }

    let mut position = 0;
    for target in &targets {
        for name in &selected {
            if let Some(status) = statuses.get(position) {
                println!("{status:<9} {}", target.join(name).display());
            }
            position += 1;
        }
    }
    let skipped = statuses
        .iter()
        .filter(|status| status.as_str() == "skipped")
        .count();
    if skipped > 0 {
        println!("{skipped} skill(s) already present; use --force to overwrite.");
    }
    ExitCode::SUCCESS
}
