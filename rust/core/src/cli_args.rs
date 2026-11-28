//! Python `argparse`-compatible parsing for the frozen CLI surface.
//!
//! The Python CLI is the observable contract: option order, `--flag=value`,
//! `--`, abbreviation, `nargs`, type validation, and every error string come
//! from `gossamer.cli`. This module parses the same argv shapes and returns
//! the same messages, so no command has to hand-roll its own scanner.
//!
//! Parse results are deliberately untyped strings plus switch states; every
//! value named in `cli_args.json` was already validated here, so callers read
//! `int`/`float` accessors that cannot fail.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use serde::Deserialize;

use crate::compat::py_repr;

/// Frozen parser specification generated from `gossamer.cli.build_parser()`.
pub const CLI_ARGS_JSON: &str = include_str!("cli_args.json");

#[derive(Debug, Deserialize)]
struct RawSpec {
    prog: String,
    usage: String,
    #[serde(default)]
    positionals: Vec<RawPos>,
    #[serde(default)]
    options: Vec<RawOpt>,
    #[serde(default)]
    subcommands: serde_json::Value,
    #[serde(default)]
    subcommand_dest: Option<String>,
    #[serde(default)]
    subcommand_required: bool,
}

#[derive(Debug, Deserialize)]
struct RawPos {
    dest: String,
    #[serde(default)]
    nargs: Option<serde_json::Value>,
    #[serde(default)]
    choices: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct RawOpt {
    option_strings: Vec<String>,
    dest: String,
    #[serde(default)]
    nargs: Option<serde_json::Value>,
    #[serde(default)]
    choices: Option<Vec<String>>,
    #[serde(default, rename = "type")]
    value_type: Option<String>,
    #[serde(default)]
    required: bool,
    action: String,
}

#[derive(Debug)]
struct Spec {
    prog: String,
    usage: String,
    positionals: Vec<PosSpec>,
    options: Vec<OptSpec>,
    subcommands: BTreeMap<String, Spec>,
    subcommand_order: Vec<String>,
    subcommand_dest: Option<String>,
    subcommand_required: bool,
}

#[derive(Debug)]
struct PosSpec {
    dest: String,
    nargs: Option<String>,
    choices: Option<Vec<String>>,
}

#[derive(Debug)]
struct OptSpec {
    option_strings: Vec<String>,
    dest: String,
    nargs: Option<String>,
    choices: Option<Vec<String>>,
    value_type: Option<String>,
    required: bool,
    action: String,
}

impl OptSpec {
    fn takes_value(&self) -> bool {
        matches!(self.action.as_str(), "store" | "append")
    }

    fn display(&self) -> String {
        self.option_strings.join("/")
    }
}

impl PosSpec {
    fn exact_one(&self) -> bool {
        self.nargs.is_none()
    }

    fn one_or_more(&self) -> bool {
        self.nargs.as_deref() == Some("+")
    }
}

impl Spec {
    fn from_raw(raw: RawSpec) -> Self {
        let mut subcommands: BTreeMap<String, Spec> = BTreeMap::new();
        let mut subcommand_order: Vec<String> = Vec::new();
        if let serde_json::Value::Object(entries) = raw.subcommands {
            for (name, value) in entries {
                let child: RawSpec = serde_json::from_value(value)
                    .unwrap_or_else(|error| panic!("invalid frozen CLI spec for {name}: {error}"));
                subcommand_order.push(name.clone());
                subcommands.insert(name, Spec::from_raw(child));
            }
        }
        Spec {
            prog: raw.prog,
            usage: raw.usage,
            positionals: raw
                .positionals
                .into_iter()
                .map(|pos| PosSpec {
                    dest: pos.dest,
                    nargs: nargs_name(pos.nargs),
                    choices: pos.choices,
                })
                .collect(),
            options: raw
                .options
                .into_iter()
                .map(|opt| OptSpec {
                    option_strings: opt.option_strings,
                    dest: opt.dest,
                    nargs: nargs_name(opt.nargs),
                    choices: opt.choices,
                    value_type: opt.value_type,
                    required: opt.required,
                    action: opt.action,
                })
                .collect(),
            subcommand_order,
            subcommands,
            subcommand_dest: raw.subcommand_dest,
            subcommand_required: raw.subcommand_required,
        }
    }

