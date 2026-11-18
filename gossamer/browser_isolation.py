"""Out-of-process rendering for the optional ``browser_oxide`` engine.

``browser_oxide`` is a native Rust extension. On some cold renders it aborts
the process (``SIGABRT`` on POSIX, ``0xC0000409`` /
``STATUS_STACK_BUFFER_OVERRUN`` on Windows) after V8's deadline watcher fires
``terminate_execution``. Python cannot catch a native abort, so an in-process
render can kill the CLI or MCP server before any ``except`` runs.

This module runs every render in a short-lived child process
(:mod:`gossamer.browser_worker`). The parent enforces a wall-clock timeout and
turns a crash, timeout, challenge, or malformed worker response into a normal
:class:`BrowserRenderError`. ``browser-first`` callers can then fall back to
the static Rust fetch; every caller gets an ordinary exception instead of a
dead process.

Environment
-----------
``GOSSAMER_BROWSER_ISOLATION``
    ``1`` (default) runs renders out of process. Set ``0`` only for debugging;
    in-process rendering restores the native-abort risk.
``GOSSAMER_BROWSER_TIMEOUT``
    Per-render worker timeout in seconds. Default ``45`` (the engine's own
    build budget is 25s). Values below 5s are clamped up.

The optional ``BrowserOxideSearchProvider`` uses the same worker unless
isolation is disabled, so a search-page crash cannot kill the host either.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from gossamer.env import getenv, getenv_bool

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 45.0
_MIN_TIMEOUT_SECONDS = 5.0
_WORKER_PATH = Path(__file__).with_name("browser_worker.py")

# Windows NTSTATUS values seen from native aborts. 0xC0000409 is what
# browser_oxide 0.1.x returns on the example.com cold-render abort.
_WINDOWS_EXIT_NAMES = {
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
}


class BrowserRenderError(RuntimeError):
    """A browser render failed, crashed, or timed out.

    ``exit_code``/``stderr`` are populated for native-abort diagnostics.
    The exception itself is ordinary Python and safe to catch; the native
    process that produced it is already gone.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: Optional[int] = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


def browser_isolation_enabled() -> bool:
    """Whether browser renders should run in a child process.

    Frozen executables (PyInstaller et al.) may not ship the worker script,
    so they keep the legacy in-process path. A missing worker file also
    falls back with a warning rather than failing every render.
    """
    if getattr(sys, "frozen", False):
        return False
    if not _WORKER_PATH.is_file():
        logger.warning(
            "browser worker %s is missing; rendering browser_oxide in-process",
            _WORKER_PATH,
        )
        return False
    return getenv_bool("GOSSAMER_BROWSER_ISOLATION", True)


def browser_timeout_seconds() -> float:
    """Resolve ``GOSSAMER_BROWSER_TIMEOUT`` (seconds) with a safe floor."""
    raw = getenv("GOSSAMER_BROWSER_TIMEOUT", None)
    if raw in (None, ""):
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "GOSSAMER_BROWSER_TIMEOUT=%r is not a number; using %.0fs",
            raw,
            DEFAULT_TIMEOUT_SECONDS,
        )
        return DEFAULT_TIMEOUT_SECONDS
    if not math.isfinite(value):
        logger.warning(
            "GOSSAMER_BROWSER_TIMEOUT=%r is not finite; using %.0fs",
            raw,
            DEFAULT_TIMEOUT_SECONDS,
        )
        return DEFAULT_TIMEOUT_SECONDS
    if value < _MIN_TIMEOUT_SECONDS:
        logger.warning(
            "GOSSAMER_BROWSER_TIMEOUT=%.1fs is below the %.0fs floor; clamping",
            value,
            _MIN_TIMEOUT_SECONDS,
        )
        return _MIN_TIMEOUT_SECONDS
    return value


def _worker_env() -> dict:
    """Environment for the child: inherit the parent's, add the repo root.

    The worker itself does not import ``gossamer``, but keeping the package
    root on ``PYTHONPATH`` makes development checkouts (where the package is
    not installed into site-packages) behave like installed wheels.
    """
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        package_root if not existing else package_root + os.pathsep + existing
    )
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _format_exit_code(code: Optional[int]) -> str:
    if code is None:
        return "unknown"
    if code < 0:
        return f"signal {-code}"
    unsigned = code & 0xFFFFFFFF
    name = _WINDOWS_EXIT_NAMES.get(unsigned)
    if code > 0x7FFFFFFF or name:
        rendered = f"0x{unsigned:08X}"
        return f"{rendered} ({name})" if name else rendered
    return str(code)


