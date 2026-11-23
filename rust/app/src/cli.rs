use std::ffi::OsString;

use clap::{Arg, ArgAction, Command, value_parser};

/// The command `units::dispatch` gets when argv names no subcommand.
pub const ROOT: &str = "gossamer";

fn rest() -> Arg {
    Arg::new("args")
        .num_args(0..)
        .trailing_var_arg(true)
        .allow_hyphen_values(true)
        .value_parser(value_parser!(OsString))
}

/// Routes argv to a command. Anything this parser rejects runs the original, so it never
/// has to match every way the original accepts its arguments.
pub fn command() -> Command {
    Command::new(ROOT)
        .disable_help_flag(true)
        .disable_version_flag(true)
        .disable_help_subcommand(true)
        .args_conflicts_with_subcommands(true)
        .arg(rest())
        .subcommand(
            Command::new("batch")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("benchmarks")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("cache")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("categories")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("check")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("cite")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("crawl")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("discover")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("download")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("extract")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("inspect")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("locate-pdf")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("research")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("search")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("skills")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("audit-cargo")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
        .subcommand(
            Command::new("verify-bundled-assets")
                .disable_help_flag(true)
                .arg(Arg::new("help").long("help").action(ArgAction::Count))
                .arg(rest()),
        )
}
