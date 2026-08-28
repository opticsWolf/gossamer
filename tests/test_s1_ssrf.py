"""
S1 — SSRF protection tests (CODE_REVIEW_2026-08-27 §3.1).

LLM-supplied URLs must never reach non-public destinations. The guard is
enforced at both layers:

- Python: ``ssrf.validate_public_url`` — the toolbox choke point
  (``_validate_url``) covering the static, browser, and document paths;
- Rust:   the ``fetch_attempt`` guard — defense in depth for the static
  engine, validating every redirect hop.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stitch_web_researcher import _core
from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.ssrf import SsrfBlockedError, validate_public_url


class TestPythonGuard:
    """Unit tests for ssrf.validate_public_url (hermetic, no network)."""

    def test_blocks_aws_metadata(self):
        with pytest.raises(SsrfBlockedError, match="not a public address"):
            validate_public_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_gcp_metadata_v6(self):
        with pytest.raises(SsrfBlockedError, match="not a public address"):
            validate_public_url("http://[fd00:ec2::254]/computeMetadata/")

    def test_blocks_localhost_name(self):
        with pytest.raises(SsrfBlockedError, match="internal name"):
            validate_public_url("http://localhost:8080/admin")

    def test_blocks_loopback_literal(self):
        with pytest.raises(SsrfBlockedError):
            validate_public_url("http://127.0.0.1:5000/x")

    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.5/x",
            "http://172.16.3.4/x",
            "http://192.168.1.10/x",
        ],
    )
    def test_blocks_rfc1918(self, url):
        with pytest.raises(SsrfBlockedError):
            validate_public_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://api.corp.local/x",
            "http://metadata.google.internal/x",
            "https://db.10.example.internal:5432/",
        ],
    )
    def test_blocks_internal_hostnames(self, url):
        with pytest.raises(SsrfBlockedError, match="internal name"):
            validate_public_url(url)

    def test_blocks_unspecified(self):
        with pytest.raises(SsrfBlockedError):
            validate_public_url("http://0.0.0.0/x")

    def test_blocks_non_http_scheme(self):
        with pytest.raises(SsrfBlockedError, match="scheme"):
            validate_public_url("file:///etc/passwd")

    def test_blocks_missing_host(self):
        with pytest.raises(SsrfBlockedError, match="no host"):
            validate_public_url("https:///path-only")

    def test_allows_public_ip_literal(self):
        # A public literal passes the IP check (no DNS involved).
        validate_public_url("http://93.184.215.9/x")

    def test_bypass_env_var(self, monkeypatch):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "true")
        validate_public_url("http://127.0.0.1/")  # operator opt-in


class TestRustGuard:
    """The Rust static engine enforces the same policy."""

    def test_blocks_metadata_ip(self):
        # Blocked before any network I/O — hermetic.
        with pytest.raises(RuntimeError, match="SSRF"):
            _core.fetch_and_extract("http://169.254.169.254/latest/meta-data/")

    def test_blocks_localhost(self):
        with pytest.raises(RuntimeError, match="SSRF"):
            _core.fetch_and_extract("http://localhost:1/x")

    def test_blocks_rfc1918(self):
        with pytest.raises(RuntimeError, match="SSRF"):
            _core.fetch_and_extract("http://192.168.1.1/x")

    def test_bypass_env_var(self, monkeypatch):
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
        with pytest.raises(RuntimeError) as exc_info:
            _core.fetch_and_extract("http://127.0.0.1:1/x")
        # Bypass disables the guard; the fetch still fails (port 1), but
        # it must not be an SSRF error.
        assert "SSRF" not in str(exc_info.value)

    def test_follows_redirect_chain(self, monkeypatch):
        """The manual redirect loop (Policy::none) still follows 3xx chains.

        SSRF is bypassed here; the redirect logic itself is under test.
        """
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/mid")
                    self.end_headers()
                elif self.path == "/mid":
                    self.send_response(301)
                    self.send_header("Location", "/end")
                    self.end_headers()
                else:
                    body = b"<html><body>redirect target</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            md, _links = _core.fetch_and_extract(f"http://127.0.0.1:{port}/start")
            assert "redirect target" in md
        finally:
            server.shutdown()


class TestToolboxIntegration:
    """The toolbox entry points apply the guard (S1 choke points)."""

    @pytest.fixture()
    def toolbox(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", raising=False)
        # respect_robots=False: batch tests use a fake example.com URL and a
        # private IP; the SSRF guard is what's under test (S4 opt-out).
        return WebResearcherToolbox(
            ToolboxConfig(cache_dir=str(tmp_path / "c"), respect_robots=False)
        )

    # The URL is still validated before any I/O; the *refusal* now travels
    # through the tool's JSON error contract instead of escaping as an
    # exception, so an LLM caller can recover by picking another link
    # (bugfix 3). The security property under test is unchanged: no fetch.

    @staticmethod
    def _sole_error(raw):
        data = json.loads(raw)
        if isinstance(data, list):
            errors = [e["error"] for e in data if e.get("error")]
            assert errors, f"expected a rejected entry, got {data!r}"
            return errors[0]
        return data["error"]

    def test_inspect_html_page_blocks_metadata(self, toolbox):
        err = self._sole_error(
            toolbox.inspect_html_page("http://169.254.169.254/latest/meta-data/")
        )
        assert "not a public address" in err

    def test_inspect_html_page_blocks_localhost(self, toolbox):
        err = self._sole_error(toolbox.inspect_html_page("http://localhost:5000/x"))
        assert "internal name" in err

    def test_batch_inspect_pages_blocks_private(self, toolbox):
        # One refused URL must not discard the batch: it becomes one error
        # record and the other entries are still processed.
        out = json.loads(
            toolbox.batch_inspect_pages(
                ["https://example.com/ok", "http://10.0.0.5/secret"]
            )
        )
        blocked = [e for e in out if "10.0.0.5" in str(e.get("url", ""))]
        assert blocked and "not a public address" in blocked[0]["error"]

    def test_extract_document_blocks_metadata(self, toolbox):
        err = self._sole_error(
            toolbox.extract_document(
                "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            )
        )
        assert "not a public address" in err

    def test_local_server_works_with_bypass(self, tmp_path, monkeypatch):
        # The operator bypass restores local dev/test servers.
        monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><head><title>t</title></head><body>ok</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            tb = WebResearcherToolbox(
                ToolboxConfig(cache_dir=str(tmp_path / "c"), respect_robots=False)
            )
            port = server.server_address[1]
            out = tb.inspect_html_page(f"http://127.0.0.1:{port}/")
            data = json.loads(out)
            assert data["markdown"]
        finally:
            server.shutdown()
