"""S3 regression: response size cap + content-type gate in the Rust core.

Before the fix, ``fetch_attempt`` called ``response.text()`` with no
Content-Length check and no streaming cap — a hostile or merely large URL
was read fully into memory (the 30 s timeout was the only bound). There was
also no Content-Type gate, so binary bodies were lossily UTF-8 decoded and
only partly rescued by ``_looks_like_text`` downstream. The core now:

  * rejects non-``text/*`` / non-xhtml / non-xml content types on the HTML
    path, naming the real type so the agent can call ``extract_document``;
  * rejects bodies whose declared Content-Length exceeds the cap;
  * streams the body chunk-by-chunk and aborts past the cap;
  * exposes the cap via the fetch bindings' ``max_bytes`` keyword,
    ``ToolboxConfig.max_response_bytes``, and
    ``STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES``.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stitch_web_researcher import _core
from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox

SMALL_HTML = "<html><body><main><p>small page</p></main></body></html>"
# ~7 KB — over the small test caps, far under the 5 MiB default.
BIG_HTML = (
    "<html><body><main>"
    + ("<p>lorem ipsum dolor sit amet consectetur adipiscing elit </p>" * 200)
    + "</main></body></html>"
)
NO_TYPE_HTML = (
    b"<html><body><main><p>no content type</p></main></body></html>"
)
PDF_BODY = b"%PDF-1.4 fake binary body \x00\x01\x02"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.startswith("/chunked"):
            # No Content-Length: forces the streaming chunk-cap path.
            data = BIG_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(0, len(data), 256):
                chunk = data[i : i + 256]
                self.wfile.write(
                    f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n"
                )
            self.wfile.write(b"0\r\n\r\n")
            return

        if self.path.startswith("/pdf"):
            body, ctype = PDF_BODY, "application/pdf"
        elif self.path.startswith("/big"):
            body, ctype = BIG_HTML.encode("utf-8"), "text/html; charset=utf-8"
        elif self.path.startswith("/no-ctype"):
            body, ctype = NO_TYPE_HTML, None
        else:
            body, ctype = SMALL_HTML.encode("utf-8"), "text/html; charset=utf-8"

        self.send_response(200)
        if ctype is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture()
def server(monkeypatch):
    # S1 blocks 127.0.0.1 targets; use the operator bypass for the
    # local test server.
    monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_small_page_ok(server):
    _html, md, _links, _removed, _prov = _core.fetch_html_full(server + "/small", 10)
    assert "small page" in md


def test_default_cap_allows_large_text_page(server):
    # ~7 KB << 5 MiB default: the cap must not starve ordinary pages.
    _html, md, _links, _removed, _prov = _core.fetch_html_full(server + "/big", 10)
    assert "lorem ipsum" in md


def test_content_length_early_reject(server, monkeypatch):
    monkeypatch.setenv("STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES", "1000")
    with pytest.raises(RuntimeError, match="Response too large: declared"):
        _core.fetch_html_full(server + "/big", 10)


def test_streaming_cap_chunked(server, monkeypatch):
    # No Content-Length header: the chunk-level cap must trip.
    monkeypatch.setenv("STITCH_WEB_RESEARCHER_MAX_RESPONSE_BYTES", "1000")
    with pytest.raises(RuntimeError, match="exceeds size cap"):
        _core.fetch_html_full(server + "/chunked", 10)


def test_explicit_max_bytes_kwarg(server):
    with pytest.raises(RuntimeError, match="Response too large: declared"):
        _core.fetch_html_full(server + "/big", 10, max_bytes=1000)


def test_pdf_content_type_rejected(server):
    with pytest.raises(
        RuntimeError,
        match=r"Unsupported content type: application/pdf.*extract_document",
    ):
        _core.fetch_html_full(server + "/pdf", 10)


def test_missing_content_type_allowed(server):
    # No Content-Type header: allowed through (downstream _looks_like_text
    # remains the safety net).
    _html, md, _links, _removed, _prov = _core.fetch_html_full(server + "/no-ctype", 10)
    assert "no content type" in md


def test_batch_respects_cap(server):
    results = _core.batch_research([server + "/big"], max_bytes=1000)
    assert len(results) == 1
    url, _html, md, links = results[0]
    assert url.startswith(server)
    assert md is not None and "too large" in md
    assert links is None


def test_toolbox_config_threads_cap(server, tmp_path):
    tb = WebResearcherToolbox(
        ToolboxConfig(
            cache_dir=str(tmp_path / "cache"),
            domain_delay=0.0,
            ddgs_delay=0.0,
            fetch_mode="static",
            max_response_bytes=1000,
        )
    )
    result = json.loads(tb.inspect_html_page(server + "/big"))
    assert "error" in result
    assert "too large" in result["error"]


def test_toolbox_config_default_cap_allows_normal_page(server, tmp_path):
    tb = WebResearcherToolbox(
        ToolboxConfig(
            cache_dir=str(tmp_path / "cache"),
            domain_delay=0.0,
            ddgs_delay=0.0,
            fetch_mode="static",
        )
    )
    result = json.loads(tb.inspect_html_page(server + "/big"))
    assert "error" not in result
    assert "lorem ipsum" in result["markdown"]