    fn long_options(&self) -> Vec<(&str, usize)> {
        let mut out = Vec::new();
        for (index, opt) in self.options.iter().enumerate() {
            for name in &opt.option_strings {
                if name.starts_with("--") {
                    out.push((name.as_str(), index));
                }
            }
        }
        out
    }

    fn short_option(&self, name: &str) -> Option<usize> {
        self.options.iter().position(|opt| {
            opt.option_strings
                .iter()
                .any(|opt| !opt.starts_with("--") && opt == name)
        })
    }
}

static ROOT: LazyLock<Spec> = LazyLock::new(|| {
    #[derive(Deserialize)]
    struct Root {
        root: RawSpec,
    }
    let raw: Root = serde_json::from_str(CLI_ARGS_JSON)
        .unwrap_or_else(|error| panic!("invalid frozen CLI spec: {error}"));
    Spec::from_raw(raw.root)
});

/// Parsed form of one invocation.
#[derive(Debug, Clone, Default)]
pub struct Parsed {
    /// Command name (`gossamer` only for the front door itself).
    pub command: String,
    /// Nested subcommand, for `gossamer skills …`.
    pub subcommand: Option<String>,
    values: BTreeMap<String, Vec<String>>,
    switches: BTreeMap<String, bool>,
    positionals: BTreeMap<String, Vec<String>>,
}

impl Parsed {
    /// First value for an option, if it was given.
    pub fn value(&self, dest: &str) -> Option<&str> {
        self.values
            .get(dest)
            .and_then(|values| values.first().map(String::as_str))
    }

    /// Every value for a repeatable option or a `nargs` option.
    pub fn values(&self, dest: &str) -> &[String] {
        self.values.get(dest).map(Vec::as_slice).unwrap_or(&[])
    }

    /// Last `store_true`/`store_false` state for a switch.
    pub fn switch(&self, dest: &str) -> Option<bool> {
        self.switches.get(dest).copied()
    }

    /// Switch state, defaulting to `false`.
    pub fn flag(&self, dest: &str) -> bool {
        self.switch(dest).unwrap_or(false)
    }

    /// First positional value.
    pub fn positional(&self, dest: &str) -> Option<&str> {
        self.positionals
            .get(dest)
            .and_then(|values| values.first().map(String::as_str))
    }

    /// Every positional value for a `nargs` positional.
    pub fn positionals(&self, dest: &str) -> &[String] {
        self.positionals.get(dest).map(Vec::as_slice).unwrap_or(&[])
    }

    /// Parsed integer value; validated during parsing.
    pub fn int(&self, dest: &str) -> Option<i64> {
        self.value(dest).and_then(py_int)
    }

    /// Parsed float value; validated during parsing.
    pub fn float(&self, dest: &str) -> Option<f64> {
        self.value(dest).and_then(py_float)
    }

    /// Effective cache directory flag (root or subparser).
    pub fn cache_dir(&self) -> Option<&str> {
        self.value("cache_dir")
    }

    /// Effective config path flag (root or subparser).
    pub fn config(&self) -> Option<&str> {
        self.value("config")
    }

    /// Effective keystore path flag (root or subparser).
    pub fn keystore(&self) -> Option<&str> {
        self.value("keystore")
    }

    /// Effective log level flag (root or subparser).
    pub fn log_level(&self) -> Option<&str> {
        self.value("log_level")
    }

