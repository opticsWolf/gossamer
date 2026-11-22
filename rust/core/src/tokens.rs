//! Token counting and truncation using `tiktoken-rs`.
//!
//! The BPE encoders are cached in a `OnceLock` because building them is expensive and the value is
//! immutable. This is a performance-only static (`M-AVOID-STATICS`), not shared mutable state.

use std::backtrace::Backtrace;
use std::collections::HashMap;
use std::error::Error;
use std::fmt;
use std::sync::OnceLock;

use tiktoken_rs::tokenizer::{get_tokenizer, Tokenizer};
use tiktoken_rs::CoreBPE;

/// Canonical token-budget error (`M-ERRORS-CANONICAL-STRUCTS`).
#[derive(Debug)]
pub struct TokenError {
    message: String,
    backtrace: Backtrace,
}

impl TokenError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            backtrace: Backtrace::capture(),
        }
    }

    /// Returns the Python-compatible error message.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the captured backtrace for diagnostics.
    pub fn backtrace(&self) -> &Backtrace {
        &self.backtrace
    }
}

impl fmt::Display for TokenError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for TokenError {}

const MODEL_ENCODING: &[(&str, &str)] = &[
    ("gpt-4", "cl100k_base"),
    ("gpt-4-0314", "cl100k_base"),
    ("gpt-4-0613", "cl100k_base"),
    ("gpt-4-32k", "cl100k_base"),
    ("gpt-4-32k-0314", "cl100k_base"),
    ("gpt-4-32k-0613", "cl100k_base"),
    ("gpt-4-0125-preview", "cl100k_base"),
    ("gpt-4-1106-preview", "cl100k_base"),
    ("gpt-4-turbo", "cl100k_base"),
    ("gpt-4-turbo-2024-04-09", "cl100k_base"),
    ("gpt-4o", "o200k_base"),
    ("gpt-4o-2024-05-13", "o200k_base"),
    ("gpt-4o-mini", "o200k_base"),
    ("gpt-4o-mini-2024-07-18", "o200k_base"),
    ("gpt-3.5-turbo", "cl100k_base"),
    ("gpt-3.5-turbo-0301", "p50k_base"),
    ("gpt-3.5-turbo-0613", "cl100k_base"),
    ("gpt-3.5-turbo-16k", "cl100k_base"),
    ("claude-3-opus", "cl100k_base"),
    ("claude-3-sonnet", "cl100k_base"),
    ("claude-3-haiku", "cl100k_base"),
    ("claude-3.5-sonnet", "cl100k_base"),
    ("claude-3.5-haiku", "cl100k_base"),
];

const DEFAULT_ENCODING: &str = "cl100k_base";
const DEFAULT_ELLIPSIS: &str = "\n\n... [truncated for token budget]";

/// Returns the default truncation ellipsis used by the Python-compatible API.
pub fn default_ellipsis() -> &'static str {
    DEFAULT_ELLIPSIS
}

fn tokenizer_name(t: Tokenizer) -> &'static str {
    match t {
        Tokenizer::O200kHarmony => "o200k_harmony",
        Tokenizer::O200kBase => "o200k_base",
        Tokenizer::Cl100kBase => "cl100k_base",
        Tokenizer::P50kBase => "p50k_base",
        Tokenizer::R50kBase => "r50k_base",
        Tokenizer::P50kEdit => "p50k_edit",
        Tokenizer::Gpt2 => "gpt2",
    }
}

