//! One-shot `browser_oxide` renderer.
//!
//! The parent `gossamer` process owns the browser policy and timeout. It
//! spawns this binary for a single render so a native browser abort cannot
//! take down the CLI or MCP server.
//!
//! Input (stdin, or a single URL/JSON argument):
//!
//! ```json
//! {"url":"https://example.com","profile":"chrome_148_windows","max_iterations":5}
//! ```
//!
//! Output is one compact JSON object on stdout. The process exits `0` for a
//! rendered page and `1` for a handled render failure; the parent still owns
//! the wall-clock timeout and native-crash handling.
//!
//! This sidecar checks only the initial URL scheme. The caller must apply the
//! gossamer SSRF policy before spawning it; browser subresource interception
//! remains a separate security requirement.

use std::io::Read;
use std::process::ExitCode;

use browser_oxide::stealth::StealthProfile;
use browser_oxide::{ChallengeVerdict, Page};
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Debug, Deserialize)]
struct Request {
    url: String,
    #[serde(default)]
    profile: Option<String>,
    #[serde(default)]
    max_iterations: Option<u8>,
}

fn default_profile_name() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        "chrome_148_windows"
    }
    #[cfg(target_os = "macos")]
    {
        "chrome_148_macos"
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        "chrome_148_linux"
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        "chrome_148_windows"
    }
}

fn profile(name: Option<&str>) -> Result<StealthProfile, String> {
    let name = name.unwrap_or(default_profile_name());
    let profile = match name {
        "chrome" | "chrome_148_windows" => browser_oxide::stealth::presets::chrome_148_windows(),
        "chrome_148_macos" => browser_oxide::stealth::presets::chrome_148_macos(),
        "chrome_148_linux" => browser_oxide::stealth::presets::chrome_148_linux(),
        "chrome_148_ru" => browser_oxide::stealth::presets::chrome_148_ru(),
        "chrome_148_cn" => browser_oxide::stealth::presets::chrome_148_cn(),
        "chrome_148_de" => browser_oxide::stealth::presets::chrome_148_de(),
        "chrome_148_jp" => browser_oxide::stealth::presets::chrome_148_jp(),
        "firefox" | "firefox_135_windows" => browser_oxide::stealth::presets::firefox_135_windows(),
        "firefox_135_macos" => browser_oxide::stealth::presets::firefox_135_macos(),
        "firefox_135_linux" => browser_oxide::stealth::presets::firefox_135_linux(),
        "pixel" | "pixel_9_pro_chrome_148" => {
            browser_oxide::stealth::presets::pixel_9_pro_chrome_148()
        }
        "iphone" | "iphone_15_pro_safari_18" => {
            browser_oxide::stealth::presets::iphone_15_pro_safari_18()
        }
        other => return Err(format!("unknown browser profile: {other}")),
    };
    Ok(profile)
}

fn error_payload(error_type: &str, message: impl Into<String>) -> Value {
    json!({
        "ok": false,
        "error": message.into(),
        "error_type": error_type,
    })
}

fn challenge_error(verdict: ChallengeVerdict) -> Value {
    json!({
        "ok": false,
        "error": format!("Anti-bot challenge detected ({})", verdict.as_str()),
        "error_type": "challenge",
        "verdict": verdict.as_str(),
    })
}

fn validate_url(raw: &str) -> Result<(), String> {
    let url = url::Url::parse(raw).map_err(|error| format!("invalid URL: {error}"))?;
    match url.scheme() {
        "http" | "https" => Ok(()),
        scheme => Err(format!("unsupported URL scheme: {scheme}")),
    }
}

fn render(request: Request) -> (Value, u8) {
    if let Err(error) = validate_url(&request.url) {
        return (error_payload("InvalidUrl", error), 1);
    }
    let profile = match profile(request.profile.as_deref()) {
        Ok(profile) => profile,
        Err(error) => return (error_payload("InvalidProfile", error), 1),
    };
    let iterations = request.max_iterations.unwrap_or(5).clamp(1, 20);

    let runtime = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            return (
                error_payload(
                    "RuntimeError",
                    format!("could not create browser runtime: {error}"),
                ),
                1,
            );
        }
    };
    let local = tokio::task::LocalSet::new();
    let result = local.block_on(&runtime, async move {
        let mut page = match Page::navigate(&request.url, profile, iterations).await {
            Ok(page) => page,
            Err(error) => {
                return (error_payload("BrowserRenderError", error.to_string()), 1);
            }
        };
        let verdict = page.challenge_verdict();
        if verdict.is_challenge() {
            return (challenge_error(verdict), 1);
        }
        let final_url = page.url().to_string();
        let title = page.title();
        let html = page.content();
        if html.is_empty() {
            return (
                error_payload(
                    "EmptyRender",
                    format!("browser_oxide returned no HTML for {}", request.url),
                ),
                1,
            );
        }
        let html_chars = html.len();
        (
            json!({
                "ok": true,
                "url": final_url,
                "title": title,
                "html": html,
                "html_chars": html_chars,
                "verdict": verdict.as_str(),
            }),
            0,
        )
    });
    result
}

fn read_request() -> Result<Request, String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let input = match args.first() {
        Some(arg) if arg.trim_start().starts_with('{') => arg.clone(),
        Some(url) => json!({"url": url}).to_string(),
        None => {
            let mut text = String::new();
            std::io::stdin()
                .read_to_string(&mut text)
                .map_err(|error| format!("could not read stdin: {error}"))?;
            text
        }
    };
    serde_json::from_str(&input).map_err(|error| format!("invalid request JSON: {error}"))
}

fn main() -> ExitCode {
    let (payload, code) = match read_request() {
        Ok(request) => render(request),
        Err(error) => (error_payload("InvalidRequest", error), 2),
    };
    match serde_json::to_string(&payload) {
        Ok(text) => {
            println!("{text}");
            ExitCode::from(code)
        }
        Err(error) => {
            eprintln!("gossamer-browser: could not serialize response: {error}");
            ExitCode::from(2)
        }
    }
}
