# tests/test_t2_9_async.py
"""Tier 2.9 / review item 9 -- real async path (thread-pool semantics).

The ``*_async`` toolbox methods are convenience wrappers that offload the
shared *blocking* implementation to the default thread-pool executor
(``loop.run_in_executor(None, ...)``). This file proves that contract and
the "document it clearly" part of the finding:

- ``batch_inspect_pages_async`` delegates to ``batch_inspect_pages`` via the
  executor and returns its result (item 9: batch was the missing async
  counterpart).
- The shared batch implementation runs on a worker thread (the executor),
  not on the event-loop thread.
- All three async wrappers document the thread-pool semantics in their
  docstrings so users understand "async" here means "worker thread, event
  loop stays responsive" -- not "native async I/O".

All tests are deterministic -- no live network, no browser.
"""

import asyncio
import json
import threading

from gossamer import agent_tools
from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox


def _make_toolbox(tmp_path, **overrides):
    defaults = dict(
        cache_dir=tmp_path / "cache",
        max_concurrency=4,
        respect_robots=False,
    )
    defaults.update(overrides)
    return WebResearcherToolbox(config=ToolboxConfig(**defaults))


class TestBatchAsyncDelegation:
    def test_batch_inspect_pages_async_delegates(self, tmp_path, monkeypatch):
        tb = _make_toolbox(tmp_path)
        calls: list = []

        def _fake_batch(urls):
            # Instance-attribute fake: invoked as _fake_batch(urls) -- no
            # self is bound (instance attributes bypass the descriptor
            # protocol).
            calls.append(list(urls))
            return json.dumps(
                [
                    {"url": u, "markdown": "m", "links": [], "metadata": {}}
                    for u in urls
                ],
                ensure_ascii=False,
            )

        monkeypatch.setattr(tb, "batch_inspect_pages", _fake_batch)
        out = asyncio.run(
            tb.batch_inspect_pages_async(
                ["https://a.example", "https://b.example"]
            )
        )
        assert calls == [["https://a.example", "https://b.example"]]
        parsed = json.loads(out)
        assert [e["url"] for e in parsed] == [
            "https://a.example",
            "https://b.example",
        ]

    def test_batch_impl_runs_on_executor_thread(self, tmp_path, monkeypatch):
        """The shared impl runs in the worker thread, not the loop thread."""
        tb = _make_toolbox(tmp_path)
        loop_thread = threading.get_ident()
        worker_threads: list = []

        def _fake_batch(urls):
            worker_threads.append(threading.get_ident())
            return json.dumps([])

        monkeypatch.setattr(tb, "batch_inspect_pages", _fake_batch)
        asyncio.run(tb.batch_inspect_pages_async(["https://a.example"]))
        assert worker_threads, "batch implementation never ran"
        assert all(t != loop_thread for t in worker_threads), (
            "batch implementation ran on the event-loop thread instead of "
            "the thread-pool executor"
        )


class TestAsyncDocstrings:
    def test_all_async_wrappers_document_thread_pool_semantics(self):
        for name in (
            "search_web_async",
            "inspect_html_page_async",
            "batch_inspect_pages_async",
        ):
            doc = (
                getattr(agent_tools.WebResearcherToolbox, name).__doc__ or ""
            )
            assert "thread pool" in doc.lower(), (
                f"{name} docstring must document the thread-pool "
                "(run_in_executor) semantics"
            )
