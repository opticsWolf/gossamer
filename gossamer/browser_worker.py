"""Standalone browser_oxide render worker.

This file is executed as a short-lived child process by
:mod:`gossamer.browser_isolation`; it is deliberately not imported by the
parent process. The child only needs the standard library and the optional
``browser_oxide`` package.

Protocol
--------
The parent passes a URL, an output path, and rendering options. On success
the worker writes the rendered HTML to ``--out`` (UTF-8) and prints one JSON
object to stdout::

    {"ok": true, "verdict": "pass", "url": "...", "html_chars": 1234}

Python-level failures (missing package, challenge, empty render) are reported
as ``{"ok": false, "error": "...", "error_type": "..."}``. A native Rust abort
cannot be caught by Python; the parent detects it from the non-zero process
exit code and the captured stderr. The process exits with ``os._exit`` after
writing the status so a broken engine teardown cannot hang the worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional


def _status(ok: bool, **extra) -> dict:
    payload = {"ok": bool(ok)}
    payload.update(extra)
    return payload


def render(
    url: str,
    out_path: str,
    max_iterations: int = 5,
    profile: str = "chrome",
) -> dict:
    """Render *url* and write the HTML to *out_path*.

    Returns a JSON-serialisable status dict. This function is intentionally
    free of ``gossamer`` imports so the worker can run even if the parent's
    package environment is partially broken.
    """
    try:
        import browser_oxide
    except Exception as exc:  # pragma: no cover - exercised by the parent
        return _status(
            False,
            error=f"browser_oxide import failed: {exc}",
            error_type=type(exc).__name__,
        )

    try:
        profile_factory = getattr(browser_oxide.Profile, profile)
    except AttributeError:
        return _status(
            False,
            error=f"unknown browser_oxide profile {profile!r}",
            error_type="ValueError",
        )

    try:
        browser = browser_oxide.Browser(profile=profile_factory())
        page = browser.navigate(url, max_iterations=max_iterations)
        verdict = str(getattr(page, "verdict", ""))
        if getattr(page, "is_challenge", False):
            return _status(
                False,
                error=f"Anti-bot challenge detected ({verdict}) for {url}",
                error_type="challenge",
                verdict=verdict,
            )
        html = page.html
        if not isinstance(html, str) or not html:
            return _status(
                False,
                error=f"browser_oxide returned no HTML for {url}",
                error_type="EmptyRender",
                verdict=verdict,
            )
        Path(out_path).write_text(html, encoding="utf-8")
        return _status(
            True,
            verdict=verdict,
            url=str(getattr(page, "url", url)),
            html_chars=len(html),
        )
    except Exception as exc:
        return _status(
            False,
            error=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
        )
    # No browser.close(): main() exits with os._exit(0) immediately after
    # writing the status. A full close is unnecessary for a one-shot worker
    # and is another place a broken engine can hang or abort.


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="gossamer browser_oxide render worker"
    )
    parser.add_argument("url")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--profile", default="chrome")
    args = parser.parse_args(argv)

    result = render(
        args.url,
        args.out,
        max_iterations=args.max_iterations,
        profile=args.profile,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    # Bypass interpreter shutdown / Browser Drop: the OS tears down the
    # engine thread, sockets and V8 state when the one-shot process exits.
    os._exit(0)
    return 0  # pragma: no cover - os._exit never returns


if __name__ == "__main__":
    raise SystemExit(main())
