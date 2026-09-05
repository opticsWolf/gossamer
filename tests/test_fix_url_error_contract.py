# tests/test_fix_url_error_contract.py
"""Bugfix 3 — URL rejections honor the JSON error contract.

``normalize_url`` / ``_validate_url`` used to run *before* each tool's
try block, so a refused URL escaped as ``SsrfBlockedError`` or
``ValueError`` instead of the ``{"error": ...}`` payload every other
failure returns. For an LLM caller a bad URL is recoverable — pick
another link — so it must come back as data, not an exception.

The batch case mattered most: one refused URL aborted the whole call and
discarded every good result with it, and scraped link lists are exactly
where a hostile URL arrives.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox

PAGE = (
    b"<html><head><title>Good</title></head><body><main>"
    b"<h1>Good page</h1><p>Real content that survives.</p>"
    b"</main></body></html>"
)

# Refused regardless of the private-host policy, so it can be mixed into a
# batch that also fetches a local test server.
BAD_LOCAL_PATH = "./notes.html"
BAD_PRIVATE = "http://169.254.169.254/latest/meta-data/"


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def toolbox(tmp_path):
    return WebResearcherToolbox(
        ToolboxConfig(cache_dir=str(tmp_path / "c"), fetch_delay=0.0, ddgs_delay=0.0)
    )


def _error_of(raw):
    """Parse a tool reply and return its error string (None if absent)."""
    data = json.loads(raw)
    if isinstance(data, list):
        return next(
            (e.get("error") for e in data if isinstance(e, dict) and e.get("error")),
            None,
        )
    return data.get("error")


class TestSingleUrlTools:
    @pytest.mark.parametrize(
        "method",
        [
            "inspect_html_page",
            "inspect_html_structured",
            "extract_document",
            "discover_resources",
        ],
    )
    def test_refused_host_returns_json_not_exception(self, toolbox, method):
        raw = getattr(toolbox, method)(BAD_PRIVATE)
        assert isinstance(raw, str)
        err = _error_of(raw)  # must parse as JSON
        assert err and "URL rejected" in err

    @pytest.mark.parametrize(
        "method",
        ["inspect_html_page", "inspect_html_structured", "discover_resources"],
    )
    def test_local_path_returns_json_not_exception(self, toolbox, method):
        # extract_document is excluded on purpose: it accepts local files, so
        # a path is a legitimate source there (and reports file-not-found as
        # JSON), not a rejected URL.
        err = _error_of(getattr(toolbox, method)(BAD_LOCAL_PATH))
        assert err and "URL rejected" in err

    def test_extract_document_still_accepts_local_paths(self, toolbox):
        # Guard against "fixing" the contract by refusing every path.
        err = _error_of(toolbox.extract_document(BAD_LOCAL_PATH))
        assert err and "URL rejected" not in err

    def test_error_names_the_reason(self, toolbox):
        data = json.loads(toolbox.inspect_html_page(BAD_PRIVATE))
        # The model needs to know *why* so it stops retrying the same URL.
        assert "not a public address" in data["error"]
        assert data["error_type"] == "SsrfBlockedError"
        assert data["url"] == BAD_PRIVATE

    def test_execute_tool_dispatcher_also_returns_json(self, toolbox):
        raw = toolbox.execute_tool("inspect_html_page", {"url": BAD_PRIVATE})
        assert "URL rejected" in _error_of(raw)


class TestBatchIsolatesBadUrls:
    def test_one_bad_url_does_not_discard_the_good_ones(
        self, toolbox, server, monkeypatch
    ):
        monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")
        good = f"{server}/good"
        out = json.loads(toolbox.batch_inspect_pages([BAD_LOCAL_PATH, good]))

        assert len(out) == 2, "every input URL must produce a record"
        by_error = {bool(e.get("error")): e for e in out}
        assert "URL rejected" in by_error[True]["error"]
        assert "Good page" in by_error[False]["markdown"]

    def test_rejected_entries_keep_input_order(self, toolbox, server, monkeypatch):
        monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "1")
        good = f"{server}/good"
        out = json.loads(toolbox.batch_inspect_pages([good, BAD_LOCAL_PATH]))
        assert len(out) == 2
        assert not out[0].get("error")
        assert "URL rejected" in out[1]["error"]

    def test_all_bad_still_returns_a_record_each(self, toolbox):
        out = json.loads(toolbox.batch_inspect_pages([BAD_PRIVATE, BAD_LOCAL_PATH]))
        assert len(out) == 2
        assert all("URL rejected" in e["error"] for e in out)