    /// Effective render mode flag (root or subparser).
    pub fn fetch_mode(&self) -> Option<&str> {
        self.value("fetch_mode")
    }
}

/// Result of parsing one argv vector.
#[derive(Debug)]
pub enum Outcome {
    /// Dispatch to `units::dispatch`.
    Run(Box<Parsed>),
    /// Print the frozen help fixture with this key.
    Help(String),
    /// Print an argparse-compatible error and exit with `status`.
    Error { stderr: String, status: u8 },
}

#[derive(Default)]
struct Level {
    values: BTreeMap<String, Vec<String>>,
    switches: BTreeMap<String, bool>,
    positionals: BTreeMap<String, Vec<String>>,
    extras: Vec<String>,
}

enum Fail {
    Error { prog: String, message: String },
    Help(String),
}

/// Parse `argv` into a dispatchable invocation or the exact CLI output.
pub fn parse(argv: &[String]) -> Outcome {
    let mut level = Level::default();
    let mut spec: &Spec = &ROOT;
    let mut tokens: Vec<String> = argv.to_vec();
    let mut path = String::from("gossamer");
    let mut command: Option<String> = None;
    let mut subcommand: Option<String> = None;
    loop {
        let help_key = if path == "gossamer" {
            "root-help".to_string()
        } else {
            format!("{path}-help")
        };
        match parse_level(spec, &tokens, &mut level, &help_key) {
            Ok(None) => break,
            Ok(Some((name, rest))) => {
                let Some(next) = spec.subcommands.get(&name) else {
                    return error_named(
                        &spec.prog,
                        format!(
                            "argument {}: invalid choice: {}",
                            spec.subcommand_dest.clone().unwrap_or_default(),
                            choice_tail(&name, spec.subcommand_order.iter())
                        ),
                    );
                };
                if command.is_none() {
                    command = Some(name.clone());
                } else {
                    subcommand = Some(name.clone());
                }
                path = if path == "gossamer" {
                    name
                } else {
                    format!("{path}-{name}")
                };
                spec = next;
                tokens = rest;
            }
            Err(Fail::Help(key)) => return Outcome::Help(key),
            Err(Fail::Error { prog, message }) => return error_named(&prog, message),
        }
    }
    Outcome::Run(Box::new(Parsed {
        command: command.unwrap_or_else(|| "gossamer".to_string()),
        subcommand,
        values: level.values,
        switches: level.switches,
        positionals: level.positionals,
    }))
}

fn nargs_name(value: Option<serde_json::Value>) -> Option<String> {
    match value {
        Some(serde_json::Value::String(name)) => Some(name),
        _ => None,
    }
}

fn parse_level(
    spec: &Spec,
    tokens: &[String],
    level: &mut Level,
    help_key: &str,
) -> Result<Option<(String, Vec<String>)>, Fail> {
    let mut index = 0usize;
    let mut positional_index = 0usize;
    let mut after_terminator = false;
    while index < tokens.len() {
        let token = tokens[index].as_str();
        if !after_terminator && token == "--" {
            after_terminator = true;
            index += 1;
            continue;
        }
        let option_like = !after_terminator
            && token != "-"
            && token.starts_with('-')
            && !is_negative_number(token);
        if option_like {
            match resolve_option(spec, token) {
                Ok(Some((opt_index, explicit))) => {
                    let opt = &spec.options[opt_index];
                    if opt.action == "help" {
                        if let Some(value) = explicit {
                            return Err(failure(
                                spec,
                                format!(
                                    "argument {}: ignored explicit argument {}",
                                    opt.display(),
                                    py_repr(&value)
                                ),
                            ));
                        }
                        return Err(Fail::Help(help_key.to_string()));
                    }
                    if !opt.takes_value() {
                        if let Some(value) = explicit {
                            return Err(failure(
                                spec,
                                format!(
                                    "argument {}: ignored explicit argument {}",
                                    opt.display(),
                                    py_repr(&value)
                                ),
                            ));
                        }
                        let state = opt.action != "store_false";
                        level.switches.insert(opt.dest.clone(), state);
                        index += 1;
                        continue;
                    }
                    let mut collected: Vec<String> = Vec::new();
                    if let Some(value) = explicit {
                        collected.push(value);
                        index += 1;
                    } else {
                        let mut cursor = index + 1;
                        let want_one = opt.nargs.is_none();
                        while cursor < tokens.len() {
                            let candidate = tokens[cursor].as_str();
                            if candidate == "--" {
                                break;
                            }
                            if candidate.starts_with('-')
                                && candidate != "-"
                                && !is_negative_number(candidate)
                            {
                                break;
                            }
                            collected.push(candidate.to_string());
                            cursor += 1;
                            if want_one {
                                break;
                            }
                        }
                        index = cursor;
                    }
                    if collected.is_empty() {
                        if opt.nargs.as_deref() == Some("*") {
                            level.values.insert(opt.dest.clone(), collected);
                            continue;
                        }
                        let message = if opt.nargs.as_deref() == Some("+") {
                            "expected at least one argument"
                        } else {
                            "expected one argument"
                        };
                        return Err(failure(
                            spec,
                            format!("argument {}: {message}", opt.display()),
                        ));
                    }
                    for value in &collected {
                        validate_value(spec, opt, value, token)?;
                    }
                    if opt.action == "append" {
                        level
                            .values
                            .entry(opt.dest.clone())
                            .or_default()
                            .extend(collected);
                    } else {
                        level.values.insert(opt.dest.clone(), collected);
                    }
                    continue;
                }
                Ok(None) => {
                    level.extras.push(token.to_string());
                    index += 1;
                    continue;
                }
                Err(message) => return Err(failure(spec, message)),
            }
        }
        if spec.subcommands.is_empty() {
            if positional_index < spec.positionals.len() {
                let pos = &spec.positionals[positional_index];
                if let Some(choices) = &pos.choices
                    && !choices.iter().any(|choice| choice == token)
                {
                    return Err(failure(
                        spec,
                        format!(
                            "argument {}: invalid choice: {}",
                            pos.dest,
                            choice_tail(token, choices.iter())
                        ),
                    ));
                }
                level
                    .positionals
                    .entry(pos.dest.clone())
                    .or_default()
                    .push(token.to_string());
                if pos.exact_one() {
                    positional_index += 1;
                }
            } else {
                level.extras.push(token.to_string());
            }
            index += 1;
            continue;
        }
        let dest = spec.subcommand_dest.clone().unwrap_or_default();
        if !spec.subcommands.contains_key(token) {
            return Err(failure(
                spec,
                format!(
                    "argument {dest}: invalid choice: {}",
                    choice_tail(token, spec.subcommand_order.iter())
                ),
            ));
        }
        return Ok(Some((token.to_string(), tokens[index + 1..].to_vec())));
    }

    let mut missing: Vec<String> = Vec::new();
    if spec.subcommand_required
        && let Some(dest) = &spec.subcommand_dest
    {
        missing.push(dest.clone());
    }
    for pos in &spec.positionals {
        let count = level.positionals.get(&pos.dest).map_or(0, Vec::len);
        if (pos.one_or_more() || pos.exact_one()) && count == 0 {
            missing.push(pos.dest.clone());
        }
    }
    for opt in &spec.options {
        if opt.required && !level.values.contains_key(&opt.dest) {
            missing.push(opt.display());
        }
    }
    if !missing.is_empty() {
        return Err(failure(
            spec,
            format!(
                "the following arguments are required: {}",
                missing.join(", ")
            ),
        ));
    }
    if !level.extras.is_empty() {
        return Err(Fail::Error {
            prog: "gossamer".to_string(),
            message: format!("unrecognized arguments: {}", level.extras.join(" ")),
        });
    }
    Ok(None)
}

fn validate_value(spec: &Spec, opt: &OptSpec, value: &str, typed: &str) -> Result<(), Fail> {
    if let Some(choices) = &opt.choices
        && !choices.iter().any(|choice| choice == value)
    {
        return Err(failure(
            spec,
            format!(
                "argument {typed}: invalid choice: {}",
                choice_tail(value, choices.iter())
            ),
        ));
    }
    match opt.value_type.as_deref() {
        Some("int") if py_int(value).is_none() => Err(failure(
            spec,
            format!("argument {typed}: invalid int value: {}", py_repr(value)),
        )),
        Some("float") if py_float(value).is_none() => Err(failure(
            spec,
            format!("argument {typed}: invalid float value: {}", py_repr(value)),
        )),
        _ => Ok(()),
    }
}

fn resolve_option(spec: &Spec, token: &str) -> Result<Option<(usize, Option<String>)>, String> {
    if let Some(body) = token.strip_prefix("--") {
        let (name, explicit) = match body.split_once('=') {
            Some((name, value)) => (format!("--{name}"), Some(value.to_string())),
            None => (token.to_string(), None),
        };
        if let Some(index) = spec
            .options
            .iter()
            .position(|opt| opt.option_strings.iter().any(|option| option == &name))
        {
            return Ok(Some((index, explicit)));
        }
        let matches: Vec<(&str, usize)> = spec
            .long_options()
            .into_iter()
            .filter(|(option, _)| option.starts_with(&name))
            .collect();
        return match matches.as_slice() {
            [] => Ok(None),
            [(_, index)] => Ok(Some((*index, explicit))),
            many => {
                let names: Vec<&str> = many.iter().map(|(option, _)| *option).collect();
                Err(format!(
                    "ambiguous option: {name} could match {}",
                    names.join(", ")
                ))
            }
        };
    }
    if let Some(index) = spec.short_option(token) {
        return Ok(Some((index, None)));
    }
    if token.len() > 2 {
        let prefix = &token[..2];
        if let Some(index) = spec.short_option(prefix) {
            let mut explicit = token[2..].to_string();
            if let Some(rest) = explicit.strip_prefix('=') {
                explicit = rest.to_string();
            }
            return Ok(Some((index, Some(explicit))));
        }
    }
    Ok(None)
}

fn choice_tail<'a>(value: &str, choices: impl Iterator<Item = &'a String>) -> String {
    let choices: Vec<String> = choices.map(|choice| py_repr(choice)).collect();
    format!("{} (choose from {})", py_repr(value), choices.join(", "))
}