fn encoders() -> &'static HashMap<&'static str, CoreBPE> {
    static CELL: OnceLock<HashMap<&'static str, CoreBPE>> = OnceLock::new();
    CELL.get_or_init(|| {
        let mut m = HashMap::new();
        m.insert(
            "cl100k_base",
            tiktoken_rs::cl100k_base()
                .unwrap_or_else(|e| panic!("failed to load cl100k_base ranks: {e}")),
        );
        m.insert(
            "p50k_base",
            tiktoken_rs::p50k_base()
                .unwrap_or_else(|e| panic!("failed to load p50k_base ranks: {e}")),
        );
        m.insert(
            "r50k_base",
            tiktoken_rs::r50k_base()
                .unwrap_or_else(|e| panic!("failed to load r50k_base ranks: {e}")),
        );
        m.insert(
            "p50k_edit",
            tiktoken_rs::p50k_edit()
                .unwrap_or_else(|e| panic!("failed to load p50k_edit ranks: {e}")),
        );
        m.insert(
            "o200k_base",
            tiktoken_rs::o200k_base()
                .unwrap_or_else(|e| panic!("failed to load o200k_base ranks: {e}")),
        );
        m.insert(
            "o200k_harmony",
            tiktoken_rs::o200k_harmony()
                .unwrap_or_else(|e| panic!("failed to load o200k_harmony ranks: {e}")),
        );
        m
    })
}

/// Resolves a model name to an embedded encoding name.
pub fn resolve_encoding(model_name: &str) -> String {
    let key = model_name.to_lowercase();
    let key = key.trim();
    if let Some(t) = get_tokenizer(key) {
        return tokenizer_name(t).to_string();
    }
    if let Some((_, enc)) = MODEL_ENCODING.iter().find(|(m, _)| *m == key) {
        return enc.to_string();
    }
    let mut best: Option<(&str, &str)> = None;
    for (m, enc) in MODEL_ENCODING {
        if key.starts_with(m) && m.len() > best.map(|(b, _)| b.len()).unwrap_or(0) {
            best = Some((m, enc));
        }
    }
    best.map(|(_, enc)| enc.to_string())
        .unwrap_or_else(|| DEFAULT_ENCODING.to_string())
}

/// Returns the sorted names of all embedded encodings.
pub fn embedded_encodings() -> Vec<String> {
    let mut names: Vec<String> = encoders().keys().map(|s| s.to_string()).collect();
    names.sort();
    names
}

fn bpe_for(model_name: &str) -> Result<&'static CoreBPE, TokenError> {
    let enc = resolve_encoding(model_name);
    encoders()
        .get(enc.as_str())
        .ok_or_else(|| TokenError::new(format!("encoding not embedded: {enc}")))
}

fn find_special(text: &str, bpe: &CoreBPE) -> Option<String> {
    let mut specials: Vec<&str> = bpe.special_tokens().into_iter().collect();
    specials.sort();
    let mut best: Option<(usize, &str)> = None;
    for tok in specials {
        if let Some(pos) = text.find(tok)
            && best.map(|(p, _)| pos < p).unwrap_or(true)
        {
            best = Some((pos, tok));
        }
    }
    best.map(|(_, tok)| tok.to_string())
}

fn disallowed_message(token: &str) -> String {
    format!(
        "Encountered text corresponding to disallowed special token '{token}'.\n\
         If you want this text to be encoded as a special token, \
         pass it to `allowed_special`, e.g. `allowed_special={{'{token}', ...}}`.\n\
         If you want this text to be encoded as normal text, disable the check for this token \
         by passing `disallowed_special=(enc.special_tokens_set - {{'{token}'}})`.\n\
         To disable this check for all special tokens, pass `disallowed_special=()`.\n"
    )
}

fn encode_strict(bpe: &CoreBPE, text: &str) -> Result<Vec<u32>, TokenError> {
    if let Some(tok) = find_special(text, bpe) {
        return Err(TokenError::new(disallowed_message(&tok)));
    }
    let empty: std::collections::HashSet<&str> = std::collections::HashSet::new();
    bpe.encode(text, &empty)
        .map(|(tokens, _)| tokens)
        .map_err(|e| TokenError::new(e.to_string()))
}

fn decode_lossy(bpe: &CoreBPE, tokens: Vec<u32>) -> Result<String, TokenError> {
    bpe.decode_bytes(&tokens)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .map_err(|e| TokenError::new(e.to_string()))
}