def _stderr_tail(stderr: str, limit: int = 1200) -> str:
    """Pick the most informative stderr line from a crashed worker."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    lines = [
        line
        for line in lines
        if not line.startswith("note: run with `RUST_BACKTRACE")
    ]
    if not lines:
        return ""
    preferred = [
        line
        for line in lines
        if any(
            marker in line.lower()
            for marker in (
                "memory allocation",
                "panicked",
                "fatal",
                "abort",
                "sigabrt",
                "error",
            )
        )
    ]
    return (preferred[-1] if preferred else lines[-1])[-limit:]


def _parse_status(stdout: str) -> Optional[dict]:
    """Return the last JSON object printed by the worker, if any."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def render_browser_html(
    url: str,
    *,
    max_iterations: int = 5,
    profile: str = "chrome",
    timeout: Optional[float] = None,
) -> str:
    """Render *url* with browser_oxide and return the HTML.

    Raises :class:`BrowserRenderError` for a native crash, timeout, challenge,
    missing package, or empty render. In-process mode is used only when
    isolation is disabled or unavailable (frozen executable / missing worker).
    """
    if not browser_isolation_enabled():
        return _render_in_process(url, max_iterations, profile)

    timeout = browser_timeout_seconds() if timeout is None else float(timeout)
    if not math.isfinite(timeout):
        timeout = DEFAULT_TIMEOUT_SECONDS
    elif timeout < _MIN_TIMEOUT_SECONDS:
        timeout = _MIN_TIMEOUT_SECONDS

    with tempfile.TemporaryDirectory(prefix="gossamer-browser-") as tmpdir:
        out_path = Path(tmpdir) / "page.html"
        cmd = [
            sys.executable,
            str(_WORKER_PATH),
            url,
            "--out",
            str(out_path),
            "--max-iterations",
            str(max_iterations),
            "--profile",
            profile,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=_worker_env(),
                # Avoid flashing a console window on Windows. Zero is a
                # no-op on POSIX.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning(
                "browser_oxide worker timed out after %.1fs for %s",
                timeout,
                url,
            )
            raise BrowserRenderError(
                f"browser_oxide render timed out after {timeout:g}s for {url}"
            ) from exc
        except OSError as exc:
            raise BrowserRenderError(
                f"could not start browser_oxide worker for {url}: {exc}"
            ) from exc

        stderr = proc.stderr or ""
        payload = _parse_status(proc.stdout or "")
        if proc.returncode != 0:
            tail = _stderr_tail(stderr)
            logger.warning(
                "browser_oxide worker crashed for %s (exit %s): %s",
                url,
                _format_exit_code(proc.returncode),
                tail or "(no stderr)",
            )
            detail = f": {tail}" if tail else ""
            raise BrowserRenderError(
                "browser_oxide worker crashed for "
                f"{url} (exit {_format_exit_code(proc.returncode)}){detail}",
                exit_code=proc.returncode,
                stderr=stderr,
            )
        if payload is None:
            tail = _stderr_tail(stderr)
            detail = f": {tail}" if tail else ""
            raise BrowserRenderError(
                f"browser_oxide worker returned no status for {url}{detail}",
                exit_code=proc.returncode,
                stderr=stderr,
            )
        if not payload.get("ok"):
            message = str(
                payload.get("error") or f"browser_oxide render failed for {url}"
            )
            raise BrowserRenderError(
                message,
                exit_code=proc.returncode,
                stderr=stderr,
            )
        try:
            html = out_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BrowserRenderError(
                f"browser_oxide worker did not write HTML for {url}: {exc}",
                exit_code=proc.returncode,
                stderr=stderr,
            ) from exc
        if not html:
            raise BrowserRenderError(
                f"browser_oxide worker wrote empty HTML for {url}",
                exit_code=proc.returncode,
                stderr=stderr,
            )
        return html


def _render_in_process(
    url: str,
    max_iterations: int = 5,
    profile: str = "chrome",
) -> str:
    """Legacy in-process render; used only when isolation is disabled."""
    import browser_oxide

    profile_factory = getattr(browser_oxide.Profile, profile)
    browser = browser_oxide.Browser(profile=profile_factory())
    try:
        page = browser.navigate(url, max_iterations=max_iterations)
        if page.is_challenge:
            raise BrowserRenderError(
                f"Anti-bot challenge detected ({page.verdict}) for {url}"
            )
        return page.html
    finally:
        browser.close()