fn failure(spec: &Spec, message: impl Into<String>) -> Fail {
    Fail::Error {
        prog: spec.prog.clone(),
        message: message.into(),
    }
}

fn error_named(prog: &str, message: String) -> Outcome {
    let Some(spec) = find_spec(prog) else {
        return Outcome::Error {
            stderr: format!("{prog}: error: {message}\n"),
            status: 2,
        };
    };
    let header = format!("{}{}", spec.usage, spec.prog);
    Outcome::Error {
        stderr: format!("{header}: error: {message}\n"),
        status: 2,
    }
}

fn find_spec(prog: &str) -> Option<&'static Spec> {
    if prog == ROOT.prog {
        return Some(&ROOT);
    }
    let path: Vec<&str> = prog.split_whitespace().collect();
    if path.len() < 2 {
        return None;
    }
    let mut spec: &Spec = ROOT.subcommands.get(path[1])?;
    for part in &path[2..] {
        spec = spec.subcommands.get(*part)?;
    }
    Some(spec)
}

/// Python's negative-number heuristic: such tokens are values, not options.
fn is_negative_number(token: &str) -> bool {
    let Some(rest) = token.strip_prefix('-') else {
        return false;
    };
    if rest.is_empty() {
        return false;
    }
    let mut dots = 0;
    let mut digits = 0;
    for ch in rest.chars() {
        if ch == '.' {
            dots += 1;
            if dots > 1 {
                return false;
            }
        } else if ch.is_ascii_digit() {
            digits += 1;
        } else {
            return false;
        }
    }
    digits > 0
}