/// Counts tokens for a model using the embedded BPE encoder.
///
/// # Examples
///
/// ```
/// use gossamer::tokens::count_tokens;
///
/// assert_eq!(count_tokens("hello world", "gpt-4o").unwrap(), 2);
/// ```
///
/// # Errors
///
/// Returns [`TokenError`] for disallowed special tokens or encoding failures.
pub fn count_tokens(text: &str, model_name: &str) -> Result<usize, TokenError> {
    Ok(encode_strict(bpe_for(model_name)?, text)?.len())
}

/// Truncates text to at most `max_tokens`, appending `ellipsis` when needed.
///
/// # Errors
///
/// Returns [`TokenError`] for disallowed special tokens or encoding failures.
pub fn truncate_to_tokens(
    text: &str,
    max_tokens: i64,
    model_name: &str,
    ellipsis: &str,
) -> Result<String, TokenError> {
    if text.is_empty() {
        return Ok(text.to_string());
    }
    let bpe = bpe_for(model_name)?;
    let tokens = encode_strict(bpe, text)?;
    if tokens.len() as i64 <= max_tokens {
        return Ok(text.to_string());
    }
    let ellipsis_tokens = encode_strict(bpe, ellipsis)?;
    let reserve = (ellipsis_tokens.len() as i64).max(1);
    let keep = (max_tokens - reserve).max(0) as usize;
    let truncated = decode_lossy(bpe, tokens[..keep].to_vec())?;
    Ok(truncated + ellipsis)
}

/// Fits non-empty pieces into `max_tokens`, truncating the first overflowing piece.
///
/// # Errors
///
/// Returns [`TokenError`] for disallowed special tokens or encoding failures.
pub fn fit_context_window(
    pieces: Vec<String>,
    max_tokens: i64,
    model_name: &str,
) -> Result<Vec<String>, TokenError> {
    let mut result = Vec::new();
    let mut remaining = max_tokens;
    for piece in &pieces {
        if piece.is_empty() {
            continue;
        }
        let n = count_tokens(piece, model_name)? as i64;
        if n <= remaining {
            result.push(piece.clone());
            remaining -= n;
        } else if remaining > 10 {
            result.push(truncate_to_tokens(
                piece,
                remaining,
                model_name,
                DEFAULT_ELLIPSIS,
            )?);
            break;
        } else {
            break;
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolution_prefers_registry_then_table() {
        assert_eq!(resolve_encoding("gpt-4o"), "o200k_base");
        assert_eq!(resolve_encoding("GPT-4o "), "o200k_base");
        assert_eq!(resolve_encoding("o1"), "o200k_base");
        assert_eq!(resolve_encoding("claude-3-sonnet"), "cl100k_base");
        assert_eq!(resolve_encoding("gpt-4o-2024-08-06"), "o200k_base");
        assert_eq!(resolve_encoding("unknown-xyz"), "cl100k_base");
    }

    #[test]
    fn counts_match_known_vectors() {
        assert_eq!(count_tokens("hello world", "gpt-4o").unwrap(), 2);
        assert_eq!(count_tokens("", "gpt-4o").unwrap(), 0);
    }

    #[test]
    fn special_tokens_raise_with_tiktoken_message() {
        let err = count_tokens("a <|endoftext|> b", "gpt-4o").unwrap_err();
        assert!(err.message().starts_with(
            "Encountered text corresponding to disallowed special token '<|endoftext|>'."
        ));
    }

    #[test]
    fn truncation_cuts_at_token_boundaries() {
        let text = "hello world, this is a test of truncation";
        let out = truncate_to_tokens(text, 4, "gpt-4o", "...").unwrap();
        assert!(out.ends_with("..."));
        assert!(count_tokens(&out, "gpt-4o").is_err() || out.len() < text.len());
    }
}
