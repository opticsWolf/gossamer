//! Glue from the front door to `skills-rs`: dispatch `gossamer skills …`, call `port.rs`, and
//! print what the Python CLI prints. Argument errors and help are emitted by the shared parser.

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use gossamer::cli_args::Parsed;
use serde_json::{Value, json};

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

pub fn run(parsed: &Parsed) -> ExitCode {
    match parsed.subcommand.as_deref() {
        Some("list") => list(),
        Some("show") => match parsed.positional("name") {
            Some(name) => show(name),
            None => super::unsupported(),
        },
        Some("install") => install(parsed),
        _ => super::unsupported(),
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

fn install(parsed: &Parsed) -> ExitCode {
    let destinations: Vec<String> = parsed.values("dest").to_vec();
    let names = if parsed.values("skill").is_empty() {
        None
    } else {
        Some(parsed.values("skill").to_vec())
    };
    let force = parsed.flag("force");
    let project = parsed.flag("project");
    if !destinations.is_empty() && project {
        return error("--dest and --project are mutually exclusive");
    }

    let targets: Vec<PathBuf> = if destinations.is_empty() {
        destination_paths(project)
    } else {
        destinations.iter().map(PathBuf::from).collect()
    };
    let selected: Vec<String> = names.clone().unwrap_or_else(|| vec!["gossamer".to_owned()]);

    let mut statuses: Vec<String> = Vec::new();
    for destination in &targets {
        let value = json!(destination.to_string_lossy());
        match skills_rs::port::install(value, names.clone(), force)
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