/// Python `int()` subset used by argparse `type=int`.
pub fn py_int(text: &str) -> Option<i64> {
    let cleaned = text.trim().replace('_', "");
    if let Ok(value) = cleaned.parse::<i64>() {
        return Some(value);
    }
    // Python integers are unbounded; saturate instead of rejecting.
    cleaned.parse::<i128>().ok().map(|value| {
        if value > i128::from(i64::MAX) {
            i64::MAX
        } else if value < i128::from(i64::MIN) {
            i64::MIN
        } else {
            value as i64
        }
    })
}

/// Python `float()` subset used by argparse `type=float`.
pub fn py_float(text: &str) -> Option<f64> {
    let cleaned = text.trim().replace('_', "");
    if let Ok(value) = cleaned.parse::<f64>() {
        return Some(value);
    }
    match cleaned.to_ascii_lowercase().as_str() {
        "infinity" | "+infinity" => Some(f64::INFINITY),
        "-infinity" => Some(f64::NEG_INFINITY),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    fn text(outcome: Outcome) -> String {
        match outcome {
            Outcome::Error { stderr, .. } => stderr,
            Outcome::Help(key) => format!("HELP:{key}"),
            Outcome::Run(_) => "RUN".to_string(),
        }
    }

    fn run(items: &[&str]) -> Parsed {
        match parse(&args(items)) {
            Outcome::Run(parsed) => *parsed,
            other => panic!("expected run, got {}", text(other)),
        }
    }

    #[test]
    fn missing_command_matches_argparse() {
        let output = text(parse(&args(&[])));
        assert!(output.contains("the following arguments are required: command"));
        assert!(output.contains("usage: gossamer [-h]"));
    }

    #[test]
    fn globals_before_command_apply() {
        let parsed = run(&["--cache-dir", "tmp", "--fetch-mode=static", "categories"]);
        assert_eq!(parsed.cache_dir(), Some("tmp"));
        assert_eq!(parsed.fetch_mode(), Some("static"));
    }

    #[test]
    fn equals_and_short_forms() {
        let parsed = run(&[
            "download",
            "src",
            "-o=out.bin",
            "--max-bytes=10",
            "--overwrite",
        ]);
        assert_eq!(parsed.value("output_path"), Some("out.bin"));
        assert_eq!(parsed.int("max_bytes"), Some(10));
        assert!(parsed.flag("overwrite"));
    }

    #[test]
    fn flags_may_precede_positionals() {
        let parsed = run(&["search", "--max-results", "2", "q"]);
        assert_eq!(parsed.positional("query"), Some("q"));
        assert_eq!(parsed.int("max_results"), Some(2));
    }

    #[test]
    fn invalid_int_is_rejected() {
        let output = text(parse(&args(&["search", "q", "--max-results", "abc"])));
        assert!(output.contains("argument --max-results: invalid int value: 'abc'"));
        assert!(output.contains("usage: gossamer search"));
    }

    #[test]
    fn unknown_option_is_unrecognized() {
        let output = text(parse(&args(&["check", "--bogus", "https://example.com"])));
        assert!(output.ends_with("gossamer: error: unrecognized arguments: --bogus\n"));
    }

    #[test]
    fn ambiguous_option_reports_candidates() {
        let output = text(parse(&args(&["--c", "categories"])));
        assert!(output.contains("ambiguous option: --c could match --cache-dir, --config"));
    }

    #[test]
    fn terminator_makes_flags_positional() {
        let parsed = run(&["check", "--", "-x"]);
        assert_eq!(parsed.positionals("urls"), ["-x".to_string()]);
    }

    #[test]
    fn nargs_plus_needs_one_value() {
        let output = text(parse(&args(&["research", "q", "--providers"])));
        assert!(output.contains("argument --providers: expected at least one argument"));
    }

    #[test]
    fn help_key_tracks_nested_path() {
        assert_eq!(text(parse(&args(&["-h"]))), "HELP:root-help");
        assert_eq!(text(parse(&args(&["check", "-h"]))), "HELP:check-help");
        assert_eq!(
            text(parse(&args(&["skills", "install", "--help"]))),
            "HELP:skills-install-help"
        );
    }

    #[test]
    fn store_false_overrides_store_true_in_order() {
        let parsed = run(&["extract", "--prefer-jats", "--no-prefer-jats", "x"]);
        assert_eq!(parsed.switch("prefer_jats"), Some(false));
    }

    #[test]
    fn skills_subcommand_is_recorded() {
        let parsed = run(&[
            "skills", "install", "--dest", "a", "--dest", "b", "--skill", "x",
        ]);
        assert_eq!(parsed.command, "skills");
        assert_eq!(parsed.subcommand.as_deref(), Some("install"));
        assert_eq!(parsed.values("dest"), ["a".to_string(), "b".to_string()]);
        assert_eq!(parsed.values("skill"), ["x".to_string()]);
    }

    #[test]
    fn python_int_and_float_subset() {
        assert_eq!(py_int("1_0"), Some(10));
        assert_eq!(py_int(" +5 "), Some(5));
        assert!(py_float("inf").is_some());
        assert!(py_float("nope").is_none());
    }
}
