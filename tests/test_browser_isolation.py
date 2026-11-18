"""Process isolation for native browser_oxide renders.

browser_oxide can abort the host process (Windows ``0xC0000409`` /
POSIX ``SIGABRT``) after a V8 deadline; Python cannot catch that. These
tests pin the out-of-process wrapper, the worker protocol, and the
browser-first static fallback without launching a real browser.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import gossamer.browser_isolation as iso
import gossamer.fetch as fetch
from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox

URL = "https://example.com/browser-isolation"


@pytest.fixture(autouse=True)
def _enable_isolation(monkeypatch):
    monkeypatch.setenv("GOSSAMER_BROWSER_ISOLATION", "1")
    monkeypatch.delenv("GOSSAMER_BROWSER_TIMEOUT", raising=False)


def _fake_run_ok(
    html: str = "<html><body>worker ok</body></html>",
    payload: dict | None = None,
):
    def fake_run(cmd, **kwargs):
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text(html, encoding="utf-8")
        status = payload if payload is not None else {"ok": True, "verdict": "pass"}
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(status) + "\n",
            stderr="",
        )

    return fake_run


class TestIsolatedRender:
    def test_success_returns_html_and_uses_worker(self, monkeypatch):
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            calls["timeout"] = kwargs["timeout"]
            out = Path(cmd[cmd.index("--out") + 1])
            out.write_text("<html><body>ok</body></html>", encoding="utf-8")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "verdict": "pass"}) + "\n",
                stderr="",
            )

        monkeypatch.setattr(iso.subprocess, "run", fake_run)

        html = iso.render_browser_html(URL, max_iterations=3, timeout=12)

        assert html == "<html><body>ok</body></html>"
        assert calls["cmd"][1].endswith("browser_worker.py")
        assert URL in calls["cmd"]
        assert "--max-iterations" in calls["cmd"]
        assert calls["cmd"][calls["cmd"].index("--max-iterations") + 1] == "3"
        assert calls["timeout"] == 12

    def test_native_abort_becomes_browser_render_error(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=3221226505,
                stdout="",
                stderr=(
                    "[V8DeadlineWatcher] deadline 25000ms expired\n"
                    "memory allocation of 68719476736 bytes failed\n"
                    "note: run with `RUST_BACKTRACE=1` environment variable\n"
                ),
            )

        monkeypatch.setattr(iso.subprocess, "run", fake_run)

        with pytest.raises(iso.BrowserRenderError) as excinfo:
            iso.render_browser_html(URL)

        message = str(excinfo.value)
        assert "0xC0000409" in message
        assert "memory allocation" in message
        assert excinfo.value.exit_code == 3221226505
        assert "V8DeadlineWatcher" in excinfo.value.stderr

    def test_timeout_becomes_browser_render_error(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr(iso.subprocess, "run", fake_run)

        with pytest.raises(iso.BrowserRenderError, match="timed out"):
            iso.render_browser_html(URL, timeout=9)

    def test_worker_challenge_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            iso.subprocess,
            "run",
            _fake_run_ok(
                payload={
                    "ok": False,
                    "error": "Anti-bot challenge detected (edge-block)",
                    "error_type": "challenge",
                }
            ),
        )

        with pytest.raises(iso.BrowserRenderError, match="challenge"):
            iso.render_browser_html(URL)

    def test_worker_without_html_is_reported(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "verdict": "pass"}) + "\n",
                stderr="",
            )

        monkeypatch.setattr(iso.subprocess, "run", fake_run)

        with pytest.raises(iso.BrowserRenderError, match="did not write HTML"):
            iso.render_browser_html(URL)

    def test_isolation_disabled_uses_in_process(self, monkeypatch):
        monkeypatch.setenv("GOSSAMER_BROWSER_ISOLATION", "0")
        monkeypatch.setattr(
            iso,
            "_render_in_process",
            lambda url, max_iterations, profile: "<html>in-process</html>",
        )

        html = iso.render_browser_html(URL)

        assert html == "<html>in-process</html>"

    def test_timeout_env_is_clamped(self, monkeypatch):
        monkeypatch.setenv("GOSSAMER_BROWSER_TIMEOUT", "1")
        assert iso.browser_timeout_seconds() == 5.0


class TestWorkerProtocol:
    def _fake_browser_oxide(self, *, challenge: bool = False):
        fake = types.ModuleType("browser_oxide")

        class FakePage:
            is_challenge = challenge
            verdict = "edge-block" if challenge else "pass"
            html = "<html><body>worker</body></html>"
            url = URL

        class FakeBrowser:
            def __init__(self, profile):
                self.profile = profile

            def navigate(self, url, max_iterations=5):
                return FakePage()

        fake.Browser = FakeBrowser
        fake.Profile = SimpleNamespace(chrome=lambda: object())
        return fake

    def test_worker_writes_html_and_reports_success(self, tmp_path, monkeypatch):
        import gossamer.browser_worker as worker

        monkeypatch.setitem(
            sys.modules, "browser_oxide", self._fake_browser_oxide()
        )
        out = tmp_path / "page.html"

        result = worker.render(URL, str(out), max_iterations=2)

        assert result["ok"] is True
        assert result["verdict"] == "pass"
        assert out.read_text(encoding="utf-8") == "<html><body>worker</body></html>"

    def test_worker_reports_challenge_without_html(self, tmp_path, monkeypatch):
        import gossamer.browser_worker as worker

        monkeypatch.setitem(
            sys.modules,
            "browser_oxide",
            self._fake_browser_oxide(challenge=True),
        )
        out = tmp_path / "page.html"

        result = worker.render(URL, str(out))

        assert result["ok"] is False
        assert result["error_type"] == "challenge"
        assert not out.exists()


class TestFallback:
    def test_browser_first_falls_back_on_native_abort(self, tmp_path, monkeypatch):
        tb = WebResearcherToolbox(
            config=ToolboxConfig(
                cache_dir=str(tmp_path / "cache"),
                respect_robots=False,
                fetch_mode="browser",
                ddgs_delay=0.0,
            )
        )
        tb._fetch_interval = 0
        tb._fetch._static_fetch = lambda url: ("static-md", [], {}, "static")
        monkeypatch.setattr(fetch, "_browser_oxide_available", True)

        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=3221226505,
                stdout="",
                stderr="memory allocation of 68719476736 bytes failed\n",
            )

        monkeypatch.setattr(iso.subprocess, "run", fake_run)

        data = json.loads(tb.inspect_html_page(URL, use_smart="browser"))

        assert data["fetch_method"] == "static"
        assert data["markdown"] == "static-md"

    def test_search_provider_uses_isolated_renderer(self, monkeypatch):
        import gossamer.search_providers as sp

        calls = []
        serp = (
            '<a class="result__a" href="https://example.com/a">'
            "Example Result</a>"
        )

        def fake_render(url, *, max_iterations=5, profile="chrome"):
            calls.append((url, max_iterations))
            return serp

        monkeypatch.setattr(iso, "browser_isolation_enabled", lambda: True)
        monkeypatch.setattr(iso, "render_browser_html", fake_render)

        provider = sp.BrowserOxideSearchProvider(delay=0.0)
        results = provider.search("isolation", max_results=1)

        assert results and results[0]["url"] == "https://example.com/a"
        assert calls and "duckduckgo.com" in calls[0][0]
        assert calls[0][1] == 8
