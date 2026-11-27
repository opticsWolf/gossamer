//! Default-off browser fallback policy.
//!
//! The Rust front door keeps browser escalation disabled until a backend has
//! SSRF-safe request interception. These kernels make that policy explicit and
//! deterministic: only a genuine shell/no-usable-text verdict may escalate,
//! and access walls are terminal regardless of the configured backend.

use serde_json::{Value, json};

/// Configured browser backend lane.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrowserMode {
    /// No browser escalation.
    Off,
    /// Escalate only on a shell verdict when a backend is available.
    Auto,
    /// Explicit built-in-Chrome lane (requires an SSRF-safe backend).
    Chrome,
    /// Explicit optional Bladebro subprocess lane.
    Bladebro,
}

impl BrowserMode {
    /// Parse one `GOSSAMER_BROWSER_FALLBACK` value; unknown values mean off.
    pub fn parse(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "auto" => Self::Auto,
            "chrome" => Self::Chrome,
            "bladebro" => Self::Bladebro,
            _ => Self::Off,
        }
    }

    /// Read the configured lane from the environment.
    pub fn from_env() -> Self {
        Self::parse(
            &std::env::var("GOSSAMER_BROWSER_FALLBACK").unwrap_or_else(|_| "off".to_string()),
        )
    }

    /// Canonical environment value for this lane.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Auto => "auto",
            Self::Chrome => "chrome",
            Self::Bladebro => "bladebro",
        }
    }
}

/// Content verdict produced by a static fetch.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContentVerdict {
    /// Usable text was extracted.
    Ok,
    /// HTML shell or no usable text: the only browser-escalation case.
    Shell,
    /// Challenge wall (Cloudflare, DataDome, Akamai, ...).
    Challenge,
    /// Authentication wall.
    Auth,
    /// Paywall.
    Paywall,
    /// Explicitly blocked by the origin.
    Blocked,
    /// Soft 404 page.
    Soft404,
}

impl ContentVerdict {
    /// Parse a verdict name; unknown names are treated as usable content.
    pub fn parse(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "shell" => Self::Shell,
            "challenge" => Self::Challenge,
            "auth" => Self::Auth,
            "paywall" => Self::Paywall,
            "blocked" => Self::Blocked,
            "soft_404" => Self::Soft404,
            _ => Self::Ok,
        }
    }
}

/// Decision returned by [`browser_decision`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrowserDecision {
    /// Serve the static result.
    KeepStatic,
    /// Escalate through the configured browser backend.
    Escalate,
    /// Stop: the page is an access wall or no backend is enabled.
    Terminal,
}

/// Decide whether a verdict may route to a browser backend.
pub fn browser_decision(mode: BrowserMode, verdict: ContentVerdict) -> BrowserDecision {
    if matches!(verdict, ContentVerdict::Ok) {
        return BrowserDecision::KeepStatic;
    }
    if verdict != ContentVerdict::Shell {
        return BrowserDecision::Terminal;
    }
    if mode == BrowserMode::Off {
        return BrowserDecision::Terminal;
    }
    BrowserDecision::Escalate
}

/// Stable metadata/error envelope for a terminal wall or disabled backend.
pub fn terminal_record(verdict: ContentVerdict, mode: BrowserMode) -> Value {
    let (code, message) = match verdict {
        ContentVerdict::Challenge => (
            "wall.challenge",
            "Access challenge page; browser fallback is not used for challenge walls.",
        ),
        ContentVerdict::Auth => (
            "wall.auth",
            "Authentication required; browser fallback is not used for auth walls.",
        ),
        ContentVerdict::Paywall => (
            "wall.paywall",
            "Paywall detected; browser fallback is not used for paywalls.",
        ),
        ContentVerdict::Blocked => (
            "wall.blocked",
            "Origin blocked the request; browser fallback is not used for blocked pages.",
        ),
        ContentVerdict::Soft404 => (
            "wall.soft_404",
            "Soft 404 page; browser fallback is not used for missing content.",
        ),
        ContentVerdict::Shell | ContentVerdict::Ok => (
            "browser.disabled",
            "Browser fallback is disabled; set GOSSAMER_BROWSER_FALLBACK to auto, chrome, or bladebro after SSRF-safe interception is available.",
        ),
    };
    json!({
        "code": code,
        "message": message,
        "browser_fallback": mode.as_str(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_mode_is_off() {
        assert_eq!(BrowserMode::parse(""), BrowserMode::Off);
        assert_eq!(BrowserMode::parse("unknown"), BrowserMode::Off);
    }

    #[test]
    fn only_shell_escalates() {
        for verdict in [
            ContentVerdict::Ok,
            ContentVerdict::Challenge,
            ContentVerdict::Auth,
            ContentVerdict::Paywall,
            ContentVerdict::Blocked,
            ContentVerdict::Soft404,
        ] {
            assert_eq!(
                browser_decision(BrowserMode::Auto, verdict),
                match verdict {
                    ContentVerdict::Ok => BrowserDecision::KeepStatic,
                    _ => BrowserDecision::Terminal,
                }
            );
        }
        assert_eq!(
            browser_decision(BrowserMode::Auto, ContentVerdict::Shell),
            BrowserDecision::Escalate
        );
        assert_eq!(
            browser_decision(BrowserMode::Off, ContentVerdict::Shell),
            BrowserDecision::Terminal
        );
    }
}
