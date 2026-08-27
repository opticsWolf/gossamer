"""S5: thread-safe cache + in-flight URL guard.

The MCP SDK dispatches synchronous tools on worker threads
(``anyio.to_thread.run_sync``), so tool calls run concurrently against
a single process-wide toolbox. These tests hammer the cache and the
toolbox from multiple threads to verify:

  * the memory tier, eviction, and stat counters stay consistent,
  * disk entries are written atomically (no ``*.tmp`` leftovers),
  * stale ``*.tmp`` files from a crashed mid-write are cleaned on init,
  * concurrent calls for the same new URL trigger exactly ONE fetch,
  * C3 semantics are preserved: failed URLs remain retryable.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.cache import Cache

N_THREADS = 8


# ───────────────────────────────────────────────
# Local test server
# ───────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    """Serves trivial pages; counts per-path hits. ``/sleep`` is slow so
    the in-flight window is wide enough for all threads to pile up."""

    protocol_version = "HTTP/1.1"
    hits: dict = {}
    hits_lock = threading.Lock()

    def log_message(self, *args):
        pass

    def _record(self):
        path = self.path.split("?")[0]
        with type(self).hits_lock:
            type(self).hits[path] = type(self).hits.get(path, 0) + 1

    def do_GET(self):
        self._record()
        path = self.path.split("?")[0]
        if path == "/sleep":
            time.sleep(0.25)
        body = (
            f"<html><head><title>T{path}</title></head>"
            f"<body><main>hello {path}</main></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    _Handler.hits = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("STITCH_WEB_RESEARCHER_ALLOW_PRIVATE", "1")
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _toolbox(tmp_path) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        ToolboxConfig(
            cache_dir=str(tmp_path / "cache"),
            domain_delay=0.0,
            fetch_delay=0.0,  # explicit 0 beats the provider's 0.5 s default
            ddgs_delay=0.0,
            fetch_mode="static",
        )
    )


def _run_parallel(fn, n: int = N_THREADS) -> list:
    """Run ``fn(i)`` for ``i in range(n)`` on ``n`` threads (barrier-synced
    start). Returns the list of exceptions raised by the workers."""
    errors: list = []
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        try:
            fn(i)
        except Exception as e:  # collected for assertions
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


# ───────────────────────────────────────────────
# Cache concurrency
# ───────────────────────────────────────────────

class TestCacheConcurrency:
    def test_parallel_put_get_no_corruption(self, tmp_path):
        """8 threads x 20 puts; every key must then be readable from
        either tier, counters consistent, and no temp files left behind."""
        c = Cache(tmp_path / "c", max_memory_entries=50, ttl_seconds=300)
        expected: dict = {}

        def worker(i):
            for j in range(20):
                key = f"u{i}_{j}"
                value = f"value-{i}-{j}" * 50
                expected[key] = value
                c.put(key, value)

        errors = _run_parallel(worker)
        assert not errors, errors

        for key, value in expected.items():
            assert c.get(key) == value, key

        stats = c.stats()
        assert stats["memory_entries"] <= 50
        assert stats["total_hits"] == len(expected)
        assert stats["total_misses"] == 0
        assert not list((tmp_path / "c").glob("*.tmp"))

    def test_parallel_get_stats_clear(self, tmp_path):
        """Interleaved get/stats/clear from many threads must not raise."""
        c = Cache(tmp_path / "c", max_memory_entries=20, ttl_seconds=300)
        for j in range(30):
            c.put(f"seed_{j}", f"v{j}" * 20)

        def worker(i):
            for j in range(15):
                c.get(f"seed_{(i * 7 + j) % 30}")
                c.stats()
                if j == 7:
                    c.clear()

        errors = _run_parallel(worker)
        assert not errors, errors

    def test_stale_tmp_cleaned_on_init(self, tmp_path):
        d = tmp_path / "c"
        d.mkdir()
        (d / "abc.cache.deadbeef.tmp").write_text("half-written")
        (d / "ok.cache").write_text("fine")
        Cache(d)
        assert not list(d.glob("*.tmp"))
        assert (d / "ok.cache").exists()


# ───────────────────────────────────────────────
# Toolbox concurrency
# ───────────────────────────────────────────────

class TestToolboxConcurrency:
    def test_same_url_fetched_exactly_once(self, server, tmp_path):
        """8 threads race for the same slow URL: exactly one HTTP request
        may reach the server; the rest get a warning or a cache hit."""
        tb = _toolbox(tmp_path)
        url = f"{server}/sleep"
        results: list = []
        results_lock = threading.Lock()

        def worker(i):
            r = json.loads(tb.inspect_html_page(url))
            with results_lock:
                results.append(r)

        errors = _run_parallel(worker)
        assert not errors, errors
        assert len(results) == N_THREADS
        for r in results:
            assert "error" not in r, r
        ok = [r for r in results if "markdown" in r]
        warned = [r for r in results if r.get("warning") == "URL already visited"]
        assert ok, "at least one call must have fetched the page"
        assert len(ok) + len(warned) == N_THREADS
        assert _Handler.hits.get("/sleep", 0) == 1
        assert url in tb.visited_urls

    def test_unique_urls_all_succeed(self, server, tmp_path):
        """Unique URLs across threads: no errors, every URL visited
        exactly once at the server."""
        tb = _toolbox(tmp_path)
        urls = [f"{server}/p{i}" for i in range(N_THREADS * 2)]
        seen: list = []
        seen_lock = threading.Lock()

        def worker(i):
            for offset in range(2):
                url = urls[i * 2 + offset]
                r = json.loads(tb.inspect_html_page(url))
                assert "error" not in r, (url, r)
                assert "markdown" in r, (url, r)
                with seen_lock:
                    seen.append(url)

        errors = _run_parallel(worker)
        assert not errors, errors
        assert sorted(seen) == sorted(urls)
        for url in urls:
            assert url in tb.visited_urls
        for i in range(N_THREADS * 2):
            assert _Handler.hits.get(f"/p{i}", 0) == 1

    def test_batch_and_single_share_in_flight_guard(self, server, tmp_path):
        """A batch claiming URLs makes concurrent single-page calls for
        the same URLs warn instead of double-fetching."""
        tb = _toolbox(tmp_path)
        urls = [f"{server}/b{i}" for i in range(3)]
        single_results: list = []
        single_lock = threading.Lock()
        start = threading.Event()

        def single_worker():
            start.wait()
            r = json.loads(tb.inspect_html_page(urls[0]))
            with single_lock:
                single_results.append(r)

        def batch_worker():
            start.wait()
            # Hold the in-flight claims for urls[0] (via the batch) while
            # the single-page call races in.
            time.sleep(0.1)
            out = json.loads(tb.batch_inspect_pages(urls))
            with single_lock:
                single_results.append(("batch", out))

        single = threading.Thread(target=single_worker)
        batch = threading.Thread(target=batch_worker)
        single.start()
        batch.start()
        start.set()
        single.join()
        batch.join()

        # Exactly one fetch of urls[0] total (batch or single, not both).
        assert _Handler.hits.get("/b0", 0) == 1
        # The single-page call got either the page or the in-flight
        # warning — never an error.
        single_r = [r for r in single_results if not isinstance(r, tuple)]
        assert single_r
        assert "error" not in single_r[0]

    def test_failed_url_stays_retryable_under_concurrency(self, server, tmp_path):
        """C3 + S5: a URL that fails for one thread (claim released) is
        still fetchable by another thread — the in-flight guard does not
        poison failures the way visited_urls did before C3."""
        tb = _toolbox(tmp_path)
        url = f"{server}/flaky"

        # Exactly three fetch attempts fail; every thread that claimed the
        # URL during a failing attempt reports the error, threads that
        # lose the claim race get the in-flight warning, and the fourth
        # claim succeeds.
        failures = {"n": 0}
        fail_lock = threading.Lock()

        orig_fetch = tb._fetch_html

        def flaky_fetch(*args, **kwargs):
            with fail_lock:
                if failures["n"] < 3:
                    failures["n"] += 1
                    raise RuntimeError("simulated reset")
            return orig_fetch(*args, **kwargs)

        tb._fetch_html = flaky_fetch
        results: list = []
        results_lock = threading.Lock()

        def worker(i):
            # Retry until the page is served, like a real agent would:
            # error -> retry, in-flight warning -> retry.
            last = None
            for _ in range(20):
                last = json.loads(tb.inspect_html_page(url))
                if "markdown" in last:
                    break
                time.sleep(0.05)
            with results_lock:
                results.append(last)

        errors = _run_parallel(worker)
        assert not errors, errors
        assert len(results) == N_THREADS
        # Every worker eventually gets the page...
        assert all("markdown" in r for r in results), results
        # ...and all three budgeted failures were consumed exactly once.
        assert failures["n"] == 3
        assert url in tb.visited_urls
        assert url not in tb._in_flight
